"""Comparación, persistencia y exportación locales. No usa la red."""
import copy
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
    # JSON evita ambigüedades entre contexto, singular y plural.
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
            self.po = polib.pofile(text, encoding='utf-8', check_for_duplicates=True)
        except Exception as exc:
            raise ReviewError(f'PO inválido: {exc}') from exc
        self.entries = [e for e in self.po if not e.obsolete]
        self.by_key = {key(e): e for e in self.entries}
        if len(self.by_key) != len(self.entries):
            raise ReviewError('Identificadores duplicados en el PO.')
        n = re.search(r'\bnplurals\s*=\s*(\d+)', self.po.metadata.get('Plural-Forms', ''))
        self.nplurals = int(n.group(1)) if n else None
        groups = []
        for match in TRANSLATION.finditer(text):
            index = match.group(1)
            if index is None or index == '0':
                groups.append([])
            if not groups:
                raise ReviewError('Plural sin forma inicial 0.')
            groups[-1].append(match)
        # polib separa el encabezado de las entradas.
        if len(groups) != len(self.entries) + 1:
            raise ReviewError('Estructura PO no compatible: se requiere un encabezado y un bloque msgstr por entrada.')
        self.spans = {}
        for entry, matches in zip(self.entries, groups[1:]):
            if entry.msgid_plural:
                indexes = [int(m.group(1)) for m in matches if m.group(1) is not None]
                if self.nplurals is None or indexes != list(range(self.nplurals)):
                    raise ReviewError(f'Formas plurales incompletas: {entry.msgid[:70]}')
            elif len(matches) != 1 or matches[0].group(1) is not None:
                raise ReviewError('msgstr singular inválido.')
            self.spans[key(entry)] = (matches[0].start(), matches[-1].end())
        # También detecta estructuras que polib pudiera haber interpretado con pérdida.
        for entry in self.entries:
            if not entry.msgid:
                raise ReviewError('Entrada sin msgid fuera del encabezado.')

    @classmethod
    def read(cls, path):
        with Path(path).open(encoding='utf-8', newline='') as stream:
            return cls(stream.read())

    def render(self, replacements, clear_fuzzy=()):
        text = self.text
        newline = '\r\n' if '\r\n' in text else '\n'
        for entry_key, values in sorted(replacements.items(), key=lambda pair: self.spans[pair[0]][0], reverse=True):
            entry = self.by_key[entry_key]
            if len(values) != len(forms(entry)):
                raise ReviewError('Cantidad de formas plurales incorrecta.')
            start, end = self.spans[entry_key]
            if forms(entry) == values:
                continue
            lines = []
            for i, value in enumerate(values):
                # Escapes PO explícitos; json produciría escapes no válidos como \u0000.
                if any(ord(c) < 32 and c not in '\n\t\r' for c in value):
                    raise ReviewError('La traducción contiene caracteres de control no válidos.')
                escaped = value.replace('\\', '\\\\').replace('"', '\\"').replace('\t', '\\t').replace('\r', '\\r').replace('\n', '\\n')
                prefix = f'msgstr[{i}]' if entry.msgid_plural else 'msgstr'
                lines.append(f'{prefix} "{escaped}"')
            trailing = newline if self.text[start:end].endswith('\n') else ''
            text = text[:start] + newline.join(lines) + trailing + text[end:]
        # Quitar fuzzy solo en entradas aprobadas; los demás bytes y flags se conservan.
        if clear_fuzzy:
            current = Catalog(text)
            edits = []
            matches = list(TRANSLATION.finditer(text))
            for entry_key in clear_fuzzy:
                if 'fuzzy' not in current.by_key[entry_key].flags:
                    continue
                start = current.spans[entry_key][0]
                before = max((m.end() for m in matches if m.end() <= start), default=0)
                for match in re.finditer(r'^#,[^\r\n]*(?:\r?\n|$)', text[before:start], re.M):
                    flags = [flag.strip() for flag in match.group(0)[2:].strip().split(',')]
                    if 'fuzzy' in flags:
                        remaining = [flag for flag in flags if flag != 'fuzzy']
                        replacement = '#, ' + ', '.join(remaining) + newline if remaining else ''
                        edits.append((before + match.start(), before + match.end(), replacement))
            for start, end, replacement in sorted(edits, reverse=True):
                text = text[:start] + replacement + text[end:]
        parsed = Catalog(text)
        if list(parsed.by_key) != list(self.by_key) or parsed.po.metadata != self.po.metadata:
            raise ReviewError('La exportación alteró los identificadores o el encabezado.')
        for entry_key, entry in self.by_key.items():
            expected = replacements.get(entry_key, forms(entry))
            if forms(parsed.by_key[entry_key]) != expected:
                raise ReviewError('La exportación alteró una traducción inesperadamente.')
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
            raise ReviewError('Los PO original y propuesto tienen identificadores distintos; no se descartarán entradas silenciosamente.')
        if self.original.nplurals != self.proposed.nplurals:
            raise ReviewError('El PO propuesto tiene una configuración de plurales diferente.')
        for entry_key, entry in self.original.by_key.items():
            if len(forms(entry)) != len(forms(self.proposed.by_key[entry_key])):
                raise ReviewError('El PO propuesto tiene una cantidad distinta de formas plurales.')
        self.changed = [k for k, e in self.original.by_key.items() if forms(e) != forms(self.proposed.by_key[k])]
        if review_keys is not None:
            if not set(review_keys).issubset(self.original.by_key):
                raise ReviewError('Hay claves de revisión fuera del PO de base.')
            self.changed = [k for k in self.original.by_key if k in set(review_keys)]
        self.data_dir = Path(data_dir)
        self.state_path = self.data_dir / 'decisiones.json'
        self.signature = digest(self.original.text + '\x00' + self.proposed.text)
        self.document = {'version': 1, 'inputs': self.signature, 'revision': 0, 'decisions': {}, 'recent': None}
        if self.state_path.exists():
            try:
                self.document = json.loads(self.state_path.read_text(encoding='utf-8'))
            except (ValueError, OSError) as exc:
                raise ReviewError('No se pudo leer decisiones.json; no se sobrescribió el progreso.') from exc
            if not isinstance(self.document, dict):
                raise ReviewError('El archivo de decisiones debe contener un objeto JSON.')
            if self.document.get('version') != 1 or self.document.get('inputs') != self.signature:
                raise ReviewError('Los PO de entrada cambiaron o el JSON tiene otro formato. Conservá una copia de datos y usá otro --data-dir para una revisión nueva.')
        self.recent = Catalog(self.document['recent']) if self.document.get('recent') is not None else None
        if self.recent:
            self.check_recent(self.recent)
        decisions = self.document.get('decisions')
        if not isinstance(decisions, dict) or not isinstance(self.document.get('revision'), int):
            raise ReviewError('El archivo de decisiones tiene una estructura inválida.')
        if not isinstance(self.document.get('dismissed_checks', {}), dict) or any(not isinstance(item, dict) for item in self.document.get('dismissed_checks', {}).values()):
            raise ReviewError('El registro de controles descartados es inválido.')
        for entry_key, decision in decisions.items():
            self.check_decision(entry_key, decision)

    def verify_inputs(self):
        # Las bases ya se parsearon al abrir la revisión. Verificar su contenido
        # mantiene la integridad sin volver a parsear miles de claves.
        with self.original_path.open(encoding='utf-8', newline='') as stream:
            original = stream.read()
        with self.proposed_path.open(encoding='utf-8', newline='') as stream:
            proposed = stream.read()
        if digest(original + '\x00' + proposed) != self.signature:
            raise ReviewError('Los PO de entrada cambiaron mientras la aplicación estaba abierta. Reiniciá la aplicación para revisar los archivos.')

    def check_revision(self, revision):
        if revision != self.document['revision']:
            raise ReviewError('El progreso cambió en otra pestaña. Recargá antes de continuar.')

    def commit(self, doc):
        doc['revision'] = self.document['revision'] + 1
        atomic_write(self.state_path, json.dumps(doc, ensure_ascii=False, indent=2) + '\n')
        self.document = doc

    def check_decision(self, entry_key, decision):
        if entry_key not in self.changed or not isinstance(decision, dict):
            raise ReviewError('La decisión no corresponde a una entrada modificada.')
        status = decision.get('status')
        if status not in ('pending', 'accepted', 'rejected', 'edited'):
            raise ReviewError('Estado de decisión inválido.')
        values = decision.get('values')
        if status == 'edited':
            if not isinstance(values, list) or len(values) != len(forms(self.original.by_key[entry_key])) or not all(isinstance(v, str) for v in values):
                raise ReviewError('La edición debe incluir todas las formas plurales como textos separados.')
        draft = decision.get('draft')
        if draft is not None and (not isinstance(draft, list) or len(draft) != len(forms(self.original.by_key[entry_key])) or not all(isinstance(v, str) for v in draft)):
            raise ReviewError('El borrador debe incluir todas las formas plurales.')
        if decision.get('resolution') not in (None, 'current', 'chosen'):
            raise ReviewError('Resolución de conflicto inválida.')

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
            raise ReviewError('El PO reciente tiene entradas agregadas, eliminadas o identificadores diferentes. Se necesita una nueva comparación; no se puede combinar automáticamente.')
        if catalog.nplurals != self.original.nplurals:
            raise ReviewError('El PO reciente tiene una configuración de plurales diferente.')
        if catalog.po.metadata.get('Language') != self.original.po.metadata.get('Language'):
            raise ReviewError('El PO reciente corresponde a otro idioma.')

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
            raise ReviewError('La decisión de validación es inválida.')
        _, errors, warnings = self.prepare_export(render=False)
        issue = next((item for item in errors + warnings + self.dismissed_issues if item['id'] == issue_id), None)
        if not issue or not issue['dismissible']:
            raise ReviewError('Este control no se puede descartar o ya no corresponde al texto actual.')
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
            errors.append(self.issue('no_changes', None, 'No hay cambios aprobados para descargar.'))
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
                errors.append(self.issue('conflict', entry_key, f'{label}: conflicto con la descarga reciente sin resolver.'))
                continue
            if decision.get('resolution') == 'current' and self.recent and decision.get('recent_hash') == digest(self.recent.text):
                continue
            if decision['status'] not in ('accepted', 'edited'):
                continue
            values = self.chosen(entry_key, decision)
            old_values = forms(self.original.by_key[entry_key])
            if len(values) != len(forms(base.by_key[entry_key])):
                errors.append(self.issue('plural_count', entry_key, f'{label}: cantidad de plurales incorrecta.'))
                continue
            for i, (old, value) in enumerate(zip(old_values, values)):
                prefix = f'{label}, forma {i}'
                if not value.strip():
                    errors.append(self.issue('empty', entry_key, f'{prefix}: traducción vacía.', i))
                if any(ord(char) < 32 and char not in '\n\r\t' for char in value):
                    errors.append(self.issue('characters', entry_key, f'{prefix}: caracteres de control no válidos.', i))
                source = self.form_sources[entry_key][i] if entry_key in self.form_sources else (self.original.by_key[entry_key].msgid if i == 0 else self.original.by_key[entry_key].msgid_plural)
                reference = source if self.source_checks or not old else old
                pattern = FORMAT_PLACEHOLDER if any(flag in self.original.by_key[entry_key].flags for flag in ('c-format', 'python-format', 'objc-format')) else PLACEHOLDER
                if tokens(pattern, reference) != tokens(pattern, value):
                    errors.append(self.issue('placeholders', entry_key, f'{prefix}: placeholders distintos de la referencia de traducción.', i, reference, value, True))
                if tokens(TECHNICAL, old) != tokens(TECHNICAL, value):
                    warnings.append(self.issue('technical', entry_key, f'{prefix}: cambiaron enlaces, etiquetas o referencias técnicas; comprobá que sea intencional.', i, old, value, True))
                source = self.form_sources[entry_key][i] if entry_key in self.form_sources else (self.original.by_key[entry_key].msgid if i == 0 else self.original.by_key[entry_key].msgid_plural)
                if tokens(pattern, reference) == tokens(pattern, value) and tokens(pattern, source) != tokens(pattern, value):
                    warnings.append(self.issue('source_placeholders', entry_key, f'{prefix}: los placeholders no coinciden con el inglés (puede ser un problema previo).', i, source, value, True))
                for term in self.glossary:
                    if not source_matches(term, source):
                        continue
                    target_language = self.original.po.metadata.get('Language', '')
                    for forbidden in variants(term['forbidden'], target_language):
                        if forbidden and re.search(r'(?<!\w)' + re.escape(forbidden) + r'(?!\w)', value, re.I):
                            warnings.append(self.issue('forbidden_term', entry_key, f'{prefix}: término prohibido por el glosario: {forbidden}.', i, source + forbidden, value, True))
                    if term['preferred'] and not contains(value, variants(term['preferred'], target_language)):
                        warnings.append(self.issue('preferred_term', entry_key, f'{prefix}: revisar terminología; se esperaba: {", ".join(term["preferred"])}.', i, source + json.dumps(variants(term['preferred'], target_language)), value, True))
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
            raise ReviewError('Modo de exportación PO inválido.')
        self.check_revision(revision)
        output, errors, warnings = self.prepare_export()
        counts = self.snapshot()['counts']
        if mode == 'changes' and not self.approved_keys() and not errors:
            errors.append(self.issue('no_changes', None, 'No hay cambios aprobados para descargar.'))
        if errors:
            return {'ok': False, 'errors': errors, 'warnings': warnings, 'dismissed': self.dismissed_issues, 'counts': counts}
        if (counts['pending'] and not confirm_pending) or (warnings and not confirm_warnings):
            return {'ok': False, 'errors': [], 'warnings': warnings, 'counts': counts, 'dismissed': self.dismissed_issues, 'confirmation_required': True}
        if mode == 'changes':
            catalog = Catalog(output)
            partial = polib.POFile()
            partial.metadata = copy.deepcopy(catalog.po.metadata)
            partial.header = 'Cambios aprobados para importar y fusionar en Weblate.'
            for entry_key in self.approved_keys():
                partial.append(copy.deepcopy(catalog.by_key[entry_key]))
            output = str(partial)
            checked = Catalog(output)
            if set(checked.by_key) != set(self.approved_keys()):
                raise ReviewError('La exportación parcial alteró las claves aprobadas.')
            for entry_key in checked.by_key:
                if forms(checked.by_key[entry_key]) != forms(catalog.by_key[entry_key]):
                    raise ReviewError('La exportación parcial alteró una traducción aprobada.')
        language = self.original.po.metadata.get('Language', 'es_419').replace('-', '_')
        if not re.fullmatch(r'[A-Za-z0-9_]+', language):
            raise ReviewError('Código de idioma de salida inválido.')
        path = self.data_dir / (('DEMO_' if getattr(self, 'demo', False) else 'SIMULATION_' if getattr(self, 'simulation', False) else '') + language + '_reviewed.po')
        if path.resolve() in {self.original_path.resolve(), self.proposed_path.resolve()}:
            raise ReviewError('La ruta de exportación coincide con un PO de entrada; elegí otro --data-dir.')
        atomic_write(path, output)
        summary = {'ok': True, 'counts': counts, 'warnings': warnings, 'dismissed': self.dismissed_issues, 'mode': mode, 'recent_base': self.recent is not None, 'revision': revision, 'sha256': digest(output)}
        atomic_write(self.data_dir / 'resumen_exportacion.json', json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
        return dict(summary, content=output, filename=path.name)
