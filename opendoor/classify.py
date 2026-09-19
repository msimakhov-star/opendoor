"""Classify one GP registration page with Gemini. The model proposes a quote; CODE checks it is really on the page."""
import asyncio, hashlib, inspect, os, random, time, weakref
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import certifi
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")  # before the Agent exists: the Google provider reads GOOGLE_API_KEY
os.environ.setdefault("SSL_CERT_FILE", certifi.where())

import logfire
from pydantic_ai import Agent, ModelAPIError, ModelHTTPError, ModelRetry, RunContext, UnexpectedModelBehavior, UsageLimits

from opendoor.models import DOC_RE, Finding, norm

logfire.configure(send_to_logfire="if-token-present", console=False)
logfire.instrument_pydantic_ai()

CACHE = ROOT / "data" / "cache"
QUOTED = ("demands_documents", "asks_softly", "says_not_needed")
STATS = {"model_runs": 0, "requests": 0, "cache_hits": 0, "prefiltered": 0, "rate_limited": 0,
         "input_tokens": 0, "output_tokens": 0, "model_secs": 0.0}

SYSTEM = """You read the visible text of one UK GP practice web page. Decide what it tells a NEW PATIENT about documents needed to REGISTER (ID, proof of address, passport, immigration status and similar).

Categories, each with two example wordings:
- demands_documents: says new patients must, need to, are required to or will be asked to provide documents to register, and nowhere says registration is possible without them.
  "You will need to bring photo ID and proof of address." / "We cannot register you without a passport and a recent utility bill."
- asks_softly: asks for documents but says registration is still possible without them, OR the page contradicts itself (one part says documents are not needed, another part demands them).
  "It helps if you bring ID, but we will still register you without it." / a page with both "You do not need proof of address" and "Please bring two forms of identification".
- says_not_needed: explicitly says documents are not required to register, and nowhere demands them.
  "You do not need proof of address or immigration status to register." / "No ID is needed to join the practice."
- unclear: documents are mentioned about registration but you cannot tell whether they are required.
  "Proof of ID" as a bare heading with no sentence / "Documents may be requested."
- no_mention: document words appear only in unrelated contexts (travel, visa medicals, passport countersigning, NHS App or online services login, cookies).

Rules:
- Only wording about registering as a new patient counts. Documents asked for online services, the NHS App, prescriptions or travel do not count.
- quote: copy ONE sentence character for character from the page text, the sentence that best supports the category. Do not fix spelling, do not change punctuation, apostrophes or capital letters, do not join separate sentences, do not add an ellipsis. For demands_documents the quoted sentence must itself name a document. Leave quote empty only for unclear and no_mention.
- documents: the document types the page asks for, lower case.
- reason: one short plain English sentence describing the wording only. Never use the words illegal, unlawful, breach or refuses.
- If you are unsure, choose unclear. Never guess. Never use not_checked."""


@dataclass
class Deps:
    code: str
    page: str  # norm(full page text)
    on_reject: Optional[Callable] = None
    rejects: int = 0


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
        ctx.deps.rejects += 1
        if ctx.deps.on_reject:
            r = ctx.deps.on_reject(ctx.deps.code, ctx.deps.rejects, problem)
            if inspect.isawaitable(r):
                await r
        raise ModelRetry(problem)
    return f


def excerpt(text: str, budget: int = 6000) -> str:
    """The parts of the page around document phrases, at most `budget` characters."""
    text = norm(text)
    hits = list(DOC_RE.finditer(text))
    if len(text) <= budget or not hits:
        return text[:budget]
    w = max(300, budget // (2 * len(hits)))
    spans: list[list[int]] = []
    for m in hits:
        a, b = max(0, m.start() - w), min(len(text), m.end() + w)
        if spans and a <= spans[-1][1]:
            spans[-1][1] = b
        else:
            spans.append([a, b])
    return " [...] ".join(text[a:b] for a, b in spans)[:budget]


_sems: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _sem() -> asyncio.Semaphore:
    # One Semaphore(3) per event loop: the app starts a new loop per run and a Semaphore sticks to its first loop.
    return _sems.setdefault(asyncio.get_running_loop(), asyncio.Semaphore(3))


def _rate_limited(e: Exception) -> bool:
    return (isinstance(e, ModelHTTPError) and e.status_code in (429, 503)) or "RESOURCE_EXHAUSTED" in str(e)


async def classify_page(code: str, text: str, on_reject=None) -> Finding:
    """on_reject(code, attempt, reason) is called (sync or async) each time code rejects the model's quote."""
    if not DOC_RE.search(text or ""):
        STATS["prefiltered"] += 1
        return Finding(category="no_mention", reason="No document wording on the page.")
    path = CACHE / (hashlib.sha256(text.encode()).hexdigest() + ".json")
    if path.exists():
        STATS["cache_hits"] += 1
        return Finding.model_validate_json(path.read_text())

    deps = Deps(code=code, page=norm(text), on_reject=on_reject)
    prompt = "Practice %s. Page text:\n\n%s" % (code, excerpt(text))
    async with _sem():
        for attempt in range(4):
            t0 = time.time()
            try:
                result = await agent.run(prompt, deps=deps, usage_limits=UsageLimits(request_limit=6))
                f, u = result.output, result.usage  # a property in pydantic-ai v2
                STATS["requests"] += u.requests; STATS["input_tokens"] += u.input_tokens; STATS["output_tokens"] += u.output_tokens
                break
            except UnexpectedModelBehavior:  # quote rejected more often than the retry budget allows
                f = Finding(category="unclear", reason="The model could not give a quote that is really on the page.")
                break
            except ModelAPIError as e:
                if not _rate_limited(e) or attempt == 3:
                    return Finding(category="not_checked", reason="Classifier error: %s" % type(e).__name__, retries=deps.rejects)
                STATS["rate_limited"] += 1
                await asyncio.sleep(2 ** attempt * 2 + random.random())
            finally:
                STATS["model_secs"] += time.time() - t0
    STATS["model_runs"] += 1
    if f.category == "not_checked":
        f.category = "unclear"
    f.quote = norm(f.quote)
    if f.quote not in deps.page:  # only possible for unclear / no_mention, where a quote is optional
        f.quote = ""
    f.quote_verified = bool(f.quote)
    f.retries = deps.rejects
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(f.model_dump_json(indent=1))
    return f


async def _demo() -> None:
    """uv run python -m opendoor.classify : classify every fixture text and print a table."""
    rejected = []
    files = sorted((ROOT / "fixtures" / "texts").glob("*.txt"))
    t0 = time.time()
    out = await asyncio.gather(*[classify_page(p.stem, p.read_text(), lambda *a: rejected.append(a)) for p in files])
    for p, f in zip(files, out):
        print("%-7s %-18s r=%d %s" % (p.stem, f.category, f.retries, f.quote[:90]))
    for r in rejected:
        print("REJECTED", r[0], "attempt", r[1], "|", r[2][:160])
    print("wall %.1fs" % (time.time() - t0), STATS)


if __name__ == "__main__":
    asyncio.run(_demo())
