"""Before/after evidence for the Pydantic Gateway challenge. advocate.py never changes between runs.

Run from the project root:
  uv run --env-file .env challenge/evidence.py run rule_off --n 5   # rule DISABLED in the Logfire UI
  uv run --env-file .env challenge/evidence.py run rule_on --n 5    # rule ENABLED, same code
  uv run challenge/evidence.py compare                              # medians, % change, outputs side by side
  uv run challenge/evidence.py compare --on backup_on               # if the backup rule was used instead
  add --dry-run to any of them: FunctionModel, no network, no keys, writes to challenge/evidence/dryrun/

Run i uses flagged verdict i (demands_documents or asks_softly, wrapping round) from
fixtures/verdicts.json and asks for both a patient note and a practice
letter, so rule_off and rule_on see byte-identical prompts, paired by (run, kind).
Writes challenge/evidence/<label>.json, <label>.md and COMPARE.md. Each record carries the sha256
of advocate.py at the moment of the call: the git-independent proof that the code did not change.
`compare` re-scores the stored outputs with the current metrics.py, so a metrics fix never needs
a new model run.
"""
import argparse
import difflib
import hashlib
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
KINDS = ('note', 'letter')


def out_dir(dry_run: bool) -> Path:
    d = HERE / 'evidence' / ('dryrun' if dry_run else '')
    d.mkdir(parents=True, exist_ok=True)
    return d


def dry_reply(label: str, kind: str, v: dict) -> str:
    """DRY RUN FAKE TEXT, not model output. 'rule_off' is deliberately bad so every metric moves."""
    name, quote, url = v.get('practice_name', ''), v.get('quote', ''), v.get('nhs_url', '')
    if label == 'rule_off':
        return (f'[DRY RUN FAKE TEXT] Following an automated assessment of the registration documentation '
                f'requirements promulgated by {name}, it has been determined that the aforementioned practice '
                f'imposes documentary prerequisites upon prospective registrants, notwithstanding national guidance. '
                f'[Your Name] [Your Position] ') * 3
    ask = 'Please update the page.' if kind == 'letter' else 'You can still sign up.'
    verdict = ('This contradicts the NHS guidance on nhs.uk.' if v.get('classification') == 'demands_documents'
               else 'The NHS guidance on nhs.uk says you do not need these to register.')
    return (f'[DRY RUN FAKE TEXT] You can sign up with a GP. You do not need ID. The website of {name} says: '
            f'"{quote}" {verdict} Read it here: {url} {ask}')


def load_run(dry_run: bool):
    """advocate.run(kind, verdict, model=None) -> pydantic-ai result. Stand-in only if absent in --dry-run."""
    try:
        import advocate
        getattr(advocate, '_setup', lambda: None)()  # configure logfire before our first span
        return advocate.run
    except Exception as e:
        if not dry_run:
            raise
        print(f'WARNING advocate.py not importable ({type(e).__name__}: {e}); dry-run uses a stand-in agent')
        import logfire
        from pydantic_ai import Agent

        logfire.configure(send_to_logfire=False, console=False)
        return lambda kind, verdict, model=None: Agent().run_sync(f'{kind}: {json.dumps(verdict)}', model=model)


def load_score():
    try:
        from metrics import score_summary
        return score_summary
    except Exception as e:
        print(f'WARNING metrics.py not importable ({type(e).__name__}: {e}); using word_count only')
        return lambda text, verdict: {'word_count': len(text.split())}


def advocate_sha() -> str:
    f = ROOT / 'advocate.py'
    return hashlib.sha256(f.read_bytes()).hexdigest() if f.exists() else 'ABSENT'


