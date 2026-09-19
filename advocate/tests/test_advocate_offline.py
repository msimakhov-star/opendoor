"""Offline proof for advocate.py. No network, no keys, no Modal.   uv run tests/test_advocate_offline.py

1. FunctionModel: both functions send the verdict facts plus the short generic instruction, no
   tools, and return the model text. No wording rules leak from the code into the prompt.
2. The Logfire scrubbing callback keeps our output text and still scrubs other attributes.
3. The real CLI and the real gateway provider run against a local fake OpenAI-style server that
   answers like Modal does (metadata.weight_versions is a list), proving route and workaround.
"""
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
for _k in ('PYDANTIC_AI_GATEWAY_API_KEY', 'PAIG_API_KEY', 'LOGFIRE_TOKEN'):
    os.environ.pop(_k, None)  # prove import and the offline path need no keys

from pydantic_ai.messages import ModelResponse, TextPart, UserPromptPart  # noqa: E402
from pydantic_ai.models.function import FunctionModel  # noqa: E402

import advocate  # noqa: E402
from metrics import banned_terms  # noqa: E402

VERDICTS = json.loads((ROOT / 'fixtures' / 'verdicts.json').read_text())
# Anything that belongs in the Gateway rule, not in code.
LEAKS = ['reading age', 'plain english', 'plain language', 'polite', 'never', 'do not say', "don't",
         'contradict', 'front desk', 'grade', 'short sentences', 'tone', 'must not', 'avoid']


def capture(reply: str):
    seen = {}

    def fn(messages, info):
        seen['user'] = '\n'.join(p.content for m in messages for p in m.parts if isinstance(p, UserPromptPart))
        seen['instructions'] = info.instructions
        seen['other_parts'] = [type(p).__name__ for m in messages for p in m.parts if not isinstance(p, UserPromptPart)]
        seen['tools'] = info.function_tools + info.output_tools
        seen['allow_text'] = info.allow_text_output
        return ModelResponse(parts=[TextPart(reply)])

    return FunctionModel(fn), seen


def test_prompts_and_text():
    assert {v['classification'] for v in VERDICTS} <= set(advocate.CLASSIFICATIONS) and len(VERDICTS) == 6
    for v in VERDICTS:
        assert v['practice_name'].startswith('Example Surgery ') and '.example.org/' in v['page_url'], v
        for fn, kind in ((advocate.patient_note, 'note'), (advocate.practice_letter, 'letter')):
            model, seen = capture(f'fake {kind} text')
            out = fn(v, model=model)
            assert out == f'fake {kind} text' and isinstance(out, str)
            assert seen['instructions'] == advocate.INSTRUCTIONS[kind], seen['instructions']
            assert seen['other_parts'] == [] and seen['tools'] == [] and seen['allow_text'], seen
            for key in ('practice_name', 'practice_url', 'page_url', 'classification', 'quote', 'nhs_url'):
                assert v[key] in seen['user'], (key, seen['user'])
            assert 'NHS_QUOTE_TBD' not in seen['user'] and v['screenshot'] not in seen['user']
            sent = (seen['instructions'] + '\n' + seen['user']).lower()
            assert sum(banned_terms(sent).values()) == 0, banned_terms(sent)
            assert not [w for w in LEAKS if w in sent], [w for w in LEAKS if w in sent]
    print('example prompt sent to the model:\n---\n' + seen['instructions'] + '\n\n' + seen['user'] + '\n---')


def test_code_prompt_is_generic():
    code_text = ' '.join([*advocate.INSTRUCTIONS.values(), *advocate.CLASSIFICATIONS.values(),
                          advocate.build_prompt({}), *[label for _, label in advocate.FIELDS]]).lower()
    assert sum(banned_terms(code_text).values()) == 0 and not [w for w in LEAKS if w in code_text]
    assert all(len(i.split()) <= 12 for i in advocate.INSTRUCTIONS.values())


