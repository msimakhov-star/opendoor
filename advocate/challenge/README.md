# Advocate: Best use of Pydantic

All paths are relative to the `advocate/` folder.

Advocate takes an Open Door finding (practice name, page URL, the practice's verbatim sentence, and red or amber category) to draft a plain-language note for a patient and a letter to the practice manager. A person reads, edits and approves each letter, meaning nothing is sent automatically. Requests run from `advocate.py` (Pydantic AI Agent) through Pydantic AI Gateway (EU region, route `modal`, configured as a BYOK provider with a Modal proxy token) to a Modal server running Qwen/Qwen2.5-7B-Instruct (Q4_K_M GGUF, Apache 2.0) served by llama.cpp (server-b11046) on Modal CPU (16 cores, 8 GiB) with Modal proxy authentication returning HTTP 401 when unauthenticated. Modal would not allow GPU types on this account without a payment method (L4, A100, H100, and B200 were tested), while gateway telemetry sends every call to Logfire. Inside `advocate.py`, only two generic instructions exist: "Write a note for a patient about this finding." and "Write a letter to the practice manager about this finding.", leaving all tone, reading level and wording policy to the Gateway rule.

## 1. The rule

The custom optimization rule named "Open Door house style" is configured under category Style, action Transform, and message role System, bound to the entire `modal` route. The live v3 text, byte for byte as in `challenge/RULE_v3.txt`:

```text
STYLE: Open Door house style. You write as Open Door, a free service that reads GP practice websites in England. Never write as the practice. Plain English a 9 year old can read. Short sentences. No jargon. No markdown. Never accuse: we only read a website, we did not see the front desk. Never use the words illegal, unlawful or refuses. Always call the NHS page "the NHS guidance on nhs.uk", never "their website". Call the practice by its name and its page "the practice website". Quote the practice's own sentence exactly once, in quotation marks. Write the NHS page as a full link: https://www.nhs.uk/nhs-services/gps/how-to-register-with-a-gp-surgery/ . Never add facts, names, footnotes or placeholders in square brackets. Sign off as: Open Door. A NOTE FOR A PATIENT: tell them the NHS guidance on nhs.uk says you can register without ID, proof of address or immigration papers; that the practice website says otherwise; that they can show the NHS link at reception. Never ask the patient to update anything. A LETTER TO A PRACTICE: if the page says documents are required, say it contradicts the NHS guidance on nhs.uk; if it only asks for them, never say contradicts, say the NHS guidance on nhs.uk says nobody needs them to register. End with one request: please update the page.
```

Earlier versions are in `challenge/RULE.md`.

Three iterations were created to address failures observed in real outputs, requiring zero code adjustments:
- v1 established plain English targeted at reading age 9, instructed the model never to accuse, applied NHS wording policy, required the NHS link and the practice quote, and forbade placeholders. When tested on real practices, v1 failed because patient notes sounded as though written by the practice ("Thank you, The ... Team") and asked the patient to "update the page".
- v2 instructed the model to write as Open Door, split guidance for patient notes from practice letters, required the full NHS link, and added an Open Door sign-off. When tested on the 9 real Newham practices, v2 failed: one letter for a page that only asks for documents used the phrase "seems to contradict" (under nhs.uk and NHS England guidance para 4.9.4 and 4.9.8, only pages that require documents contradict NHS guidance); one note added [1] footnotes; and the wording "the NHS guidance on their website" proved ambiguous to the frontend team.
- v3 (in production) prohibits markdown, requires the exact wording "the NHS guidance on nhs.uk", addresses the practice by name, removes footnotes, and forbids "contradicts" when a page only asks.

## 2. Before and after

Testing was conducted using `challenge/evidence.py`, submitting the same 5 synthetic verdicts from `fixtures/verdicts.json` (for dummy practices "Example Surgery A" to "D") for a note and a letter. The benchmark executed 5 runs per condition, comparing the rule disabled against the rule enabled. The sha256 hash of `advocate.py` was recorded on every API call and remained identical across every run: `0bd552801e0557d4`.

All reported figures represent medians across 5 runs, taken from `challenge/evidence/COMPARE_rule_on_v3.md` (with raw output preserved in `rule_off.json` and `rule_on_v3.json`). Earlier test runs remain recorded in `challenge/evidence/COMPARE_rule_v1.md` and `COMPARE_rule_on_v2.md`.

## 3. Trace links

Logfire needs a login, so there are no public trace links. Dashboard screenshots: `challenge/evidence/screenshots/logfire_rule_v3_usage.png` (Gateway usage of rule v3), `challenge/evidence/screenshots/guardrail_nhs_number_fired.png` and `challenge/evidence/screenshots/guardrail_uk_phone_fired.png` (each guardrail shows "1 matched of 115 evaluated": the echo test below).

A judge with access can inspect the trace ids below:
- note pair, run 3, Example Surgery C: rule_off trace `01a0b9e0ffa8faf21e852b369cec1eaa`, rule_on_v3 trace `01a0b9f213e99d882a70e9f0970f01a2` (`challenge/evidence/COMPARE_rule_on_v3.md`)
- letter pair, run 5, Example Surgery A: rule_off trace `01a0b9e25b5c5cdab9dfa4b2b4ec2344`, rule_on_v3 trace `01a0b9f31b1e3b8221d82a772af21113` (`challenge/evidence/COMPARE_rule_on_v3.md`)
- echo test: trace `01a0b9e2bc295ae97f2b8d921fe482d9` (`challenge/evidence/echo.json`)

## 4. Numbers

The median results comparing rule v3 against no rule are set out below.

### Letters

| Metric | No rule | Rule v3 | Difference |
|---|---|---|---|
| Words | 270 | 143 | 47% fewer |
| Output tokens | 367 | 164 | 55% fewer |
| Latency | 30.6 s | 14.3 s | 53% lower |
| Flesch-Kincaid grade | 8.9 | 7.0 | Lower reading level |
| NHS link present | 4 of 5 | 5 of 5 | +1 |
| Bracket placeholders like [Your Name] | 5 | 0 | Removed |

### Patient notes

| Metric | No rule | Rule v3 | Difference |
|---|---|---|---|
| Words | 126 | 75 | 40% fewer |
| Flesch-Kincaid grade | 7.9 | 6.5 | Lower reading level |
| NHS link present | 0 of 5 | 5 of 5 | +5 |
| Placeholders | 3 | 0 | Removed |

Cost of the rule: about 300 extra input tokens per call (median +299 for both notes and letters, `challenge/evidence/COMPARE_rule_on_v3.md`), corresponding to the rule text itself.

What was not achieved: patient notes quoted the practice's own sentence in 0 of 5 runs (letters achieved 5 of 5). Banned words were 0 before and after, so the rule instruction to never accuse showed no measurable effect on this sample.

## 5. Guardrail and the echo test

Two custom protections with action Redact are scoped to route `modal`: "NHS number" using regex `[Nn][Hh][Ss]\D{0,30}\d{3}[ -]?\d{3}[ -]?\d{4}` and "UK phone number" using the pattern from the guide. Neither pattern uses lookarounds. Pattern checks in `challenge/guardrail_patterns.py` test 10 should-match and 10 should-not-match cases each, with all passing (for instance, "Ring NHS 111" and "08:30" are not redacted).

Patients sometimes paste personal details when seeking assistance, so this request-side redaction ensures the model never receives them.

A live echo test through the real route (`challenge/echo_test.py`, recorded in `challenge/evidence/echo.json`) submitted the prompt:
"Repeat the following back to me character for character, with no other words: my NHS number is 999 123 4567 and my phone is 07700 900123"

The model returned:
"my [REDACTED] and my phone is [REDACTED]"

In the Gateway dashboard each guardrail shows "1 matched of 115 evaluated": this echo test, and 0 matches on the other 114 requests (`challenge/evidence/screenshots/guardrail_nhs_number_fired.png`, `challenge/evidence/screenshots/guardrail_uk_phone_fired.png`).

Both values fall inside documentation-safe ranges: NHS test numbers start with 999, and 07700 900xxx is reserved for drama by Ofcom.

## 6. Why this is worth doing

The intended audience includes people who are homeless, newly arrived in the UK, or older individuals without documents; notes written beyond an accessible level do not help them. In addition, because the agent only inspects a website, an accusatory letter to a practice manager would be inappropriate and ignored.

Wording policy belongs to the people running the advocacy service rather than developers. Through Gateway, policies can be altered, rolled back, or disabled in seconds across every caller of the route without deploying code. Furthermore, bracket placeholders such as [Your Name] in a draft awaiting volunteer approval represent a genuine failure, which the rule eliminated entirely.

## 7. Second line of defence in code

In `gen_frozen.py`, which generates demonstration data across 9 real Newham practices, a code gate evaluates the text. The gate rejects any response containing a banned word, bracket placeholder, footnote, "their website", or "contradict" on an amber page, triggering a regeneration up to 3 attempts. On the final run (attempts recorded in `data/frozen/newham/advocate.json` at the repo root), 2 of 18 texts needed a retry, with 3 rejections in total, and all passed within 3 tries. The Gateway rule shapes the model's output, while code controls what is permitted to be shown.

## 8. How to reproduce

Initial setup:
```bash
uv sync
```

Offline execution without API keys:
```bash
uv run tests/test_advocate_offline.py
uv run challenge/guardrail_patterns.py
uv run challenge/evidence.py run rule_off --n 5 --dry-run
```

Live execution:
1. Define `PYDANTIC_AI_GATEWAY_BASE_URL=https://gateway-eu.pydantic.dev/proxy` and `PYDANTIC_AI_GATEWAY_API_KEY` in an env file.
2. Deploy the model:
```bash
uv run modal deploy serve_modal.py
```
3. Add the deployed endpoint to Gateway as BYOK provider `modal` (`ENDPOINT.md`).
4. Execute the evaluation runs:
```bash
uv run --env-file <file> challenge/evidence.py run rule_off --n 5
```
Enable the rule in Gateway, then execute:
```bash
uv run --env-file <file> challenge/evidence.py run rule_on --n 5
uv run challenge/evidence.py compare
```
