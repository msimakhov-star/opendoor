"""Open Door pipeline: find practices, resolve and capture on Modal, classify with Gemini, box the verified quotes.
Writes data/runs/<run_id>/{events.jsonl, results.json, stats.json, shots/<code>.png}. Reads website wording only, never front-desk behaviour.

  uv run python -m opendoor.pipeline --postcode "E13 8AA" --limit 40            (one postcode or outcode: the 40 nearest practices)
  uv run python -m opendoor.pipeline --postcode E13,E6,E7 --limit 40            (several prefixes: every practice in them)
  uv run python -m opendoor.pipeline --london --limit 1500                      (every London postcode area)
  uv run python -m opendoor.pipeline --fake     (no network, no Modal, no Gemini: the 40 Newham practices in fixtures/fake, whatever the postcode)
"""
import argparse, asyncio, json, re, shutil, threading, time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from opendoor.models import COLOUR, DOC_RE, NHS_GUIDANCE_QUOTE, NHS_GUIDANCE_URL, Finding, PracticeResult, SecondOpinion, doc_types, norm
from opendoor.orgs import LONDON_PREFIXES, find_nearest, find_practices, single_place

ROOT = Path(__file__).resolve().parent.parent
RUNS, CACHE = ROOT / "data" / "runs", ROOT / "data" / "cache"
FAKE = ROOT / "fixtures" / "fake"  # practices.json + shots/, built from data/frozen/newham
VERDICTS = ROOT / "data" / "verdicts.json"  # latest real run, one object per quoted practice, for a teammate
BOXED = ("demands_documents", "asks_softly", "says_not_needed")  # categories whose verified quote gets boxed on a screenshot
MIN_TEXT = 200  # a page with less visible text than this was not really read, so it must not show as green
DISAGREED = "A second reader arguing the surgery's side did not agree it is a requirement."
NO_SECOND = "The second reader gave no answer, so this is not shown as red."
UNREACHABLE = "The practice website could not be reached."


def _readable(reason: str) -> str:
    """Grey-pin reasons in plain words, not error text. The timeout wording keeps "timeout": _why() sorts on it."""
    if "net::ERR_" in reason:
        return UNREACHABLE
    if reason.startswith("TimeoutError"):
        return "The practice website did not load within the page timeout."
    if reason.startswith(("HTTPError", "URLError")):  # resolve's one GET to the practice's nhs.uk profile
        return "The practice has no profile page on nhs.uk." if "404" in reason else "The practice's nhs.uk profile could not be read."
    return reason


def parse_prefixes(s: str) -> list[str]:
    """One place stays whole: 'e13 9az' -> ['E13 9AZ'], 'E13' -> ['E13'] (both mean: nearest practices).
    Otherwise prefixes: 'e13, e6 E7' -> ['E13', 'E6', 'E7'];  'SE' -> ['SE'];  'London' -> []."""
    one = single_place(s)
    return [one] if one else list(dict.fromkeys(re.findall(r"\b[A-Z]{1,2}(?:\d[A-Z\d]?)?\b", s.upper())))


def new_run_id(prefixes: list[str]) -> str:
    return time.strftime("%Y%m%d-%H%M%S-") + "-".join(p.replace(" ", "") for p in prefixes)[:20]


def apply_finding(r: PracticeResult, f: Finding) -> None:
    r.category, r.quote, r.quote_verified = f.category, f.quote, f.quote_verified
    r.documents, r.reason, r.retries = f.documents, f.reason, f.retries
    r.colour = COLOUR[r.category]
    r.self_contradiction = bool(r.links_national_form and r.category == "demands_documents")
    r.doc_types = doc_types([r.quote] + r.documents) if r.colour in ("red", "amber") else []


def peak_overlap(spans: list[tuple[float, float]]) -> int:
    """Most (start, end) spans open at one moment. The peak always falls on some span's start."""
    return max((sum(a <= s < b for a, b in spans) for s, _ in spans), default=0)


def _why(e: dict) -> str:
    """Why a practice was not checked, from its last classified event. Anything else (a page with almost no text, a classifier error) is error."""
    if e.get("status") == "blocked":
        return "blocked"
    if "timeout" in (e.get("reason") or "").lower():
        return "timeout"
    if e.get("status") == "no_site":
        return "no_site"
    return "no_registration_page" if (e.get("reason") or "").startswith("No registration page") else "error"


