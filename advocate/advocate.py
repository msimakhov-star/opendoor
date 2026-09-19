"""Open Door advocate: one verdict in, a patient note or a practice letter out.

  uv run --env-file .env advocate.py --verdict fixtures/verdicts.json --index 0 --kind note
  uv run --env-file .env advocate.py --verdict fixtures/verdicts.json --index 0 --kind letter --score
  uv run --env-file .env advocate.py --verdict ../data/frozen/newham/results.json --code <ODS code> --kind letter

An open-weight model on Modal, called through the Pydantic AI Gateway (route 'modal').
DESIGN RULE: the instructions below stay short and generic. Tone, reading level and wording
rules live in a Gateway optimization rule, so behaviour changes without touching this file.
Env: PYDANTIC_AI_GATEWAY_BASE_URL, PYDANTIC_AI_GATEWAY_API_KEY, LOGFIRE_TOKEN, ADVOCATE_MODEL.
Offline check: uv run tests/test_advocate_offline.py
"""
import argparse
import json
import os
import sys
from functools import cache
from typing import Any

import logfire
from pydantic_ai import Agent

INSTRUCTIONS = {
    'note': 'Write a note for a patient about this finding.',
    'letter': 'Write a letter to the practice manager about this finding.',
}
# Must equal MODEL_ID in serve_modal.py (what the Modal server reports). ADVOCATE_MODEL overrides.
DEFAULT_MODEL = 'Qwen/Qwen2.5-7B-Instruct'
NHS_QUOTE_PLACEHOLDER = 'NHS_QUOTE_TBD'
NHS_URL = 'https://www.nhs.uk/nhs-services/gps/how-to-register-with-a-gp-surgery/'
NHS_QUOTE = 'You do not need ID, proof of address or proof of immigration status.'  # verbatim, fetched 2026-09-19
CLASSIFICATIONS = {
    'demands_documents': 'the page says documents are required to register',
    'asks_softly': 'the page asks for documents but does not say they are required',
    'compliant': 'the page says no documents are needed to register',
    'says_not_needed': 'the page says no documents are needed to register',  # session 2 name for compliant
    'unclear': 'the page does not say clearly what is needed to register',
}
FIELDS = [  # (verdict key, label in the prompt)
    ('practice_name', 'Practice'),
    ('practice_url', 'Practice website'),
    ('page_url', 'Page read'),
    ('classification', 'Classification'),
    ('quote', 'Sentence quoted from the practice page'),
    ('nhs_quote', 'Sentence quoted from the NHS guidance'),
    ('nhs_url', 'NHS guidance page'),
]

agent = Agent()  # no tools, plain text output; the model is passed per run so import needs no keys


def from_practice_result(r: dict) -> dict:
    """One PracticeResult dict from Open Door's results.json (opendoor/models.py) -> verdict contract."""
    return {'practice_name': r.get('name'), 'practice_url': r.get('site'), 'page_url': r.get('reg_url') or r.get('site'),
            'classification': r.get('category'), 'quote': r.get('quote'), 'nhs_quote': NHS_QUOTE, 'nhs_url': NHS_URL,
            'screenshot': r.get('shot')}


def build_prompt(verdict: dict) -> str:
    """Facts only. Missing, empty and placeholder fields are skipped."""
    lines = ['Finding from an automated read of a GP practice website in England.']
    for key, label in FIELDS:
        value = str(verdict.get(key) or '').strip()
        if not value or value == NHS_QUOTE_PLACEHOLDER:
            continue
        if key == 'classification':
            value = f'{value} ({CLASSIFICATIONS[value]})' if value in CLASSIFICATIONS else value
        elif key in ('quote', 'nhs_quote'):
            value = f'"{value}"'
        lines.append(f'{label}: {value}')
    return '\n'.join(lines)


def _keep_our_text(match: logfire.ScrubMatch) -> Any:
    # Logfire's default scrubber blanks any value containing words like "session", "secret" or
    # "auth" (as in "local authority"). Keep the model text we log ourselves; scrub everything else.
    if match.path[:2] in (('attributes', 'output'), ('attributes', 'final_result')):
        return match.value
    return None


@cache
def _setup() -> None:
    from openai.types.chat import ChatCompletion
    from pydantic_ai.models.openai import _ChatCompletion

    logfire.configure(
        send_to_logfire='if-token-present',
        service_name='opendoor-advocate',
        console=False,  # keep stdout clean for the note or letter
        scrubbing=logfire.ScrubbingOptions(callback=_keep_our_text),
    )
    logfire.instrument_pydantic_ai()

    # Modal returns `metadata.weight_versions` as a list, but the OpenAI schema types
    # `metadata` as `dict[str, str]`. Widen it on both models that see the payload:
    # the SDK's (which serializes it) and pydantic-ai's (which validates it).
    for _model in (ChatCompletion, _ChatCompletion):
        _model.model_fields['metadata'].annotation = dict[str, Any] | None
        _model.model_rebuild(force=True)


@cache
def gateway_model():
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.gateway import gateway_provider

    provider = gateway_provider('openai-chat', route='modal')
    return OpenAIChatModel(os.environ.get('ADVOCATE_MODEL') or DEFAULT_MODEL, provider=provider)


def run(kind: str, verdict: dict, model=None):
    """Full pydantic-ai result (output, usage) for evidence scripts. `model` is for offline tests."""
    _setup()
    with logfire.span('advocate {kind}', kind=kind, practice=verdict.get('practice_name')):
        result = agent.run_sync(
            build_prompt(verdict),
            instructions=INSTRUCTIONS[kind],
            model=model or gateway_model(),
            # ponytail: CPU-only serving is slow, so cap the reply; raise if letters get cut off.
            # pydantic-ai sends this as max_completion_tokens, which the llama.cpp server honours
            # (tests/test_endpoint_inside.py: cap 16 -> 16 tokens, finish_reason length).
            model_settings={'max_tokens': 800, 'temperature': 0.3},
        )
        logfire.info('advocate output', kind=kind, output=result.output)
    return result


def patient_note(verdict: dict, model=None) -> str:
    return run('note', verdict, model).output


def practice_letter(verdict: dict, model=None) -> str:
    return run('letter', verdict, model).output


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--verdict', required=True, help='JSON file: one verdict, a list of verdicts, or an Open Door results.json')
    ap.add_argument('--index', type=int, default=0)
    ap.add_argument('--code', help='with an Open Door results.json: the ODS code of the practice')
    ap.add_argument('--kind', choices=list(INSTRUCTIONS), default='note')
    ap.add_argument('--score', action='store_true', help='print metrics.score_summary to stderr')
    args = ap.parse_args()

    with open(args.verdict) as f:
        data = json.load(f)
    if isinstance(data, dict) and 'results' in data:  # Open Door pipeline output: data/runs/<run_id>/results.json
        rows = data['results']
        verdict = from_practice_result(next(r for r in rows if r.get('code') == args.code) if args.code else rows[args.index])
    else:
        verdict = data[args.index] if isinstance(data, list) else data
    text = run(args.kind, verdict).output
    print(text)
    if args.score:
        from metrics import score_summary

        print(json.dumps(score_summary(text, verdict), indent=2), file=sys.stderr)
