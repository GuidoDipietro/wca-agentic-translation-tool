"""Adaptación Ruby YAML ↔ revisión interna PO. La salida conserva el YAML nativo."""
import copy
import io
import json
from pathlib import Path

import polib
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from languages import language_code, plural_names
from core import Catalog, Review, ReviewError, atomic_write, digest, forms, key


def detached(value):
    if isinstance(value, dict):
        return {k: detached(v) for k, v in value.items()}
    if isinstance(value, list):
        return [detached(v) for v in value]
    return copy.deepcopy(value)


def yaml_load(text, expected_language='es_419', aliases=()):
    parser = YAML(typ='rt')
    parser.preserve_quotes = True
    parser.allow_duplicate_keys = False
    try:
        data = parser.load(text)
        if not isinstance(data, dict) or len(data) != 1:
            raise ValueError('Se requiere una raíz de idioma')
        language = next(iter(data))
        allowed = {language_code(expected_language).lower()} | {str(alias).replace('-', '_').lower() for alias in aliases}
        if language_code(str(language)).lower() not in allowed or not isinstance(data[language], dict):
            raise ValueError('Idioma incorrecto')
        return parser, data, language
    except Exception as exc:
        raise ReviewError(f'YAML de {expected_language} inválido, con claves duplicadas o idioma incorrecto.') from None


def lookup(tree, path):
    value = tree
    for part in path:
        if isinstance(value, list):
            if not part.isdigit() or int(part) >= len(value):
                return None
            value = value[int(part)]
        elif isinstance(value, dict):
            value = value.get(part, value.get(int(part)) if part.isdigit() else None)
        else:
            return None
    return value


def assign(tree, path, value):
    for i, part in enumerate(path):
        last = i == len(path) - 1
        if isinstance(tree, list):
            if not part.isdigit() or int(part) >= len(tree):
                raise ReviewError('No se puede añadir una traducción a un índice YAML inexistente.')
            if last:
                tree[int(part)] = value
            else:
                tree = tree[int(part)]
        elif isinstance(tree, dict):
            part = int(part) if part.isdigit() and int(part) in tree else part
            if last:
                tree[part] = value
            else:
                if part not in tree:
                    tree[part] = CommentedMap()
                tree = tree[part]
        else:
            raise ReviewError('La ruta de la traducción colisiona con un valor YAML existente.')


def yaml_values(tree, item):
    value = lookup(tree, item['path'])
    if item['plural_forms']:
        if value is not None and not isinstance(value, dict):
            raise ReviewError('Una entrada plural YAML no es un mapa.')
        values = [(value or {}).get(name, '') for name in item['plural_forms']]
    else:
        values = ['' if value is None else value]
    if not all(isinstance(v, str) for v in values):
        raise ReviewError('Una traducción YAML contiene valores que no son texto.')
    return list(map(str, values))


def yaml_from_units(units, language, filename='', aliases=()):
    """Archivo de importación por contextos; no sustituye el archivo del repositorio.

    Las rutas numéricas siguen siendo claves: la API no informa si eran listas.
    Weblate fusiona las traducciones por contexto al importar este archivo.
    """
    code = language_code(language)
    root = Path(filename).stem if filename else code
    allowed = {code.lower()} | {str(alias).replace('-', '_').lower() for alias in aliases}
    if language_code(root).lower() not in allowed:
        raise ReviewError('El nombre del archivo no corresponde al idioma seleccionado.')
    number = max((len(u.get('target', [])) for u in units if len(u.get('source', [])) > 1), default=None)
    names = plural_names(code, number=number)
    tree, seen = CommentedMap(), set()
    for unit in units:
        context, source, values = unit.get('context'), unit.get('source'), unit.get('target')
        if not isinstance(context, str) or not context or not isinstance(source, list) or len(source) not in (1, 2):
            raise ReviewError('Una unidad no permite reconstruir su contexto YAML.')
        path = tuple(context.split('->'))
        if any(not part for part in path) or path in seen or any(path[:i] in seen for i in range(1, len(path))) or any(old[:len(path)] == path for old in seen):
            raise ReviewError('Hay contextos duplicados o incompatibles en las unidades YAML.')
        seen.add(path)
        if not isinstance(values, list) or len(values) != (len(names) if len(source) == 2 else 1) or not all(isinstance(v, str) for v in values):
            raise ReviewError('Las unidades no permiten identificar todas las formas del idioma.')
        value = CommentedMap(zip(names, values)) if len(source) == 2 else values[0]
        assign(tree, path, value)
    data = CommentedMap({root: tree})
    parser = YAML(typ='rt')
    parser.width = 4096
    stream = io.StringIO()
    parser.dump(data, stream)
    native = '# Reconstruido desde unidades de Weblate: importar como traducciones; no reemplazar el archivo del repositorio.\n' + stream.getvalue()
    yaml_load(native, code, aliases)
    return native


