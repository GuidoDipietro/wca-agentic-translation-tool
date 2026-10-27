"""Local comparison, persistence and export. No network access."""
import copy
from bisect import bisect_right
from glossary import contains, source_matches, variants
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

import polib


class ReviewError(ValueError):
    pass


def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def key(entry):
    # JSON avoids ambiguity between context, singular and plural.
    return digest(json.dumps([entry.msgctxt, entry.msgid, entry.msgid_plural], ensure_ascii=False))


def forms(entry):
    if entry.msgid_plural:
        return [entry.msgstr_plural[i] for i in sorted(entry.msgstr_plural)]
    return [entry.msgstr]


TRANSLATION = re.compile(r'^msgstr(?:\[(\d+)\])? "(?:[^"\\\r\n]|\\.)*"[ \t]*(?:\r?\n|$)(?:^"(?:[^"\\\r\n]|\\.)*"[ \t]*(?:\r?\n|$))*', re.M)


class Catalog:
    def __init__(self, text):
        self.text = text
        try:
            self.po = polib.pofile(text, encoding='utf-8', check_for_duplicates=False)
        except Exception as exc:
            raise ReviewError(f'Invalid PO: {exc}') from exc
        self.entries = [e for e in self.po if not e.obsolete]
        identifiers = [(entry.msgctxt, entry.msgid) for entry in self.entries]
        if len(set(identifiers)) != len(identifiers):
            raise ReviewError('Duplicate identifiers in the PO.')
        self.by_key = {key(e): e for e in self.entries}
        if len(self.by_key) != len(self.entries):
            raise ReviewError('Duplicate identifiers in the PO.')
        n = re.search(r'\bnplurals\s*=\s*(\d+)', self.po.metadata.get('Plural-Forms', ''))
        self.nplurals = int(n.group(1)) if n else None
        groups = []
        for match in TRANSLATION.finditer(text):
            index = match.group(1)
            if index is None or index == '0':
                groups.append([])
            if not groups:
                raise ReviewError('Plural without initial form 0.')
            groups[-1].append(match)
        # polib separates the header from entries.
        if len(groups) != len(self.entries) + 1:
            raise ReviewError('Unsupported PO structure: a header and one msgstr block per entry are required.')
        self.spans = {}
        for entry, matches in zip(self.entries, groups[1:]):
            if entry.msgid_plural:
                indexes = [int(m.group(1)) for m in matches if m.group(1) is not None]
                if self.nplurals is None or indexes != list(range(self.nplurals)):
                    raise ReviewError(f'Incomplete plural forms: {entry.msgid[:70]}')
            elif len(matches) != 1 or matches[0].group(1) is not None:
                raise ReviewError('Invalid singular msgstr.')
            self.spans[key(entry)] = (matches[0].start(), matches[-1].end())
        # Also detect structures that polib might have parsed with data loss.
        for entry in self.entries:
            if not entry.msgid:
                raise ReviewError('Entry without msgid outside the header.')

    @classmethod
    def read(cls, path):
        with Path(path).open(encoding='utf-8', newline='') as stream:
            return cls(stream.read())

    def render(self, replacements, clear_fuzzy=()):
        text = self.text
        edits = []
        newline = '\r\n' if '\r\n' in text else '\n'
        for entry_key, values in sorted(replacements.items(), key=lambda pair: self.spans[pair[0]][0], reverse=True):
            entry = self.by_key[entry_key]
            if len(values) != len(forms(entry)):
                raise ReviewError('Incorrect number of plural forms.')
            start, end = self.spans[entry_key]
            if forms(entry) == values:
                continue
            lines = []
            for i, value in enumerate(values):
                # Explicit PO escapes; JSON would produce invalid escapes such as \u0000.
                if any(ord(c) < 32 and c not in '\n\t\r' for c in value):
                    raise ReviewError('The translation contains invalid control characters.')
                escaped = value.replace('\\', '\\\\').replace('"', '\\"').replace('\t', '\\t').replace('\r', '\\r').replace('\n', '\\n')
                prefix = f'msgstr[{i}]' if entry.msgid_plural else 'msgstr'
                lines.append(f'{prefix} "{escaped}"')
            trailing = newline if self.text[start:end].endswith('\n') else ''
            edits.append((start, end, newline.join(lines) + trailing))
        # Remove fuzzy only from approved entries; preserve all other bytes and flags.
        if clear_fuzzy:
            matches = list(TRANSLATION.finditer(text))
            ends = [match.end() for match in matches]
            for entry_key in set(clear_fuzzy):
                if 'fuzzy' not in self.by_key[entry_key].flags:
                    continue
                start = self.spans[entry_key][0]
                index = bisect_right(ends, start)
                before = ends[index - 1] if index else 0
                for match in re.finditer(r'^#,[^\r\n]*(?:\r?\n|$)', text[before:start], re.M):
                    flags = [flag.strip() for flag in match.group(0)[2:].strip().split(',')]
                    if 'fuzzy' in flags:
                        remaining = [flag for flag in flags if flag != 'fuzzy']
                        replacement = '#, ' + ', '.join(remaining) + newline if remaining else ''
                        edits.append((before + match.start(), before + match.end(), replacement))
        parts, position = [], 0
        for start, end, replacement in sorted(edits):
            if start < position:
                raise ReviewError('Overlapping PO edits are not supported.')
            parts.extend((text[position:start], replacement))
            position = end
        parts.append(text[position:])
        text = ''.join(parts)
        parsed = Catalog(text)
        if list(parsed.by_key) != list(self.by_key) or parsed.po.metadata != self.po.metadata:
            raise ReviewError('Export changed identifiers or the header.')
        for entry_key, entry in self.by_key.items():
            expected = replacements.get(entry_key, forms(entry))
            if forms(parsed.by_key[entry_key]) != expected:
                raise ReviewError('Export unexpectedly changed a translation.')
        return text