def event_stats(events: list[dict]) -> dict:
    """Run numbers computed from the events of one run (the last run_started onwards). The last classified event per code wins."""
    events = events[max([i for i, e in enumerate(events) if e["type"] == "run_started"], default=0):]
    last, cap, found, fin = {}, {}, {}, {}
    for e in events:
        if e["type"] == "classified":
            last[e["code"]] = e
        elif e["type"] == "captured":
            cap[e["code"]] = e
        elif e["type"] == "practices_found":
            found = e
        elif e["type"] == "run_finished":
            fin = e
    cls, so = list(last.values()), [e for e in events if e["type"] == "second_opinion"]
    nc = [e for e in cls if e["category"] == "not_checked"]
    return {
        "practices": found.get("count", 0), "outside_area": found.get("outside_area", 0),
        "pages_read_ok": sum(e["status"] == "ok" and (e.get("secs") or 0) > 0 for e in cap.values()),  # 0 s: only a national form link, never loaded
        "not_checked": {"total": len(nc), **{k: 0 for k in ("blocked", "timeout", "no_site", "no_registration_page", "error")}, **Counter(map(_why, nc))},
        "counts": {c: 0 for c in COLOUR} | Counter(e["category"] for e in cls),
        "self_contradictions": sum(bool(e.get("self_contradiction")) for e in cls),
        "quotes_verified": sum(bool(e.get("quote")) for e in cls),  # the classifier empties every quote code could not find on the page
        "quotes_rejected": sum(e["type"] == "quote_rejected" and not e.get("cached") for e in events),
        "second_opinions": {"checked": len(so), "agreed": sum(bool(e["agreed"]) for e in so), "downgraded": sum(not e["agreed"] for e in so),
                            "no_answer": sum(e["verdict"] == "not_checked" for e in so)},
        "gemini_calls": fin.get("gemini_calls"), "cache_hits": sum(bool(e.get("from_cache")) for e in cls),
        "containers_peak": fin.get("containers_peak"), "wall_secs": fin.get("wall_secs"),
    }


def _warm():
    """Boots one Modal browser container. It does not warm capture's own pool (each Modal Function has its own);
    capture's scaledown_window=900 keeps its containers up between a rehearsal and the take.
    ponytail: a spawn_map of no-op capture calls was tried at 14:08 and that run's tail ran one capture at a time (161 s)."""
    try:  # a failed warm-up must never stop a run
        import modal
        modal.Function.from_name("opendoor", "warm").spawn()
    except Exception:
        pass


def write_verdicts(results: list[PracticeResult], run_dir: Path, path: Path = VERDICTS) -> int:
    rows = [{"practice_name": r.name, "practice_url": r.site, "page_url": r.reg_url,
             "classification": "compliant" if r.category in ("says_not_needed", "no_mention") else r.category,
             "quote": r.quote, "nhs_quote": NHS_GUIDANCE_QUOTE, "nhs_url": NHS_GUIDANCE_URL,
             "screenshot": str((run_dir / r.shot).resolve()) if r.shot else None}
            for r in results if r.quote and r.category != "not_checked"]
    path.write_text(json.dumps(rows, indent=1))
    return len(rows)


async def _amap(fn, inputs):
    """Stream results as they finish. fn is a deployed modal.Function, or a plain local function in --fake mode."""
    if hasattr(fn, "map"):
        async for out in fn.map.aio(inputs, order_outputs=False, return_exceptions=True):
            yield out
    else:
        for x in inputs:
            await asyncio.sleep(0.05)
            yield fn(x)


