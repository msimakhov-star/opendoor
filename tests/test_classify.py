"""No network. Run: uv run python tests/test_classify.py"""
import asyncio, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pydantic_ai import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from opendoor import classify
from opendoor.models import Finding

PAGE = "Welcome to the surgery.\nYou will need to bring photo ID and\n proof of address to register. Opening hours 8 to 6."
TRUE = "You will need to bring photo ID and proof of address to register."
FAKE = "All patients must show a passport before registering."


def model(*answers):
    """A stub model that returns the given Finding dicts in order and counts calls."""
    calls = []

    def fn(messages, info):
        calls.append(1)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, answers[len(calls) - 1])])
    return FunctionModel(fn), calls


def test_quote_problem():
    page = classify.norm(PAGE)
    assert classify.quote_problem(Finding(category="demands_documents", quote=TRUE), page) is None
    assert "NOT on the page" in classify.quote_problem(Finding(category="demands_documents", quote=FAKE), page)
    assert "empty" in classify.quote_problem(Finding(category="says_not_needed", quote=""), page)
    assert "names no document" in classify.quote_problem(Finding(category="demands_documents", quote="Welcome to the surgery."), page)
    assert classify.quote_problem(Finding(category="unclear", quote=""), page) is None


def test_excerpt():
    assert classify.excerpt(PAGE) == classify.norm(PAGE)  # short page goes whole
    menu = "Home Appointments Prescriptions Services Contact " * 300  # unpunctuated, 15,000 characters
    soft = "It is not a requirement for adults to provide documentation."  # no document phrase from DOC_RE
    filler = "We are open from 8 to 6 on weekdays. " * 200
    text = menu + TRUE + " " + filler + soft + " " + filler + "Welcome."
    e = classify.excerpt(text)
    assert len(e) <= 10000 and TRUE in e and soft in e and "[...]" in e, e[:300]
    page = classify.norm(text)
    assert all(seg.strip() in page for seg in e.split("[...]")), "every passage is verbatim page text"


def test_prefilter_makes_no_call():
    m, calls = model()
    with classify.agent.override(model=m):
        f = asyncio.run(classify.classify_page("T1", "Welcome. Opening hours 8 to 6. Register online."))
    assert f.category == "no_mention" and calls == []


def test_retry_then_cache():
    classify.CACHE = Path(tempfile.mkdtemp())
    rejected = []
    m, calls = model({"category": "demands_documents", "quote": FAKE}, {"category": "demands_documents", "quote": TRUE})
    with classify.agent.override(model=m):
        f = asyncio.run(classify.classify_page("T2", PAGE, lambda *a: rejected.append(a)))
        assert (f.category, f.quote, f.retries, f.quote_verified) == ("demands_documents", TRUE, 1, True), f
        assert len(calls) == 2 and len(rejected) == 1 and rejected[0][:2] == ("T2", 1)
        replayed = []
        again = asyncio.run(classify.classify_page("T2", PAGE, lambda *a: replayed.append(a)))  # second event loop, cache hit, stub would IndexError if called
    assert again == f and len(calls) == 2
    assert replayed == rejected, (replayed, rejected)  # same events on a re-run, nothing invented


def test_never_verified_becomes_unclear():
    classify.CACHE = Path(tempfile.mkdtemp())
    m, calls = model(*[{"category": "demands_documents", "quote": FAKE}] * 6)
    with classify.agent.override(model=m):
        f = asyncio.run(classify.classify_page("T3", PAGE))
    assert f.category == "unclear" and f.quote == "" and not f.quote_verified and f.retries == len(calls) == 4, (f, calls)
    replayed = []
    asyncio.run(classify.classify_page("T3", PAGE, lambda *a: replayed.append(a)))
    assert [r[1] for r in replayed] == [1, 2, 3, 4] and len(calls) == 4


def test_unclear_needs_document_wording():
    classify.CACHE = Path(tempfile.mkdtemp())
    m, calls = model({"category": "unclear", "quote": ""}, {"category": "unclear", "quote": "photo ID and proof of address"})
    with classify.agent.override(model=m):
        f = asyncio.run(classify.classify_page("T4", PAGE))
        g = asyncio.run(classify.classify_page("T5", PAGE + " Parking at the back."))
    assert f.category == "no_mention" and f.quote == "", f
    assert g.category == "unclear" and g.quote_verified, g


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)
