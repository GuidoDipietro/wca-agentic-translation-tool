"""Explicit variant matching; never invent preferred translations."""
import re

# Only reviewable grammatical equivalents. No stemming or translation.
VARIANTS = {
    'en': (
        ('delegate', 'delegates'), ('competitor', 'competitors'),
        ('competition', 'competitions'), ('organizer', 'organizers'),
        ('organiser', 'organisers'), ('judge', 'judges'),
        ('scrambler', 'scramblers'), ('scramble', 'scrambles'),
        ('WCA Delegate', 'WCA Delegates'),
    ),
    'es': (
        ('delegado', 'delegada', 'delegados', 'delegadas'),
        ('competidor', 'competidora', 'competidores', 'competidoras'),
        ('competencia', 'competencias'), ('competición', 'competiciones'),
        ('organizador', 'organizadora', 'organizadores', 'organizadoras'),
        ('juez', 'jueza', 'jueces', 'juezas'),
        ('mezclador', 'mezcladora', 'mezcladores', 'mezcladoras'),
        ('mezcla', 'mezclas'), ('delegado de la WCA', 'delegada de la WCA', 'delegados de la WCA', 'delegadas de la WCA'),
    ),
}
ESSENTIAL_SOURCE = {word.casefold() for group in VARIANTS['en'] for word in group}


def variants(words, language):
    code = (language or '').replace('-', '_').split('_')[0].lower()
    groups = VARIANTS.get(code, ())
    result = []
    for word in words:
        if not word:
            continue
        group = next((group for group in groups if word.casefold() in {v.casefold() for v in group}), ())
        for value in (word, *group):
            if value not in result:
                result.append(value)
    return result


def contains(text, words):
    return any(word and re.search(r'(?<!\w)' + re.escape(word) + r'(?!\w)', text, re.I) for word in words)


def source_matches(term, text):
    return contains(text, variants(term['source'], 'en'))


def essential(term):
    return any(word.casefold() in ESSENTIAL_SOURCE for word in term['source'])


def term_matches(glossary, text):
    """Match terms; prefer specific phrases over fully covered generic terms.

    The boolean controls only preferred-translation checks. Every matched term
    still participates in forbidden-translation checks.
    """
    matched = []
    for term in glossary:
        spans = {(match.start(), match.end())
                 for word in variants(term['source'], 'en') if word
                 for match in re.finditer(r'(?<!\w)' + re.escape(word) + r'(?!\w)', text, re.I)}
        if spans:
            matched.append((term, spans))
    result = []
    for index, (term, spans) in enumerate(matched):
        uncovered = any(not any(
            other_index != index and other['preferred'] and left <= start and end <= right
            and (left < start or end < right)
            for other_index, (other, other_spans) in enumerate(matched)
            for left, right in other_spans)
            for start, end in spans)
        result.append((term, uncovered))
    return result
