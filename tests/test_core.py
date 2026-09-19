"""Core checks, no network, no Modal, no Gemini.   uv run python tests/test_core.py"""
import json, re, shutil, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from opendoor import orgs, pipeline
from opendoor.letter import draft_letter
from opendoor.models import COLOUR, NHS_GUIDANCE_QUOTE, NHS_GUIDANCE_URL, Finding, PracticeResult, doc_types


def test_prefix_matching_and_dedupe():
    m = orgs.matches
    assert m("E1", "E1 4AB") and m("E1", "E1W 2AA") and not m("E1", "E13 9AZ") and not m("E1", "E10 5NP")
    assert m("E13", "E13 9AZ") and not m("E13", "E1 4AB")
    assert m("E", "E13 9AZ") and m("E", "E1W 2AA") and not m("E", "EC1A 1BB") and not m("E", "EN1 1AA")
    assert m("SE", "SE10 9GB") and m("EC1A", "EC1A 7BE") and not m("W", "WC1N 3JH") and not m("N", "NW1 0AA")
    assert orgs._queries("E") == ["E%d" % d for d in range(10)] and orgs._queries("E13") == ["E13"]
    assert orgs.clean_prefix(" e13 ") == "E13"
    assert orgs.NOT_GP.search("COMMUNITY DERMATOLOGY CLINIC") and not orgs.NOT_GP.search("RUSTON STREET CLINIC")
    assert pipeline.parse_prefixes(" e13  9az") == ["E13 9AZ"] and pipeline.parse_prefixes("e13, e6 E7,e13") == ["E13", "E6", "E7"]
    assert pipeline.parse_prefixes("E13") == ["E13"] and pipeline.parse_prefixes("E,N") == ["E", "N"]
    assert pipeline.parse_prefixes("SE") == ["SE"] and pipeline.parse_prefixes("London") == []
    sp = orgs.single_place
    assert sp("E138AA") == "E13 8AA" and sp("sw1a 1aa") == "SW1A 1AA" and sp("E1W") == "E1W" and sp("E1 3AA") == "E1 3AA"
    assert sp("SE") is None and sp("E13, E6") is None and sp("E13 E6") is None and sp("") is None
    assert " " not in pipeline.new_run_id(["E13 8AA"]) and pipeline.new_run_id(["E13 8AA"]).endswith("E138AA")
    fake = {"E1": [{"code": "A", "region": "London"}, {"code": "B", "region": "South East"}],
            "E": [{"code": "B", "region": "South East"}, {"code": "C", "region": "London"}, {"code": "A", "region": "London"}]}
    real, orgs._prefix_orgs = orgs._prefix_orgs, lambda client, p: fake[p]
    try:
        assert [o["code"] for o in orgs.find_practices(["E1", "E", "e1"], 10)] == ["A", "B", "C"]
        assert [o["code"] for o in orgs.find_practices(["E1", "E"], 2)] == ["A", "B"]
        assert [o["code"] for o in orgs.find_practices(["E1", "E"], 10, region="London")] == ["A", "C"]
        fake["N"] = [{"code": c, "postcode": pc} for c, pc in (("N1a", "N1 1AA"), ("N1b", "N1 2BB"), ("N2a", "N2 1AA"))]
        assert [o["code"] for o in orgs.find_practices(["N"], 2)] == ["N1a", "N2a"]  # a limit spreads over districts
    finally:
        orgs._prefix_orgs = real


