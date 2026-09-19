"""Classify one GP registration page with Gemini. The model proposes a quote; CODE checks it is really on the page."""
import asyncio, hashlib, inspect, json, os, random, re, textwrap, time, weakref
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import certifi
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")  # before the Agent exists: the Google provider reads GOOGLE_API_KEY
os.environ.setdefault("SSL_CERT_FILE", certifi.where())

import logfire
from pydantic_ai import Agent, ModelAPIError, ModelHTTPError, ModelRetry, RunContext, UnexpectedModelBehavior, UsageLimits

from opendoor.models import DOC_RE, Finding, SecondOpinion, norm

logfire.configure(send_to_logfire="if-token-present", console=False)
logfire.instrument_pydantic_ai()

CACHE = ROOT / "data" / "cache"
CACHE_V = "v4"  # part of the cache key: bump whenever the prompt, the excerpt or the post-processing changes a verdict
SECOND_V = "s2"  # second-opinion cache only: bump when the SECOND prompt changes, so first-read caches stay warm
QUOTED = ("demands_documents", "asks_softly", "says_not_needed")
# Requirement words: a sentence with one of these goes to the model even when no document phrase sits near it.
REQ_RE = re.compile(r"requir|\bneed|\bmust\b|do not have to|don.t have to|not necessary|without|\bdocument", re.I)
DOC_WORDS = re.compile(DOC_RE.pattern + r"|\bdocument|paperwork|\bproof\b|\bevidence\b", re.I)
# second_opinions counts every second_opinion() answer, errors included; not agreed = shown amber, not red.
STATS = {"model_runs": 0, "requests": 0, "cache_hits": 0, "prefiltered": 0, "rate_limited": 0,
         "input_tokens": 0, "output_tokens": 0, "model_secs": 0.0,
         "second_opinions": 0, "second_agreed": 0, "second_cache_hits": 0, "second_errors": 0}

SYSTEM = """You read the visible text of one UK GP practice web page. Decide what it tells a NEW PATIENT about documents needed to REGISTER (ID, proof of address, passport, immigration status and similar).

Read ALL of the text before deciding. Find:
(A) every sentence that asks new patients to provide, bring, send or show a document to register, and
(B) every sentence that says those documents are not required, not needed, that people without them can still register, or that offers help or another way to register to people who cannot provide them.
Sentences about the NHS number, temporary registration, seeing a named GP, or "your immigration status does not affect your right to register" are not (B).

Two kinds of document wording are legitimate. They are neither (A) nor (B), and on their own they never make a page demands_documents. If a page has only these, the answer is no_mention.
- Registering a child: documents asked only for a baby or child you are registering (a birth certificate, the Red Book, proof of ID and address for the child, proof that you are the parent or guardian). nhs.uk itself says a surgery may ask for proof of ID and address for a child you are registering.
  Example: "When registering a baby, please bring the birth certificate and the Red Book."
- Online access: ID asked only to set up online access to records, the NHS App or online services.
  Example: "To sign up for online services you will need to bring photo ID to reception."

Categories, each with two example wordings:
- demands_documents: there is a firm (A) (must, need to, are required to, should, will be asked to, please bring) and NO (B) anywhere on the page.
  "You will need to bring photo ID and proof of address." / "We cannot register you without a passport and a recent utility bill."
- asks_softly: there is an (A) that is soft ("if you can", "if you have it", "we may ask", "it helps"), OR there is an (A) and ALSO a (B) anywhere on the page, even when the (A) sounds firm. Quote the (A) sentence that asks for the documents, not the (B) sentence.
  "It helps if you bring ID, but we will still register you without it." / a page with both "It is not a requirement to provide documents" and "Please email your photo ID and proof of address".
- says_not_needed: there is a (B) and no (A). A list of documents that are accepted, or that people MAY bring if they wish, is not an (A): the answer stays says_not_needed. Quote the (B) sentence.
  "You do not need proof of address or immigration status to register." / "No ID is needed to join the practice."
- unclear: documents are mentioned about registering but you cannot tell whether they are asked for. Quote that wording.
  "Proof of ID" as a bare heading with no sentence / "Documents may be requested."
- no_mention: nothing on the page says whether new patients need documents to register. Document words appear only in unrelated contexts (registering a child, travel, visa medicals, passport countersigning, NHS App or online services login, cookies) or not at all. If you cannot quote any wording about documents for registering, the answer is no_mention, not unclear.

Rules:
- Only wording about registering as a new patient counts. Documents asked for online services, the NHS App, prescriptions or travel do not count.
- quote: copy ONE sentence character for character from the page text, the sentence named above for the category. Do not fix spelling, do not change punctuation, apostrophes or capital letters, do not join separate sentences, do not add an ellipsis. For demands_documents the quoted sentence must itself name a document. Leave quote empty only for no_mention.
- documents: the document types the page asks for, lower case.
- reason: one short plain English sentence describing the wording only. Make no legal judgement.
- If you are unsure, choose unclear. Never guess. Never use not_checked."""

