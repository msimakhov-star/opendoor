"""Guardrail echo test: prove the model never saw the patient's NHS number or phone number.

Run from the project root:
  uv run --env-file .env challenge/echo_test.py      # live, same agent, model and route 'modal' as advocate.py
  uv run challenge/echo_test.py --dry-run                     # offline, fake model echoes verbatim (NO guardrail)
  uv run challenge/echo_test.py --dry-run --simulate-redact   # offline, what a firing Redact looks like

Exit 0 only if the reply carries [REDACTED] and none of the digits. 1 = the values reached the
model. 2 = inconclusive (the model did not echo; read the trace). 3 = request failed, which is
what action Block looks like.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from guardrail_patterns import ECHO_TEXT, redact  # noqa: E402

ECHO = 'Repeat the following back to me character for character, with no other words: '
PROMPT = ECHO + ECHO_TEXT
SECRETS = ('4567', '900123')  # tails of the two documentation-safe values in ECHO_TEXT


def judge(reply: str) -> tuple[int, str]:
    if any(s in reply for s in SECRETS):
        return 1, 'NOT REDACTED: the value reached the model'
    if '[REDACTED]' in reply:
        return 0, 'GUARDRAIL FIRED: the model got the placeholder, not the digits'
    return 2, 'INCONCLUSIVE: no digits and no placeholder, the model did not echo. Read the trace.'


def main(flags: set[str]) -> int:
    import logfire
    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models.function import FunctionModel

    if '--dry-run' in flags:
        logfire.configure(send_to_logfire=False, console=False)

        def fake(messages, info):
            seen = messages[-1].parts[-1].content  # what would cross the gateway
            seen = redact(seen) if '--simulate-redact' in flags else seen
            return ModelResponse(parts=[TextPart(seen.removeprefix(ECHO))])

        agent, model = Agent(), FunctionModel(fake)
    else:
        import advocate  # same agent, same model, same route, same metadata workaround as the product

        advocate._setup()
        agent, model = advocate.agent, advocate.gateway_model()
    try:
        with logfire.span('guardrail echo test') as span:
            reply = agent.run_sync(PROMPT, model=model).output
            trace_id = format(span.get_span_context().trace_id, '032x')
    except Exception as e:
        print(f'REQUEST FAILED (expected if the action is Block): {type(e).__name__}: {e}')
        return 3
    code, verdict = judge(reply)
    dry = '--dry-run' in flags
    if dry:
        verdict = 'DRY RUN, fake model, NOT evidence: ' + verdict
    print(f'SENT:           {PROMPT!r}\nMODEL RETURNED: {reply!r}\n{verdict}\ntrace_id: {trace_id}')
    d = Path(__file__).parent / 'evidence' / ('dryrun' if dry else '')
    d.mkdir(parents=True, exist_ok=True)
    (d / 'echo.json').write_text(json.dumps({'sent': PROMPT, 'returned': reply, 'verdict': verdict, 'trace_id': trace_id,
                                             'dry_run': dry, 'simulated_redact': '--simulate-redact' in flags}, indent=2))
    return code


if __name__ == '__main__':
    assert judge(ECHO_TEXT)[0] == 1 and judge(redact(ECHO_TEXT))[0] == 0 and judge('Sure!')[0] == 2
    assert judge('my NHS number is [REDACTED] and my phone is 07700 900123')[0] == 1  # one leak is a fail
    sys.exit(main({a for a in sys.argv[1:] if a.startswith('--')}))