def test_nearest_n_ordering():
    """Stubbed geocoder: the point sits at (51.5, 0). One degree of latitude is about 111 km."""
    pt = {"lat": 51.5, "lon": 0.0}
    at = lambda km_north: {"lat": 51.5 + km_north / 111.2, "lon": 0.0}
    districts = {"A1": at(0.5), "A2": at(3), "A3": at(6), "A4": at(40)}
    practices = {"A1": [{"code": "p1", "postcode": "A1 1AA", **at(1.0)}, {"code": "p2", "postcode": "A1 1AB", **at(0.2)},
                        {"code": "nogeo", "postcode": "A1 1AC", "lat": None, "lon": None}],
                 "A2": [{"code": "p3", "postcode": "A2 1AA", **at(2.5)}, {"code": "p2", "postcode": "A1 1AB", **at(0.2)}],
                 "A3": [{"code": "p4", "postcode": "A3 1AA", **at(5.0)}], "A4": [{"code": "far", "postcode": "A4 1AA", **at(40)}]}
    read = []
    stubs = {"_point": lambda client, place: {**pt, "outcode": "A1"},
             "_nearest_outcodes": lambda client, oc: [{"outcode": k, **v} for k, v in reversed(districts.items())],
             "_prefix_orgs": lambda client, p: read.append(p) or practices[p]}
    real = {k: getattr(orgs, k) for k in (*stubs, "BATCH")}
    try:
        for k, v in stubs.items():
            setattr(orgs, k, v)
        found, c = orgs.find_nearest("A1 1AA", 3)
        assert [o["code"] for o in found] == ["p2", "p1", "p3"], found  # sorted by distance, deduped, no-coordinates dropped
        assert [o["distance_km"] for o in found] == [0.2, 1.0, 2.5] and read == ["A1", "A2", "A3", "A4"]  # one batch of 8 reads all
        assert abs(orgs.km(pt, at(10)) - 10) < 0.05
        orgs.BATCH = 1  # stop rule: stop once n are found and the next centroid is beyond the n-th practice + MARGIN_KM
        read.clear()
        assert [o["code"] for o in orgs.find_nearest("A1", 3)[0]] == ["p2", "p1", "p3"] and read == ["A1", "A2"]  # A3 centroid 6 > 2.5 + 2
        read.clear()
        assert [o["code"] for o in orgs.find_nearest("A1", 1)[0]] == ["p2"] and read == ["A1"]  # A2 centroid 3 > 0.2 + 2
        read.clear()
        assert [o["code"] for o in orgs.find_nearest("A1", 4)[0]] == ["p2", "p1", "p3", "p4"] and read == ["A1", "A2", "A3"]  # A4 centroid 40 > 5 + 2
        assert orgs.find_nearest("A1", 0)[0] == []
    finally:
        for k, v in real.items():
            setattr(orgs, k, v)


def test_colour_doc_types_and_self_contradiction():
    for cat, colour in COLOUR.items():
        for national in (True, False):
            r = PracticeResult(code="X", name="X", links_national_form=national)
            pipeline.apply_finding(r, Finding(category=cat, quote="Bring your passport.", documents=["proof of address"], quote_verified=True))
            assert r.colour == colour and r.category == cat
            assert r.self_contradiction == (national and cat == "demands_documents"), (cat, national)
            assert r.doc_types == (["passport", "proof_of_address"] if colour in ("red", "amber") else []), (cat, r.doc_types)


def test_peak_and_verdicts():
    assert pipeline.peak_overlap([]) == 0 and pipeline.peak_overlap([(0, 0)]) == 0
    assert pipeline.peak_overlap([(0, 5), (1, 2), (1.5, 6), (5, 7)]) == 3 and pipeline.peak_overlap([(0, 1), (1, 2)]) == 1
    run_dir = pipeline.RUNS / "test-verdicts"
    rs = [PracticeResult(code=c, name=c, site="https://s.test/", reg_url="https://s.test/join", category=cat, quote=q, shot=shot)
          for c, cat, q, shot in [("R", "demands_documents", "You must bring ID.", "shots/R.png"), ("G", "says_not_needed", "No ID needed.", None),
                                  ("N", "no_mention", "", None), ("X", "not_checked", "stale", None), ("U", "unclear", "ID maybe.", None)]]
    out = run_dir / "verdicts.json"
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        assert pipeline.write_verdicts(rs, run_dir, out) == 3
        v = json.loads(out.read_text())
        assert [(x["practice_name"], x["classification"]) for x in v] == [("R", "demands_documents"), ("G", "compliant"), ("U", "unclear")]
        assert v[0]["screenshot"] == str((run_dir / "shots/R.png").resolve()) and v[0]["screenshot"].startswith("/") and v[1]["screenshot"] is None
        assert v[0]["practice_url"] == "https://s.test/" and v[0]["page_url"] == "https://s.test/join"
        assert v[0]["nhs_quote"] == NHS_GUIDANCE_QUOTE and v[0]["nhs_url"] == NHS_GUIDANCE_URL
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def test_api():
    from fastapi.testclient import TestClient
    from opendoor import app as api
    calls = []
    real, api.pipeline.run = api.pipeline.run, lambda *a, **k: calls.append((a, k))
    try:
        c = TestClient(api.app)
        r = c.post("/api/run", json={"postcode": "e13 8aa", "limit": 5, "lacking": ["passport"]})  # an old client's extra field is ignored
        assert r.status_code == 200 and set(r.json()) == {"run_id"}, r.text
        for _ in range(50):
            if calls:
                break
            __import__("time").sleep(0.02)
        (args, kw), = calls
        assert args[:2] == (["E13 8AA"], 5) and "lacking" not in kw
        assert c.post("/api/run", json={"postcode": "London"}).status_code == 400
        for bad in (".", "..", ".hidden"):  # a run id must not start with a dot (URLs normalise "/./", so call run_dir itself)
            try:
                api.run_dir(bad); raise AssertionError(bad)
            except api.HTTPException as e:
                assert e.status_code == 400, bad
    finally:
        api.pipeline.run = real


