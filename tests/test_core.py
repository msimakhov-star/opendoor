"""Core checks, no network, no Modal, no Gemini.   uv run python tests/test_core.py"""
import json, re, shutil, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from opendoor import orgs, pipeline
from opendoor.letter import draft_letter
from opendoor.models import COLOUR, NHS_GUIDANCE_QUOTE, NHS_GUIDANCE_URL, Finding, PracticeResult


def test_prefix_matching_and_dedupe():
    m = orgs.matches
    assert m("E1", "E1 4AB") and m("E1", "E1W 2AA") and not m("E1", "E13 9AZ") and not m("E1", "E10 5NP")
    assert m("E13", "E13 9AZ") and not m("E13", "E1 4AB")
    assert m("E", "E13 9AZ") and m("E", "E1W 2AA") and not m("E", "EC1A 1BB") and not m("E", "EN1 1AA")
    assert m("SE", "SE10 9GB") and m("EC1A", "EC1A 7BE") and not m("W", "WC1N 3JH") and not m("N", "NW1 0AA")
    assert orgs._queries("E") == ["E%d" % d for d in range(1, 10)] and orgs._queries("E13") == ["E13"]
    assert orgs.clean_prefix(" e13 ") == "E13"
    assert pipeline.parse_prefixes("E13 9AZ") == ["E13"] and pipeline.parse_prefixes("e13, e6 E7,e13") == ["E13", "E6", "E7"]
    assert pipeline.parse_prefixes("SE") == ["SE"] and pipeline.parse_prefixes("London") == []
    fake = {"E1": [{"code": "A", "region": "London"}, {"code": "B", "region": "South East"}],
            "E": [{"code": "B", "region": "South East"}, {"code": "C", "region": "London"}, {"code": "A", "region": "London"}]}
    real, orgs._prefix_orgs = orgs._prefix_orgs, lambda client, p: fake[p]
    try:
        assert [o["code"] for o in orgs.find_practices(["E1", "E", "e1"], 10)] == ["A", "B", "C"]
        assert [o["code"] for o in orgs.find_practices(["E1", "E"], 2)] == ["A", "B"]
        assert [o["code"] for o in orgs.find_practices(["E1", "E"], 10, region="London")] == ["A", "C"]
    finally:
        orgs._prefix_orgs = real


def test_colour_and_self_contradiction():
    for cat, colour in COLOUR.items():
        for national in (True, False):
            r = PracticeResult(code="X", name="X", links_national_form=national)
            pipeline.apply_finding(r, Finding(category=cat, quote="q", quote_verified=True))
            assert r.colour == colour and r.category == cat
            assert r.self_contradiction == (national and cat == "demands_documents"), (cat, national)


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
    real, pipeline.find_practices = pipeline.find_practices, lambda *a, **k: [
        {"code": g["code"], "name": g["name"], "postcode": "E13 9AZ", "lat": 51.5, "lon": 0.03} for g in gps]
    got, run_id = [], "test-fake-run"
    shutil.rmtree(pipeline.RUNS / run_id, ignore_errors=True)
    try:
        s = pipeline.run(["E13"], 40, run_id, got.append, fake=True)
        d = pipeline.RUNS / run_id
        on_disk = [json.loads(l) for l in (d / "events.jsonl").read_text().splitlines()]
        assert on_disk == got and len(got) > len(gps)
        types = [e["type"] for e in got]
        assert types[0] == "run_started" and types[1] == "practices_found" and types[-1] == "run_finished" and types.count("run_finished") == 1
        assert got[1]["count"] == len(gps) == s["total"]
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
        assert s["counts"]["demands_documents"] >= 1 and s["counts"]["not_checked"] >= 1 and types.count("boxed") == s["quotes_boxed"] >= 1
    finally:
        pipeline.find_practices = real
        shutil.rmtree(pipeline.RUNS / run_id, ignore_errors=True)


if __name__ == "__main__":
    tests = [(k, v) for k, v in list(globals().items()) if k.startswith("test_")]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(len(tests), "passed")
