"""Deterministic text metrics for advocate outputs. Stdlib only.

Self-check: uv run metrics.py

Syllable heuristic (documented, naive on purpose): count groups of consecutive vowels
(a e i o u y) in a word, then subtract one for a silent ending:
  - trailing "e" (but not consonant + "le", as in "table")
  - trailing "ed" unless it follows t or d ("required" 2, "needed" 2)
  - trailing "es" unless it follows s, x, z, c, g or h ("sites" 1, "practices" 3)
Every word has at least one syllable. URLs are removed before counting and numbers are not
words. A sentence ends at . ! ? or at a line break (so "Dear Practice Manager," and list
items count as their own sentences).
# ponytail: heuristic syllables, a few percent off on odd words; swap in a CMU dictionary
# lookup if the grade ever needs to be exact rather than comparable before/after.
"""
import re

BANNED = {
    'illegal': r'\billegal\w*',
    'unlawful': r'\bunlawful\w*',
    'breaking the law': r'\b(?:break|breaks|breaking|broke|broken)\s+the\s+law\b',
    'refuses': r'\brefuses\b',
    'refused': r'\brefused\b',
    'discriminat*': r'\bdiscriminat\w*',
}
_URL = re.compile(r'https?://\S+|www\.\S+')
_WORD = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?")
# nhs.uk itself, not a practice subdomain such as example-surgery.nhs.uk
_NHS_URL = re.compile(r'(?<![\w.-])(?:https?://)?(?:www\.)?nhs\.uk\b', re.I)


def banned_terms(text: str) -> dict[str, int]:
    return {name: len(re.findall(rx, text, re.I)) for name, rx in BANNED.items()}


def _words(text: str) -> list[str]:
    return _WORD.findall(_URL.sub(' ', text))


def word_count(text: str) -> int:
    return len(_words(text))


def sentence_count(text: str) -> int:
    parts = re.split(r'[.!?]+|\n+', _URL.sub(' ', text))
    return max(1, sum(1 for p in parts if _WORD.search(p)))


def syllables(word: str) -> int:
    w = re.sub(r'[^a-z]', '', word.lower())
    n = len(re.findall(r'[aeiouy]+', w))
    if w.endswith('e') and not re.search(r'[^aeiouy]le$', w):
        n -= 1
    elif w.endswith('ed') and not re.search(r'[td]ed$', w):
        n -= 1
    elif w.endswith('es') and not re.search(r'[sxzcgh]es$', w):
        n -= 1
    return max(1, n)


def _ratios(text: str) -> tuple[float, float]:
    words = _words(text)
    if not words:
        return 0.0, 0.0
    return len(words) / sentence_count(text), sum(map(syllables, words)) / len(words)


def flesch_reading_ease(text: str) -> float:
    """Higher is easier. 60 to 70 is plain English, 90+ is very easy."""
    wps, spw = _ratios(text)
    return round(206.835 - 1.015 * wps - 84.6 * spw, 1) if wps else 0.0


def flesch_kincaid_grade(text: str) -> float:
    """US school grade. Grade 5 to 6 is roughly a UK reading age of 10 to 12."""
    wps, spw = _ratios(text)
    return round(0.39 * wps + 11.8 * spw - 15.59, 1) if wps else 0.0


def has_nhs_url(text: str) -> bool:
    return bool(_NHS_URL.search(text))


def _norm(s: str) -> str:
    s = s.replace('‘', "'").replace('’', "'").replace('“', '"').replace('”', '"')
    return ' '.join(s.split()).casefold()


def contains_quote(text: str, quote: str) -> bool:
    """Verbatim match, ignoring case, whitespace runs, curly versus straight quotes and the quote's
    final full stop or comma (models often close the quotation mark before the full stop)."""
    q = _norm(quote).rstrip('.,')
    return bool(q) and q in _norm(text)


def placeholders(text: str) -> int:
    """Unfilled template slots such as [Your Name] or [date]. Markdown links are not counted."""
    return len(re.findall(r'\[[^\]\n]{1,40}\](?!\()', text))


