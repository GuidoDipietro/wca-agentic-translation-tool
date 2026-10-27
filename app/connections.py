"""Local credentials: expose status, never keys, in the interface."""
import json
import os
from pathlib import Path
import re

from core import ReviewError, atomic_write
from weblate import checked_token

ROOT = Path(__file__).resolve().parent.parent


def env_value(name, path=ROOT / '.env'):
    if name in os.environ:
        return os.environ[name].strip()
    path = Path(path)
    if not path.exists():
        return ''
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line.startswith('export '):
            line = line[7:].strip()
        key, sep, value = line.partition('=')
        if sep and key.strip() == name:
            value = value.strip()
            if value[:1] in ('"', "'"):
                if len(value) < 2 or value[-1] != value[0]:
                    raise ReviewError(f'{name} has unclosed quotes.')
                value = value[1:-1]
            return value
    return ''


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
        allowed = {'WEBLATE_TOKEN', 'OPENAI_API_KEY', 'OPENAI_MODEL'}
        if not isinstance(values, dict) or not set(values).issubset(allowed):
            raise ReviewError('Invalid connection fields.')
        cleaned = {}
        for name, value in values.items():
            if not isinstance(value, str):
                raise ReviewError('Configuration values must be strings.')
            value = value.strip()
            if name != 'OPENAI_MODEL':
                checked_token(value)
            elif value and not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,160}', value):
                raise ReviewError('Invalid model identifier.')
            cleaned[name] = value
        if self.demo:
            raise ReviewError('The demo does not use credentials.')
        self.overrides.update(cleaned)

    def status(self):
        return {'weblate': bool(self.get('WEBLATE_TOKEN')), 'openai': bool(self.get('OPENAI_API_KEY')), 'model': self.get('OPENAI_MODEL')}
