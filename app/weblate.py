"""Cliente de solo lectura para Weblate WCA. Nunca envía traducciones."""
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

import polib

from core import ReviewError
from languages import language_code

BASE = 'https://translate.worldcubeassociation.org'


def checked_token(value):
    if any(c.isspace() or ord(c) < 33 or ord(c) > 126 for c in value):
        raise ReviewError('WEBLATE_TOKEN contiene espacios o caracteres no válidos. Revisá .env sin compartir su contenido.')
    return value


def load_token(env_path):
    if 'WEBLATE_TOKEN' in os.environ:
        return checked_token(os.environ['WEBLATE_TOKEN'].strip())
    path = Path(env_path)
    if not path.exists():
        return ''
    # No ejecuta shell ni expande variables del archivo.
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line.startswith('export '):
            line = line[7:].strip()
        name, sep, value = line.partition('=')
        if sep and name.strip() == 'WEBLATE_TOKEN':
            value = value.strip()
            if value[:1] in ('"', "'"):
                if len(value) < 2 or value[-1] != value[0]:
                    raise ReviewError('WEBLATE_TOKEN tiene comillas sin cerrar en .env.')
                value = value[1:-1]
            return checked_token(value)
    return ''


class WeblateHTTPError(ReviewError):
    def __init__(self, code, path, message):
        self.code = code
        super().__init__(f'Weblate HTTP {code} en {path}: {message}')


class RemoteChanged(ReviewError):
    """Las revisiones cambiaron durante una descarga completa."""


class SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.startswith(BASE + '/'):
            raise ReviewError('Weblate intentó redirigir fuera del servidor permitido.')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Client:
    def __init__(self, env_path, component="regulations", language="es_419"):
        self.env_path = Path(env_path)
        self.component = component
        self.language = language_code(language)
        self.token_getter = None
        self.progress = None
        self.stage = 0
        self.stage_message = ""
        self.opener = urllib.request.build_opener(SameOriginRedirect())

    def token(self):
        return self.token_getter() if self.token_getter else load_token(self.env_path)

    def notify(self, stage, message):
        self.stage, self.stage_message = stage, message
        if self.progress:
            self.progress({'stage': stage, 'total': 7, 'message': message})

    def get(self, path):
        if not path.startswith('/api/') or '..' in path:
            raise ReviewError('Ruta Weblate no permitida.')
        token = self.token()
        headers = {'User-Agent': 'wca-agentic-translation/1.0'}
        if token:
            headers['Authorization'] = 'Token ' + token
        for attempt in range(3):
            req = urllib.request.Request(BASE + path, headers=headers, method='GET')
            try:
                with self.opener.open(req, timeout=35) as response:
                    raw = response.read(12 * 1024 * 1024 + 1)
                    if len(raw) > 12 * 1024 * 1024:
                        raise ReviewError('La descarga supera el máximo de 12 MB.')
                    return raw.decode('utf-8-sig')
            except urllib.error.HTTPError as exc:
                code = exc.code
                retry_after = exc.headers.get('Retry-After', '') if exc.headers else ''
                exc.close()
                if code in (429, 502, 503, 504) and attempt < 2:
                    try:
                        delay = max(0, float(retry_after)) if retry_after else 2 ** attempt
                    except ValueError:
                        try:
                            delay = max(0, (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds())
                        except (ValueError, TypeError, OverflowError):
                            delay = 2 ** attempt
                    if delay > 10:
                        raise ReviewError(f'Weblate pide esperar {int(delay) + 1} segundos. Volvé a sincronizar después.') from None
                    self.notify(self.stage, f'{self.stage_message} · Reintento {attempt + 1}/2 en {delay:g} s')
                    time.sleep(delay)
                    continue
                messages = {
                    401: 'Token ausente o inválido. Configurá WEBLATE_TOKEN en .env.',
                    402: 'El servidor exige una condición adicional de acceso. Revisá el acceso en Weblate; el token no garantiza resolverlo.',
                    403: 'No hay permiso para descargar este recurso. Configurá el token en .env y verificá sus permisos.',
                    404: 'No se encontró el componente o idioma en Weblate.',
                    429: 'Se alcanzó el límite de solicitudes. Esperá antes de sincronizar otra vez.',
                }
                raise WeblateHTTPError(code, path, messages.get(code, 'No se pudo descargar el recurso.')) from None
            except (urllib.error.URLError, TimeoutError, OSError):
                raise ReviewError('No se pudo conectar con Weblate. Revisá la conexión y volvé a sincronizar.') from None
            except UnicodeError:
                raise ReviewError('Weblate devolvió un archivo que no es UTF-8.') from None

    def translations(self, component='locales'):
        if component not in ('locales', 'regulations', 'payload'):
            raise ReviewError('Componente inválido.')
        path = f'/api/components/wca/{component}/translations/?page_size=500'
        seen, result = set(), []
        while path:
            if path in seen:
                raise ReviewError('Paginación circular de idiomas.')
            seen.add(path)
            page = json.loads(self.get(path))
            for item in page['results']:
                lang = item['language']
                if lang['code'] == 'en':
                    continue
                result.append({'code': lang['code'], 'name': lang['name'], 'direction': lang.get('direction', 'ltr'), 'total': item.get('total', 0), 'translated': item.get('translated', 0)})
            next_url = page.get('next')
            if next_url and not next_url.startswith(BASE + '/api/'):
                raise ReviewError('URL de idiomas fuera del servidor permitido.')
            path = next_url[len(BASE):] if next_url else None
        return sorted(result, key=lambda item: item['name'].casefold())

    def project_status(self, language):
        """Contrasta el resumen global con los componentes admitidos, incluso privados."""
        code = language_code(language)
        path = '/api/projects/wca/languages/'
        seen, overall = set(), None
        while path:
            if path in seen:
                raise ReviewError('Paginación circular de estadísticas del proyecto.')
            seen.add(path)
            page = json.loads(self.get(path))
            items = page if isinstance(page, list) else page['results']
            overall = next((item for item in items if language_code(item.get('code', '')) == code), None)
            if overall is not None:
                break
            next_url = page.get('next') if isinstance(page, dict) else None
            if next_url and (not isinstance(next_url, str) or not next_url.startswith(BASE + '/api/')):
                raise ReviewError('URL de estadísticas fuera del servidor permitido.')
            path = next_url[len(BASE):] if next_url else None
        if overall is None:
            raise ReviewError('No se encontró el idioma en las estadísticas del proyecto.')

        def counts(item):
            total, translated = item.get('total'), item.get('translated')
            if type(total) is not int or type(translated) is not int or not 0 <= translated <= total:
                raise ReviewError('Weblate devolvió estadísticas inválidas.')
            return total, total - translated

        total, pending = counts(overall)
        supported_pending = 0
        for component in ('locales', 'regulations', 'payload'):
            try:
                item = json.loads(self.get(f'/api/translations/wca/{component}/{code}/'))
            except WeblateHTTPError as exc:
                if exc.code not in (403, 404):
                    raise
                # Sin estadísticas de todos los componentes no se atribuyen pendientes.
                return {'language': code, 'total': total, 'pending': pending, 'outside_supported': None}
            supported_pending += counts(item)[1]
        return {'language': code, 'total': total, 'pending': pending,
                'outside_supported': max(0, pending - supported_pending)}

    def metadata(self, language):
        try:
            result = json.loads(self.get(f'/api/translations/wca/{self.component}/{language}/'))
            if not isinstance(result, dict) or not result.get('revision'):
                raise ValueError()
            return result
        except ReviewError:
            raise
        except (ValueError, TypeError):
            raise ReviewError('Weblate devolvió metadatos de traducción inválidos.') from None

    def units(self, language='en'):
        # La fuente puede ser de solo lectura y no permitir exportar su archivo.
        # Esta ruta usa las unidades fuente actuales, también para claves aún ausentes en es_419.
        path = f'/api/translations/wca/{self.component}/{language}/units/?page_size=500'
        seen = set()
        entries = []
        while path:
            if path in seen:
                raise ReviewError('Weblate devolvió paginación circular de unidades.')
            seen.add(path)
            try:
                page = json.loads(self.get(path))
                entries.extend(page['results'])
                self.notify(2, f'Descargando unidades {language}: {len(entries)} / {page.get("count", "?")}')
                next_url = page.get('next')
            except ReviewError:
                raise
            except (ValueError, TypeError, KeyError):
                raise ReviewError('Weblate devolvió unidades de traducción inválidas.') from None
            if next_url and (not isinstance(next_url, str) or not next_url.startswith(BASE + '/api/')):
                raise ReviewError('Weblate devolvió una URL de paginación fuera del servidor permitido.')
            path = next_url[len(BASE):] if next_url else None
        if not entries:
            raise ReviewError('La API no devolvió unidades de traducción.')
        return entries

    def english_from_units(self):
        entries = self.units()
        po = polib.POFile()
        po.header = 'Referencia inglesa reconstruida desde las unidades fuente de Weblate WCA.'
        po.metadata = {'Language': 'en', 'Content-Type': 'text/plain; charset=UTF-8', 'Plural-Forms': 'nplurals=2; plural=n != 1;'}
        for unit in entries:
            source = unit.get('source')
            if not isinstance(source, list) or not 1 <= len(source) <= 2 or not all(isinstance(value, str) for value in source):
                raise ReviewError('Una unidad inglesa tiene una estructura fuente no admitida.')
            entry = polib.POEntry(msgctxt=unit.get('context') or None, msgid=source[0], comment=unit.get('note') or '')
            location = unit.get('location') or ''
            for reference in location.split():
                filename, sep, line = reference.rpartition(':')
                entry.occurrences.append((filename, line) if sep and line.isdigit() else (reference, ''))
            entry.flags = [flag.strip() for flag in (unit.get('flags') or '').split(',') if flag.strip().endswith('-format')]
            if len(source) == 2:
                entry.msgid_plural = source[1]
                entry.msgstr_plural = {0: source[0], 1: source[1]}
            else:
                entry.msgstr = source[0]
            po.append(entry)
        return str(po)

    def download(self):
        for attempt in range(2):
            try:
                return self._download_once()
            except RemoteChanged:
                if attempt:
                    raise ReviewError('Weblate siguió cambiando durante las dos descargas. No se actualizó la sesión local; esperá un momento y sincronizá otra vez.') from None
                self.notify(1, 'Weblate cambió durante la descarga. Reintentando con una base nueva (2/2)')

    def _download_once(self):
        self.notify(1, 'Consultando el estado de inglés y el idioma seleccionado')
        target_language = self.language
        before = {lang: self.metadata(lang) for lang in ('en', target_language)}
        warnings = []
        native = None
        mapping = None
        if self.component == 'locales':
            from locales import convert_units, yaml_from_units
            self.notify(2, 'Descargando el YAML de Locales y sus unidades')
            try:
                native = self.get(f'/api/translations/wca/locales/{target_language}/file/')
            except WeblateHTTPError as exc:
                if exc.code != 403:
                    raise
                warnings.append('El archivo YAML no permite descarga. La base se reconstruyó con las unidades accesibles de Weblate: solo incluye claves localizables, sin comentarios ni otros datos del archivo original. Importá el resultado en Weblate; no reemplaces el archivo del repositorio con él.')
            units = self.units(target_language)
            if native is None:
                native = yaml_from_units(units, target_language, before[target_language].get('filename', ''), before[target_language].get('language', {}).get('aliases', []))
            if before['en'].get('total') is not None and len(units) != before['en']['total']:
                raise ReviewError('La fuente inglesa y Locales tienen distinta cantidad de unidades. Volvé a sincronizar cuando Weblate termine de actualizar el componente.')
            self.notify(3, 'Validando el YAML, las claves y las formas plurales')
            english, target, mapping = convert_units(units, native, target_language, before[target_language].get('language', {}).get('aliases', []))
            files = {'en': english, target_language: target}
        elif self.component == 'payload':
            from payload import convert_units as convert_payload, json_load as payload_json_load
            self.notify(2, 'Descargando el JSON de Payload CMS y sus unidades')
            native_json = self.get(f'/api/translations/wca/payload/{target_language}/file/')
            units = self.units(target_language)
            if before['en'].get('total') is not None and len(units) != before['en']['total']:
                raise ReviewError('La fuente inglesa y Payload tienen distinta cantidad de unidades. Volvé a sincronizar.')
            english, target, json_mapping = convert_payload(units, native_json, target_language)
            extra_count = len(set(payload_json_load(native_json)) - set(json_mapping.values()))
            if extra_count:
                warnings.append(f'Payload: el JSON contiene {extra_count} claves que no aparecen en las unidades actuales de la API. Se conservan en la base original y en la exportación completa; no se proponen ni se incluyen en la descarga de cambios aprobados.')
            files = {'en': english, target_language: target}
        else:
            self.notify(2, 'Descargando los archivos de Regulations')
            files = {target_language: self.get(f'/api/translations/wca/regulations/{target_language}/file/')}
        try:
            if self.component == 'regulations':
                files['en'] = self.get('/api/translations/wca/regulations/en/file/')
        except WeblateHTTPError as exc:
            if exc.code != 403:
                raise
            files['en'] = self.english_from_units()
            warnings.append('El archivo inglés no permite descarga. Se obtuvo la referencia inglesa desde las unidades fuente actuales de Weblate; el PO del idioma seleccionado se descargó sin modificaciones.')
        self.notify(4, 'Comprobando que la base no haya cambiado durante la descarga')
        after = {lang: self.metadata(lang) for lang in before}
        if any(before[lang]['revision'] != after[lang]['revision'] for lang in before):
            raise RemoteChanged('Weblate cambió durante la descarga.')
        self.notify(5, 'Descargando el glosario (opcional)')
        try:
            glossary = self.get(f'/api/translations/wca/glossary/{target_language}/file/')
        except ReviewError as exc:
            glossary = None
            warnings.append('Glosario no descargado: ' + str(exc))
        result = {'english': files['en'], 'target': files[target_language], 'glossary': glossary, 'metadata': after, 'warnings': warnings, 'native_yaml': native, 'yaml_mapping': mapping}
        if self.component == 'payload':
            result.update(native_json=native_json, json_mapping=json_mapping)
        return result
