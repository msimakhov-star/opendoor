"""Two custom Gateway guardrail patterns for Open Door, plus the samples to paste into Pattern tests.

Self-check: uv run challenge/guardrail_patterns.py

No lookarounds, no inline flags, no backreferences, so the patterns stay portable across regex
engines. Every sample uses documentation-safe values only: phone numbers from the Ofcom drama
range 07700 900000 to 07700 900999, NHS numbers from the 999 test range.
"""
import json
import re
from pathlib import Path

# "NHS", then up to 30 non-digit characters (" number is ", " no. ", ": "), then 3-3-4 digits with
# optional space or hyphen separators. The task brief suggested
#   NHS(?: number| no\.?)?:?\s*\d{3}[ -]?\d{3}[ -]?\d{4}
# but that does NOT match "my NHS number is 999 123 4567" (the echo test sentence), because of
# the word "is". \D{0,30} covers "is", ":", "-", "no." and lower case without listing them.
# ponytail: a bare "999 123 4567" with no "NHS" before it is not caught. Without lookbehind the
# only portable fix is a second protection \d{3}[ -]\d{3}[ -]\d{4}, which also hits US-style
# phone numbers; add it if patients are seen pasting bare numbers.
NHS_NUMBER = r'[Nn][Hh][Ss]\D{0,30}\d{3}[ -]?\d{3}[ -]?\d{4}'

# Verbatim from the hackathon guide README.
UK_PHONE = r'(?:\+44[\s.-]?\(?0\)?|\+44|0)[\s.-]?\d{2,4}[\s.-]?\d{3,4}[\s.-]?\d{3,4}'

PATTERNS = {
    'NHS number': {
        'regex': NHS_NUMBER,
        'match': [
            'my NHS number is 999 123 4567',
            'NHS number: 999 123 4567',
            'NHS number 9991234567',
            'NHS no. 999-123-4567',
            'NHS no 999 123 4567',
            'NHS: 999 000 0001',
            'nhs number 999 123 4567 please help',
            'NHS Number - 999 765 4321',
            'My NHS number, I think, is 9990001234',
            'NHS No: 999 123 4567, they still asked for a passport',
        ],
        'no_match': [
            'Ring NHS 111 if you feel unwell',
            'NHS number: 999 123 456',
            'NHS number 99 123 4567',
            'my number is 999 123 4567',
            'National Health number 999 123 4567',
            'NHS guidance was updated on 2026-09-18',
            'The NHS app is on version 2.1.4',
            'The NHS helpline opens 0800 to 1800',
            'I have been with the NHS since 1999',
            'NHS 111 or 999 in an emergency',
        ],
    },
    'UK phone number': {
        'regex': UK_PHONE,
        'match': [
            'Call me on 07700 900123',
            'Mobile: 07700900123',
            'Reach me at +44 7700 900123',
            '+447700900123 is my mobile',
            'tel: +44 (0)7700 900123',
            'Direct dial 07700-900-456',
            '07700.900.789 after 5pm',
            'and my phone is 07700 900123',
            'Text 07700 900 999 for a call back',
            '+44-7700-900000',
        ],
        'no_match': [
            'The appointment is on 2026-09-18',
            'We checked 45 practice websites',
            'Invoice total 1234.56',
            'Room 401, Building 3',
            'Surgery opens at 08:30',
            'Budget is 25000 GBP',
            'Form version 2.1.4',
            'Ref ABC-123-XY',
            'reception@example-surgery-a.example',
            'NHS number 999 123 4567',
        ],
    },
}

ECHO_TEXT = 'my NHS number is 999 123 4567 and my phone is 07700 900123'


def redact(text: str) -> str:
    """What two Redact protections do to a request. Used by echo_test.py --simulate-redact."""
    for p in PATTERNS.values():
        text = re.sub(p['regex'], '[REDACTED]', text)
    return text


def check() -> None:
    for name, p in PATTERNS.items():
        assert len(p['match']) == 10 and len(p['no_match']) == 10, name
        assert '(?=' not in p['regex'] and '(?!' not in p['regex'] and '(?<' not in p['regex'], name
        for s in p['match']:
            assert re.search(p['regex'], s), f'{name} should match: {s!r}'
        for s in p['no_match']:
            assert not re.search(p['regex'], s), f'{name} should NOT match: {s!r}'
    for s in PATTERNS['UK phone number']['match']:  # documentation-safe range only
        assert re.sub(r'\D', '', s).removeprefix('44').removeprefix('0').startswith('7700900'), s
    for s in PATTERNS['NHS number']['match']:
        assert re.search(r'\d', s).start() == re.search(r'999', s).start(), s
    assert redact(ECHO_TEXT) == 'my [REDACTED] and my phone is [REDACTED]', redact(ECHO_TEXT)
    assert redact('Please help me register with a GP.') == 'Please help me register with a GP.'
    # The protections sit on the product route too, so they must leave the advocate's own prompts alone.
    fixtures = Path(__file__).parent.parent / 'fixtures' / 'verdicts.json'
    for v in json.loads(fixtures.read_text()) if fixtures.exists() else []:
        for value in v.values():
            assert redact(str(value)) == str(value), f'guardrail would alter a product prompt: {value!r}'


if __name__ == '__main__':
    check()
    for name, p in PATTERNS.items():
        print(f'OK {name}: 10/10 match, 10/10 do not match\n\nRegex:\n{p["regex"]}\n')
        print('Should match:\n' + '\n'.join(p['match']))
        print('\nShould not match:\n' + '\n'.join(p['no_match']) + '\n')
    print(f'Echo text after both redactions: {redact(ECHO_TEXT)!r}')