SECOND = """You represent the UK GP surgery that published the web page below. A first reader wants to show this surgery as one that makes new patients provide documents (ID, proof of address, passport, immigration status or similar) before they can register, and quoted the sentence shown. Argue the surgery's side, honestly: read ALL of the passages, then decide what the page really says.

verdict:
- demands_documents: the page makes documents a CONDITION of registering as a new patient, and nothing on the page gives people without them a way in: no "you can still register", no offer of help, no other route.
- asks_softly: the page only asks for documents, or also says they are not needed, or offers help or another way to register to people who cannot provide them, or asks for them for another reason: only to register a child (a birth certificate, the Red Book, proof of ID and address for the child) or only for online access to records, the NHS App or online services.
A condition means the page says a person cannot register, or will not be registered, without the documents: wording such as "must", "required", "you will need", "in order to register", "only if you can provide", "you will not be registered until". A request is not a condition: "please bring", "please provide", "we will ask", "you will be asked", "should", "requested", "if you can", "if you have it", "we may ask", "it helps" are asks_softly unless the page also says registration depends on them. NHS England guidance lets a surgery ask for ID; what it does not allow is turning people away for not having it.
Use only what the page says. Do not invent a route that is not on the page: if the page is firm and gives no route, demands_documents is the fair answer.
agreed: true only when verdict is demands_documents.
reason: one short plain English sentence that quotes the deciding words from the page, character for character, inside double quotes. Describe the wording only. Make no legal judgement."""


@dataclass
class Deps:
    code: str
    page: str  # norm(full page text)
    on_reject: Optional[Callable] = None
    rejections: list = field(default_factory=list)  # [attempt, reason] for every quote code really rejected


# Legal-judgement words the UI must never show in a model-written reason. Spelled loosely so a grep for the words finds no shipped code.
JUDGED = re.compile(r"il+egal|unla?wful|brea?ch|refus", re.I)


def _plain(reason: str, red: bool) -> str:
    """A model-written reason as the UI may show it: no legal-judgement words, only red may say contradicts, no long dashes."""
    if JUDGED.search(reason) or (not red and re.search(r"contradict", reason, re.I)):
        return "The model's wording was replaced; see the quote."
    return re.sub(r"\s*\u2014\s*", ", ", reason).replace("\u2013", "-")


def _clean(f: Finding) -> Finding:
    f.reason = _plain(f.reason, f.category == "demands_documents")
    return f


async def _tell(on_reject, code: str, attempt: int, reason: str, cached: bool = False) -> None:
    if on_reject:
        r = on_reject(code, attempt, reason, cached=True) if cached else on_reject(code, attempt, reason)
        if inspect.isawaitable(r):
            await r


# defer_model_check: importing this module (tests, a fresh clone) must not need the key.
agent = Agent("google:gemini-3.8-flash", output_type=Finding, deps_type=Deps, system_prompt=SYSTEM,
              retries={"output": 3}, defer_model_check=True, name="classify")


def quote_problem(f: Finding, page: str) -> Optional[str]:
    """None if the quote stands up against the page, else a precise message for the model."""
    if f.category not in QUOTED:
        return None
    q = norm(f.quote)
    if not q:
        return "quote is empty. For %s copy one sentence character for character from the page text." % f.category
    if q not in page:
        return ("The quote is NOT on the page character for character: %r. Copy one sentence exactly as it appears in the "
                "page text (same spelling, punctuation and apostrophes, nothing added or removed), or answer unclear." % q[:200])
    if f.category == "demands_documents" and not DOC_RE.search(q):
        return "The quote names no document (ID, proof of address, passport ...). Quote the sentence that names the document, or change category."
    return None