def run(label: str, n: int, dry_run: bool) -> dict:
    if dry_run:
        os.environ.pop('LOGFIRE_TOKEN', None)  # fake text must never land in the real project
    elif not os.getenv('LOGFIRE_TOKEN'):
        print('WARNING LOGFIRE_TOKEN is not set: no traces will be sent, and traces are required evidence')
    import logfire
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models.function import FunctionModel

    advocate_run, score = load_run(dry_run), load_score()
    # Only verdicts that lead to a letter: a compliant or unclear page gets no "please fix it" request.
    verdicts = [v for v in json.loads((ROOT / 'fixtures' / 'verdicts.json').read_text())
                if v.get('classification') in ('demands_documents', 'asks_softly')]
    records = []
    for i in range(1, n + 1):
        v = verdicts[(i - 1) % len(verdicts)]
        for kind in KINDS:
            rec = {'run': i, 'kind': kind, 'verdict': v, 'advocate_sha256': advocate_sha()}
            model = FunctionModel(lambda m, info, k=kind: ModelResponse(parts=[TextPart(dry_reply(label, k, v))])) if dry_run else None
            try:
                with logfire.span('evidence {label} run {i} {kind}', label=label, i=i, kind=kind) as span:
                    t0 = time.perf_counter()
                    result = advocate_run(kind, v, model)
                    latency = time.perf_counter() - t0
                    trace_id = format(span.get_span_context().trace_id, '032x')
                rec |= {'trace_id': trace_id, 'model': result.response.model_name,
                        'input_tokens': result.usage.input_tokens, 'output_tokens': result.usage.output_tokens,
                        'latency_s': round(latency, 3), 'output': result.output, 'metrics': score(result.output, v)}
                print(f'[{label} {i}/{n} {kind}] in={rec["input_tokens"]} out={rec["output_tokens"]} {latency:.2f}s trace={trace_id}')
            except Exception as e:  # e.g. 503 modal_no_live_containers on cold start: keep the other runs
                rec['error'] = f'{type(e).__name__}: {e}'
                print(f'[{label} {i}/{n} {kind}] ERROR {rec["error"]}')
            records.append(rec)
    data = {'label': label, 'dry_run': dry_run, 'when_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'records': records}
    d = out_dir(dry_run)
    (d / f'{label}.json').write_text(json.dumps(data, indent=2))
    (d / f'{label}.md').write_text(label_md(data))
    print('\n' + label_md(data))
    return data


def ok(data: dict, kind: str) -> list[dict]:
    return [r for r in data['records'] if r['kind'] == kind and 'error' not in r]


def summarise(records: list[dict]) -> dict[str, tuple[str, float]]:
    """metric -> (display, number). Numbers give 'median [min to max]', booleans 'k of n'. Dicts are skipped."""
    out = {}
    flat = [{k: r[k] for k in ('output_tokens', 'input_tokens', 'latency_s')} | r['metrics'] for r in records]
    for key in (flat[0] if flat else {}):
        vals = [f[key] for f in flat if key in f]
        if all(isinstance(x, bool) for x in vals):
            out[key] = (f'{sum(vals)} of {len(vals)}', float(sum(vals)))
        elif all(isinstance(x, (int, float)) for x in vals):
            med = statistics.median(vals)
            out[key] = (f'{med:g} [{min(vals):g} to {max(vals):g}]', float(med))
    return out


def pct(before: float, after: float) -> str:
    """Absolute change, then % of the baseline size (abs, because Flesch reading ease can be negative)."""
    return f'{after - before:+g} (' + ('n/a' if before == 0 else f'{(after - before) / abs(before) * 100:+.1f}%') + ')'


def label_md(data: dict) -> str:
    rows = [f'## {data["label"]}{" (DRY RUN, fake text)" if data["dry_run"] else ""}, {data["when_utc"]}', '']
    for kind in KINDS:
        rows += [f'### {kind}: {len(ok(data, kind))} ok', '', '| metric | median [min to max] or count |', '| --- | --- |']
        rows += [f'| {m} | {disp} |' for m, (disp, _) in summarise(ok(data, kind)).items()] + ['']
    rows += ['| run | kind | practice | model | trace_id | advocate.py sha256 |', '| --- | --- | --- | --- | --- | --- |']
    rows += [f'| {r["run"]} | {r["kind"]} | {r["verdict"].get("practice_name")} | {r.get("model", r.get("error"))} '
             f'| `{r.get("trace_id", "")}` | `{r["advocate_sha256"][:16]}` |' for r in data['records']]
    return '\n'.join(rows) + '\n'


def cell(text: str) -> str:
    return text.strip().replace('|', '\\|').replace('\n', '<br>')


def compare(off_label: str, on_label: str, dry_run: bool) -> str:
    d = out_dir(dry_run)
    off, on = (json.loads((d / f'{label}.json').read_text()) for label in (off_label, on_label))
    score = load_score()
    for data in (off, on):
        for r in data['records']:
            if 'error' not in r:
                r['metrics'] = score(r['output'], r['verdict'])
    shas = {r['advocate_sha256'] for data in (off, on) for r in data['records']}
    same_prompts = [(r['run'], r['kind'], r['verdict']) for r in off['records']] == [(r['run'], r['kind'], r['verdict']) for r in on['records']]
    rows = [f'# {off_label} vs {on_label}{" (DRY RUN, fake text)" if dry_run else ""}', '',
            f'advocate.py identical across every call: **{len(shas) == 1}** (sha256 {", ".join(f"`{s[:16]}`" for s in sorted(shas))})  ',
            f'Same verdicts in the same order: **{same_prompts}**  ',
            f'{off_label} at {off["when_utc"]}, {on_label} at {on["when_utc"]}', '']
    for kind in KINDS:
        s_off, s_on = summarise(ok(off, kind)), summarise(ok(on, kind))
        rows += [f'## {kind} ({len(ok(off, kind))} vs {len(ok(on, kind))} ok runs)', '',
                 f'| metric | {off_label} | {on_label} | change |', '| --- | --- | --- | --- |']
        rows += [f'| {m} | {s_off[m][0]} | {s_on[m][0]} | {pct(s_off[m][1], s_on[m][1])} |' for m in s_off if m in s_on]
        on_by_run = {r['run']: r for r in ok(on, kind)}
        pairs = [(a, on_by_run[a['run']]) for a in ok(off, kind) if a['run'] in on_by_run]
        if pairs:  # the most different pair of outputs for the same prompt
            a, b = min(pairs, key=lambda p: difflib.SequenceMatcher(None, p[0]['output'], p[1]['output']).ratio())
            rows += ['', f'Most different {kind} pair: run {a["run"]}, {a["verdict"].get("practice_name")}', '',
                     f'| {off_label} (trace `{a["trace_id"]}`) | {on_label} (trace `{b["trace_id"]}`) |', '| --- | --- |',
                     f'| {cell(a["output"])} | {cell(b["output"])} |']
        rows.append('')
    text = '\n'.join(rows)
    (d / ('COMPARE.md' if on_label == 'rule_on' else f'COMPARE_{on_label}.md')).write_text(text)
    print(text)
    return text


def selfcheck() -> None:
    assert pct(200, 50) == '-150 (-75.0%)' and pct(-40, 80) == '+120 (+300.0%)' and pct(0, 5) == '+5 (n/a)'
    recs = [{'output_tokens': t, 'input_tokens': 5, 'latency_s': 1.0,
             'metrics': {'word_count': w, 'has_nhs_url': u, 'banned_terms': {'illegal': 0}}}
            for t, w, u in ((10, 7, True), (30, 9, False), (20, 8, True))]
    s = summarise(recs)
    assert s['output_tokens'] == ('20 [10 to 30]', 20.0) and s['word_count'] == ('8 [7 to 9]', 8.0), s
    assert s['has_nhs_url'] == ('2 of 3', 2.0) and 'banned_terms' not in s, s
    assert summarise([]) == {} and cell('a|b\nc') == 'a\\|b<br>c'
    data = {'records': [{'kind': 'note'}, {'kind': 'note', 'error': 'x'}, {'kind': 'letter'}]}
    assert len(ok(data, 'note')) == 1 and len(ok(data, 'letter')) == 1
    print('selfcheck OK')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['run', 'compare'])
    ap.add_argument('label', nargs='?', help='for run: rule_off, rule_on or backup_on')
    ap.add_argument('--n', '-n', type=int, default=5)
    ap.add_argument('--off', default='rule_off')
    ap.add_argument('--on', default='rule_on')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    if args.dry_run:
        selfcheck()
    if args.cmd == 'compare':
        compare(args.off, args.on, args.dry_run)
    elif not args.label:
        ap.error('run needs a label: rule_off or rule_on')
    else:
        run(args.label, args.n, args.dry_run)
