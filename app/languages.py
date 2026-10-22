"""Códigos Weblate y nombres de formas plurales Rails por idioma."""
import re
from babel import Locale, UnknownLocaleError
from core import ReviewError

ORDER = ('zero', 'one', 'two', 'few', 'many', 'other')


def language_code(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z]{2,8}(?:[_-][A-Za-z0-9]{2,8}){0,3}', value):
        raise ReviewError('Código de idioma inválido.')
    return value.replace('-', '_')


def plural_names(language, observed=(), number=None):
    try:
        locale = Locale.parse(language_code(language))
        names = [name for name in ORDER if name == 'other' or name in locale.plural_form.tags]
    except (UnknownLocaleError, ValueError):
        names = []
    observed = set(observed)
    # El archivo nativo es la autoridad cuando el servidor conserva otra versión de CLDR.
    if observed and all(name in ORDER for name in observed) and (number is None or len(observed) == number):
        names = [name for name in ORDER if name in observed]
    if not names or (number is not None and len(names) != number):
        raise ReviewError('No se pudo identificar con seguridad el esquema plural de este idioma. La exportación se bloquea para no mezclar formas.')
    return names
