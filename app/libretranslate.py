"""Proposals from a LibreTranslate server (Argos Translate): a configured address, or the user's own installed `libretranslate` command."""
import atexit
from collections import deque
import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
from urllib.parse import urlsplit
import urllib.request

from core import ReviewError, PLACEHOLDER, FORMAT_PLACEHOLDER, TECHNICAL, tokens
from network import AI_IDLE_TIMEOUT, MAX_PROVIDER_JSON_BYTES
from openai_auth import NoRedirect

# mask() swaps each run of placeholders, tags and links for a sentinel such as XPH1X, a made-up word that LibreTranslate usually copies unchanged, and unmask() puts the originals back.
# Of a Markdown link, TECHNICAL (from core) matches only the `](target)` half. LINK_OPEN adds the opening `[`, because LibreTranslate otherwise drops or moves that bracket and the link breaks.
LINK_OPEN = r'\[(?=[^\]\n]*\]\()'
RUN = re.compile('(?:' + LINK_OPEN + '|' + TECHNICAL.pattern + '|' + PLACEHOLDER.pattern + ')+')
SENTINEL = re.compile(r'( ?)XPH(\d+)X( ?)', re.I)
# Regional languages that have their own LibreTranslate model. Current servers list such a model under the first code and older servers under the second, so resolve() tries both.
REGIONAL = {'pt-br': ('pt-BR', 'pb'), 'zh-hans': ('zh-Hans', 'zh'), 'zh-cn': ('zh-Hans', 'zh'), 'zh-hant': ('zh-Hant', 'zt'), 'zh-tw': ('zh-Hant', 'zt')}
INSTALL_HINT = 'Set a LibreTranslate server address in Connections, or install LibreTranslate to use Argos: pipx install libretranslate'
STARTING = 'Starting the local Argos engine. The first use of a language downloads its model.'
# Seconds to wait for the local LibreTranslate engine to start. The wait is this long because a start can include a model download of several hundred megabytes.
STARTUP_DEADLINE = 900


def request_json(url, body=None, timeout=AI_IDLE_TIMEOUT):
    if not url.startswith(('http://', 'https://')):
        raise ReviewError('The LibreTranslate address must start with http:// or https://.')
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'} if data else {}, method='GET' if data is None else 'POST')
    # Requests to this machine skip any proxy set in the environment (http_proxy). Without the bypass, the proxy would receive them and the local LibreTranslate engine would look unreachable.
    direct = [urllib.request.ProxyHandler({})] if urlsplit(url).hostname in ('127.0.0.1', 'localhost', '::1') else []
    try:
        with urllib.request.build_opener(NoRedirect(), *direct).open(request, timeout=timeout) as response:
            raw = response.read(MAX_PROVIDER_JSON_BYTES + 1)
        if len(raw) > MAX_PROVIDER_JSON_BYTES:
            raise ReviewError('The LibreTranslate response was too large.')
        return json.loads(raw)
    except ReviewError:
        raise
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read(MAX_PROVIDER_JSON_BYTES))
            detail = payload.get('error') if isinstance(payload, dict) else None
        except (ValueError, OSError):
            detail = None
        finally:
            exc.close()
        detail = ' '.join(str(detail or 'no details').split())[-300:]
        raise ReviewError(f'LibreTranslate HTTP {exc.code}: {detail}. No translations were approved.') from None
    except (urllib.error.URLError, OSError, http.client.HTTPException):
        raise ReviewError('Could not reach the LibreTranslate server, or it did not answer in time. Check its address in Connections.') from None
    except ValueError:
        raise ReviewError('The LibreTranslate server did not return valid JSON.') from None


def mask(text):
    runs = []

    def sentinel(match):
        # When a sentinel touches a letter or digit, LibreTranslate takes both for one unknown word and leaves that word untranslated. A space is added on that side, and unmask() removes it again.
        left = match.start() > 0 and text[match.start() - 1].isalnum()
        right = match.end() < len(text) and text[match.end()].isalnum()
        runs.append((match.group(0), left, right))
        return ' ' * left + f'XPH{len(runs)}X' + ' ' * right
    return RUN.sub(sentinel, text), runs


def unmask(text, runs):
    # unmask() returns None unless every sentinel comes back exactly once, because a lost or repeated sentinel means a lost or repeated placeholder. SENTINEL ignores case because LibreTranslate sometimes lowercases a sentinel.
    numbers = [int(number) for _, number, _ in SENTINEL.findall(text)]
    if sorted(numbers) != list(range(1, len(runs) + 1)):
        return None
    # A translation may move placeholders, but the sentinels that stand for tags and link halves must keep their order: a closing tag that comes back before its opening tag passes the check above, which only counts sentinels, and still breaks the markup. A run counts as markup when it holds anything besides placeholders.
    # shortcut: two whole links that swap places are also refused and keep the English text. Compare tag pairs instead if that proves common.
    markup = [number for number in numbers if PLACEHOLDER.sub('', runs[number - 1][0])]
    if markup != sorted(markup):
        return None

    def restore(match):
        run, left, right = runs[int(match.group(2)) - 1]
        return ('' if left else match.group(1)) + run + ('' if right else match.group(3))
    return SENTINEL.sub(restore, text)


