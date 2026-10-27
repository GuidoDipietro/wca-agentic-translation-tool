"""Payload CMS: message JSON, with PO used only as an internal reference."""
import copy
import json
from pathlib import Path

import polib

from core import Catalog, Review, ReviewError, atomic_write, digest, forms, key
from languages import language_code


def json_load(text):
    def unique(pairs):
        result = {}
        for name, value in pairs:
            if name in result:
                raise ValueError('Duplicate key')
            result[name] = value
        return result
    try:
        data = json.loads(text, object_pairs_hook=unique)
        if not isinstance(data, dict) or any(not name or not isinstance(item, dict) or not isinstance(item.get('message'), str) for name, item in data.items()):
            raise ValueError('Incorrect structure')
        return data
    except (ValueError, TypeError):
        raise ReviewError('Invalid Payload JSON: unique keys with a string message field are required.') from None


def convert_units(units, native, language='es_419'):
    data = json_load(native)
    english, target = polib.POFile(), polib.POFile()
    for po, code in ((english, 'en'), (target, language_code(language))):
        po.metadata = {'Language': code, 'Content-Type': 'text/plain; charset=UTF-8'}
    mapping, contexts = {}, set()
    for unit in units:
        context, source, values = unit.get('context'), unit.get('source'), unit.get('target')
        if not isinstance(context, str) or not context or context in contexts:
            raise ReviewError('Missing or duplicate Payload context.')
        if not isinstance(source, list) or len(source) != 1 or not isinstance(source[0], str) or not source[0]:
            raise ReviewError('Unsupported Payload source; one singular message per key is required.')
        if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], str):
            raise ReviewError('Unsupported Payload translation.')
        # Monolingual files may omit keys that have not been translated yet.
        # A missing nonempty translation indicates an inconsistent download.
        if context not in data:
            if values[0] != '':
                raise ReviewError(f'The API has a translation for {context}, but the key is missing from the JSON. Sync again.')
        elif data[context]['message'] != values[0]:
            raise ReviewError(f'The API and JSON have different translations for {context}. Sync again.')
        contexts.add(context)
        entry = polib.POEntry(msgctxt=context, msgid=source[0], msgstr=source[0], comment=unit.get('note') or '')
        mapping[key(entry)] = context
        english.append(entry)
        translated = copy.deepcopy(entry)
        translated.msgstr = values[0]
        if unit.get('fuzzy') or unit.get('state') == 10:
            translated.flags.append('fuzzy')
        target.append(translated)
    # JSON may retain entries that are no longer among the active
    # units. Keep them in the native base without inventing sources or proposals.
    if not contexts:
        raise ReviewError('The API returned no Payload units to review.')
    return str(english), str(target), mapping


class PayloadReview(Review):
    def __init__(self, *args, native_path, mapping, language='es_419', **kwargs):
        self.language = language_code(language)
        self.native_path = Path(native_path)
        self.native_text = self.native_path.read_text(encoding='utf-8')
        self.mapping = mapping
        json_load(self.native_text)
        super().__init__(*args, **kwargs)

    def set_recent(self, text, revision):
        self.check_revision(revision)
        catalog = None
        if text is not None:
            data = json_load(text)
            if any(context not in data and forms(self.original.by_key[k])[0] != '' for k, context in self.mapping.items()):
                raise ReviewError('The recent Payload download does not contain all session keys.')
            catalog = Catalog(self.original.render({k: [data.get(context, {}).get('message', '')] for k, context in self.mapping.items()}))
        doc = copy.deepcopy(self.document)
        doc['recent'] = catalog.text if catalog else None
        doc['native_recent'] = text
        self.commit(doc)
        self.recent = catalog

    def export(self, revision, confirm_pending=False, confirm_warnings=False, mode='full'):
        if mode not in ('full', 'suggestions', 'changes'):
            raise ReviewError('Invalid JSON export mode.')
        self.check_revision(revision)
        output, errors, warnings = self.prepare_export()
        counts = self.snapshot()['counts']
        if mode == 'changes' and not self.approved_keys() and not errors:
            errors.append(self.issue('no_changes', None, 'There are no approved changes to download.'))
        if errors:
            return {'ok': False, 'errors': errors, 'warnings': warnings, 'dismissed': self.dismissed_issues, 'counts': counts}
        if (counts['pending'] and not confirm_pending) or (warnings and not confirm_warnings):
            return {'ok': False, 'errors': [], 'warnings': warnings, 'counts': counts, 'dismissed': self.dismissed_issues, 'confirmation_required': True}
        if self.native_path.read_text(encoding='utf-8') != self.native_text:
            raise ReviewError('The base JSON changed during review.')
        base = self.document.get('native_recent') or self.native_text
        data = json_load(base)
        catalog = Catalog(output)
        before = copy.deepcopy(data)
        for entry_key in self.changed:
            context = self.mapping[entry_key]
            value = forms(catalog.by_key[entry_key])[0]
            if context in data or value != '':
                data.setdefault(context, {})['message'] = value
        if mode in ('suggestions', 'changes'):
            data = {self.mapping[k]: data[self.mapping[k]] for k in self.approved_keys()}
        native = base if mode == 'full' and data == before else json.dumps(data, ensure_ascii=False, indent=4) + '\n'
        if json_load(native) != data:
            raise ReviewError('JSON export changed values during serialization.')
        name = self.language.replace('_', '-') + ('_payload_suggestions.json' if mode == 'suggestions' else '_payload_reviewed.json')
        if getattr(self, 'demo', False):
            name = 'DEMO_' + name
        elif getattr(self, 'simulation', False):
            name = 'SIMULATION_' + name
        atomic_write(self.data_dir / name, native)
        summary = {'ok': True, 'counts': counts, 'warnings': warnings, 'component': 'payload', 'mode': mode, 'dismissed': self.dismissed_issues, 'revision': revision, 'sha256': digest(native)}
        atomic_write(self.data_dir / 'resumen_exportacion.json', json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
        return dict(summary, content=native, filename=name)
