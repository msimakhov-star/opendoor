"""Score a core-pipeline results.json against the reference labels. Run: uv run run_eval.py --results <path>

Reference labels are LLM-produced (3 blind LLM labellers, majority in code) and spot-checked by a person. Not labelled by a person.
Uses pydantic-evals (Dataset, Case, custom Evaluator), API read from the installed 2.46.0 source.
"""
import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import Evaluator, EvaluatorContext

HERE = Path(__file__).parent
CATS = ["demands_documents", "asks_softly", "says_not_needed", "no_mention", "unclear", "not_checked"]
RED = "demands_documents"


def norm(s: str) -> str:  # same rule as opendoor/models.py norm()
    return re.sub(r"\s+", " ", s or "").strip()


@dataclass
class CategoryMatch(Evaluator):
    def evaluate(self, ctx: EvaluatorContext) -> dict[str, bool]:
        return {"category_correct": ctx.output["category"] == ctx.expected_output["category"]}


@dataclass
class QuoteVerbatim(Evaluator):
    """Is the predicted quote a substring of the captured page text (whitespace-normalised)? Skipped when there is no quote."""

    def evaluate(self, ctx: EvaluatorContext) -> dict[str, bool]:
        q = norm(ctx.output["quote"])
        return {"quote_verbatim": q in ctx.metadata["text"]} if q else {}


def load_results(path: Path) -> dict[str, dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):  # object with the list under some key (results / practices / ...)
        keys = sorted(data, key=lambda k: k not in ("results", "practices"))  # named keys first, so an "events" list never wins
        lists = [data[k] for k in keys if isinstance(data[k], list) and data[k] and isinstance(data[k][0], dict) and "code" in data[k][0] and "type" not in data[k][0]]
        if not lists and all(isinstance(v, dict) and "category" in v for v in data.values()):
            lists = [[{"code": k, **v} for k, v in data.items()]]  # dict keyed by practice code
        if not lists:
            raise SystemExit(f"No list of practice dicts with a 'code' field found in {path}")
        data = lists[0]
    return {r["code"]: r for r in data}


