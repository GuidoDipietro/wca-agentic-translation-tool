"""Reconocimiento explícito de variantes; nunca inventa traducciones preferidas."""
import re

# Solo equivalencias gramaticales revisables. No se aplica stemming ni traducción.
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
