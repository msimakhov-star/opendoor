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
    for word in ("illegal", "Unlawful", "a breach", "refuses", "refusal"):
        assert "replaced" in classify._clean(Finding(category="unclear", reason="This is " + word)).reason, word
    assert classify._clean(Finding(category="unclear", reason="Asks for ID.")).reason == "Asks for ID."
    assert "replaced" in classify._clean(Finding(category="asks_softly", reason="The page contradicts itself.")).reason  # amber never says contradicts
    assert classify._clean(Finding(category="demands_documents", reason="Asks for ID \u2014 firmly.")).reason == "Asks for ID, firmly."
    assert "Red Book" in classify.SYSTEM and "online access" in classify.SYSTEM  # the two legitimate asks stay in the prompt


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
        again = asyncio.run(classify.classify_page("T2", PAGE, lambda *a, **k: replayed.append((*a, k))))  # second event loop, cache hit, stub would IndexError if called
    assert again.from_cache and not f.from_cache and again.model_copy(update={"from_cache": False}) == f and len(calls) == 2
    assert replayed == [(*x, {"cached": True}) for x in rejected], (replayed, rejected)  # same events on a re-run, marked as from the cache


def test_never_verified_becomes_unclear():
    classify.CACHE = Path(tempfile.mkdtemp())
    m, calls = model(*[{"category": "demands_documents", "quote": FAKE}] * 6)
    with classify.agent.override(model=m):
        f = asyncio.run(classify.classify_page("T3", PAGE))
    assert f.category == "unclear" and f.quote == "" and not f.quote_verified and f.retries == len(calls) == 4, (f, calls)
    assert not list(classify.CACHE.glob("*.json")), "a never-verified answer must not be cached"


def test_unclear_needs_document_wording():
    classify.CACHE = Path(tempfile.mkdtemp())
    m, calls = model({"category": "unclear", "quote": ""}, {"category": "unclear", "quote": "photo ID and proof of address"})
    with classify.agent.override(model=m):
        f = asyncio.run(classify.classify_page("T4", PAGE))
        g = asyncio.run(classify.classify_page("T5", PAGE + " Parking at the back."))
    assert f.category == "no_mention" and f.quote == "", f
    assert g.category == "unclear" and g.quote_verified, g


HELP = PAGE + " If you cannot provide ID, please call us and we will do our best to help you."
RED = Finding(category="demands_documents", quote=TRUE)


def test_second_opinion_disagrees_then_cache():
    classify.CACHE = Path(tempfile.mkdtemp())
    s0 = dict(classify.STATS)
    m, calls = model({"verdict": "asks_softly", "agreed": True, "reason": 'The page says "we will do our best to help you".'})
    with classify.second.override(model=m):
        o = asyncio.run(classify.second_opinion("T6", HELP, RED))
        again = asyncio.run(classify.second_opinion("T6", HELP, RED))  # cache hit: the stub would IndexError if called
    assert (o.verdict, o.agreed) == ("asks_softly", False) and "do our best" in o.reason, o  # agreed comes from code, not the model
    assert again == o and len(calls) == 1
    d = {k: classify.STATS[k] - s0[k] for k in ("second_opinions", "second_agreed", "second_cache_hits", "second_errors")}
    assert d == {"second_opinions": 2, "second_agreed": 0, "second_cache_hits": 1, "second_errors": 0}, d


def test_second_opinion_misquote_retry_then_agree():
    classify.CACHE = Path(tempfile.mkdtemp())
    m, calls = model({"verdict": "demands_documents", "agreed": False, "reason": 'It says "you cannot register without ID".'},
                     {"verdict": "demands_documents", "agreed": False, "reason": "It says \u201cYou will need to bring photo ID\u201d."})
    with classify.second.override(model=m):
        o = asyncio.run(classify.second_opinion("T7", PAGE, RED))
    assert (o.verdict, o.agreed, len(calls)) == ("demands_documents", True, 2) and "photo ID" in o.reason, o


def test_second_opinion_never_shows_a_fake_quote():
    classify.CACHE = Path(tempfile.mkdtemp())
    m, calls = model(*[{"verdict": "asks_softly", "agreed": False, "reason": 'It says "ID is optional".'}] * 3)
    with classify.second.override(model=m):
        o = asyncio.run(classify.second_opinion("T8", PAGE, RED))
    assert o.verdict == "asks_softly" and "ID is optional" not in o.reason and len(calls) == 3, (o, calls)


def test_second_opinion_backs_off_on_429():
    classify.CACHE = Path(tempfile.mkdtemp())
    s0, calls = classify.STATS["rate_limited"], []

    def fn(messages, info):
        calls.append(1)
        if len(calls) == 1:
            raise classify.ModelHTTPError(429, "stub", "RESOURCE_EXHAUSTED")
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"verdict": "demands_documents", "agreed": True, "reason": ""})])
    with classify.second.override(model=FunctionModel(fn)):
        o = asyncio.run(classify.second_opinion("T9", PAGE, RED))
    assert o.agreed and len(calls) == 2 and classify.STATS["rate_limited"] == s0 + 1, o


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)
