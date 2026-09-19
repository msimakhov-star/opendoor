"""Open Door pipeline: find practices, resolve and capture on Modal, classify with Gemini, box the verified quotes.
Writes data/runs/<run_id>/{events.jsonl, results.json, shots/<code>.png}. Reads website wording only, never front-desk behaviour.

  uv run python -m opendoor.pipeline --postcode E13 --limit 40
  uv run python -m opendoor.pipeline --postcode E13,E6,E7 --limit 40 --fake     (no Modal, no Gemini: probe data + keyword classifier)
  uv run python -m opendoor.pipeline --london --limit 1500                      (every London postcode area)
"""
import argparse, asyncio, json, re, shutil, time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from opendoor.models import COLOUR, DOC_RE, NHS_GUIDANCE_QUOTE, NHS_GUIDANCE_URL, Finding, PracticeResult, norm
from opendoor.orgs import LONDON_PREFIXES, find_practices

ROOT = Path(__file__).resolve().parent.parent
RUNS, CACHE = ROOT / "data" / "runs", ROOT / "data" / "cache"
BOXED = ("demands_documents", "asks_softly", "says_not_needed")  # categories whose verified quote gets boxed on a screenshot
MIN_TEXT = 200  # a page with less visible text than this was not really read, so it must not show as green


def parse_prefixes(s: str) -> list[str]:
    """'E13 9AZ' -> ['E13'];  'e13, e6 E7' -> ['E13', 'E6', 'E7'];  'SE' -> ['SE'];  'London' -> []."""
    return list(dict.fromkeys(re.findall(r"\b[A-Z]{1,2}(?:\d[A-Z\d]?)?\b", s.upper())))


def apply_finding(r: PracticeResult, f: Finding) -> None:
    r.category, r.quote, r.quote_verified = f.category, f.quote, f.quote_verified
    r.documents, r.reason, r.retries = f.documents, f.reason, f.retries
    r.colour = COLOUR[r.category]
    r.self_contradiction = bool(r.links_national_form and r.category == "demands_documents")


async def _amap(fn, inputs):
    """Stream results as they finish. fn is a deployed modal.Function, or a plain local function in --fake mode."""
    if hasattr(fn, "map"):
        async for out in fn.map.aio(inputs, order_outputs=False, return_exceptions=True):
            yield out
    else:
        for x in inputs:
            await asyncio.sleep(0.05)
            yield fn(x)


async def _arun(orgs, R, emit, shots, resolve, capture, box, classify):
    def finish(r, status, reason):  # a practice that never reaches the classifier still gets its grey pin
        r.status, r.reason = status, reason
        emit("classified", code=r.code, category=r.category, colour=r.colour, quote="", retries=0)

    # ponytail: resolve finishes for everyone before capture starts (3s on a 5 practice run). Feed capture an async generator if the demo needs it faster.
    targets = []
    async for t in _amap(resolve, orgs):
        if not isinstance(t, dict) or t.get("code") not in R:
            continue
        r = R[t["code"]]
        r.site, r.reg_url = t.get("site"), t.get("reg_url")
        emit("resolved", code=r.code, name=r.name, lat=r.lat, lon=r.lon, reg_url=r.reg_url)
        if r.reg_url or r.site:
            targets.append(t)
        else:
            finish(r, "error" if t.get("error") else "no_site", t.get("error") or "No website listed on the practice's nhs.uk profile.")
    done = {t["code"] for t in targets}
    for r in R.values():
        if r.code not in done and r.status == "ok" and not r.reason:
            finish(r, "error", "Could not look up the practice website.")

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

    async def classify_one(r, c):
        text = c.get("text") or ""
        if c.get("national_form_only"):
            f = Finding(category="no_mention", reason="The registration link goes straight to the national NHS registration form.")
        elif len(text) < MIN_TEXT:
            f = Finding(category="not_checked", reason="The page had almost no readable text.")
        else:
            try:
                f = await classify(r.code, text, on_reject=lambda code, attempt, reason: emit("quote_rejected", code=code, attempt=attempt, reason=reason))
            except Exception as e:
                f = Finding(category="not_checked", reason=f"Classifier error: {type(e).__name__}")
        apply_finding(r, f)
        emit("classified", code=r.code, category=r.category, colour=r.colour, quote=r.quote, retries=r.retries)
        if r.quote_verified and r.quote and r.category in BOXED:
            await box_one({"code": r.code, "final_url": r.reg_url, "reg_url": r.reg_url, "quote": r.quote, "colour": r.colour})

    tasks, captured = [], set()
    if not (CACHE / "nhs.png").exists():  # the nhs.uk reference screenshot, made once and reused by later runs
        tasks.append(asyncio.create_task(box_one({"code": "nhs", "final_url": NHS_GUIDANCE_URL, "reg_url": NHS_GUIDANCE_URL,
                                                  "quote": NHS_GUIDANCE_QUOTE, "colour": "green"})))
    async for c in _amap(capture, targets):
        if not isinstance(c, dict) or c.get("code") not in R:
            continue
        r = R[c["code"]]
        captured.add(r.code)
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
            tasks.append(asyncio.create_task(classify_one(r, c)))  # classification overlaps with the captures still running
        else:
            finish(r, r.status, (c.get("error") or "The site did not let the automated browser read the page.").splitlines()[0])
    for t in targets:
        if t["code"] not in captured:
            finish(R[t["code"]], "error", "The page capture failed.")
    await asyncio.gather(*tasks)