def form_sources(item):
    source = item['source']
    if item.get('plural_forms'):
        return [source[0] if name == 'one' else source[-1] for name in item['plural_forms']]
    return [source[min(i, len(source) - 1)] for i in range(item['target_count'])]


def resolve(language, targets):
    by_lower = {code.lower(): code for code in targets}
    hyphenated = language.replace('_', '-').lower()
    for candidate in (hyphenated, *(code.lower() for code in REGIONAL.get(hyphenated, ()))):
        if candidate in by_lower:
            return by_lower[candidate], False
    base = re.split('[_-]', hyphenated)[0]
    # A regional language falls back to the model of its base language, except Chinese: the `zh` model is Simplified Chinese and must not be offered for Traditional Chinese.
    if base != 'zh' and base in by_lower:
        return by_lower[base], True
    raise ReviewError(f'This LibreTranslate server has no English → {language} model.')


def accept(source, raw, runs):
    out = unmask(raw, runs)
    if out is None or not out.strip() or len(out) > 100000 or any(ord(c) < 32 and c not in '\n\r\t' for c in out):
        return None
    out = source[:len(source) - len(source.lstrip())] + out.strip() + source[len(source.rstrip()):]
    # validate_batch (translation.py) checks format-flagged entries with FORMAT_PLACEHOLDER, which also counts prose such as "25% of", and rejects the whole batch on one mismatch. The batch context that propose() receives does not carry an entry's flags, so PLACEHOLDER and FORMAT_PLACEHOLDER are both applied to every entry.
    # shortcut: an unflagged entry with prose such as "25% of" keeps the English text although its translation would be valid. Pass the flags in the batch context if too many strings are affected.
    if any(tokens(pattern, source) != tokens(pattern, out) for pattern in (PLACEHOLDER, FORMAT_PLACEHOLDER)):
        return None
    return out


def engine_code(language):
    hyphenated = language.replace('_', '-')
    return REGIONAL.get(hyphenated.lower(), (hyphenated.split('-')[0],))[0]


def english_targets(url, timeout=AI_IDLE_TIMEOUT):
    languages = request_json(url + '/languages', timeout=timeout)
    english = next((item for item in languages if isinstance(item, dict) and item.get('code') == 'en'), None) if isinstance(languages, list) else None
    targets = english.get('targets') if english else None
    return [code for code in targets if isinstance(code, str)] if isinstance(targets, list) else []


class ManagedEngine:
    """The user's own `libretranslate` command, bound to 127.0.0.1 and stopped when the app exits."""

    def __init__(self, command='libretranslate'):
        self.command = command
        self.process = None
        self.port = None
        self.reader = None
        self.tail = deque(maxlen=20)
        self.confirmed = set()
        self.lock = threading.Lock()
        self.registered = False
        self.closed = False

    @property
    def url(self):
        return f'http://127.0.0.1:{self.port}'

    def installed(self):
        return shutil.which(self.command) is not None

    def alive(self):
        return self.process is not None and self.process.poll() is None

    def serving(self, code, generic=False):
        # With generic=True, the model of a base language (pt) counts as serving a regional code (pt-BR). ensure() passes generic=True after the restart with --update-models, so that pt-BR still works with the pt model when the regional model could not be downloaded.
        try:
            return self.alive() and (not resolve(code, english_targets(self.url, 5))[1] or generic)
        except ReviewError:
            return False

    def ensure(self, language, report=None):
        with self.lock:
            if self.alive() and language in self.confirmed:
                return self.url
            code = engine_code(language)
            # The first start does not pass --update-models, so that start works offline and takes about a second. LibreTranslate is restarted with --update-models, which downloads from the model index, only when the requested model is not installed.
            if not self.alive():
                self.start(code, False, report)
            if not self.serving(code):
                self.start(code, True, report)
                if not self.serving(code, generic=True):
                    died = not self.alive()
                    self.stop()
                    if self.reader:
                        self.reader.join(2)
                    lines = [' '.join(line.split()) for line in self.tail]
                    if died:
                        raise ReviewError('The local Argos engine stopped unexpectedly. ' + (' '.join(lines) or 'no engine output')[-300:])
                    # When a download fails, LibreTranslate prints a "Cannot update models" line and keeps running. That line is quoted when it was captured. A pipx install starts LibreTranslate in a way that loses that line, so the general message below covers that case.
                    reason = next((line for line in reversed(lines) if 'Cannot update models' in line), None)
                    if reason:
                        raise ReviewError(f'The local Argos engine has no English → {language} model and could not download one. {reason[-300:]}')
                    raise ReviewError(f'The local Argos engine could not get an English → {language} model. Argos may not offer this language, or the download failed.')
            self.confirmed.add(language)
            return self.url

    def start(self, code, update, report=None):
        self.stop()
        if self.closed:
            return
        binary = shutil.which(self.command)
        if not binary:
            raise ReviewError(INSTALL_HINT)
        if report:
            report(STARTING)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            self.port = probe.getsockname()[1]
        # Only the JSON API on 127.0.0.1 is needed, so the web interface and file translation are switched off. --load-only names the one language pair to download. PYTHONUNBUFFERED makes LibreTranslate print its messages at once, so that ensure() can quote the reason for a failed download.
        command = [binary, '--host', '127.0.0.1', '--port', str(self.port), '--load-only', 'en,' + code, '--disable-web-ui', '--disable-files-translation'] + ['--update-models'] * update
        try:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace', env=dict(os.environ, PYTHONUNBUFFERED='1'))
        except OSError:
            raise ReviewError('Could not start the local LibreTranslate engine. ' + INSTALL_HINT) from None
        self.process, tail = process, deque(maxlen=20)
        self.tail = tail
        def drain():
            with process.stdout:
                for line in process.stdout:
                    tail.append(line)
        self.reader = threading.Thread(target=drain, daemon=True)
        self.reader.start()
        if not self.registered:
            self.registered = True
            atexit.register(self.close)
        deadline = time.monotonic() + STARTUP_DEADLINE
        while process.poll() is None and time.monotonic() < deadline:
            try:
                request_json(self.url + '/languages', timeout=2)
                return
            except ReviewError:
                time.sleep(0.5)

    # stop() must not take self.lock. At exit, ensure() may still hold the lock in a request thread while it waits for LibreTranslate to start, and stop() would then block the exit for up to STARTUP_DEADLINE.
    def stop(self):
        process, self.process = self.process, None
        self.confirmed.clear()
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    def close(self):
        self.closed = True
        self.stop()


