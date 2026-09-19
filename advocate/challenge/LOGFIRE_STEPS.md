# Logfire and Gateway: step-by-step instructions (EU region)

Every interface label listed here originates from the hackathon guide README (`github.com/laisbsc/demo_hack_tech_eu`, branch `master`). If the documentation omits a detail, the step is marked UNVERIFIED and you should follow the on-screen prompts.

Sections A through C require manual human completion, because they demand credentials, proxy tokens, and secret keys. Plan for roughly 45 minutes overall, largely spent waiting on CPU executions.

Execute all commands within the root directory of the project (`advocate/`).

## A. Prior to launching Logfire

1. First, deploy your model endpoint via `uv run modal deploy serve_modal.py`. When gathering evidence, maintain a single warm container by executing `ADVOCATE_MIN_CONTAINERS=1 uv run modal deploy serve_modal.py`. You will discover the endpoint URL alongside the BYOK configuration values inside `ENDPOINT.md`.
2. Obtain a Modal proxy token, comprising an ID (`wk-...`) and a secret (`ws-...`). Modal displays this secret only once. Generate it personally through the Modal control panel or by running `modal workspace proxy-tokens create`. For tokens restricted to specific environments, apply `modal workspace proxy-tokens allow <token-id> main`.

## B. Logfire project setup, feature flags, BYOK provider, and credentials

1. Sign in via `https://logfire-eu.pydantic.dev/`. Load or establish the project dedicated to Open Door.
2. Enable both hidden sections. Append the following string to your browser URL bar and press Enter:
   `#enableFlags=gateway_optimizations,gateway_guardrails_beta`
   The complete URL appears as: `https://logfire-eu.pydantic.dev/<org>/<project>#enableFlags=gateway_optimizations,gateway_guardrails_beta`
   Confirm that **Optimizations** and **Guardrails** now appear beneath the **Gateway** menu.
3. Within **Gateway**, configure Modal as your BYOK provider across four inputs:

   | Field | Value |
   | --- | --- |
   | Provider name | `modal` (must be exactly this, the code uses `route='modal'`) |
   | Base URL | `<endpoint-url>/v1` from `ENDPOINT.md`. The field is prefilled with `https://api.modal.com/v1`: REPLACE it, that host is not an inference API. |
   | Proxy token ID | `wk-...` |
   | Proxy token secret | `ws-...` |

4. Generate an API key for the Gateway (UNVERIFIED regarding the exact location; inspect the Gateway area for API key management). This provides `PYDANTIC_AI_GATEWAY_API_KEY`.
5. Produce a write token for the project under **project settings > Write tokens**. This becomes `LOGFIRE_TOKEN`.
6. Insert these entries into a `.env` file situated in your project root, which git ignores. Input these manually instead of copying them into conversation windows:

   ```
   PYDANTIC_AI_GATEWAY_BASE_URL=https://gateway-eu.pydantic.dev/proxy
   PYDANTIC_AI_GATEWAY_API_KEY=...
   LOGFIRE_TOKEN=...
   ADVOCATE_MODEL=Qwen/Qwen2.5-7B-Instruct
   ```

   Set the base URL to the gateway root path. Because the underlying code attaches the route identifier automatically, appending `/modal` here duplicates the segment. Ensure `ADVOCATE_MODEL` matches `MODEL_ID` inside `serve_modal.py`.
7. Conduct a sanity check with `uv run --env-file .env advocate.py --verdict fixtures/verdicts.json --index 0 --kind note`
   The command should output a note. Receiving `503 modal_no_live_containers` signals that instances are cold: pause for 2 minutes before trying again. Seeing `Route not found` indicates that the provider label is something other than `modal`. Any parsing failure or the error `'str' object has no attribute 'output'` reveals that your Base URL still references api.modal.com.

## C. Caveman trial run: verify system responsiveness (5 minutes)

1. Navigate to **Gateway > Optimizations**. Locate **Caveman mode (terse)** within the suggested catalogue and add it.
2. During the second installation stage, **Choose endpoints**, select `modal`. Inside the configuration screen, the entry under **Targeting** ought to specify route `modal` (whole route).
3. Repeat the sanity check from step B7. You should observe a distinctly more concise note.
4. Review the rule summary: the **Usage** graph needs to display both processed requests and modified outputs. Should it remain empty, your rule lacks an active link to `modal`. Correct this issue prior to proceeding.
5. Deactivate or remove this Caveman policy. It functions solely as a preliminary check rather than the competition submission.

## D. Generating the evaluation evidence: strict sequence

Keep `advocate.py` untouched from step D1 through D5, as the evaluation script logs its sha256 checksum during each invocation.

1. **Baseline.** Ensure that zero optimizations remain active on `modal`. Afterward, execute:
   `uv run --env-file .env challenge/evidence.py run rule_off --n 5`
   This produces 10 generations via CPU hardware. The process will take a few minutes. Individual query errors are recorded rather than halting the run.
2. **Install the rule.** Go to **Gateway > Optimizations > New optimization** to set up a bespoke rule.
   Populate the name, assign category `Style`, select action `Transform`, and extract the prompt text directly from `challenge/RULE_v3.txt` (the live version; v1 and v2 are in `challenge/RULE.md`). At step 2, **Choose endpoints**, select `modal`. Store the rule and verify that it remains active.
   SCREENSHOT 1: display the optimization interface highlighting the prompt text and the `modal` attachment.