def run(postcode_prefixes, limit, run_id=None, on_event=None, fake=False, region=None) -> dict:
    """Blocking. Returns the summary. on_event(event_dict) is called for every event, in order."""
    t0 = time.time()
    run_id = run_id or time.strftime("%Y%m%d-%H%M%S-") + "-".join(postcode_prefixes)[:20]
    run_dir = RUNS / run_id
    shots = run_dir / "shots"
    shots.mkdir(parents=True, exist_ok=True)
    log = open(run_dir / "events.jsonl", "a")
    n_rejected = 0

    def emit(type, **kw):
        nonlocal n_rejected
        n_rejected += type == "quote_rejected"
        ev = {"type": type, "t": round(time.time() - t0, 2), **kw}
        log.write(json.dumps(ev) + "\n")
        log.flush()
        if on_event:
            on_event(ev)

    R, error, stats, calls0 = {}, None, {}, 0
    emit("run_started", postcode_prefixes=postcode_prefixes, limit=limit, run_id=run_id, fake=fake)
    try:
        orgs = find_practices(postcode_prefixes, limit, region=region)
        R = {o["code"]: PracticeResult(**{k: o.get(k) for k in ("code", "name", "postcode", "lat", "lon")}) for o in orgs}
        emit("practices_found", count=len(orgs))
        if fake:
            resolve, capture, box, classify = _fakes()
        else:
            import modal
            from opendoor import classify as cl  # lazy: pulls in pydantic-ai and logfire
            resolve, capture, box = (modal.Function.from_name("opendoor", n) for n in ("resolve", "capture", "box"))
            classify, stats, calls0 = cl.classify_page, cl.STATS, cl.STATS["requests"]
        asyncio.run(_arun(orgs, R, emit, shots, resolve, capture, box, classify))
    except Exception as e:  # still write what we have and close the run, so the UI stops polling
        error = f"{type(e).__name__}: {str(e)[:300]}"

    nhs = (CACHE / "nhs.png").exists()
    if nhs:
        shutil.copy(CACHE / "nhs.png", shots / "nhs.png")
    results = list(R.values())
    counts = {c: 0 for c in COLOUR} | Counter(r.category for r in results)
    summary = {
        "run_id": run_id, "postcode_prefixes": postcode_prefixes, "limit": limit, "fake": fake, "error": error,
        "total": len(results), "counts": counts,
        "statuses": dict(Counter(r.status for r in results)),
        "self_contradictions": sum(r.self_contradiction for r in results),
        "quotes_verified": sum(r.quote_verified for r in results),
        "quotes_boxed": sum(1 for r in results if r.quote_box),
        "quotes_rejected": n_rejected,
        "gemini_calls": stats.get("requests", 0) - calls0,
        "wall_secs": round(time.time() - t0, 1),
        "nhs": {"url": NHS_GUIDANCE_URL, "quote": NHS_GUIDANCE_QUOTE, "shot": "shots/nhs.png" if nhs else None,
                "quote_box": json.loads((CACHE / "nhs.json").read_text()) if nhs and (CACHE / "nhs.json").exists() else None},
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (run_dir / "results.json").write_text(json.dumps({"summary": summary, "results": [r.model_dump() for r in results]}, indent=1))
    emit("run_finished", counts=counts, wall_secs=summary["wall_secs"], total=len(results), quotes_rejected=n_rejected,
         gemini_calls=summary["gemini_calls"], error=error)
    log.close()
    return summary


# ---------- --fake: local stand-ins so the pipeline, the API and the UI can be exercised without Modal or Gemini ----------
_NOT_NEEDED = re.compile(r"do(?:es)? not (?:need|have)|don.t (?:need|have)|not (?:required|necessary|needed)|still register|without (?:id|proof|documents)", re.I)
_SOFT = re.compile(r"if you (?:can|have)|may (?:request|ask)|helpful|speed up|if possible", re.I)
_DEMAND = re.compile(r"\bmust\b|need to|required|will need|should (?:provide|bring)|please (?:provide|bring)|will be asked|produce", re.I)


def _fakes():
    probe = ROOT.parent / "probe"
    sites = {g["code"]: g for g in json.loads((probe / "targets.json").read_text())["gps"]}
    pages = {g["code"]: g for g in json.loads((probe / "results.json").read_text())["gps"]}
    state = {"rejected": False}

    def resolve(org):
        g = sites.get(org["code"], {})
        return {**org, "site": g.get("site"), "reg_links": g.get("reg_links", []), "reg_url": g.get("url"), "error": None}

    def capture(t):
        g, fx, png = pages.get(t["code"], {}), ROOT / "fixtures" / "texts" / f"{t['code']}.txt", probe / "shots" / f"{t['code']}.png"
        text = norm(fx.read_text() if fx.exists() else g.get("find_text") or "")
        st = g.get("status")
        status = "error" if g.get("error") or st is None else "blocked" if g.get("challenge") or st >= 400 else "ok"
        return {**t, "status": status, "http_status": st, "final_url": g.get("final_url") or t.get("reg_url"), "title": g.get("title") or "",
                "challenge": bool(g.get("challenge")), "text": text, "links_national_form": "gp-registration.nhs.uk" in text,
                "doc_hits": sorted({m.group(0).lower() for m in DOC_RE.finditer(text)}), "_png": png.read_bytes() if png.exists() else None,
                "secs": g.get("secs") or 0.0, "error": g.get("error")}

    def box(t):
        b, png = pages.get(t["code"], {}).get("find_box"), probe / "shots" / f"{t['code']}.png"
        return {**t, "found": bool(b), "quote_box": {k: round(v) for k, v in b.items()} if b else None,
                "_png": png.read_bytes() if b and png.exists() else None, "error": None}

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

    return resolve, capture, box, classify


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
