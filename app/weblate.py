"""Read-only WCA Weblate client. Never sends translations."""
import json
from pathlib import Path
import time
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

import polib

from core import ReviewError
from configuration import checked_token, env_value
from languages import language_code

BASE = 'https://translate.worldcubeassociation.org'


def load_token(env_path):
    return checked_token(env_value('WEBLATE_TOKEN', env_path))


class WeblateHTTPError(ReviewError):
    def __init__(self, code, path, message):
        self.code = code
        super().__init__(f'Weblate HTTP {code} at {path}: {message}')


class RemoteChanged(ReviewError):
    """Revisions changed during a complete download."""


class SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.startswith(BASE + '/'):
            raise ReviewError('Weblate tried to redirect outside the allowed server.')
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
            raise ReviewError('Weblate path not allowed.')
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
                        raise ReviewError('The download exceeds the 12 MB limit.')
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
                        raise ReviewError(f'Weblate asks you to wait {int(delay) + 1} seconds. Sync again later.') from None
                    self.notify(self.stage, f'{self.stage_message} · Retry {attempt + 1}/2 in {delay:g} s')
                    time.sleep(delay)
                    continue
                messages = {
                    401: 'Missing or invalid token. Configure WEBLATE_TOKEN in .env.',
                    402: 'The server requires an additional access condition. Check Weblate access; the token alone may not resolve it.',
                    403: 'No permission to download this resource. Configure the token in .env and check its permissions.',
                    404: 'Component or language not found in Weblate.',
                    429: 'Request limit reached. Wait before syncing again.',
                }
                raise WeblateHTTPError(code, path, messages.get(code, 'Could not download the resource.')) from None
            except (urllib.error.URLError, TimeoutError, OSError):
                raise ReviewError('Could not connect to Weblate. Check your connection and sync again.') from None
            except UnicodeError:
                raise ReviewError('Weblate returned a non-UTF-8 file.') from None

    def translations(self, component='locales'):
        if component not in ('locales', 'regulations', 'payload'):
            raise ReviewError('Invalid component.')
        path = f'/api/components/wca/{component}/translations/?page_size=500'
        seen, result = set(), []
        while path:
            if path in seen:
                raise ReviewError('Circular language pagination.')
            seen.add(path)
            page = json.loads(self.get(path))
            for item in page['results']:
                lang = item['language']
                if lang['code'] == 'en':
                    continue
                result.append({'code': lang['code'], 'name': lang['name'], 'direction': lang.get('direction', 'ltr'), 'total': item.get('total', 0), 'translated': item.get('translated', 0)})
            next_url = page.get('next')
            if next_url and not next_url.startswith(BASE + '/api/'):
                raise ReviewError('Language URL outside the allowed server.')
            path = next_url[len(BASE):] if next_url else None
        return sorted(result, key=lambda item: item['name'].casefold())

    def project_status(self, language):
        """Cross-check the global summary against supported components, including private ones."""
        code = language_code(language)
        path = '/api/projects/wca/languages/'
        seen, overall = set(), None
        while path:
            if path in seen:
                raise ReviewError('Circular project statistics pagination.')
            seen.add(path)
            page = json.loads(self.get(path))
            items = page if isinstance(page, list) else page['results']
            overall = next((item for item in items if language_code(item.get('code', '')) == code), None)
            if overall is not None:
                break
            next_url = page.get('next') if isinstance(page, dict) else None
            if next_url and (not isinstance(next_url, str) or not next_url.startswith(BASE + '/api/')):
                raise ReviewError('Statistics URL outside the allowed server.')
            path = next_url[len(BASE):] if next_url else None
        if overall is None:
            raise ReviewError('Language not found in project statistics.')

        def counts(item):
            total, translated = item.get('total'), item.get('translated')
            if type(total) is not int or type(translated) is not int or not 0 <= translated <= total:
                raise ReviewError('Weblate returned invalid statistics.')
            return total, total - translated

        total, pending = counts(overall)
        supported_pending = 0
        for component in ('locales', 'regulations', 'payload'):
            try:
                item = json.loads(self.get(f'/api/translations/wca/{component}/{code}/'))
            except WeblateHTTPError as exc:
                if exc.code not in (403, 404):
                    raise
                # Do not attribute pending counts without statistics for every component.
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
            raise ReviewError('Weblate returned invalid translation metadata.') from None

    def units(self, language='en'):
        # The source may be read-only and disallow file export.
        # This route uses current source units, including keys still missing in es_419.
        path = f'/api/translations/wca/{self.component}/{language}/units/?page_size=500'
        seen = set()
        entries = []
        while path:
            if path in seen:
                raise ReviewError('Weblate returned circular unit pagination.')
            seen.add(path)
            try:
                page = json.loads(self.get(path))
                entries.extend(page['results'])
                self.notify(2, f"Downloading units {language}: {len(entries)} / {page.get('count', '?')}")
                next_url = page.get('next')
            except ReviewError:
                raise
            except (ValueError, TypeError, KeyError):
                raise ReviewError('Weblate returned invalid translation units.') from None
            if next_url and (not isinstance(next_url, str) or not next_url.startswith(BASE + '/api/')):
                raise ReviewError('Weblate returned a pagination URL outside the allowed server.')
            path = next_url[len(BASE):] if next_url else None
        if not entries:
            raise ReviewError('The API returned no translation units.')
        return entries

    def english_from_units(self):
        entries = self.units()
        po = polib.POFile()
        po.header = 'English reference reconstructed from WCA Weblate source units.'
        po.metadata = {'Language': 'en', 'Content-Type': 'text/plain; charset=UTF-8', 'Plural-Forms': 'nplurals=2; plural=n != 1;'}
        for unit in entries:
            source = unit.get('source')
            if not isinstance(source, list) or not 1 <= len(source) <= 2 or not all(isinstance(value, str) for value in source):
                raise ReviewError('An English unit has an unsupported source structure.')
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
                    raise ReviewError('Weblate kept changing during both downloads. The local session was not updated; wait a moment and sync again.') from None
                self.notify(1, 'Weblate changed during download. Retrying with a new base (2/2)')

    def _download_once(self):
        self.notify(1, 'Checking English and selected language status')
        target_language = self.language
        before = {lang: self.metadata(lang) for lang in ('en', target_language)}
        warnings = []
        native = None
        mapping = None
        if self.component == 'locales':
            from locales import convert_units, yaml_from_units
            self.notify(2, 'Downloading Locales YAML and units')
            try:
                native = self.get(f'/api/translations/wca/locales/{target_language}/file/')
            except WeblateHTTPError as exc:
                if exc.code != 403:
                    raise
                warnings.append('The YAML file cannot be downloaded. The base was reconstructed from accessible Weblate units: it includes only translatable keys, without comments or other original file data. Import the result into Weblate; do not replace the repository file with it.')
            units = self.units(target_language)
            if native is None:
                native = yaml_from_units(units, target_language, before[target_language].get('filename', ''), before[target_language].get('language', {}).get('aliases', []))
            if before['en'].get('total') is not None and len(units) != before['en']['total']:
                raise ReviewError('The English source and Locales have different unit counts. Sync again when Weblate finishes updating the component.')
            self.notify(3, 'Validating YAML, keys and plural forms')
            english, target, mapping = convert_units(units, native, target_language, before[target_language].get('language', {}).get('aliases', []))
            files = {'en': english, target_language: target}
        elif self.component == 'payload':
            from payload import convert_units as convert_payload, json_load as payload_json_load
            self.notify(2, 'Downloading Payload CMS JSON and units')
            native_json = self.get(f'/api/translations/wca/payload/{target_language}/file/')
            units = self.units(target_language)
            if before['en'].get('total') is not None and len(units) != before['en']['total']:
                raise ReviewError('The English source and Payload have different unit counts. Sync again.')
            english, target, json_mapping = convert_payload(units, native_json, target_language)
            extra_count = len(set(payload_json_load(native_json)) - set(json_mapping.values()))
            if extra_count:
                warnings.append(f'Payload: the JSON contains {extra_count} keys missing from current API units. They are preserved in the original base and full export; they are not proposed or included in the approved changes download.')
            files = {'en': english, target_language: target}
        else:
            self.notify(2, 'Downloading Regulations files')
            files = {target_language: self.get(f'/api/translations/wca/regulations/{target_language}/file/')}
        try:
            if self.component == 'regulations':
                files['en'] = self.get('/api/translations/wca/regulations/en/file/')
        except WeblateHTTPError as exc:
            if exc.code != 403:
                raise
            files['en'] = self.english_from_units()
            warnings.append('The English file cannot be downloaded. The English reference was built from current Weblate source units; the selected language PO was downloaded unchanged.')
        self.notify(4, 'Checking that the base did not change during download')
        after = {lang: self.metadata(lang) for lang in before}
        if any(before[lang]['revision'] != after[lang]['revision'] for lang in before):
            raise RemoteChanged('Weblate changed during download.')
        self.notify(5, 'Downloading the glossary (optional)')
        try:
            glossary = self.get(f'/api/translations/wca/glossary/{target_language}/file/')
        except ReviewError as exc:
            glossary = None
            warnings.append('Glossary not downloaded: ' + str(exc))
        result = {'english': files['en'], 'target': files[target_language], 'glossary': glossary, 'metadata': after, 'warnings': warnings, 'native_yaml': native, 'yaml_mapping': mapping}
        if self.component == 'payload':
            result.update(native_json=native_json, json_mapping=json_mapping)
        return result
