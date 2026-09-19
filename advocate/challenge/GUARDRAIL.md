# Guardrail policy: keeping patient information away from the model

## Why it is worth doing here

Open Door serves individuals who need assistance, including migrants, people experiencing homelessness, or elderly residents lacking identification documents. Enquirers in such circumstances frequently paste everything in their possession into the submission box: "my NHS number is ..., call me on ...". The advocacy agent requires none of these details, because it compiles its summary note and formal letter purely from an assessment of a GP practice website.

Our principle is therefore straightforward: neither an NHS number nor a telephone number must ever reach the model. System prompt instructions like "please ignore personal details" only address a model that has already viewed the private text. Employing a Gateway guardrail with the **Redact** action provides a much stronger assurance. It substitutes the sensitive text before the payload departs the Gateway, so the model, the Modal container and its logs never get it. Client-side telemetry is different: `advocate.py` traces its own calls to Logfire when a Logfire token is set, so a prompt is recorded on the client before the Gateway redacts it. The echo test used documentation-safe test values only.

## The two protections

Since each safeguard targets a particular regular expression, two separate rules are configured. For each one, select **New protection > Custom pattern**, set **Apply to** to the targeted endpoint `modal`, and define **Action** as `Redact`. These safeguards operate purely on incoming requests, as detailed later.

| Name | Regex | Description to paste |
| --- | --- | --- |
| `NHS number` | `[Nn][Hh][Ss]\D{0,30}\d{3}[ -]?\d{3}[ -]?\d{4}` | NHS number (3-3-4 digits) written after the letters NHS. Patients paste it when asking for help. The model never needs it. |
| `UK phone number` | `(?:\+44[\s.-]?\(?0\)?\|\+44\|0)[\s.-]?\d{2,4}[\s.-]?\d{3,4}[\s.-]?\d{3,4}` | UK phone numbers: mobile, landline, international form. From the hackathon guide. |

Markdown syntax requires escaping the `|` delimiters within the telephone pattern inside the table above. Instead, obtain the raw expressions directly from `uv run challenge/guardrail_patterns.py`, which outputs them unescaped alongside 10 should-match and 10 should-not-match examples for **Pattern tests**.

These patterns avoid lookarounds, inline flags, and backreferences, making them fully portable across different regex engines.

### Design notes, including one change from the brief

- Without lookbehind assertions, an isolated 10 digit number cannot be distinguished from other numerical sequences. For this reason, the NHS rule anchors against the characters "NHS" and accepts up to 30 non-numerical characters ahead of the digits. The project brief proposed `NHS(?: number| no\.?)?:?\s*\d{3}[ -]?\d{3}[ -]?\d{4}`. In verification tests, that syntax failed to match the echo sample "my NHS number is 999 123 4567" owing to the term "is". Using `\D{0,30}` covers "is", ":", "-", "no." and lower case in one go.
- The redaction therefore removes the lead-in words too: "my NHS number is 999 123 4567" becomes "my [REDACTED]". That is fine: the lead-in carries no meaning once the number is gone.
- Known ceiling: a bare "999 123 4567" with no "NHS" before it is not caught. A portable resolution would involve a third safeguard matching `\d{3}[ -]\d{3}[ -]\d{4}`, which would also capture phone numbers formatted in the US style. Implement this additional check should applicants begin submitting numbers without prefixes.
- Recognised interaction: whenever an excerpt taken from a surgery website includes a telephone contact, that number gets masked in the advocate prompt as well, leaving the placeholder visible within the quoted passage in the letter. No test fixtures contain phone numbers (`guardrail_patterns.py` asserts this). Letters addressed to a GP practice have no requirement for the practice telephone number, so this consequence is entirely acceptable.
- Every test example strictly employs documentation-safe values: telephone numbers taken from the Ofcom drama range 07700 900000 to 07700 900999, and NHS numbers drawn from the 999 test range.

## Direction: request side only

The Redact action filters the **request**. Our fundamental commitment is that the model never receives this sensitive material. We make no claims regarding generated output. If the model fabricates a phone or NHS number in its response, that constitutes an entirely distinct concern handled by another mechanism (`Flag response`).

## Proof it fires: the echo test

Simply receiving a response that omits personal figures demonstrates nothing. The echo test instructs the model to replicate the provided string character by character, operating through the exact agent, model, and network route used in production:

```
uv run --env-file .env challenge/echo_test.py
```

| Result | Meaning |
| --- | --- |
| `MODEL RETURNED: 'my [REDACTED] and my phone is [REDACTED]'`, exit 0 | Both protections fired. The model got placeholders. |
| digits in the reply, exit 1 | A protection is missing, or its action is `Observe` or `Flag response`. |
| no digits and no placeholder, exit 2 | The model did not echo. Read the trace, run again. |
| request failed, exit 3 | What action `Block` looks like. |

Local testing without network calls: running `--dry-run` illustrates the exit 1 scenario, while `--dry-run --simulate-redact` demonstrates the exit 0 outcome. As recorded in `challenge/evidence/echo.json`, the live run was made (dry_run false, simulated_redact false); the model returned "my [REDACTED] and my phone is [REDACTED]"; trace id `01a0b9e2bc295ae97f2b8d921fe482d9`. So the placeholder text `[REDACTED]` is now confirmed by the live run.

Execute the echo verification with the optimisation rule **disabled**, ensuring that a stylistic rule cannot be held responsible for an altered output. Take a screenshot of the terminal session and capture the request trace inside Logfire.
