# Open Door: classifier eval

Results file: `data/frozen/newham/results.json`

Reference labels: LLM-produced (three blind LLM labellers, majority vote in code). No person has checked them yet. They are not labelled by a person. Small sample from one London borough: these numbers describe this run on these pages only.
The tool reads website wording, not front-desk behaviour.

- Pages scored: 19 of 20 labelled (missing from results: F84717)
- Predicted not_checked (blocked or error, counted as wrong): 5
- Accuracy on unanimous pages: 14/19 = 74%
- Accuracy on all majority pages: 14/19 = 74%
- Red vs not red (demands_documents): precision 100%, recall 100% (TP 3, FP 0, FN 0)
  - A false red wrongly says a practice website contradicts the NHS guidance on nhs.uk. A missed red hides one from a patient.
- Predicted quotes that are verbatim on the page: 8/8 = 100%
- Pages where results say quote_verified=true but the quote is not on the page: none

## Confusion table (rows: reference label, columns: predicted)

| reference \ predicted | demands | asks_softly | says_not | no_mention | unclear | not_checked |
|---|---|---|---|---|---|---|
| demands | 3 | 0 | 0 | 0 | 0 | 0 |
| asks_softly | 0 | 3 | 0 | 0 | 0 | 1 |
| says_not | 0 | 0 | 2 | 0 | 0 | 0 |
| no_mention | 0 | 0 | 0 | 6 | 0 | 4 |

## Disagreements (5)

### F84088: reference no_mention, predicted not_checked
- Reference quote: none
- Predicted quote: none

### F84092: reference no_mention, predicted not_checked
- Reference quote: none
- Predicted quote: none

### F84642: reference asks_softly, predicted not_checked
- Reference quote: "When you register with our GP Practice, we might ask for ID or proof of address."
- Predicted quote: none

### F84670: reference no_mention, predicted not_checked
- Reference quote: none
- Predicted quote: none

### F84677: reference no_mention, predicted not_checked
- Reference quote: none
- Predicted quote: none
