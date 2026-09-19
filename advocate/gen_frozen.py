"""Write the advocate's patient note and practice letter for every letter-worthy practice in an Open Door results.json.

  uv run --env-file .env --env-file ~/.config/opendoor/keys.env gen_frozen.py <results.json> <run name>

Writes out/advocate_<run name>.json: {"<ODS code>": {"note", "letter", "model", "rule", "trace_id", "metrics"}}.
Run it with the Gateway rule enabled; the file records which rule was on because code cannot see it.
Skips codes already in the output file, so a stopped run resumes where it left off.
"""
import json
import re
import sys
from pathlib import Path

import logfire

import advocate
from metrics import score_summary

RULE = 'Open Door house style'
LETTER_WORTHY = ('demands_documents', 'asks_softly')
ATTEMPTS = 3


def plain(text: str) -> str:
    """Formatting only, never wording: drop markdown bold/headings and a bare 'Note for a patient:' title line."""
    text = re.sub(r'^\s*#+\s*', '', text.replace('**', ''), flags=re.M)
    return re.sub(r'^\s*note for a patient:?\s*\n+', '', text, flags=re.I).strip()


def problems(text: str, category: str, metrics: dict, kind: str = 'note') -> list[str]:
    """Code, not a model, decides whether a text may be shown. Empty list = OK."""
    found = []
    if kind == 'letter' and not metrics.get('contains_quote'):
        found.append('letter does not quote the practice sentence word for word')  # a misquote would undo the product
    if metrics['banned_total']:
        found.append('banned word')
    if metrics['placeholders'] or re.search(r'\[\d+\]', text):
        found.append('placeholder or footnote')
    if '**' in text or re.search(r'^\s*#', text, re.M):
        found.append('markdown')  # the UI shows plain text; rule v3 already says "No markdown"
    if 'their website' in text.lower():
        found.append('"their website" is ambiguous (NHS or practice?)')
    if category == 'asks_softly' and 'contradict' in text.lower():
        found.append('"contradicts" on a page that only asks')  # docs/CLAIMS.md: asking is allowed, requiring is not
    return found


def main(results_path: str, run_name: str) -> None:
    advocate._setup()  # logfire.configure before the first span, or the span has no trace id
    data = json.loads(Path(results_path).read_text())
    rows = data if isinstance(data, list) else data.get('results') or data.get('practices') or []
    rows = [r for r in rows if r.get('category') in LETTER_WORTHY and r.get('quote') and r.get('quote_verified')]
    out = Path(__file__).parent / 'out' / f'advocate_{run_name}.json'
    out.parent.mkdir(exist_ok=True)
    done = json.loads(out.read_text()) if out.exists() else {}
    cat = {r['code']: r['category'] for r in rows}
    # re-check stored entries too, so tightening problems() regenerates only what now fails
    for e in done.values():  # stored texts from before plain() existed
        e['note'], e['letter'] = plain(e['note']), plain(e['letter'])
    done = {c: e for c, e in done.items()
            if not any(problems(e[k], cat.get(c, ''), e['metrics'][k], k) for k in ('note', 'letter'))}
    print(f'{len(rows)} letter-worthy practices, {len(done)} already written and passing')
    for n, r in enumerate(rows, 1):
        if r['code'] in done:
            continue
        v = advocate.from_practice_result(r)
        entry = {'model': None, 'rule': RULE, 'trace_id': None, 'metrics': {}, 'attempts': {}}
        try:
            with logfire.span('frozen advocate {code}', code=r['code']) as span:
                for kind in ('note', 'letter'):
                    for attempt in range(1, ATTEMPTS + 1):
                        res = advocate.run(kind, v)
                        text = plain(res.output)
                        m = score_summary(text, v)
                        bad = problems(text, r['category'], m, kind)
                        if not bad:
                            break
                        print(f'  {r["code"]} {kind} attempt {attempt} rejected by code: {", ".join(bad)}')
                    else:
                        raise ValueError(f'{kind} failed the code check {ATTEMPTS} times')
                    entry[kind], entry['metrics'][kind], entry['attempts'][kind] = text, m, attempt
                    entry['model'] = res.response.model_name
                entry['trace_id'] = format(span.get_span_context().trace_id, '032x')
        except Exception as e:  # keep going; a rerun fills the gap
            print(f'[{n}/{len(rows)}] {r["code"]} ERROR {type(e).__name__}: {e}')
            continue
        done[r['code']] = entry
        out.write_text(json.dumps(done, indent=2, ensure_ascii=False))  # after every practice, so nothing is lost
        print(f'[{n}/{len(rows)}] {r["code"]} ok')
    out.write_text(json.dumps(done, indent=2, ensure_ascii=False))  # also drops entries that now fail the gate
    print(f'wrote {out} ({len(done)} practices)')


if __name__ == '__main__':
    main(*sys.argv[1:3])
