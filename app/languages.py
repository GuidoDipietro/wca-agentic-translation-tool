"""Weblate codes and Rails plural form names by language."""
import re
from babel import Locale, UnknownLocaleError
from core import ReviewError

ORDER = ('zero', 'one', 'two', 'few', 'many', 'other')


def language_code(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z]{2,8}(?:[_-][A-Za-z0-9]{2,8}){0,3}', value):
        raise ReviewError('Invalid language code.')
    return value.replace('-', '_')


def plural_names(language, observed=(), number=None):
    try:
        locale = Locale.parse(language_code(language))
        names = [name for name in ORDER if name == 'other' or name in locale.plural_form.tags]
    except (UnknownLocaleError, ValueError):
        names = []
    observed = set(observed)
    # The native file is authoritative when the server uses another CLDR version.
    if observed and all(name in ORDER for name in observed) and (number is None or len(observed) == number):
        names = [name for name in ORDER if name in observed]
    if not names or (number is not None and len(names) != number):
        raise ReviewError('Could not reliably identify this language’s plural scheme. Export is blocked to avoid mixing forms.')
    return names