BANNED = re.compile(r"illegal|unlawful|breach|refuse|[\u2013\u2014]", re.I)


def test_letter():
    r = {"code": "F1", "name": "EXAMPLE SURGERY", "postcode": "E13 9AZ", "reg_url": "https://example.test/register", "category": "demands_documents",
         "quote": "You must bring photo ID and proof of address.", "quote_verified": True, "checked_at": "2026-09-19T12:00:00+00:00"}
    t = draft_letter(r)
    assert r["quote"] in t and NHS_GUIDANCE_QUOTE in t and NHS_GUIDANCE_URL in t and r["reg_url"] in t and "2026-09-19" in t
    assert "contradicts the NHS guidance on nhs.uk" in t and not BANNED.search(t), BANNED.search(t)
    assert "national NHS registration form" not in t and "national NHS registration form" in draft_letter({**r, "self_contradiction": True})
    assert draft_letter({**r, "category": "asks_softly"}) == ""  # amber does not contradict nhs.uk: no letter
    assert draft_letter({**r, "category": "says_not_needed"}) == "" and draft_letter({**r, "quote_verified": False}) == ""


def test_outside_box_and_wales():
    at = lambda i, lat, lon: {"code": "P%d" % i, "name": "P", "lat": lat, "lon": lon}
    london = [at(i, 51.40 + i * 0.01, -0.30 + i * 0.02) for i in range(25)]  # 51.40..51.64, -0.30..0.18
    chelmsford, nogeo = at(99, 51.7356, 0.4685), {"code": "N", "name": "N", "lat": None, "lon": None}
    assert orgs.outside_box(london + [chelmsford, nogeo]) == [chelmsford] and orgs.outside_box(london) == []
    assert orgs.outside_box([chelmsford, london[0]]) == []  # too few practices to say where the area is
    assert not orgs.keep({"code": "W97001", "name": "A PRACTICE IN WALES"}) and orgs.keep({"code": "F84004", "name": "MARKET STREET"})
    manchester = [at(90 + i, 53.48 + i * 0.01, -2.24) for i in range(2)]
    fake = {"E": london + [chelmsford], "M": manchester}
    real, orgs._prefix_orgs = orgs._prefix_orgs, lambda client, p: fake[p]
    try:
        far = []
        assert len(orgs.find_practices(["E"], 100, dropped=far)) == 25 and far == [chelmsford]
        assert len(orgs.find_practices(["E", "M"], 100)) == 27  # a small area swept with a big one keeps its own box
    finally:
        orgs._prefix_orgs = real


