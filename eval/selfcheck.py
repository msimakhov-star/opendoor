"""Offline proof: fake results = the labels with two deliberate errors, then assert run_eval reports exactly those. Run: uv run selfcheck.py"""
import json
from pathlib import Path

from run_eval import score

HERE = Path(__file__).parent
labels = json.loads((HERE / "labels.json").read_text(encoding="utf-8"))
fake = [{"code": l["code"], "name": l["code"], "category": l["category"], "quote": l["quote"], "quote_verified": bool(l["quote"])} for l in labels]
by = {r["code"]: r for r in fake}
by["F84014"]["category"] = "asks_softly"  # error 1: a missed red (the costly kind for a patient)
by["F84124"].update(category="says_not_needed", quote="You do not need any documents to register with us.", quote_verified=True)  # error 2: wrong category + invented quote
out = HERE / "fake_results.json"
out.write_text(json.dumps({"run_id": "fake", "results": fake}, indent=1, ensure_ascii=False), encoding="utf-8")  # object shape
(HERE / "fake_results_list.json").write_text(json.dumps(fake, ensure_ascii=False), encoding="utf-8")  # bare list shape

m, _ = score(out)
m2, _ = score(HERE / "fake_results_list.json")
assert m == m2, "list shape and object shape must score the same"
assert m["acc_all"] == (18, 20) and m["acc_unanimous"] == (18, 20), m
assert (m["red_tp"], m["red_fp"], m["red_fn"]) == (3, 0, 1) and m["red_recall"] == 0.75 and m["red_precision"] == 1.0, m
assert (m["quotes_verbatim"], m["quotes_predicted"]) == (10, 11) and m["quote_verified_flag_wrong"] == ["F84124"], m
assert sorted(m["disagreements"]) == ["F84014", "F84124"], m
print("selfcheck OK:", {k: m[k] for k in ("acc_all", "red_precision", "red_recall", "quotes_verbatim", "quotes_predicted", "disagreements")})