async def _arun(orgs, R, emit, shots, resolve, capture, box, classify, second) -> int:
    """Returns the peak number of capture calls running at once, from each call's own runtime."""
    hit = {}  # code -> its verdict came from an earlier run's disk cache

    def classified(r):
        emit("classified", code=r.code, category=r.category, colour=r.colour, quote=r.quote, retries=r.retries,
             doc_types=r.doc_types, distance_km=r.distance_km, from_cache=hit.get(r.code, False),
             status=r.status, reason=r.reason, self_contradiction=r.self_contradiction)

    async def second_look(r, f, text):
        """Before a surgery stays red, a second reader argues the surgery's side. Anything but agreement shows it amber."""
        try:
            o = await second(r.code, text, f)
        except Exception as e:
            o = SecondOpinion(verdict="not_checked", agreed=False, reason=f"The second reader gave no answer ({type(e).__name__}).")
        agreed = bool(o.agreed) and o.verdict == "demands_documents"
        emit("second_opinion", code=r.code, verdict=o.verdict, agreed=agreed, reason=o.reason)
        if not agreed:  # a NEW classified event: the UI renders the last one per code. The first reader's red reason is dropped:
            # it would argue red on an amber pin. o.reason already passed classify._plain with the amber rule.
            apply_finding(r, f.model_copy(update={"category": "asks_softly",
                                                  "reason": NO_SECOND if o.verdict == "not_checked" else f"{DISAGREED} {o.reason}".strip()}))
            classified(r)

    def finish(r, status, reason):  # a practice that never reaches the classifier still gets its grey pin
        r.status, r.reason = status, _readable(reason)
        classified(r)

    async def box_one(job):  # box draws on capture's offline snapshot, found by final_url, so it rarely touches the site again
        try:
            b = await box.remote.aio(job) if hasattr(box, "remote") else box(job)
        except Exception:
            return
        if not isinstance(b, dict) or not b.get("_png") or not b.get("quote_box"):
            return  # quote not found on the page: keep the plain capture screenshot
        if job["code"] == "nhs":
            CACHE.mkdir(parents=True, exist_ok=True)
            (CACHE / "nhs.png").write_bytes(b["_png"])
            (CACHE / "nhs.json").write_text(json.dumps(b["quote_box"]))
        else:
            r = R[job["code"]]
            (shots / f"{r.code}.png").write_bytes(b["_png"])
            r.quote_box, r.shot = b["quote_box"], f"shots/{r.code}.png"
            emit("boxed", code=r.code, shot=r.shot)

    def on_reject(code, attempt, reason, cached=False):
        emit("quote_rejected", code=code, attempt=attempt, reason=reason, **({"cached": True} if cached else {}))

    async def classify_one(r, c):
        text = c.get("text") or ""
        if c.get("national_form_only"):
            f = Finding(category="no_mention", reason="The registration link goes straight to the national NHS registration form.")
        elif not c.get("reg_links"):  # only the homepage was read (or a parked domain): that is not a registration page
            f = Finding(category="not_checked", reason="No registration page found on the practice website.")
        elif len(text) < MIN_TEXT:
            f = Finding(category="not_checked", reason="The page had almost no readable text.")
        else:
            try:
                f = await classify(r.code, text, on_reject=on_reject)
            except Exception as e:
                f = Finding(category="not_checked", reason=f"Classifier error: {type(e).__name__}")
        apply_finding(r, f)
        hit[r.code] = f.from_cache
        classified(r)
        if r.category == "demands_documents" and r.quote_verified:
            await second_look(r, f, text)
        if r.quote_verified and r.quote and r.category in BOXED:
            await box_one({"code": r.code, "final_url": r.reg_url, "reg_url": r.reg_url, "quote": r.quote, "colour": r.colour})

    spans = []

    async def capture_one(t):
        """Starts as soon as this practice is resolved, so the first pages are read while other practices are still looked up."""
        r = R[t["code"]]
        try:
            c = await capture.remote.aio(t) if hasattr(capture, "remote") else capture(t)
        except Exception:
            c = None
        if not isinstance(c, dict):
            return finish(r, "error", "The page capture failed.")
        now = time.time()
        spans.append((now - (c.get("secs") or 0.0), now))  # when this call ran, by its own clock; 0 s calls never opened a browser
        if c.get("text"):  # kept so a human can check the classification against exactly what was read
            (shots.parent / "texts").mkdir(exist_ok=True)
            (shots.parent / "texts" / f"{r.code}.txt").write_text(c["text"])
        if c.get("_png"):
            (shots / f"{r.code}.png").write_bytes(c["_png"])
            r.shot = f"shots/{r.code}.png"
        r.status = c.get("status") if c.get("status") in ("ok", "blocked", "no_site", "error") else "error"
        r.reg_url = c.get("final_url") or r.reg_url
        r.links_national_form, r.doc_hits, r.secs = bool(c.get("links_national_form")), c.get("doc_hits") or [], c.get("secs") or 0.0
        r.checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        emit("captured", code=r.code, status=r.status, challenge=bool(c.get("challenge")), secs=r.secs, http_status=c.get("http_status"))
        if r.status == "ok":
            await classify_one(r, c)
        else:  # finish() turns error text (net::ERR_NAME_NOT_RESOLVED, TimeoutError ...) into plain words
            finish(r, r.status, (c.get("error") or "The site did not let the automated browser read the page.").splitlines()[0])

    tasks = []
    if not (CACHE / "nhs.png").exists():  # the nhs.uk reference screenshot, made once and reused by later runs
        tasks.append(asyncio.create_task(box_one({"code": "nhs", "final_url": NHS_GUIDANCE_URL, "reg_url": NHS_GUIDANCE_URL,
                                                  "quote": NHS_GUIDANCE_QUOTE, "colour": "green"})))
    started = set()
    async for t in _amap(resolve, orgs):
        if not isinstance(t, dict) or t.get("code") not in R:
            continue
        r = R[t["code"]]
        r.site, r.reg_url = t.get("site"), t.get("reg_url")
        emit("resolved", code=r.code, name=r.name, lat=r.lat, lon=r.lon, reg_url=r.reg_url)
        if r.reg_url or r.site:
            started.add(r.code)
            tasks.append(asyncio.create_task(capture_one(t)))
        else:
            finish(r, "error" if t.get("error") else "no_site", t.get("error") or "No website listed on the practice's nhs.uk profile.")
    for r in R.values():
        if r.code not in started and r.status == "ok" and not r.reason:
            finish(r, "error", "Could not look up the practice website.")
    await asyncio.gather(*tasks)
    return peak_overlap(spans)