def test_event_stats():
    ev = [{"type": "run_started", "t": 0}, {"type": "run_started", "t": 0}, {"type": "practices_found", "count": 5, "outside_area": 1},
          {"type": "captured", "code": "A", "status": "ok", "secs": 3.0}, {"type": "captured", "code": "B", "status": "blocked", "secs": 2.0},
          {"type": "captured", "code": "N", "status": "ok", "secs": 0.0}, {"type": "captured", "code": "T", "status": "error", "secs": 26.0},
          {"type": "quote_rejected", "code": "A"}, {"type": "quote_rejected", "code": "A", "cached": True},
          {"type": "classified", "code": "A", "category": "demands_documents", "quote": "Bring ID.", "self_contradiction": True, "from_cache": True},
          {"type": "second_opinion", "code": "A", "verdict": "asks_softly", "agreed": False, "reason": "r"},
          {"type": "classified", "code": "A", "category": "asks_softly", "quote": "Bring ID.", "self_contradiction": False, "from_cache": True},
          {"type": "classified", "code": "B", "category": "not_checked", "status": "blocked", "reason": "The site did not let ..."},
          {"type": "classified", "code": "N", "category": "no_mention", "status": "ok", "reason": ""},
          {"type": "classified", "code": "T", "category": "not_checked", "status": "error", "reason": "TimeoutError: Page.goto: Timeout 25000ms exceeded."},
          {"type": "classified", "code": "S", "category": "not_checked", "status": "no_site", "reason": "No website listed"},
          {"type": "run_finished", "gemini_calls": 7, "containers_peak": 3, "wall_secs": 9.5}]
    s = pipeline.event_stats(ev)
    assert (s["practices"], s["outside_area"], s["pages_read_ok"], s["quotes_verified"], s["quotes_rejected"], s["cache_hits"]) == (5, 1, 1, 1, 1, 1)
    assert s["not_checked"] == {"total": 3, "blocked": 1, "timeout": 1, "no_site": 1, "no_registration_page": 0, "error": 0}
    assert s["counts"]["asks_softly"] == 1 and s["counts"]["demands_documents"] == 0 and s["self_contradictions"] == 0  # the last classified wins
    assert s["second_opinions"] == {"checked": 1, "agreed": 0, "downgraded": 1, "no_answer": 0}
    assert (s["gemini_calls"], s["containers_peak"], s["wall_secs"]) == (7, 3, 9.5)
    assert pipeline.event_stats(ev + [{"type": "run_started", "t": 0}])["practices"] == 0  # only the last run in an appended file counts


def test_real_run_stops_without_second_opinion():
    """No surgery may be shown red unchecked: a classify.py without second_opinion stops a real run before any page is read."""
    from opendoor import classify as cl
    saved = {"so": getattr(cl, "second_opinion", None), "warm": pipeline._warm, "fp": pipeline.find_practices}
    pipeline._warm, pipeline.find_practices = (lambda: None), (lambda *a, **k: [{"code": "X1", "name": "X", "postcode": "E13 9AZ"}])
    if saved["so"]:
        del cl.second_opinion
    got, run_id = [], "test-no-second"
    try:
        s = pipeline.run(["E13", "E6"], 5, run_id, got.append)
        assert "second_opinion" in s["error"] and got[-1]["type"] == "run_finished" and got[-1]["error"] == s["error"]
        assert not any(e["type"] in ("resolved", "captured") for e in got)
    finally:
        if saved["so"]:
            cl.second_opinion = saved["so"]
        pipeline._warm, pipeline.find_practices = saved["warm"], saved["fp"]
        shutil.rmtree(pipeline.RUNS / run_id, ignore_errors=True)


