"""votes_raw.json (3 blind LLM labellers) -> labels.json + HUMAN_CHECK.md. Run: uv run build_labels.py"""
import json
import re
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent


def norm(s: str) -> str:  # same rule as opendoor/models.py norm()
    return re.sub(r"\s+", " ", s or "").strip()


def context(text: str, quote: str, after: int = 350) -> str:
    i = text.find(quote)
    return text[i : i + len(quote) + after] if i >= 0 else ""


def main() -> None:
    labels, dropped = [], []
    for entry in json.loads((HERE / "votes_raw.json").read_text(encoding="utf-8")):
        code = entry["code"]
        text = norm((HERE / "texts" / f"{code}.txt").read_text(encoding="utf-8"))
        votes = []
        for v in entry["votes"]:
            v = dict(v)
            if v["quote"] and norm(v["quote"]) not in text:
                dropped.append((code, v["quote"]))
                v["quote"], v["quote_dropped"] = "", True
            votes.append(v)
        (top, n), = Counter(v["category"] for v in votes).most_common(1)
        majority = top if n >= 2 else None  # three different answers: no majority
        assert majority == entry["majority"], (code, majority, entry["majority"])
        unanimous = n == len(votes)
        quotes = Counter(v["quote"] for v in votes if v["category"] == majority and v["quote"])
        labels.append({
            "code": code,
            "category": majority,
            "quote": quotes.most_common(1)[0][0] if quotes else "",
            "unanimous": unanimous,
            "needs_human_check": not unanimous,
            "borderline": any(v["confidence"] != "high" for v in votes),  # at least one labeller was unsure
            "votes": votes,
        })
    (HERE / "labels.json").write_text(json.dumps(labels, indent=1, ensure_ascii=False), encoding="utf-8")

    split = [l for l in labels if l["needs_human_check"]]
    border = [l for l in labels if l["borderline"] and not l["needs_human_check"]]
    md = [
        "# Human check of the reference labels (about 5 minutes)",
        "",
        "The reference labels in labels.json are LLM-produced (three independent blind LLM labellers, majority vote computed in code).",
        "A person can check them using this file. Until then no write-up may say a person checked or labelled them.",
        "",
        f"Pages: {len(labels)}. Unanimous: {sum(l['unanimous'] for l in labels)}. Not unanimous: {len(split)}. "
        f"Quotes dropped because they were not found on the page: {len(dropped)}.",
        "",
        "## 1. Pages where the labellers disagreed (must settle)",
        "",
    ]
    if not split:
        md += ["None. All three labellers gave the same category on every page.", ""]
    for l in split:
        md += [f"### {l['code']}  majority: {l['category']}", ""]
        for v in l["votes"]:
            md += [f"- {v['category']} ({v['confidence']}): \"{v['quote']}\"  {v['note']}"]
        md += ["", "- [ ] majority is right   - [ ] change to: ________", ""]
    md += [
        "## 2. Optional: unanimous, but at least one labeller marked medium confidence",
        "",
        "Three runs of similar models can agree and still be wrong together, so these are the pages worth a look by eye.",
        "The text shown is copied from the captured page: the quoted sentence plus what follows it.",
        "",
    ]
    for l in border:
        text = norm((HERE / "texts" / f"{l['code']}.txt").read_text(encoding="utf-8"))
        doubt = next(v["note"] for v in l["votes"] if v["confidence"] != "high")
        md += [f"### {l['code']}  label: {l['category']}", ""]
        hit = re.search(r"immigration status|proof of|photo ?id|passport", text, re.I)  # no quote: show the first document phrase
        if l["quote"]:
            md += [f"> {context(text, norm(l['quote']))}", ""]
        elif hit:
            md += [f"> {text[max(0, hit.start() - 250) : hit.end() + 150]}", ""]
        md += [f"Labeller doubt: {doubt}", "", "- [ ] label is right   - [ ] change to: ________", ""]
    md += ["To change a label: create overrides.json next to labels.json, e.g. {\"F84014\": \"unclear\"}. run_eval.py applies it and says so in EVAL.md.", ""]
    (HERE / "HUMAN_CHECK.md").write_text("\n".join(md), encoding="utf-8")
    print(f"labels: {len(labels)}  unanimous: {sum(l['unanimous'] for l in labels)}  needs_human_check: {len(split)}  "
          f"borderline: {len(border)}  quotes_dropped: {len(dropped)}")
    for code, q in dropped:
        print("DROPPED", code, q[:80])


if __name__ == "__main__":
    main()
