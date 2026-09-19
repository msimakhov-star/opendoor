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
    assert orgs._queries("E") == ["E%d" % d for d in range(1, 10)] and orgs._queries("E13") == ["E13"]
    assert orgs.clean_prefix(" e13 ") == "E13"
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


def test_api_echoes_lacking():
    from fastapi.testclient import TestClient
    from opendoor import app as api
    calls = []
    real, api.pipeline.run = api.pipeline.run, lambda *a, **k: calls.append((a, k))
    try:
        c = TestClient(api.app)
        r = c.post("/api/run", json={"postcode": "e13 8aa", "limit": 5, "lacking": ["passport", "immigration", "passport"]})
        assert r.status_code == 200 and r.json()["lacking"] == ["passport", "immigration"], r.text
        for _ in range(50):
            if calls:
                break
            __import__("time").sleep(0.02)
        (args, kw), = calls
        assert args[:2] == (["E13 8AA"], 5) and kw["lacking"] == ["passport", "immigration"]
        assert c.post("/api/run", json={"postcode": "E13", "lacking": ["bank card"]}).status_code == 422
        assert c.post("/api/run", json={"postcode": "London"}).status_code == 400
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
    assert not BANNED.search(draft_letter({**r, "self_contradiction": True, "category": "asks_softly"}))
    assert draft_letter({**r, "category": "says_not_needed"}) == "" and draft_letter({**r, "quote_verified": False}) == ""


def test_fake_pipeline_event_order():
    gps = json.loads((ROOT.parent / "probe" / "targets.json").read_text())["gps"]
    real, pipeline.find_nearest = pipeline.find_nearest, lambda place, n: ([
        {"code": g["code"], "name": g["name"], "postcode": "E13 9AZ", "lat": 51.5, "lon": 0.03, "distance_km": i / 10} for i, g in enumerate(gps)],
        {"lat": 51.5, "lon": 0.03, "outcode": "E13"})
    got, run_id = [], "test-fake-run"
    shutil.rmtree(pipeline.RUNS / run_id, ignore_errors=True)
    try:
        s = pipeline.run(["E13 9AZ"], 40, run_id, got.append, fake=True, lacking=["passport"])
        d = pipeline.RUNS / run_id
        on_disk = [json.loads(l) for l in (d / "events.jsonl").read_text().splitlines()]
        assert on_disk == got and len(got) > len(gps)
        types = [e["type"] for e in got]
        assert types[:3] == ["run_started", "warming", "practices_found"] and types[-1] == "run_finished" and types.count("run_finished") == 1
        assert got[0]["lacking"] == ["passport"] == s["lacking"] and got[0]["near"] == "E13 9AZ" and got[2]["outcodes"] == ["E13"]
        assert got[2]["count"] == len(gps) == s["total"] and got[-1]["containers_peak"] is None  # fake: no containers
        dist = {g["code"]: i / 10 for i, g in enumerate(gps)}
        for e in got:
            if e["type"] == "classified":
                assert e["distance_km"] == dist[e["code"]] and (e["doc_types"] != []) <= (e["colour"] in ("red", "amber")), e
        assert all("t" in e for e in got) and [e["t"] for e in got] == sorted(e["t"] for e in got)
        order = {"resolved": 0, "captured": 1, "quote_rejected": 2, "classified": 3, "boxed": 4}
        for g in gps:
            mine = [order[e["type"]] for e in got if e.get("code") == g["code"]]
            assert mine == sorted(mine) and mine[0] == 0 and mine.count(3) == 1, (g["code"], mine)  # every practice: resolved first, classified exactly once
        out = json.loads((d / "results.json").read_text())
        assert len(out["results"]) == len(gps) and sum(out["summary"]["counts"].values()) == len(gps)
        assert out["summary"]["quotes_rejected"] == types.count("quote_rejected") == 1 and out["summary"]["gemini_calls"] == 0
        for r in out["results"]:
            PracticeResult(**r)
            assert r["colour"] == COLOUR[r["category"]]
            assert not r["shot"] or (d / r["shot"]).exists()
            assert not r["quote_box"] or r["quote_verified"]
            assert r["distance_km"] == dist[r["code"]]
            if r["colour"] in ("red", "amber"):
                assert r["doc_types"] == doc_types([r["quote"]] + r["documents"]) != [], r
        assert s["counts"]["demands_documents"] >= 1 and s["counts"]["not_checked"] >= 1 and types.count("boxed") == s["quotes_boxed"] >= 1
    finally:
        pipeline.find_nearest = real
        shutil.rmtree(pipeline.RUNS / run_id, ignore_errors=True)


if __name__ == "__main__":
    tests = [(k, v) for k, v in list(globals().items()) if k.startswith("test_")]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(len(tests), "passed")
