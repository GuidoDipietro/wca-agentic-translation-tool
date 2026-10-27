"""Parse local configuration as data; never execute or expand shell values."""
import os
from pathlib import Path

from core import ReviewError

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
        key, separator, value = line.partition('=')
        if separator and key.strip() == name:
            value = value.strip()
            if value[:1] in ('"', "'"):
                if len(value) < 2 or value[-1] != value[0]:
                    raise ReviewError(f'{name} has unclosed quotes.')
                value = value[1:-1]
            return value
    return ''


def checked_token(value, name='WEBLATE_TOKEN'):
    if any(character.isspace() or not 33 <= ord(character) <= 126 for character in value):
        raise ReviewError(f'{name} contains spaces or invalid characters. Check your configuration without sharing its contents.')
    return value