3. **Optimized.** Trigger the identical script with an updated tag:
   `uv run --env-file .env challenge/evidence.py run rule_on --n 5`
4. **Compare.** Run `uv run challenge/evidence.py compare`
   This command writes a comparison file such as `challenge/evidence/COMPARE_rule_on_v3.md`, detailing median scores, shifts, parallel comparisons of the most divergent note and letter examples, trace identifiers, and sha256 validation hashes.
5. **Weak effect?** Should fewer than 3 metrics show favourable progress, deactivate rule 1 and set up rule 2 (the fallback) found in `RULE.md`. Then run:
   `uv run --env-file .env challenge/evidence.py run backup_on --n 5` followed by
   `uv run challenge/evidence.py compare --on backup_on`. Present the option demonstrating superior outcomes and state your choice clearly.
6. **Traces.** Within Logfire, inspect a single `rule_off` trace alongside a matching `rule_on` trace sharing the same run index and output category. You can retrieve trace identifiers from `challenge/evidence/rule_off.md` and `rule_on.md`.
   Locate these by their trace identifier or by searching for spans designated `evidence rule_off run 1 letter` and `evidence rule_on run 1 letter`.
   SCREENSHOT 2 and 3: display both traces exposing the `chat` span and incoming prompt. Within the `rule_on` view, check whether the prompt text includes the injected content (UNVERIFIED whether client telemetry captures this directly, given that the Gateway injects the text; the Gateway trace records should contain it). Retain both trace URLs for your entry.
   SCREENSHOT 4: capture the **Usage** diagram of the rule following the test, demonstrating the count of altered queries.

## E. The guardrail bonus section

1. Deactivate the optimization policy during this stage so that output modifications stem exclusively from protections.
2. Output the regular expressions and test values by executing `uv run challenge/guardrail_patterns.py`
3. Go to **Gateway > Guardrails > New protection > Custom pattern**.
   Input `NHS number` as the title, insert the explanation given in `GUARDRAIL.md`, and enter the NHS regex pattern. The web console checks syntax dynamically.
4. Under **Pattern tests**, insert the 10 positive test cases and 10 negative test cases produced in step 2. Every single one of the 20 test inputs must match its intended classification. The guardrail configuration retains these test entries.
   SCREENSHOT 5: illustrate all test patterns succeeding.
5. For **Apply to**, choose targeted endpoints and mark `modal`. Set **Action** to `Redact`. Avoid selecting `Observe`, which solely notes occurrences without altering content. Commit your changes.
6. Carry out instructions 3 through 5 once more for `UK phone number`, utilizing the telephone regex pattern alongside its corresponding sample data.
7. Optional reference benchmark (1 minute): toggle both actions to `Off`, then execute
   `uv run --env-file .env challenge/echo_test.py`. The raw numbers should return with exit code 1.
   Return both settings to `Redact`.
8. Validation: execute `uv run --env-file .env challenge/echo_test.py`
   Anticipate receiving `MODEL RETURNED: 'my [REDACTED] and my phone is [REDACTED]'` alongside exit code 0.
   SCREENSHOT 6: provide the console output.
   SCREENSHOT 7: capture the Logfire trace showing the sanitized payload (the console logs the trace ID), accompanied by the guardrail overview screen in **Guardrails** demonstrating the recorded detection.
9. Switch the optimization policy back on. The finished solution operates with this rule and both guardrails active.

## F. Submission deliverables

1. Rule configuration: SCREENSHOT 1 together with the prompt instructions taken from `RULE.md`.
2. Comparative outcomes: `challenge/evidence/COMPARE_rule_on_v3.md` (displaying comparative responses across identical inputs).
3. A pair of Logfire trace hyperlinks: one for `rule_off` and one for `rule_on` (refer to D6).
4. Quantitative data: the summary median tables documented in `COMPARE_rule_on_v3.md`, indicating whether each goal defined in `RULE.md` succeeded or fell short.
5. Guardrail evidence: SCREENSHOT 5 through 7, both regex expressions, and `challenge/evidence/echo.json`.
6. Justification statement: stylistic rules and phrasing standards for vulnerable audiences belong inside the Gateway, allowing the advocacy lead to modify them without triggering a deployment, whilst preventing sensitive patient identifiers from ever reaching the model.

## Troubleshooting (from the guide)

| Symptom | Cause |
| --- | --- |
| `503 modal_no_live_containers` | Scaled to zero. Retry after the cold start, or deploy with `ADVOCATE_MIN_CONTAINERS=1`. |
| `Route not found` | The provider is not named `modal`. The error lists the valid names. |
| `UnexpectedModelBehavior: 1 validation error` | The `metadata` widening is missing. `advocate.py` has it in `_setup()`. |
| Protection matches in the trace but digits still reach the model | Action is `Observe` or `Flag response`. Switch to `Redact`. |
| Trace shows `[Scrubbed due to ...]` instead of the text | Logfire's default scrubber hit a word such as "session" or "auth". The text is still in the `chat` span. |