def convert_units(units, native, language='es_419', aliases=()):
    target_language = language_code(language)
    _, data, language = yaml_load(native, target_language, aliases)
    tree = data[language]
    english, target = polib.POFile(), polib.POFile()
    number = max((len(u.get('target', [])) for u in units if len(u.get('source', [])) > 1), default=None)
    observed = set()
    for unit in units:
        if len(unit.get('source', [])) > 1:
            value = lookup(tree, (unit.get('context') or '').split('->'))
            if isinstance(value, dict):
                observed.update(str(name) for name in value)
    names = plural_names(target_language, observed, number)
    number = len(names)
    for po in (english, target):
        po.metadata = {'Language': target_language if po is target else 'en', 'Content-Type': 'text/plain; charset=UTF-8', 'Plural-Forms': f'nplurals={number}; plural=0;'}
    mapping = {}
    for unit in units:
        source, translations = unit.get('source'), unit.get('target')
        context = unit.get('context')
        if not isinstance(context, str) or not context or not isinstance(source, list) or not 1 <= len(source) <= 2 or not all(isinstance(v, str) for v in source):
            raise ReviewError('Una unidad Locales tiene contexto o fuente no admitidos.')
        entry = polib.POEntry(msgctxt=context, msgid=source[0], comment=unit.get('note') or '')
        plural = len(source) == 2
        if plural:
            entry.msgid_plural = source[1]
            entry.msgstr_plural = {i: source[0] if name == 'one' else source[1] for i, name in enumerate(names)}
        else:
            entry.msgstr = source[0]
        entry_key = key(entry)
        if entry_key in mapping:
            raise ReviewError('Claves Locales duplicadas en la API.')
        item = {'path': context.split('->'), 'plural_forms': names if plural else [], 'source_forms': [source[0] if name == 'one' else source[-1] for name in names] if plural else source}
        values = yaml_values(tree, item)
        if translations != values:
            raise ReviewError(f'La API y el YAML no coinciden para {context}. Volvé a sincronizar.')
        mapping[entry_key] = item
        english.append(entry)
        translated = copy.deepcopy(entry)
        if plural:
            translated.msgstr_plural = dict(enumerate(values))
        else:
            translated.msgstr = values[0]
        if unit.get('fuzzy') or unit.get('state') == 10:
            translated.flags.append('fuzzy')
        target.append(translated)
    return str(english), str(target), mapping