def score(results_path: Path) -> tuple[dict, str]:
    labels = json.loads((HERE / "labels.json").read_text(encoding="utf-8"))
    ov_path = HERE / "overrides.json"
    overrides = json.loads(ov_path.read_text(encoding="utf-8")) if ov_path.exists() else {}
    for l in labels:
        if l["code"] in overrides:
            l["category"], l["unanimous"] = overrides[l["code"]], True  # a person settled it
    labels = [l for l in labels if l["category"]]  # no majority and not settled: cannot score
    pred = load_results(results_path)
    missing = [l["code"] for l in labels if l["code"] not in pred]
    by_code = {l["code"]: l for l in labels}

    dataset = Dataset(
        name="opendoor_reference_pages",
        cases=[
            Case(
                name=l["code"],
                inputs=l["code"],
                expected_output={"category": l["category"], "quote": l["quote"]},
                metadata={"unanimous": l["unanimous"], "text": norm((HERE / "texts" / f"{l['code']}.txt").read_text(encoding="utf-8"))},
            )
            for l in labels if l["code"] in pred
        ],
        evaluators=[CategoryMatch(), QuoteVerbatim()],
    )

    def task(code: str) -> dict:  # no model call here: replay what the core pipeline already wrote
        r = pred[code]
        return {"category": r.get("category", "not_checked"), "quote": r.get("quote", "") or "", "claimed_verified": bool(r.get("quote_verified"))}

    report = dataset.evaluate_sync(task, progress=False)
    if report.failures:
        raise SystemExit(f"pydantic-evals case failures: {[f.name + ': ' + f.error_message for f in report.failures]}")
    cases = report.cases

    def acc(cs):
        return (sum(c.assertions["category_correct"].value for c in cs), len(cs))

    una = [c for c in cases if c.metadata["unanimous"]]
    conf = {e: {p: 0 for p in CATS} for e in CATS}
    for c in cases:
        conf[c.expected_output["category"]][c.output["category"]] += 1
    tp = conf[RED][RED]
    fp = sum(conf[e][RED] for e in CATS) - tp
    fn = sum(conf[RED].values()) - tp
    quoted = [c for c in cases if "quote_verbatim" in c.assertions]
    verb = sum(c.assertions["quote_verbatim"].value for c in quoted)
    lied = [c.name for c in quoted if c.output["claimed_verified"] and not c.assertions["quote_verbatim"].value]
    wrong = [c for c in cases if not c.assertions["category_correct"].value]
    m = {
        "pages_scored": len(cases), "missing_from_results": missing, "not_checked_predictions": sum(c.output["category"] == "not_checked" for c in cases),
        "acc_unanimous": acc(una), "acc_all": acc(cases), "red_tp": tp, "red_fp": fp, "red_fn": fn,
        "red_precision": tp / (tp + fp) if tp + fp else None, "red_recall": tp / (tp + fn) if tp + fn else None,
        "quotes_predicted": len(quoted), "quotes_verbatim": verb, "quote_verified_flag_wrong": lied,
        "disagreements": [c.name for c in wrong], "overrides": overrides,
    }

    def frac(t):
        return f"{t[0]}/{t[1]}" + (f" = {t[0] / t[1]:.0%}" if t[1] else "")

    def pct(x):
        return "n/a (no such pages)" if x is None else f"{x:.0%}"

    short = {c: c.replace("_documents", "").replace("_needed", "") for c in CATS}
    md = [
        "# Open Door: classifier eval", "",
        f"Results file: `{results_path}`", "",
        "Reference labels: LLM-produced (three blind LLM labellers, majority vote in code), spot-checked by a person via HUMAN_CHECK.md. "
        "They are not labelled by a person. Small sample from one London borough: these numbers describe this run on these pages only.",
        "The tool reads website wording, not front-desk behaviour.", "",
        f"- Pages scored: {len(cases)} of {len(labels)} labelled" + (f" (missing from results: {', '.join(missing)})" if missing else ""),
        f"- Predicted not_checked (blocked or error, counted as wrong): {m['not_checked_predictions']}",
        f"- Accuracy on unanimous pages: {frac(m['acc_unanimous'])}",
        f"- Accuracy on all majority pages: {frac(m['acc_all'])}",
        f"- Red vs not red (demands_documents): precision {pct(m['red_precision'])}, recall {pct(m['red_recall'])} (TP {tp}, FP {fp}, FN {fn})",
        "  - A false red wrongly says a practice website contradicts the NHS guidance on nhs.uk. A missed red hides one from a patient.",
        f"- Predicted quotes that are verbatim on the page: {frac((verb, len(quoted)))}",
        f"- Pages where results say quote_verified=true but the quote is not on the page: {', '.join(lied) or 'none'}",
    ]
    if overrides:
        md += [f"- Labels changed by a person via overrides.json: {json.dumps(overrides)}"]
    md += ["", "## Confusion table (rows: reference label, columns: predicted)", "",
           "| reference \\ predicted | " + " | ".join(short[c] for c in CATS) + " |", "|---|" + "---|" * len(CATS)]
    md += [f"| {short[e]} | " + " | ".join(str(conf[e][p]) for p in CATS) + " |" for e in CATS if sum(conf[e].values())]
    md += ["", f"## Disagreements ({len(wrong)})", ""]
    for c in wrong:
        l = by_code[c.name]
        md += [f"### {c.name}: reference {l['category']}, predicted {c.output['category']}" + ("" if l["unanimous"] else " (labellers were split)"),
               f"- Reference quote: \"{l['quote']}\"" if l["quote"] else "- Reference quote: none",
               f"- Predicted quote: \"{c.output['quote']}\"" if c.output["quote"] else "- Predicted quote: none", ""]
    if not wrong:
        md += ["None.", ""]
    return m, "\n".join(md)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=HERE / "EVAL.md")
    a = ap.parse_args()
    _, text = score(a.results)
    a.out.write_text(text, encoding="utf-8")
    print(text)
    print(f"\nwritten: {a.out}")