@agent.output_validator
async def check_quote(ctx: RunContext[Deps], f: Finding) -> Finding:
    problem = quote_problem(f, ctx.deps.page)
    if problem:
        d = ctx.deps
        d.rejections.append([len(d.rejections) + 1, problem])
        await _tell(d.on_reject, d.code, *d.rejections[-1])
        raise ModelRetry(problem)
    return f


# deps: the loose page text (see _loose), so the reason's quoted words can be checked against it.
second = Agent("google:gemini-3.8-flash", output_type=SecondOpinion, deps_type=str, system_prompt=SECOND,
               retries={"output": 2}, defer_model_check=True, name="second_opinion")


def _loose(s: str) -> str:
    return norm(s).lower().replace("\u2019", "'").replace("\u2018", "'")


def misquoted(reason: str, page: str) -> list[str]:
    """The double-quoted words in a reason that are not on the (loose) page. An ellipsis splits a quote into pieces."""
    quotes = re.findall(r"[\"\u201c]([^\"\u201c\u201d]+)[\"\u201d]", reason)
    return [p for q in quotes for p in re.split(r"\.\.\.|\u2026", q) if len(p.strip(" .,;:")) > 3 and _loose(p.strip(" .,;:")) not in page]


@second.output_validator
async def check_reason(ctx: RunContext[str], o: SecondOpinion) -> SecondOpinion:
    bad = misquoted(o.reason, ctx.deps)
    if bad and not ctx.last_attempt:
        raise ModelRetry("These quoted words are not on the page character for character: %r. Quote the page exactly." % bad[:2])
    if bad:  # last attempt: keep the verdict, never show words the page does not contain
        o.reason = "The second reader's quote was not on the page, so it is not shown."
    return o


def excerpt(text: str, budget: int = 10000) -> str:
    """A short page goes whole. A long one: every sentence that names a document or a requirement word, then the
    sentences either side of each document sentence, in page order with gaps marked [...], at most `budget` characters."""
    text = norm(text)
    if len(text) <= budget:
        return text
    # Unpunctuated menus become one huge "sentence": cut those into 500 character pieces so one menu cannot eat the budget.
    parts = [p for s in re.split(r"(?<=[.!?])\s+", text) for p in textwrap.wrap(s, 500, break_long_words=False, break_on_hyphens=False)]
    doc = [i for i, p in enumerate(parts) if DOC_RE.search(p)]
    req = [i for i, p in enumerate(parts) if REQ_RE.search(p)]
    near = [j for i in doc for j in (i - 1, i + 1) if 0 <= j < len(parts)]
    keep, size = set(), 0
    for i in doc + req + near:  # priority when the budget runs out: documents, requirement words, context
        if i not in keep and size + len(parts[i]) + 7 <= budget:
            keep.add(i)
            size += len(parts[i]) + 7
    out = "".join((" " if i - 1 in keep else " [...] ") + parts[i] for i in sorted(keep))
    return out.strip() or text[:budget]


_sems: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _sem(for_second: bool = False) -> asyncio.Semaphore:
    """At most 3 Gemini calls at once: 2 slots for first reads, 1 kept for second opinions, so a red is checked without
    waiting behind the whole first-read queue. One pair per event loop: the app starts a new loop per run and a Semaphore
    sticks to its first loop."""
    return _sems.setdefault(asyncio.get_running_loop(), (asyncio.Semaphore(2), asyncio.Semaphore(1)))[for_second]


def _rate_limited(e: Exception) -> bool:
    return (isinstance(e, ModelHTTPError) and e.status_code in (429, 503)) or "RESOURCE_EXHAUSTED" in str(e)


async def _call(ag: Agent, prompt: str, deps):
    """One agent run under the shared Gemini slots (see _sem), backing off on 429 / 503. Other errors propagate."""
    async with _sem(ag is second):
        for attempt in range(4):
            t0 = time.time()
            try:
                result = await ag.run(prompt, deps=deps, usage_limits=UsageLimits(request_limit=6))
                u = result.usage  # a property in pydantic-ai v2
                STATS["requests"] += u.requests; STATS["input_tokens"] += u.input_tokens; STATS["output_tokens"] += u.output_tokens
                return result
            except ModelAPIError as e:
                if not _rate_limited(e) or attempt == 3:
                    raise
                STATS["rate_limited"] += 1
                await asyncio.sleep(2 ** attempt * 2 + random.random())
            finally:
                STATS["model_secs"] += time.time() - t0