def score_summary(text: str, verdict: dict) -> dict:
    quote = verdict.get('quote') or ''
    # The practice's own words may contain a banned term; only the advocate's words count.
    own_words = _norm(text).replace(_norm(quote), ' ') if quote.strip() else text
    banned = banned_terms(own_words)
    return {
        'word_count': word_count(text),
        'sentence_count': sentence_count(text),
        'flesch_reading_ease': flesch_reading_ease(text),
        'flesch_kincaid_grade': flesch_kincaid_grade(text),
        'banned_terms': banned,
        'banned_total': sum(banned.values()),
        'has_nhs_url': has_nhs_url(text),
        'contains_quote': contains_quote(text, quote),
        'contains_nhs_quote': contains_quote(text, verdict.get('nhs_quote') or ''),
        'placeholders': placeholders(text),
    }


def _self_check() -> None:
    for word, n in {
        'the': 1, 'cat': 1, 'make': 1, 'please': 1, 'proof': 1, 'sites': 1, 'you': 1,
        'address': 2, 'guidance': 2, 'people': 2, 'table': 2, 'required': 2, 'needed': 2,
        'website': 2, 'practice': 2, 'refused': 2, 'surgery': 3, 'practices': 3, 'registered': 3,
        'addresses': 3, 'registration': 4, 'immigration': 4, 'identification': 6,
    }.items():
        assert syllables(word) == n, (word, syllables(word), n)

    cat = 'The cat sat on the mat.'  # 6 words, 6 syllables, 1 sentence
    assert word_count(cat) == 6 and sentence_count(cat) == 1
    assert flesch_reading_ease(cat) == 116.1, flesch_reading_ease(cat)  # 206.835 - 6.09 - 84.6
    assert flesch_kincaid_grade(cat) == -1.4, flesch_kincaid_grade(cat)  # 2.34 + 11.8 - 15.59 = -1.45

    hard = ('Registration necessitates documentary identification substantiating residential '
            'eligibility notwithstanding contradictory national guidance.')
    easy = 'You can sign up with a GP. You do not need ID. You do not need proof of where you live.'
    assert flesch_reading_ease(easy) > 90 > 0 > flesch_reading_ease(hard)
    assert flesch_kincaid_grade(easy) < 3 < 20 < flesch_kincaid_grade(hard)

    letter = 'Dear Practice Manager,\n\nPlease fix the page. See https://www.nhs.uk/a.b.c/d for more.\n\nThanks'
    assert sentence_count(letter) == 4 and word_count(letter) == 11, (sentence_count(letter), word_count(letter))
    assert flesch_reading_ease('') == 0.0 and word_count('') == 0

    bad = 'This is ILLEGAL and unlawful. They are breaking the law. The surgery refuses and refused. It discriminates; discrimination!'
    assert banned_terms(bad) == {'illegal': 1, 'unlawful': 1, 'breaking the law': 1, 'refuses': 1,
                                 'refused': 1, 'discriminat*': 2}, banned_terms(bad)
    assert sum(banned_terms('This contradicts the NHS guidance on nhs.uk.').values()) == 0

    assert has_nhs_url('See https://www.nhs.uk/nhs-services/gps/') and has_nhs_url('the guidance on nhs.uk.')
    assert not has_nhs_url('See https://example-surgery.nhs.uk/register') and not has_nhs_url('no link')

    assert contains_quote('They say: “You  must bring\nphoto ID.”', 'you must bring photo ID.')
    assert contains_quote('They say "You must bring photo ID". So', 'You must bring photo ID.')  # full stop moved outside
    assert not contains_quote('You must bring ID.', 'You must bring photo ID.') and not contains_quote('x', '')
    assert not contains_quote('anything', '.')

    assert placeholders('Dear [Practice Manager],\n[Your Full Name]\n[date]') == 3
    assert placeholders('See [the guidance](https://www.nhs.uk/) and [1].') == 1 and placeholders('no slots') == 0

    verdict = {'quote': 'Patients who are refused must show a passport.', 'nhs_quote': 'NHS_QUOTE_TBD'}
    s = score_summary('Your page says: "Patients who are refused must show a passport." See nhs.uk.', verdict)
    assert s['contains_quote'] and s['has_nhs_url'] and s['banned_total'] == 0 and not s['contains_nhs_quote'], s
    assert s['placeholders'] == 0
    s = score_summary('The surgery refused you. That is illegal. [Your name]', verdict)
    assert s['banned_total'] == 2 and not s['contains_quote'] and s['word_count'] == 9 and s['placeholders'] == 1, s
    print('metrics self-check OK')


if __name__ == '__main__':
    _self_check()
