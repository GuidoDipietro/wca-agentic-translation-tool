"""Versioned language glossaries: bundled defaults and local replacements."""
import argparse
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from core import ReviewError, atomic_write, digest
from languages import language_code

DEFAULTS = Path(__file__).resolve().parents[1] / 'glossaries'
MAX_BYTES = 2 * 1024 * 1024


def canonical_language(value):
    return language_code(value).lower()


def validate_document(value, language):
    language = canonical_language(language)
    if not isinstance(value, dict) or set(value) != {'version', 'language', 'entries'} or type(value['version']) is not int or value['version'] != 1:
        raise ReviewError('Invalid glossary: expected version, language and entries.')
    if canonical_language(value['language']) != language:
        raise ReviewError('The glossary language does not match the selected language.')
    entries = value['entries']
    if not isinstance(entries, list) or len(entries) > 10000:
        raise ReviewError('Invalid glossary: entries must be a list of at most 10000 terms.')
    result, seen = [], set()
    for entry in entries:
        if not isinstance(entry, dict) or not {'source', 'preferred', 'forbidden'} <= entry.keys() or set(entry) - {'source', 'preferred', 'forbidden', 'note'}:
            raise ReviewError('Invalid glossary entry: expected source, preferred, forbidden and optional note.')
        clean = {}
        for field in ('source', 'preferred', 'forbidden'):
            words = entry[field]
            if not isinstance(words, list) or len(words) > 100:
                raise ReviewError('Glossary term fields must be lists of at most 100 strings.')
            if any(not isinstance(word, str) or not word.strip() or len(word) > 1000 or any(ord(c) < 32 for c in word) for word in words):
                raise ReviewError('Glossary terms must be nonempty text without control characters (1000 characters maximum).')
            clean[field] = list(dict.fromkeys(word.strip() for word in words))
        if not clean['source'] or not (clean['preferred'] or clean['forbidden']):
            raise ReviewError('Each glossary entry needs an English term and a preferred or forbidden translation.')
        keys = {word.casefold() for word in clean['source']}
        if seen & keys:
            raise ReviewError('Duplicate English glossary term. Group variants in one entry.')
        seen.update(keys)
        if {w.casefold() for w in clean['preferred']} & {w.casefold() for w in clean['forbidden']}:
            raise ReviewError('A glossary translation cannot be both preferred and forbidden.')
        note = entry.get('note', '')
        if not isinstance(note, str) or len(note) > 4000 or any(ord(c) < 32 and c not in '\n\t' for c in note):
            raise ReviewError('Invalid glossary note (4000 characters maximum).')
        if note.strip():
            clean['note'] = note.strip()
        result.append(clean)
    return {'version': 1, 'language': language, 'entries': result}


def encode(document):
    text = json.dumps(document, ensure_ascii=False, indent=2) + '\n'
    if len(text.encode('utf-8')) > MAX_BYTES:
        raise ReviewError('The glossary exceeds the 2 MB limit.')
    return text


def parse_document(text, language):
    if len(text.encode('utf-8')) > MAX_BYTES:
        raise ReviewError('The glossary exceeds the 2 MB limit.')
    try:
        document = json.loads(text)
    except ValueError:
        raise ReviewError('The glossary must be valid UTF-8 JSON.') from None
    return validate_document(document, language)


class GlossaryStore:
    def __init__(self, data_dir, defaults_dir=DEFAULTS):
        self.directory = Path(data_dir) / 'glossaries'
        self.defaults = Path(defaults_dir)

    def snapshot(self, language):
        code = canonical_language(language)
        local, bundled = self.directory / f'{code}.json', self.defaults / f'{code}.json'
        path = local if local.exists() else bundled
        origin = 'local' if local.exists() else 'bundled' if bundled.exists() else 'empty'
        try:
            document = parse_document(path.read_text(encoding='utf-8'), code) if path.exists() else {'version': 1, 'language': code, 'entries': []}
        except UnicodeError:
            raise ReviewError('The glossary must be valid UTF-8 JSON.') from None
        return {**document, 'origin': origin, 'revision': digest(origin + encode(document))}

    def save(self, language, document, revision):
        current = self.snapshot(language)
        if not isinstance(revision, str) or revision != current['revision']:
            raise ReviewError('The glossary changed. Reopen it before saving your changes.')
        clean = validate_document(document, language)
        atomic_write(self.directory / f'{clean["language"]}.json', encode(clean))
        return self.snapshot(language)


def merge_terms(remote, configured):
    """Configured entries replace remote entries sharing any English variant."""
    sources = {s.casefold() for term in configured for s in term['source']}
    return [t for t in remote if not any(s.casefold() in sources for s in t['source'])] + configured


def terms_tbx(terms, language):
    root = ET.Element('martif', type='TBX')
    body = ET.SubElement(ET.SubElement(root, 'text'), 'body')
    for index, term in enumerate(terms):
        entry = ET.SubElement(body, 'termEntry', id=f'term-{index}')
        for code, fields in [('en', [('source', None)]), (language.replace('_', '-'), [('preferred', None), ('forbidden', 'forbidden')])]:
            lang = ET.SubElement(entry, 'langSet', {'{http://www.w3.org/XML/1998/namespace}lang': code})
            for field, status in fields:
                for word in term[field]:
                    tig = ET.SubElement(lang, 'tig')
                    ET.SubElement(tig, 'term').text = word
                    if status:
                        ET.SubElement(tig, 'termNote', type='administrativeStatus').text = status
    return ET.tostring(root, encoding='unicode', xml_declaration=True)


def main():
    parser = argparse.ArgumentParser(description='Validate or import a language glossary without network access.')
    parser.add_argument('file', type=Path, help='UTF-8 JSON glossary')
    parser.add_argument('--language', required=True)
    parser.add_argument('--data-dir', type=Path, default=DEFAULTS.parent / 'data')
    parser.add_argument('--import-file', action='store_true', help='Replace this language’s local glossary; otherwise only validate')
    args = parser.parse_args()
    try:
        document = parse_document(args.file.read_text(encoding='utf-8'), args.language)
        encode(document)
        if args.import_file:
            # Use the same process lock as the server to avoid lost UI edits.
            import fcntl
            args.data_dir.mkdir(parents=True, exist_ok=True)
            with (args.data_dir / '.lock').open('a') as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise ReviewError('Stop the server before importing a glossary.') from None
                store = GlossaryStore(args.data_dir)
                store.save(args.language, document, store.snapshot(args.language)['revision'])
        print(f'{"Imported" if args.import_file else "Valid"}: {len(document["entries"])} terms ({document["language"]}).')
    except (ReviewError, OSError, UnicodeError) as exc:
        parser.exit(1, f'{exc}\n')


if __name__ == '__main__':
    main()