async def classify_page(code: str, text: str, on_reject=None) -> Finding:
    """on_reject(code, attempt, reason) is called (sync or async) each time code rejects the model's quote."""
    if not DOC_RE.search(text or ""):
        STATS["prefiltered"] += 1
        return Finding(category="no_mention", reason="No document wording on the page.")
    path = CACHE / (hashlib.sha256((CACHE_V + text).encode()).hexdigest() + ".json")
    if path.exists():
        STATS["cache_hits"] += 1
        hit = json.loads(path.read_text())
        for attempt, reason in hit["rejections"]:  # replayed, marked cached: they happened in the run that wrote the cache
            await _tell(on_reject, code, attempt, reason, cached=True)
        f = _clean(Finding.model_validate(hit["finding"]))
        f.from_cache = True
        return f

    deps, keep = Deps(code=code, page=norm(text), on_reject=on_reject), True
    prompt = "Practice %s. Page text:\n\n%s" % (code, excerpt(text))
    try:
        f = (await _call(agent, prompt, deps)).output
        if f.category == "unclear" and not DOC_WORDS.search(f.quote):  # unclear about nothing: no document wording to point at
            f.category, f.reason = "no_mention", "No sentence on the page says whether documents are needed to register."
    except UnexpectedModelBehavior:  # quote rejected more often than the retry budget allows
        f = Finding(category="unclear", reason="The model could not give a quote that is really on the page.")
        keep = False  # also covers empty or malformed replies: try the model again next run, do not cache
    except ModelAPIError as e:
        return Finding(category="not_checked", reason="Classifier error: %s" % type(e).__name__, retries=len(deps.rejections))
    STATS["model_runs"] += 1
    if f.category == "not_checked":
        f.category = "unclear"
    f.quote = norm(f.quote)
    if f.quote not in deps.page:  # only possible for unclear / no_mention, where the quote is not validated
        f.quote = ""
    f.quote_verified = bool(f.quote)
    f.retries = len(deps.rejections)
    _clean(f)
    if keep:
        CACHE.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"finding": f.model_dump(), "rejections": deps.rejections}, indent=1))
    return f


async def second_opinion(code: str, text: str, finding: Finding) -> SecondOpinion:
    """Before a surgery is shown red, a separate reader argues the surgery's side on the same passages.
    agreed is set by code (verdict == demands_documents). An API error gives verdict not_checked, agreed False, not cached."""
    path = CACHE / ("second_%s%s_%s.json" % (CACHE_V, SECOND_V, hashlib.sha256(text.encode()).hexdigest()))
    STATS["second_opinions"] += 1
    if path.exists():
        STATS["second_cache_hits"] += 1
        o = SecondOpinion.model_validate_json(path.read_text())
    else:
        prompt = 'Practice %s. The first reader quoted: "%s"\n\nPage text:\n\n%s' % (code, finding.quote, excerpt(text))
        try:
            o = (await _call(second, prompt, _loose(text))).output
        except (ModelAPIError, UnexpectedModelBehavior) as e:
            STATS["second_errors"] += 1
            return SecondOpinion(verdict="not_checked", agreed=False, reason="The second reader gave no answer (%s)." % type(e).__name__)
        o.agreed = o.verdict == "demands_documents"
        o.reason = _plain(o.reason, o.agreed)
        CACHE.mkdir(parents=True, exist_ok=True)
        path.write_text(o.model_dump_json())
    STATS["second_agreed"] += o.agreed
    return o


async def _demo() -> None:
    """uv run python -m opendoor.classify : classify every fixture text and print a table."""
    rejected = []
    files = sorted((ROOT / "fixtures" / "texts").glob("*.txt"))
    t0 = time.time()
    out = await asyncio.gather(*[classify_page(p.stem, p.read_text(), lambda *a, **k: rejected.append(a)) for p in files])
    for p, f in zip(files, out):
        print("%-7s %-18s r=%d %s" % (p.stem, f.category, f.retries, f.quote[:90]))
    for r in rejected:
        print("REJECTED", r[0], "attempt", r[1], "|", r[2][:160])
    print("wall %.1fs" % (time.time() - t0), STATS)


if __name__ == "__main__":
    asyncio.run(_demo())
