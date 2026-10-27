"""Synthetic data to test the workflow without network access or credentials."""
from pathlib import Path
import json
import time

from locales import convert_units


class DemoClient:
    is_demo = True

    def __init__(self, delay=.25):
        self.component = 'locales'
        self.delay = delay
        self.progress = None
        self.env_path = Path('/nonexistent/wca-demo-no-credentials')

    def download(self):
        for stage, message in [(1, 'DEMO · checking simulated status'), (2, 'DEMO · loading fictional keys'), (3, 'DEMO · validating placeholders and plurals'), (4, 'DEMO · checking the simulated base'), (5, 'DEMO · preparing the sample glossary')]:
            if self.progress:
                self.progress({'stage': stage, 'total': 7, 'message': message})
            time.sleep(self.delay)
        translations = {'demo->copied': ['Copiado al portapapeles'], 'demo->welcome': ['Te damos la bienvenida, %{name}'], 'demo->cubes': ['%{count} cubo', '%{count} cubos', '%{count} cubos']}
        units = [
            {'context': 'demo->copied', 'source': ['Copied to clipboard'], 'target': ['']},
            {'context': 'demo->welcome', 'source': ['Welcome, %{name}'], 'target': ['Bienvenido, %{name}'], 'fuzzy': True},
            {'context': 'demo->cubes', 'source': ['%{count} cube', '%{count} cubes'], 'target': ['', '', '']},
            {'context': 'demo->untouched', 'source': ['Already translated'], 'target': ['Ya traducido']},
        ]
        native = '''# DEMO: fictional data. Do not upload to Weblate.
es-419:
  demo:
    copied: ""
    welcome: Bienvenido, %{name}
    cubes:
      one: ""
      many: ""
      other: ""
    untouched: Ya traducido
'''
        english, target, mapping = convert_units(units, native)
        payload = {'english': english, 'target': target, 'glossary': None, 'metadata': {'en': {'revision': 'demo'}, 'es_419': {'revision': 'demo'}}, 'warnings': [], 'demo': True, 'demo_proposals': translations}
        if self.component == 'locales':
            payload.update(native_yaml=native, yaml_mapping=mapping)
        if self.component == 'payload':
            from payload import convert_units as convert_payload
            singular = [unit for unit in units if len(unit['source']) == 1]
            native_json = json.dumps({u['context']: {'message': u['target'][0]} for u in singular})
            english, target, mapping = convert_payload(singular, native_json, getattr(self, 'language', 'es_419'))
            payload.update(english=english, target=target, native_json=native_json, json_mapping=mapping)
        return payload