def libretranslate_endpoint(connections):
    def endpoint():
        url = connections.get('LIBRETRANSLATE_URL')
        return (url.rstrip('/'), connections.get('LIBRETRANSLATE_API_KEY')) if url else None
    return endpoint


class LibreTranslateTranslator:
    def __init__(self, endpoint, engine=None):
        self.endpoint = endpoint
        self.engine = engine
        self.resolved = {}

    def status(self):
        return {'configured': bool(self.endpoint()), 'installed': bool(self.engine and self.engine.installed())}

    def check_access(self):
        status = self.status()
        if not (status['configured'] or status['installed']):
            raise ReviewError(INSTALL_HINT)

    def models(self):
        self.check_access()
        return [{'id': 'default', 'name': 'Server default model'}]

    def location(self, language, report=None):
        configured = self.endpoint()
        if configured:
            return configured
        if not self.engine:
            raise ReviewError(INSTALL_HINT)
        return self.engine.ensure(language, report), None

    def prepare(self, language, report=None):
        url, _ = self.location(language, report)
        self.resolved[language] = resolve(language, english_targets(url))
        return self.resolved[language]

    def propose(self, model, context, progress=None):
        language = context['language']
        code, fallback = self.resolved.get(language) or self.prepare(language)
        url, api_key = self.location(language)
        plans = []
        for item in context['requested_entries']:
            sources = form_sources(item)
            plans.append((item['key'], sources, [mask(source) for source in sources]))
        queries = list(dict.fromkeys(masked for _, _, forms in plans for masked, _ in forms))
        body = dict({'q': queries, 'source': 'en', 'target': code, 'format': 'text'}, **({'api_key': api_key} if api_key else {}))
        # shortcut: the whole batch goes in one request, which is the simplest code and is enough for a local engine. A hosted LibreTranslate server that limits characters or strings per request will reject such a request. Split the batch if such servers must be supported.
        response = request_json(url + '/translate', body) if queries else {'translatedText': []}
        translated = response.get('translatedText') if isinstance(response, dict) else None
        if not isinstance(translated, list) or len(translated) != len(queries) or not all(isinstance(text, str) for text in translated):
            raise ReviewError('The LibreTranslate server returned an unexpected response.')
        output = dict(zip(queries, translated))
        entries = []
        for key, sources, forms in plans:
            results = [accept(source, output[masked], runs) for source, (masked, runs) in zip(sources, forms)]
            if None in results:
                raw = ' | '.join(output[masked] for result, (masked, _) in zip(results, forms) if result is None)
                entries.append({'key': key, 'target': sources, 'note': 'Machine translation changed placeholders or markup; English kept. Raw output: ' + raw})
            else:
                note = f'Translated with the generic {code} model; no dedicated {language} model exists.' if fallback else ''
                entries.append({'key': key, 'target': results, 'note': note})
        return {'entries': entries}
