# The custom optimisation rule

The prize task aims to modify how the agent acts without altering its codebase. Inside `advocate.py`, there are only two simple single-line prompts ("Write a note for a patient about this finding." and "Write a letter to the practice manager about this finding."). All instructions regarding style, vocabulary and comprehension difficulty reside entirely within the Gateway rule detailed below. With each run, `challenge/evidence.py` logs the sha256 hash for `advocate.py`, verifying that the script was never edited.

## Why this rule is necessary

- Intended recipients include people experiencing homelessness, recent immigrants, or elderly individuals lacking identity documents. If the text is too difficult, it serves no purpose. The text aims for a reading age of approximately 9.
- The system merely inspected a public web page rather than in-person reception desks. Accusatory correspondence addressed to a surgery would be inaccurate and dismissed. Using specific phrasing ("contradicts the NHS guidance on nhs.uk", never the banned words) serves as an overarching safeguard across the project.
- Content guidelines are maintained by the advocacy manager rather than software engineers. Through the Gateway, staff can modify, disable, or revert this policy in moments without redeploying software, updating every client using the `modal` route immediately.

## Rule 1 (main): Open Door house style

| Field | Value |
| --- | --- |
| Name | `Open Door house style` |
| Category | `Style` |
| Action | `Transform` |
| Endpoint (step 2, "Choose endpoints") | `modal` |

The official documentation only details `Style` and `Transform` for the category and action options (matching the setup displayed for the default Caveman example). Should the interface present other possibilities, retain `Style` and `Transform` unless another option clearly fits "inject an instruction into every request" better.

Injected instruction (130 words, paste exactly):

```
STYLE: Open Door house style. You write for patients and GP practice staff in England. Use plain English that a 9 year old can read. Short sentences. No jargon. Be polite and never accuse: we only read a website, we did not see what happens at the front desk. Never use the words illegal, unlawful or refuses. If the page says documents are required to register, say it "contradicts the NHS guidance on nhs.uk". If the page only asks for documents, say the NHS guidance on nhs.uk says they are not needed to register. Always include the NHS link given in the prompt. Quote the practice's own sentence exactly once, in quotation marks. Never add facts, names or placeholders. End a letter with one clear request: please update the page.
```

The reasoning behind both statements: nhs.uk notes that a surgery "may ask for extra documents", whilst official NHS England guidance calls a consistent, non-discriminatory ask "legitimate" (docs/CLAIMS.md, claim 1). Phrasing that turns paperwork into a strict requirement for enrolment is the sole thing that conflicts with this guidance. Because the incoming prompt specifies the category in plain text ("the page says documents are required to register" or "the page asks for documents but does not say they are required"), the language model knows which sentence to select.

## Rule 2 (backup): Open Door short and structured

Turn to this fallback solely if rule 1 fails to produce sufficient change. Since we are running a lightweight open-weight model via CPU (`Qwen/Qwen2.5-7B-Instruct` in `serve_modal.py`), compact models manage structural constraints more dependably than extensive lists of behavioural requirements.

| Field | Value |
| --- | --- |
| Name | `Open Door short and structured` |
| Category | `Style` |
| Action | `Transform` |
| Endpoint | `modal` |

Injected instruction (60 words, paste exactly):

```
STYLE: Short and structured. Patient note: at most 80 words, short sentences, no headings, no lists. Letter: at most 150 words, in this order: greeting, what the page says with one quoted sentence, the NHS link from the prompt, one request to update the page, sign-off. One idea per sentence. No preamble. No closing summary. No placeholders in square brackets.
```

Log the results by running `uv run --env-file .env challenge/evidence.py run backup_on --n 5`, followed by `uv run challenge/evidence.py compare --on backup_on`. Ensure just one rule remains active at any given time.

## Hypotheses (written before any live run)

Measurements derive from the dictionary keys in `metrics.score_summary` alongside token figures recorded by pydantic-ai. Every evaluation assesses 5 notes together with 5 letters for each label, preserving the identical order and verdicts.

| Metric | rule_off expectation | rule_on hypothesis (rule 1) | Backup rule hypothesis |
| --- | --- | --- | --- |
| `banned_total` (illegal, unlawful, breaking the law, refuses, refused, discriminat*) | above 0 in at least some outputs | median 0, and 0 in every output | no claim |
| `flesch_reading_ease` | 40 to 60 (formal letter English) | up by 15 points or more; notes at 80 or higher | up a little (shorter sentences) |
| `flesch_kincaid_grade` | 9 to 12 | down to about 4 to 6 for notes | down a little |
| `word_count` and `output_tokens` | notes about 150 words, letters about 250 | down by 30 percent or more | down by 40 percent or more, notes at 80 words or fewer |
| `has_nhs_url` | fewer than 5 of 5 | 5 of 5 for notes and for letters | 5 of 5 for letters |
| `contains_quote` | fewer than 5 of 5 | 5 of 5 | 5 of 5 for letters |
| `placeholders` (square-bracket slots such as `[Your Name]`) | above 0 in letters (a 7B model wrote "Dear Ms. Thompson" and "[Your Full Name]" in the inside test) | 0 in every output | 0 in every output |
| `latency_s` | baseline | down roughly in line with `output_tokens`, because CPU decoding time scales with tokens produced | same |
| `input_tokens` | baseline | UNVERIFIED: if the model server counts the injected text, this goes UP by roughly the rule length (about 140 tokens) with identical code, which is direct proof the Gateway injected it | up by about 80 |

Should any predicted outcome fall short, state the failure plainly in the final entry. Demonstrating shifts in 4 of 6 metrics using verifiable data holds greater value than unsubstantiated claims.

## What the judges can see without taking our word for it

