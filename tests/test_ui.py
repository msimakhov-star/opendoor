"""Checks for the UI and its sample fixture. Run: uv run python tests/test_ui.py
Re-embed the fixture into the page after editing it: uv run python tests/test_ui.py --embed
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from opendoor.models import COLOUR, DOC_TYPES, PracticeResult  # noqa: E402

HTML = ROOT / "opendoor/ui/index.html"
FIX = ROOT / "fixtures/sample_run.json"
TAG = re.compile(r'(<script id="sample" type="application/json">)(.*?)(</script>)', re.S)
NEEDS = {
    "run_started": {"postcode_prefixes", "limit"}, "practices_found": {"count"},
    "resolved": {"code", "name", "lat", "lon", "reg_url"}, "captured": {"code", "status", "challenge", "secs"},
    "classified": {"code", "category", "colour", "quote", "retries"}, "quote_rejected": {"code", "attempt", "reason"},
    "boxed": {"code", "shot"}, "run_finished": {"counts", "wall_secs"},
}


def main():
    fix = json.loads(FIX.read_text())
    if "--embed" in sys.argv:
        body = json.dumps(fix, separators=(",", ":")).replace("</", "<\\/")
        HTML.write_text(TAG.sub(lambda m: m.group(1) + body + m.group(3), HTML.read_text(), count=1))
    html = HTML.read_text()

    results = {r["code"]: PracticeResult(**r) for r in fix["results"]}
    assert len(results) == 8, len(results)
    ts = [e["t"] for e in fix["events"]]
    assert ts == sorted(ts), "events must be in time order"
    assert fix["events"][0]["type"] == "run_started" and fix["events"][-1]["type"] == "run_finished"
    for e in fix["events"]:
        assert NEEDS[e["type"]] <= e.keys(), e
        if e["type"] == "classified":
            r = results[e["code"]]
            assert (e["category"], e["colour"], e["quote"]) == (r.category, COLOUR[r.category], r.quote), e
    for r in results.values():
        assert r.colour == COLOUR[r.category]
        assert r.retries == sum(e["type"] == "quote_rejected" and e["code"] == r.code for e in fix["events"])
    done = fix["events"][-1]["counts"]
    assert done == {c: sum(r.category == c for r in results.values()) for c in done}, done

    assert json.loads(TAG.search(html).group(2)) == fix, "embedded sample differs from the fixture: run with --embed"
    for name, text in (("index.html", html), ("sample_run.json", FIX.read_text())):
        assert not re.search("[–—]", text), f"en or em dash in {name}"
        assert not re.search(r"illegal|unlawful|breach|refuse", text, re.I), f"banned wording in {name}"
    assert not re.search(r"innerHTML|insertAdjacentHTML|document\.write", html.split("<script>")[-1].replace("never innerHTML", "")), "use textContent"
    for needle in ("Two NHS websites that disagree", "Nothing is sent automatically", "Approve and open in my email",
                   "Reads what practice websites say, not what happens at the front desk", "Replay a saved run",
                   "I don't have:", 'data-doc="passport"', 'data-doc="photo_id"', 'data-doc="proof_of_address"', 'data-doc="immigration"',
                   "lacking: [...lacking]", "NHS directory", "Modal browsers", "Gemini", "Code check", "Evidence", "Area summary",
                   "say on their website", "you can register without", "for something you ticked", "not a promise"):
        assert needle in html, needle
    # the persona fallback in the page must use exactly the backend's four patterns
    js_types = json.loads(re.search(r"const DOC_TYPES = (\{.*?\});", html).group(1))
    assert js_types == {k: rx.pattern for k, rx in DOC_TYPES.items()}, "DOC_TYPES in index.html differs from opendoor/models.py"
    print(f"ok: {len(fix['events'])} events, {len(results)} results, embedded copy matches, wording rules hold")


if __name__ == "__main__":
    main()