class LocalesReview(Review):
    def __init__(self, *args, native_path, mapping, language='es_419', aliases=(), **kwargs):
        self.language = language_code(language)
        self.aliases = aliases
        self.native_path = Path(native_path)
        self.native_text = self.native_path.read_text(encoding='utf-8')
        self.mapping = mapping
        yaml_load(self.native_text, self.language, self.aliases)
        kwargs['form_sources'] = {k: item['source_forms'] for k, item in mapping.items() if item.get('source_forms')}
        super().__init__(*args, **kwargs)

    def set_recent(self, text, revision):
        self.check_revision(revision)
        catalog = None
        if text is not None:
            _, data, lang = yaml_load(text, self.language, self.aliases)
            replacements = {k: yaml_values(data[lang], self.mapping[k]) for k in self.original.by_key}
            catalog = Catalog(self.original.render(replacements))
        doc = copy.deepcopy(self.document)
        doc['recent'] = catalog.text if catalog else None
        doc['native_recent'] = text
        self.commit(doc)
        self.recent = catalog

    def export(self, revision, confirm_pending=False, confirm_warnings=False, mode='full'):
        if mode not in ('full', 'suggestions', 'changes'):
            raise ReviewError('Modo de exportación YAML inválido.')
        self.check_revision(revision)
        output, errors, warnings = self.prepare_export()
        counts = self.snapshot()['counts']
        if mode == 'changes' and not self.approved_keys() and not errors:
            errors.append(self.issue('no_changes', None, 'No hay cambios aprobados para descargar.'))
        if errors:
            return {'ok': False, 'errors': errors, 'warnings': warnings, 'dismissed': self.dismissed_issues, 'counts': counts}
        if (counts['pending'] and not confirm_pending) or (warnings and not confirm_warnings):
            return {'ok': False, 'errors': [], 'warnings': warnings, 'counts': counts, 'dismissed': self.dismissed_issues, 'confirmation_required': True}
        if self.native_path.read_text(encoding='utf-8') != self.native_text:
            raise ReviewError('El YAML de base cambió mientras revisabas.')
        base = self.document.get('native_recent') or self.native_text
        parser, data, lang = yaml_load(base, self.language, self.aliases)
        before = copy.deepcopy(data)
        catalog = Catalog(output)
        changed = False
        for entry_key in self.changed:
            values = forms(catalog.by_key[entry_key])
            item = self.mapping[entry_key]
            current = yaml_values(data[lang], item)
            if values == current:
                continue
            changed = True
            if item['plural_forms']:
                for name, value in zip(item['plural_forms'], values):
                    assign(data[lang], item['path'] + [name], value)
            else:
                assign(data[lang], item['path'], values[0])
        if changed:
            stream = io.StringIO()
            parser.width = 4096
            parser.dump(data, stream)
            native = stream.getvalue()
            _, parsed, _ = yaml_load(native, self.language, self.aliases)
            if parsed != data:
                raise ReviewError('La exportación YAML alteró valores al serializar.')
            # Comprobar todo el árbol contra una copia con solo los cambios autorizados.
            expected = detached(before)
            for entry_key in self.changed:
                item = self.mapping[entry_key]
                values = forms(catalog.by_key[entry_key])
                if values == yaml_values(before[lang], item):
                    continue
                paths = [item['path'] + [name] for name in item['plural_forms']] if item['plural_forms'] else [item['path']]
                for path, value in zip(paths, values):
                    assign(expected[lang], path, value)
            if parsed != expected:
                raise ReviewError('La exportación YAML alteró otras claves (por ejemplo por alias).')
        else:
            native = base
        if mode in ('suggestions', 'changes'):
            sparse = CommentedMap({lang: CommentedMap()})
            for entry_key in self.approved_keys():
                item = self.mapping[entry_key]
                values = forms(catalog.by_key[entry_key])
                paths = [item['path'] + [name] for name in item['plural_forms']] if item['plural_forms'] else [item['path']]
                for path, value in zip(paths, values):
                    # Las claves numéricas de mapas no son índices de listas. Si sí
                    # es una lista, conservarla completa evita cambiar posiciones.
                    tree = data[lang]
                    for index, part in enumerate(path):
                        if isinstance(tree, list):
                            assign(sparse[lang], path[:index], copy.deepcopy(tree))
                            break
                        tree = lookup(tree, [part])
                    else:
                        assign(sparse[lang], path, value)
            stream = io.StringIO()
            parser.dump(sparse, stream)
            native = stream.getvalue()
            _, parsed, _ = yaml_load(native, self.language, self.aliases)
            for entry_key in self.approved_keys():
                if yaml_values(parsed[lang], self.mapping[entry_key]) != forms(catalog.by_key[entry_key]):
                    raise ReviewError('La exportación parcial alteró una traducción aprobada.')
        name = f'{lang}_suggestions.yml' if mode == 'suggestions' else f'{lang}_reviewed.yml'
        if getattr(self, 'demo', False):
            name = 'DEMO_' + name
            native = '# DEMO: datos ficticios. No subir a Weblate.\n' + native
        elif getattr(self, 'simulation', False):
            name = 'SIMULATION_' + name
            native = '# SIMULACIÓN: copia de prueba. No subir a Weblate.\n' + native
        path = self.data_dir / name
        atomic_write(path, native)
        summary = {'ok': True, 'counts': counts, 'warnings': warnings, 'component': 'locales', 'mode': mode, 'dismissed': self.dismissed_issues, 'revision': revision, 'sha256': digest(native)}
        atomic_write(self.data_dir / 'resumen_exportacion.json', json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
        return dict(summary, content=native, filename=path.name)