1. Identical inputs generate paired results displayed together in `challenge/evidence/COMPARE_rule_on_v3.md`, each accompanied by its own Logfire trace id.
2. The sha256 checksum for `advocate.py` matches across every execution across both tests.
3. The dedicated **Usage** graph inside the Gateway displays both the requests processed and those altered by the directive.

## Iteration log (live, 2026-09-19)

Configured within the Logfire EU Gateway under the custom rule "Open Door house style" (category Style, action Transform, message role System, binding: route `modal`, whole route). Telemetry from the Gateway forwards every interaction directly to the Logfire project.

**v1 (the text above).** Evaluated relative to the rule_off baseline using unaltered code (advocate.py sha256 `0bd552801e0557d4` on every call) across the identical 5 verdicts: letters showed 51% fewer words, 58% fewer output tokens, 32% lower latency, reading grade dropping from 8.9 to 6.8, and placeholders falling from 5 to 1; notes saw their reading grade shift from 7.9 to 6.2 and NHS link inclusion rise from 0 of 5 to 3 of 5. The full comparison sits in `evidence/COMPARE_rule_v1.md`.
Testing v1 against active surgeries highlighted issues: patient notes mimicked surgery staff ("Thank you, The ... Team") and concluded by stating "please update the page", an instruction suitable solely for correspondence. Furthermore, letters omitted the web address more frequently, dropping from 4 of 5 to 2 of 5. These problems were corrected via the Gateway configuration rather than the code.

**v2.** Injected instruction:

```
STYLE: Open Door house style. You write as Open Door, a free service that reads GP practice websites in England. Never write as the practice. Plain English a 9 year old can read. Short sentences. No jargon. Never accuse: we only read a website, we did not see the front desk. Never use the words illegal, unlawful or refuses. Quote the practice's own sentence exactly once, in quotation marks. Write the NHS page as a full link: https://www.nhs.uk/nhs-services/gps/how-to-register-with-a-gp-surgery/ . Never add facts, names or placeholders in square brackets. Sign off as: Open Door. A NOTE FOR A PATIENT: tell them the NHS guidance on nhs.uk says you can register without ID, proof of address or immigration papers; that this practice's website says otherwise; that they can show the NHS link at reception. Never ask the patient to update anything. A LETTER TO A PRACTICE: if the page says documents are required, say it contradicts the NHS guidance on nhs.uk; if it only asks for them, say nhs.uk says they are not needed to register. End with one request: please update the page.
```

Result: `evidence/COMPARE_rule_on_v2.md`.

Outcomes for v2 compared to rule_off: see `evidence/COMPARE_rule_on_v2.md`. The public numbers for this entry are rule v3 only (see `evidence/COMPARE_rule_on_v3.md`).
When tested on 9 actual surgeries in Newham, several flaws appeared: an amber-tier letter employed "seems to contradict" (improper when a site simply requests documentation), a note inserted [1] style citations, and interface developers spotted the ambiguous phrase "the NHS guidance on their website", which made "their" resemble the surgery.

**v3.** Incorporates: "No markdown", "Always call the NHS page 'the NHS guidance on nhs.uk', never 'their website'. Call the practice by its name and its page 'the practice website'", "never add footnotes", and for amber pages "never say contradicts". The full text, byte for byte as configured in the Gateway (`RULE_v3.txt`):

```text
STYLE: Open Door house style. You write as Open Door, a free service that reads GP practice websites in England. Never write as the practice. Plain English a 9 year old can read. Short sentences. No jargon. No markdown. Never accuse: we only read a website, we did not see the front desk. Never use the words illegal, unlawful or refuses. Always call the NHS page "the NHS guidance on nhs.uk", never "their website". Call the practice by its name and its page "the practice website". Quote the practice's own sentence exactly once, in quotation marks. Write the NHS page as a full link: https://www.nhs.uk/nhs-services/gps/how-to-register-with-a-gp-surgery/ . Never add facts, names, footnotes or placeholders in square brackets. Sign off as: Open Door. A NOTE FOR A PATIENT: tell them the NHS guidance on nhs.uk says you can register without ID, proof of address or immigration papers; that the practice website says otherwise; that they can show the NHS link at reception. Never ask the patient to update anything. A LETTER TO A PRACTICE: if the page says documents are required, say it contradicts the NHS guidance on nhs.uk; if it only asks for them, never say contradicts, say the NHS guidance on nhs.uk says nobody needs them to register. End with one request: please update the page.
```

Outcomes are detailed in `evidence/COMPARE_rule_on_v3.md`.

An additional layer of protection exists in the codebase rather than the Gateway: `gen_frozen.py` discards generated copy containing forbidden terms, bracketed placeholders, citations, the phrase "their website", or the term "contradict" regarding an amber surgery, requesting fresh output up to 3 tries. Gateway directives guide the generation, whilst local software enforces what can appear.

Outcomes for v3 compared with rule_off (same code, sha256 `0bd552801e0557d4`, same 5 verdicts): notes decreased word counts by 40%, lowered reading grade from 7.9 to 6.5, boosted NHS link inclusion from 0 of 5 to 5 of 5, and cut placeholders from 3 to 0; letters showed 47% fewer words, 55% fewer output tokens, 53% lower latency, reading grade drop from 8.9 to 7.0, complete NHS link coverage from 4 of 5 to 5 of 5, and placeholders reduced from 5 to 0. One target was missed: notes quoted the surgery sentence 0 of 5 times (whereas letters achieved 5 of 5). Across the 9 actual Newham surgeries, our programmatic validation sent 2 of 18 generated drafts back for a retry (3 rejections in total, recorded in `data/frozen/newham/advocate.json` at the repo root), and all passed within 3 tries. The live deployment runs v3.