def run(postcode_prefixes, limit, run_id=None, on_event=None, fake=False, region=None) -> dict:
    """Blocking. Returns the summary. on_event(event_dict) is called for every event, in order.
    One full postcode or outcode (['E13 8AA'] or ['E13']) finds the `limit` nearest practices; anything else sweeps prefixes.
    What a person does not have never reaches the server: the UI filters in the browser."""
    t0 = time.time()
    run_id = run_id or new_run_id(postcode_prefixes)
    near = single_place(postcode_prefixes[0]) if len(postcode_prefixes) == 1 and not region else None
    run_dir = RUNS / run_id
    shots = run_dir / "shots"
    shots.mkdir(parents=True, exist_ok=True)
    log = open(run_dir / "events.jsonl", "a")
    n_rejected, evs = 0, []

    def emit(type, **kw):
        nonlocal n_rejected
        n_rejected += type == "quote_rejected" and not kw.get("cached")  # a cache hit replays an earlier run's rejections
        ev = {"type": type, "t": round(time.time() - t0, 2), **kw}
        evs.append(ev)
        log.write(json.dumps(ev) + "\n")
        log.flush()
        if on_event:
            on_event(ev)

    R, error, stats, calls0, peak, where, far = {}, None, {}, 0, None, {}, []
    emit("run_started", postcode_prefixes=postcode_prefixes, limit=limit, run_id=run_id, fake=fake, near=near)
    if not fake:
        emit("warming")  # a Modal browser container boots while practices are found and resolved
        threading.Thread(target=_warm, daemon=True).start()
    try:
        fx = _fakes() if fake else None
        if near:
            orgs, centre = (fx["orgs"][:max(0, limit)], fx["centre"]) if fake else find_nearest(near, limit)
            where = {"centre": centre, "outcodes": sorted({o["postcode"].split()[0] for o in orgs}),
                     "furthest_km": orgs[-1]["distance_km"] if orgs else None}
        else:
            orgs = fx["orgs"][:max(0, limit)] if fake else find_practices(postcode_prefixes, limit, region=region, dropped=far)
            where = {"outside_area": len(far), "outside_area_codes": [o["code"] for o in far]}  # geocoded outside a box around the areas
        R = {o["code"]: PracticeResult(**{k: o.get(k) for k in ("code", "name", "postcode", "lat", "lon", "distance_km")}) for o in orgs}
        emit("practices_found", count=len(orgs), **where)
        if fake:
            resolve, capture, box, classify, second = (fx[k] for k in ("resolve", "capture", "box", "classify", "second_opinion"))
        else:
            import modal
            from opendoor import classify as cl  # lazy: pulls in pydantic-ai and logfire
            second = getattr(cl, "second_opinion", None)
            if second is None:  # no surgery may be shown red without the second reader, so stop before any site is contacted
                raise RuntimeError("opendoor.classify has no second_opinion(code, text, finding) yet: no practice page was read.")
            resolve, capture, box = (modal.Function.from_name("opendoor", n) for n in ("resolve", "capture", "box"))
            classify, stats, calls0 = cl.classify_page, cl.STATS, cl.STATS["requests"]
        peak = asyncio.run(_arun(orgs, R, emit, shots, resolve, capture, box, classify, second))
        peak = None if fake else peak  # fake captures replay recorded timings, not containers
    except Exception as e:  # still write what we have and close the run, so the UI stops polling
        error = f"{type(e).__name__}: {str(e)[:300]}"

    nhs = (CACHE / "nhs.png").exists()
    if nhs:
        shutil.copy(CACHE / "nhs.png", shots / "nhs.png")
    results = list(R.values())
    counts = {c: 0 for c in COLOUR} | Counter(r.category for r in results)
    ev = event_stats(evs)
    so = {k: ev["second_opinions"][k] for k in ("checked", "agreed", "downgraded")}
    summary = {
        "run_id": run_id, "postcode_prefixes": postcode_prefixes, "limit": limit, "fake": fake, "error": error,
        "near": near, **where, "containers_peak": peak,
        "total": len(results), "counts": counts,
        "statuses": dict(Counter(r.status for r in results)),
        "self_contradictions": sum(r.self_contradiction for r in results),
        "quotes_verified": sum(r.quote_verified for r in results),
        "quotes_boxed": sum(1 for r in results if r.quote_box),
        "quotes_rejected": n_rejected,
        "gemini_calls": stats.get("requests", 0) - calls0,
        "cache_hits": ev["cache_hits"],  # pages whose verdict came from an earlier run's disk cache
        "second_opinions": so,
        "wall_secs": round(time.time() - t0, 1),
        "nhs": {"url": NHS_GUIDANCE_URL, "quote": NHS_GUIDANCE_QUOTE, "shot": "shots/nhs.png" if nhs else None,
                "quote_box": json.loads((CACHE / "nhs.json").read_text()) if nhs and (CACHE / "nhs.json").exists() else None},
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (run_dir / "results.json").write_text(json.dumps({"summary": summary, "results": [r.model_dump() for r in results]}, indent=1))
    if results and not fake and not error:  # fake runs use a keyword classifier and a failed run is partial: never hand those on
        write_verdicts(results, run_dir)
    emit("run_finished", counts=counts, wall_secs=summary["wall_secs"], total=len(results), quotes_rejected=n_rejected,
         gemini_calls=summary["gemini_calls"], cache_hits=summary["cache_hits"], second_opinions=so, error=error, containers_peak=peak)
    log.close()
    write_stats(run_dir)
    return summary


def write_stats(run_dir: Path) -> dict:
    """data/runs/<id>/stats.json, computed from that run's events.jsonl only."""
    s = event_stats([json.loads(l) for l in (run_dir / "events.jsonl").read_text().splitlines() if l.strip()])
    (run_dir / "stats.json").write_text(json.dumps(s, indent=1))
    return s


# ---------- --fake: local stand-ins so the pipeline, the API and the UI can be exercised without Modal or Gemini ----------
_NOT_NEEDED = re.compile(r"do(?:es)? not (?:need|have)|don.t (?:need|have)|not (?:required|necessary|needed)|still register|without (?:id|proof|documents)", re.I)
_SOFT = re.compile(r"if you (?:can|have)|may (?:request|ask)|helpful|speed up|if possible", re.I)
_DEMAND = re.compile(r"\bmust\b|need to|required|will need|should (?:provide|bring)|please (?:provide|bring)|will be asked|produce", re.I)


def _fakes() -> dict:
    """Stand-ins that replay fixtures/fake/practices.json: what each Newham practice's resolve and capture returned in a real run."""
    fx = json.loads((FAKE / "practices.json").read_text())
    pages = {g["code"]: g for g in fx["practices"]}
    orgs = [{k: g[k] for k in ("code", "name", "postcode", "lat", "lon", "distance_km")} for g in fx["practices"]]  # nearest first
    state = {"rejected": False, "disagreed": False}
    png = lambda code: (FAKE / "shots" / f"{code}.png").read_bytes() if (FAKE / "shots" / f"{code}.png").exists() else None

    def resolve(org):
        g = pages.get(org["code"], {})
        return {**org, "site": g.get("site"), "reg_links": g.get("reg_links", []), "reg_url": g.get("reg_url"), "error": g.get("resolve_error")}

    def capture(t):
        g = pages.get(t["code"], {})
        text = norm(g.get("text"))
        return {**t, "status": g.get("status", "error"), "http_status": g.get("http_status"), "final_url": g.get("reg_url") or t.get("reg_url"),
                "title": "", "challenge": bool(g.get("challenge")), "text": text, "links_national_form": bool(g.get("links_national_form")),
                "national_form_only": bool(g.get("national_form_only")), "reg_links": g.get("reg_links", []),
                "doc_hits": sorted({m.group(0).lower() for m in DOC_RE.finditer(text)}), "_png": png(t["code"]),
                "secs": g.get("secs") or 0.0, "error": g.get("error")}

    def box(t):  # the recorded PNG already has the frozen quote boxed: hand it on only when the fake quote contains that sentence
        g = pages.get(t["code"]) or {"quote_box": fx.get("nhs_quote_box"), "quote": NHS_GUIDANCE_QUOTE}
        b = g.get("quote_box") if g.get("quote") and norm(g["quote"]) in norm(t.get("quote")) else None
        return {**t, "found": bool(b), "quote_box": b, "_png": png(t["code"]) if b else None, "error": None}

    async def classify(code, text, on_reject=None):
        ss = [s for s in re.split(r"(?<=[.!?])\s+", norm(text)) if 20 < len(s) < 400 and DOC_RE.search(s)]
        first = lambda rx: next((s for s in ss if rx.search(s)), "")
        nn, soft, dem = first(_NOT_NEEDED), first(_SOFT), first(_DEMAND)
        if not ss:
            return Finding(category="no_mention", reason="No document wording on the page.")
        cat, quote = (("asks_softly", soft or dem) if soft or (dem and nn) else ("demands_documents", dem) if dem
                      else ("says_not_needed", nn) if nn else ("unclear", ""))
        retries = 0
        if quote and on_reject and not state["rejected"]:  # one staged rejection per fake run, so the UI can show the event
            state["rejected"], retries = True, 1
            on_reject(code, 1, "FAKE RUN: staged rejection so the interface can be tested. No model was called.")
        return Finding(category=cat, quote=quote, quote_verified=bool(quote), retries=retries,
                       documents=sorted({m.group(0).lower() for m in DOC_RE.finditer(quote)}), reason="FAKE RUN: keyword match, no model was called.")

    async def second_opinion(code, text, finding):  # the first red disagrees, every later red agrees, so the UI can show both
        if not state["disagreed"]:
            state["disagreed"] = True
            return SecondOpinion(verdict="asks_softly", agreed=False, reason="FAKE RUN: staged disagreement so the interface can be tested. No model was called.")
        return SecondOpinion(verdict="demands_documents", agreed=True, reason="FAKE RUN: staged agreement. No model was called.")

    return {"orgs": orgs, "centre": fx["centre"], "resolve": resolve, "capture": capture, "box": box, "classify": classify, "second_opinion": second_opinion}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--postcode", default="E13", help="one or more postcode prefixes, comma separated: E13 or E13,E6,E7 or SE")
    ap.add_argument("--london", action="store_true", help="every London postcode area, keeping only practices postcodes.io places in London")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--run-id")
    ap.add_argument("--fake", action="store_true")
    a = ap.parse_args()
    show = lambda ev: print(json.dumps(ev)[:220])
    if a.london:
        s = run(LONDON_PREFIXES, a.limit, a.run_id, show, fake=a.fake, region="London")
    else:
        s = run(parse_prefixes(a.postcode), a.limit, a.run_id, show, fake=a.fake)
    print(json.dumps(s, indent=1))