PLACEHOLDER = re.compile(r'%%|%\{[^{}]+\}|%\([^)]+\)[#0+\-]*(?:\d+|\*)?(?:\.(?:\d+|\*))?[a-zA-Z]|%(?:\d+\$)?[#0+\-]*(?:\d+|\*)?(?:\.(?:\d+|\*))?[hlL]?[diouxXeEfFgGcrsa]|\{\{[^{}]+\}\}|(?<!\{)\{[a-zA-Z_\d][^{}\n]*\}(?!\})')
FORMAT_PLACEHOLDER = re.compile(PLACEHOLDER.pattern.replace('[#0+\\-]*', '[#0 +\\-]*'))
TECHNICAL = re.compile(r'\]\(([^)]+)\)|https?://[^\s<>\)\]]+|</?[A-Za-z][^>]*>|\[[^\]\n]+\]:\s*\S+')


def tokens(pattern, value):
    return Counter(m.group(0) for m in pattern.finditer(value))


class Review:
    def __init__(self, original, proposed, data_dir, review_keys=None, glossary=None, source_checks=False, form_sources=None):
        self.glossary = glossary or []
        self.source_checks = source_checks
        self.form_sources = form_sources or {}
        self.original_path = Path(original)
        self.proposed_path = Path(proposed)
        self.original = Catalog.read(original)
        self.proposed = Catalog.read(proposed)
        if set(self.original.by_key) != set(self.proposed.by_key):
            raise ReviewError('The original and proposed PO files have different identifiers; entries will not be silently discarded.')
        if self.original.nplurals != self.proposed.nplurals:
            raise ReviewError('The proposed PO has a different plural configuration.')
        for entry_key, entry in self.original.by_key.items():
            if len(forms(entry)) != len(forms(self.proposed.by_key[entry_key])):
                raise ReviewError('The proposed PO has a different number of plural forms.')
        self.changed = [k for k, e in self.original.by_key.items() if forms(e) != forms(self.proposed.by_key[k])]
        if review_keys is not None:
            review_keys = set(review_keys)
            if not review_keys.issubset(self.original.by_key):
                raise ReviewError('Some review keys are outside the base PO.')
            self.changed = [k for k in self.original.by_key if k in review_keys]
        self.data_dir = Path(data_dir)
        self.state_path = self.data_dir / 'decisiones.json'
        self.signature = digest(self.original.text + '\x00' + self.proposed.text)
        self.document = {'version': 1, 'inputs': self.signature, 'revision': 0, 'decisions': {}, 'recent': None}
        if self.state_path.exists():
            try:
                self.document = json.loads(self.state_path.read_text(encoding='utf-8'))
            except (ValueError, OSError) as exc:
                raise ReviewError('Could not read decisiones.json; progress was not overwritten.') from exc
            if not isinstance(self.document, dict):
                raise ReviewError('The decisions file must contain a JSON object.')
            if self.document.get('version') != 1 or self.document.get('inputs') != self.signature:
                raise ReviewError('The input PO files changed or the JSON has a different format. Keep a data backup and use another --data-dir for a new review.')
        self.recent = Catalog(self.document['recent']) if self.document.get('recent') is not None else None
        if self.recent:
            self.check_recent(self.recent)
        decisions = self.document.get('decisions')
        if not isinstance(decisions, dict) or not isinstance(self.document.get('revision'), int):
            raise ReviewError('The decisions file has an invalid structure.')
        if not isinstance(self.document.get('dismissed_checks', {}), dict) or any(not isinstance(item, dict) for item in self.document.get('dismissed_checks', {}).values()):
            raise ReviewError('The dismissed checks record is invalid.')
        for entry_key, decision in decisions.items():
            self.check_decision(entry_key, decision)

    def verify_inputs(self):
        # Bases were parsed when the review opened. Checking their contents
        # preserves integrity without parsing thousands of keys again.
        with self.original_path.open(encoding='utf-8', newline='') as stream:
            original = stream.read()
        with self.proposed_path.open(encoding='utf-8', newline='') as stream:
            proposed = stream.read()
        if digest(original + '\x00' + proposed) != self.signature:
            raise ReviewError('The input PO files changed while the application was open. Restart the application to review the files.')

    def check_revision(self, revision):
        if revision != self.document['revision']:
            raise ReviewError('Progress changed in another tab. Reload before continuing.')

    def commit(self, doc):
        doc['revision'] = self.document['revision'] + 1
        atomic_write(self.state_path, json.dumps(doc, ensure_ascii=False, indent=2) + '\n')
        self.document = doc

    def check_decision(self, entry_key, decision):
        if entry_key not in self.changed or not isinstance(decision, dict):
            raise ReviewError('The decision does not belong to a changed entry.')
        status = decision.get('status')
        if status not in ('pending', 'accepted', 'rejected', 'edited'):
            raise ReviewError('Invalid decision status.')
        values = decision.get('values')
        if status == 'edited':
            if not isinstance(values, list) or len(values) != len(forms(self.original.by_key[entry_key])) or not all(isinstance(v, str) for v in values):
                raise ReviewError('The edit must include all plural forms as separate strings.')
        draft = decision.get('draft')
        if draft is not None and (not isinstance(draft, list) or len(draft) != len(forms(self.original.by_key[entry_key])) or not all(isinstance(v, str) for v in draft)):
            raise ReviewError('The draft must include all plural forms.')
        if decision.get('resolution') not in (None, 'current', 'chosen'):
            raise ReviewError('Invalid conflict resolution.')

    def decide(self, entry_key, decision, revision):
        self.check_revision(revision)
        self.check_decision(entry_key, decision)
        cleaned = {'status': decision['status']}
        if 'draft' in decision:
            cleaned['draft'] = decision['draft']
        if cleaned['status'] == 'edited':
            cleaned['values'] = decision['values']
        if decision.get('resolution'):
            cleaned['resolution'] = decision['resolution']
            cleaned['recent_hash'] = digest(self.recent.text) if self.recent else None
        doc = copy.deepcopy(self.document)
        doc['decisions'][entry_key] = cleaned
        self.commit(doc)

    def check_recent(self, catalog):
        if set(catalog.by_key) != set(self.original.by_key):
            raise ReviewError('The recent PO has added or removed entries or different identifiers. A new comparison is required; it cannot be merged automatically.')
        if catalog.nplurals != self.original.nplurals:
            raise ReviewError('The recent PO has a different plural configuration.')
        if catalog.po.metadata.get('Language') != self.original.po.metadata.get('Language'):
            raise ReviewError('The recent PO belongs to another language.')

    def set_recent(self, text, revision):
        self.check_revision(revision)
        recent = Catalog(text) if text is not None else None
        if recent:
            self.check_recent(recent)
        doc = copy.deepcopy(self.document)
        doc['recent'] = text
        self.commit(doc)
        self.recent = recent

    def chosen(self, entry_key, decision):
        if decision.get('status') == 'accepted':
            return forms(self.proposed.by_key[entry_key])
        if decision.get('status') == 'edited':
            return decision['values']
        return forms(self.original.by_key[entry_key])

    def conflict(self, entry_key, decision):
        if not self.recent:
            return False
        current = forms(self.recent.by_key[entry_key])
        old = forms(self.original.by_key[entry_key])
        chosen = self.chosen(entry_key, decision)
        resolved = decision.get('resolution') and decision.get('recent_hash') == digest(self.recent.text)
        return current != old and current != chosen and not resolved

    def snapshot(self):
        items = []
        counts = dict.fromkeys(['accepted', 'rejected', 'edited', 'pending'], 0)
        for entry_key in self.changed:
            entry = self.original.by_key[entry_key]
            decision = self.document['decisions'].get(entry_key, {'status': 'pending'})
            counts[decision['status']] += 1
            items.append({'key': entry_key, 'context': entry.msgctxt or '', 'source': [entry.msgid] + ([entry.msgid_plural] if entry.msgid_plural else []), 'original': forms(entry), 'proposed': forms(self.proposed.by_key[entry_key]), 'current': forms(self.recent.by_key[entry_key]) if self.recent else None, 'decision': decision, 'conflict': self.conflict(entry_key, decision), 'comment': entry.comment, 'translator_comment': entry.tcomment, 'references': entry.occurrences, 'flags': entry.flags})
        return {'revision': self.document['revision'], 'counts': counts, 'entries': items, 'recent': self.recent is not None, 'total_entries': len(self.original.entries)}

    def issue(self, code, entry_key, message, form=None, reference='', value='', dismissible=False):
        fingerprint = digest(json.dumps([entry_key, code, form, reference, value], ensure_ascii=False))
        return {'id': fingerprint, 'key': entry_key, 'code': code, 'form': form,
                'message': message, 'dismissible': dismissible}

    def dismiss_check(self, issue_id, revision, dismissed=True):
        self.check_revision(revision)
        if not isinstance(dismissed, bool):
            raise ReviewError('The validation decision is invalid.')
        _, errors, warnings = self.prepare_export(render=False)
        issue = next((item for item in errors + warnings + self.dismissed_issues if item['id'] == issue_id), None)
        if not issue or not issue['dismissible']:
            raise ReviewError('This check cannot be dismissed or no longer applies to the current text.')
        doc = copy.deepcopy(self.document)
        records = doc.setdefault('dismissed_checks', {})
        if dismissed:
            records[issue_id] = dict(issue, dismissed_at=datetime.now(timezone.utc).isoformat())
        else:
            records.pop(issue_id, None)
        self.commit(doc)

    def approved_keys(self):
        base = self.recent or self.original
        result = []
        for entry_key in self.changed:
            decision = self.document['decisions'].get(entry_key, {'status': 'pending'})
            if decision['status'] not in ('accepted', 'edited') or decision.get('resolution') == 'current':
                continue
            if self.chosen(entry_key, decision) != forms(base.by_key[entry_key]) or 'fuzzy' in self.original.by_key[entry_key].flags:
                result.append(entry_key)
        return result

    def validate_export(self, revision, mode='changes'):
        self.check_revision(revision)
        _, errors, warnings = self.prepare_export(render=False)
        count = len(self.approved_keys())
        if mode == 'changes' and not count and not errors:
            errors.append(self.issue('no_changes', None, 'There are no approved changes to download.'))
        return {'ok': not errors, 'errors': errors, 'warnings': warnings,
                'dismissed': self.dismissed_issues, 'counts': self.snapshot()['counts'], 'change_count': count}

    def prepare_export(self, render=True):
        self.verify_inputs()
        base = self.recent or self.original
        replacements, errors, warnings = {}, [], []
        for entry_key in self.changed:
            decision = self.document['decisions'].get(entry_key, {'status': 'pending'})
            label = self.original.by_key[entry_key].msgctxt or self.original.by_key[entry_key].msgid[:70]
            if self.conflict(entry_key, decision):
                errors.append(self.issue('conflict', entry_key, f'{label}: unresolved conflict with the recent download.'))
                continue
            if decision.get('resolution') == 'current' and self.recent and decision.get('recent_hash') == digest(self.recent.text):
                continue
            if decision['status'] not in ('accepted', 'edited'):
                continue
            values = self.chosen(entry_key, decision)
            old_values = forms(self.original.by_key[entry_key])
            if len(values) != len(forms(base.by_key[entry_key])):
                errors.append(self.issue('plural_count', entry_key, f'{label}: incorrect plural count.'))
                continue
            for i, (old, value) in enumerate(zip(old_values, values)):
                prefix = f'{label}, form {i}'
                if not value.strip():
                    errors.append(self.issue('empty', entry_key, f'{prefix}: empty translation.', i))
                if any(ord(char) < 32 and char not in '\n\r\t' for char in value):
                    errors.append(self.issue('characters', entry_key, f'{prefix}: invalid control characters.', i))
                source = self.form_sources[entry_key][i] if entry_key in self.form_sources else (self.original.by_key[entry_key].msgid if i == 0 else self.original.by_key[entry_key].msgid_plural)
                reference = source if self.source_checks or not old else old
                pattern = FORMAT_PLACEHOLDER if any(flag in self.original.by_key[entry_key].flags for flag in ('c-format', 'python-format', 'objc-format')) else PLACEHOLDER
                if tokens(pattern, reference) != tokens(pattern, value):
                    errors.append(self.issue('placeholders', entry_key, f'{prefix}: placeholders differ from the translation reference.', i, reference, value, True))
                if tokens(TECHNICAL, old) != tokens(TECHNICAL, value):
                    warnings.append(self.issue('technical', entry_key, f'{prefix}: links, tags or technical references changed; check that this is intentional.', i, old, value, True))
                source = self.form_sources[entry_key][i] if entry_key in self.form_sources else (self.original.by_key[entry_key].msgid if i == 0 else self.original.by_key[entry_key].msgid_plural)
                if tokens(pattern, reference) == tokens(pattern, value) and tokens(pattern, source) != tokens(pattern, value):
                    warnings.append(self.issue('source_placeholders', entry_key, f'{prefix}: placeholders do not match the English source (this may be a pre-existing issue).', i, source, value, True))
                for term in self.glossary:
                    if not source_matches(term, source):
                        continue
                    target_language = self.original.po.metadata.get('Language', '')
                    for forbidden in variants(term['forbidden'], target_language):
                        if forbidden and re.search(r'(?<!\w)' + re.escape(forbidden) + r'(?!\w)', value, re.I):
                            warnings.append(self.issue('forbidden_term', entry_key, f'{prefix}: term forbidden by the glossary: {forbidden}.', i, source + forbidden, value, True))
                    if term['preferred'] and not contains(value, variants(term['preferred'], target_language)):
                        warnings.append(self.issue('preferred_term', entry_key, f"{prefix}: check terminology; expected: {', '.join(term['preferred'])}.", i, source + json.dumps(variants(term['preferred'], target_language)), value, True))
            replacements[entry_key] = values
        self.dismissed_issues = []
        for collection in (errors, warnings):
            for item in collection[:]:
                record = self.document.get('dismissed_checks', {}).get(item['id'])
                if item['dismissible'] and record:
                    collection.remove(item)
                    self.dismissed_issues.append(dict(item, dismissed_at=record.get('dismissed_at')))
        output = None
        if render and not errors:
            try:
                output = base.render(replacements, clear_fuzzy=replacements if self.source_checks else ())
            except ReviewError as exc:
                errors.append(self.issue('structure', None, str(exc)))
        return output, errors, warnings

    def export(self, revision, confirm_pending=False, confirm_warnings=False, mode='full'):
        if mode not in ('full', 'changes'):
            raise ReviewError('Invalid PO export mode.')
        self.check_revision(revision)
        output, errors, warnings = self.prepare_export()
        counts = self.snapshot()['counts']
        if mode == 'changes' and not self.approved_keys() and not errors:
            errors.append(self.issue('no_changes', None, 'There are no approved changes to download.'))
        if errors:
            return {'ok': False, 'errors': errors, 'warnings': warnings, 'dismissed': self.dismissed_issues, 'counts': counts}
        if (counts['pending'] and not confirm_pending) or (warnings and not confirm_warnings):
            return {'ok': False, 'errors': [], 'warnings': warnings, 'counts': counts, 'dismissed': self.dismissed_issues, 'confirmation_required': True}
        if mode == 'changes':
            catalog = Catalog(output)
            partial = polib.POFile()
            partial.metadata = copy.deepcopy(catalog.po.metadata)
            partial.header = 'Approved changes to import and merge in Weblate.'
            for entry_key in self.approved_keys():
                partial.append(copy.deepcopy(catalog.by_key[entry_key]))
            output = str(partial)
            checked = Catalog(output)
            if set(checked.by_key) != set(self.approved_keys()):
                raise ReviewError('Partial export changed the approved keys.')
            for entry_key in checked.by_key:
                if forms(checked.by_key[entry_key]) != forms(catalog.by_key[entry_key]):
                    raise ReviewError('Partial export changed an approved translation.')
        language = self.original.po.metadata.get('Language', 'es_419').replace('-', '_')
        if not re.fullmatch(r'[A-Za-z0-9_]+', language):
            raise ReviewError('Invalid output language code.')
        path = self.data_dir / (('DEMO_' if getattr(self, 'demo', False) else 'SIMULATION_' if getattr(self, 'simulation', False) else '') + language.replace('_', '-') + '_regulations_reviewed.po')
        if path.resolve() in {self.original_path.resolve(), self.proposed_path.resolve()}:
            raise ReviewError('The export path matches an input PO; choose another --data-dir.')
        atomic_write(path, output)
        summary = {'ok': True, 'counts': counts, 'warnings': warnings, 'dismissed': self.dismissed_issues, 'mode': mode, 'recent_base': self.recent is not None, 'revision': revision, 'sha256': digest(output)}
        atomic_write(self.data_dir / 'resumen_exportacion.json', json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
        return dict(summary, content=output, filename=path.name)
