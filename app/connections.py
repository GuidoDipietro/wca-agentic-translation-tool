"""Local credentials: expose status, never keys, in the interface."""
from pathlib import Path
import re

from core import ReviewError
from configuration import ROOT, checked_token, env_value


class Connections:
    def __init__(self, env_path=ROOT / '.env', demo=False):
        self.env_path = Path(env_path)
        self.demo = demo
        self.overrides = {}

    def get(self, name):
        if self.demo:
            return ''
        return self.overrides[name] if name in self.overrides else env_value(name, self.env_path)

    def configure(self, values):
        allowed = {'WEBLATE_TOKEN', 'OPENAI_API_KEY', 'OPENAI_MODEL', 'LIBRETRANSLATE_URL', 'LIBRETRANSLATE_API_KEY'}
        if not isinstance(values, dict) or not set(values).issubset(allowed):
            raise ReviewError('Invalid connection fields.')
        cleaned = {}
        for name, value in values.items():
            if not isinstance(value, str):
                raise ReviewError('Configuration values must be strings.')
            value = value.strip()
            if name != 'OPENAI_MODEL':
                checked_token(value, name)
            elif value and not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,160}', value):
                raise ReviewError('Invalid model identifier.')
            if name == 'LIBRETRANSLATE_URL':
                if value and not value.startswith(('http://', 'https://')):
                    raise ReviewError('The LibreTranslate address must start with http:// or https://.')
                value = value.rstrip('/')
            cleaned[name] = value
        if self.demo:
            raise ReviewError('The demo does not use credentials.')
        self.overrides.update(cleaned)

    def status(self):
        return {'weblate': bool(self.get('WEBLATE_TOKEN')), 'openai': bool(self.get('OPENAI_API_KEY')), 'model': self.get('OPENAI_MODEL'), 'libretranslate': bool(self.get('LIBRETRANSLATE_URL'))}