def test_tolerant_contract():
    model, seen = capture('ok')
    assert advocate.patient_note({}, model=model) == 'ok'  # missing keys tolerated
    assert advocate.practice_letter({'practice_name': 'Example Surgery Z', 'extra': 1, 'classification': 'new_kind',
                                     'nhs_quote': 'A real NHS sentence.'}, model=model) == 'ok'
    assert 'Example Surgery Z' in seen['user'] and 'new_kind' in seen['user'] and '"A real NHS sentence."' in seen['user']
    # Open Door pipeline shape (opendoor/models.py PracticeResult) -> verdict contract
    v = advocate.from_practice_result({'code': 'X00001', 'name': 'Example Surgery Y', 'site': 'https://y.example.org/',
                                       'reg_url': 'https://y.example.org/join/', 'category': 'says_not_needed',
                                       'quote': 'No ID needed.', 'quote_verified': True, 'shot': 'shots/X00001.png'})
    assert v['practice_name'] == 'Example Surgery Y' and v['page_url'].endswith('/join/') and v['nhs_quote'] == advocate.NHS_QUOTE
    assert advocate.CLASSIFICATIONS['says_not_needed'] in advocate.build_prompt(v) and 'X00001.png' not in advocate.build_prompt(v)
    assert advocate.DEFAULT_MODEL == 'Qwen/Qwen2.5-7B-Instruct'  # must equal serve_modal.MODEL_ID


def test_scrubbing_callback():
    import logfire
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    advocate._setup()  # the real configure call already ran above without error; now add an exporter
    exporter = InMemorySpanExporter()
    logfire.configure(send_to_logfire=False, console=False,
                      scrubbing=logfire.ScrubbingOptions(callback=advocate._keep_our_text),
                      additional_span_processors=[SimpleSpanProcessor(exporter)])
    risky = 'Ask your local authority. Book a session. It is no secret.'
    model, _ = capture(risky)
    assert advocate.patient_note(VERDICTS[0], model=model) == risky
    logfire.info('control', password='hunter2-not-real')
    logfire.force_flush()
    attrs = [dict(s.attributes) for s in exporter.get_finished_spans()]
    kept = [a for a in attrs if a.get('output') == risky]
    final = [a for a in attrs if a.get('final_result') == risky]
    control = [a for a in attrs if 'password' in a]
    assert kept and final, [sorted(a) for a in attrs]
    assert control and 'hunter2' not in str(control[0]['password']), control
    print('scrubbing: output and final_result kept verbatim, control attribute scrubbed to', control[0]['password'])


def test_cli_through_fake_gateway():
    seen = {}

    class Fake(BaseHTTPRequestHandler):
        def do_POST(self):
            seen['path'] = self.path
            seen['body'] = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            body = json.dumps({
                'id': 'chatcmpl-offline', 'object': 'chat.completion', 'created': 1, 'model': seen['body']['model'],
                'choices': [{'index': 0, 'finish_reason': 'stop',
                             'message': {'role': 'assistant', 'content': 'Dear Practice Manager, see https://www.nhs.uk/x'}}],
                'usage': {'prompt_tokens': 50, 'completion_tokens': 9, 'total_tokens': 59},
                'metadata': {'weight_versions': ['default']},  # the Modal quirk the workaround exists for
            }).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = HTTPServer(('127.0.0.1', 0), Fake)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = {k: v for k, v in os.environ.items() if k != 'LOGFIRE_TOKEN'}
    env.update(PYDANTIC_AI_GATEWAY_BASE_URL=f'http://127.0.0.1:{server.server_port}/proxy',
               PYDANTIC_AI_GATEWAY_API_KEY='offline-placeholder-not-a-key', ADVOCATE_MODEL='fake/open-model',
               NO_PROXY='127.0.0.1', no_proxy='127.0.0.1')
    r = subprocess.run([sys.executable, 'advocate.py', '--verdict', 'fixtures/verdicts.json', '--index', '1',
                        '--kind', 'letter', '--score'], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    server.shutdown()
    assert r.returncode == 0, r.stderr[-2000:]
    assert r.stdout.strip() == 'Dear Practice Manager, see https://www.nhs.uk/x', r.stdout
    assert seen['path'] == '/proxy/modal/chat/completions', seen['path']
    body = seen['body']
    assert body['model'] == 'fake/open-model' and 'tools' not in body and 'response_format' not in body, body.keys()
    roles = [m['role'] for m in body['messages']]
    assert roles == ['system', 'user'], roles
    assert body['messages'][0]['content'] == advocate.INSTRUCTIONS['letter']
    assert VERDICTS[1]['quote'] in body['messages'][1]['content']
    assert json.loads(r.stderr[r.stderr.index('{'):])['has_nhs_url'] is True
    print('CLI via fake gateway: POST', seen['path'], 'roles', roles, 'keys', sorted(body))


if __name__ == '__main__':
    for name, test in list(globals().items()):
        if name.startswith('test_'):
            test()
            print('PASS', name)
    print('all offline advocate checks OK')