def test_fake_pipeline_event_order():
    """Runs from fixtures/fake only: no network, nothing outside the repo."""
    gps = json.loads((ROOT / "fixtures" / "fake" / "practices.json").read_text())["practices"]
    got, run_id = [], "test-fake-run"
    shutil.rmtree(pipeline.RUNS / run_id, ignore_errors=True)
    try:
        s = pipeline.run(["E13 9AZ"], 40, run_id, got.append, fake=True)
        d = pipeline.RUNS / run_id
        on_disk = [json.loads(l) for l in (d / "events.jsonl").read_text().splitlines()]
        assert on_disk == got and len(got) > len(gps) and s["error"] is None
        types = [e["type"] for e in got]
        assert types[:2] == ["run_started", "practices_found"] and types[-1] == "run_finished" and types.count("run_finished") == 1  # fake: no warming
        assert "lacking" not in got[0] and "lacking" not in s and got[0]["near"] == "E13 9AZ"
        assert got[1]["outcodes"] == sorted({g["postcode"].split()[0] for g in gps}) and got[1]["furthest_km"] == max(g["distance_km"] for g in gps)
        assert got[1]["count"] == len(gps) == s["total"] and got[-1]["containers_peak"] is None  # fake: no containers
        dist = {g["code"]: g["distance_km"] for g in gps}
        for e in got:
            if e["type"] == "classified":
                assert e["distance_km"] == dist[e["code"]] and (e["doc_types"] != []) <= (e["colour"] in ("red", "amber")), e
                assert e["from_cache"] is False and isinstance(e["self_contradiction"], bool) and not BANNED.search(e["reason"]), e
        assert all("t" in e for e in got) and [e["t"] for e in got] == sorted(e["t"] for e in got)
        order = {"resolved": 0, "captured": 1, "quote_rejected": 2, "classified": 3, "second_opinion": 4, "boxed": 6}
        so = [e for e in got if e["type"] == "second_opinion"]
        for g in gps:
            mine = [order[e["type"]] for e in got if e.get("code") == g["code"]]
            if 4 in mine:  # red: classified, second opinion, and a second classified only when it disagreed
                i = mine.index(4)
                mine[i + 1:i + 2] = [5] if mine[i + 1:i + 2] == [3] else mine[i + 1:i + 2]
            assert mine == sorted(mine) and mine[0] == 0 and mine.count(3) == 1, (g["code"], mine)  # every practice: resolved first, classified once
        # exactly one staged disagreement: that practice is re-classified amber with the reason, every other red stays red
        assert [set(e) for e in so] == [{"type", "t", "code", "verdict", "agreed", "reason"}] * len(so) and len(so) >= 2
        down = [e["code"] for e in so if not e["agreed"]]
        assert len(down) == 1
        last = {e["code"]: e for e in got if e["type"] == "classified"}
        assert last[down[0]]["category"] == "asks_softly" and last[down[0]]["colour"] == "amber" and pipeline.DISAGREED in last[down[0]]["reason"]
        assert "keyword match" not in last[down[0]]["reason"]  # the first reader's red reason never rides on an amber pin
        assert all(last[e["code"]]["colour"] == "red" for e in so if e["agreed"])
        want = {"checked": len(so), "agreed": len(so) - 1, "downgraded": 1}
        assert got[-1]["second_opinions"] == s["second_opinions"] == want and got[-1]["cache_hits"] == s["cache_hits"] == 0
        out = json.loads((d / "results.json").read_text())
        assert len(out["results"]) == len(gps) and sum(out["summary"]["counts"].values()) == len(gps)
        assert out["summary"]["quotes_rejected"] == types.count("quote_rejected") == 1 and out["summary"]["gemini_calls"] == 0
        for r in out["results"]:
            PracticeResult(**r)
            assert r["colour"] == COLOUR[r["category"]] and last[r["code"]]["category"] == r["category"]
            assert not r["shot"] or (d / r["shot"]).exists()
            assert not r["quote_box"] or r["quote_verified"]
            assert r["distance_km"] == dist[r["code"]]
            if r["colour"] in ("red", "amber"):
                assert r["doc_types"] == doc_types([r["quote"]] + r["documents"]) != [], r
        assert s["counts"]["demands_documents"] >= 1 and s["counts"]["not_checked"] >= 1 and types.count("boxed") == s["quotes_boxed"] >= 1
        st = json.loads((d / "stats.json").read_text())  # from events.jsonl, so it must agree with results.json
        assert st["counts"] == s["counts"] and st["practices"] == s["total"] and st["quotes_verified"] == s["quotes_verified"]
        assert st["self_contradictions"] == s["self_contradictions"] and st["quotes_rejected"] == 1 and st["second_opinions"] == {**want, "no_answer": 0}
        assert st["not_checked"]["total"] == s["counts"]["not_checked"] == sum(v for k, v in st["not_checked"].items() if k != "total")
        assert st["pages_read_ok"] == sum(e["status"] == "ok" and e["secs"] > 0 for e in got if e["type"] == "captured") and st["wall_secs"] == s["wall_secs"]
    finally:
        shutil.rmtree(pipeline.RUNS / run_id, ignore_errors=True)


def test_fake_box_and_readable_errors():
    fx = pipeline._fakes()
    nhs = fx["box"]({"code": "nhs", "quote": NHS_GUIDANCE_QUOTE})
    assert nhs["_png"] and nhs["quote_box"]  # a fresh clone's --fake run still gets the nhs.uk reference shot
    assert fx["box"]({"code": "F84741", "quote": "Any patients registering must provide proof of entry."})["_png"] is None  # PNG boxes another sentence
    r = pipeline._readable
    assert r("net::ERR_NAME_NOT_RESOLVED at http://x") == pipeline.UNREACHABLE and r("Plain words.") == "Plain words."
    assert pipeline._why({"reason": r("TimeoutError: Page.goto: Timeout 25000ms exceeded.")}) == "timeout"
    assert r("HTTPError: HTTP Error 404: Not Found") == "The practice has no profile page on nhs.uk."


if __name__ == "__main__":
    tests =[(k, v) for k, v in list(globals().items()) if k.startswith("test_")]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(len(tests), "passed")
