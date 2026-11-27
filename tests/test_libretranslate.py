import json
import os
import textwrap
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import polib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from connections import Connections
from core import ReviewError, forms
from libretranslate import INSTALL_HINT, STARTING, LibreTranslateTranslator, ManagedEngine, libretranslate_endpoint
from locales import convert_units
from network import MAX_PROVIDER_JSON_BYTES
from server import ai_translators
from translation import batch_context, generate_proposals, validate_batch
from workspace import Workspace
from test_workspace import downloads

KEY = 'secret-key-123'
GREETING = 'Hello, %{name}! Read <a href="https://x.org/r">the rules</a>.'
LANGUAGES = [{'code': 'en', 'name': 'English', 'targets': ['en', 'es', 'es-419', 'ja', 'pt-BR']}, {'code': 'es', 'name': 'Spanish', 'targets': ['en', 'es']}]


def spanish(text):
    return text.replace('Hello', 'Hola').replace('Save', 'Guardar').strip()


def placeholder_download():
    native = "es-419:\n  stable: 'Sin tocar'\n  count:\n    one: Un cubo\n    many: Varios cubos\n    other: Otros cubos\n"
    units = [{'context': 'stable', 'source': ['Untouched'], 'target': ['Sin tocar']},
             {'context': 'count', 'source': ['One %{count} cube', 'Many %{count} cubes'], 'target': ['Un cubo', 'Varios cubos', 'Otros cubos'], 'fuzzy': True},
             {'context': 'greeting', 'source': [GREETING], 'target': ['']},
             {'context': 'save', 'source': ['  Save changes  '], 'target': ['']}]
    english, target, mapping = convert_units(units, native)
    return dict(downloads(target, english), native_yaml=native, yaml_mapping=mapping)


def regulations_download():
    english, target = polib.POFile(), polib.POFile()
    english.metadata = {'Language': 'en', 'Content-Type': 'text/plain; charset=UTF-8', 'Plural-Forms': 'nplurals=2; plural=(n != 1);'}
    target.metadata = dict(english.metadata, Language='es_419', **{'Plural-Forms': 'nplurals=3; plural=(n==1 ? 0 : n>=2 && n<=4 ? 1 : 2);'})
    plural = dict(msgctxt='rules:1', msgid='One %s cube', msgid_plural='Many %s cubes')
    english.append(polib.POEntry(msgstr_plural={0: 'One %s cube', 1: 'Many %s cubes'}, **plural))
    target.append(polib.POEntry(msgstr_plural={0: '', 1: '', 2: ''}, **plural))
    return {'english': str(english), 'target': str(target), 'metadata': {}, 'warnings': []}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, payload, raw=None, headers=()):
        data = raw if raw is not None else json.dumps(payload).encode()
        self.send_response(status)
        for name, value in headers:
            self.send_header(name, value)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def handle_any(self, method):
        length = int(self.headers.get('Content-Length') or 0)
        body = json.loads(self.rfile.read(length)) if length else None
        self.server.requests.append((method, self.path, body))
        if self.server.override and self.server.override(self, method, self.path):
            return
        if self.path == '/languages':
            self.reply(200, self.server.languages)
        elif self.path == '/translate':
            self.reply(200, {'translatedText': [self.server.translate(q) for q in body['q']]})
        else:
            self.reply(404, {'error': 'not found'})

    def do_GET(self):
        self.handle_any('GET')

    def do_POST(self):
        self.handle_any('POST')


class Fake(ThreadingHTTPServer):
    def __init__(self):
        super().__init__(('127.0.0.1', 0), Handler)
        self.requests, self.languages, self.translate, self.override = [], LANGUAGES, spanish, None

    @property
    def url(self):
        return f'http://127.0.0.1:{self.server_port}'

    def translations(self):
        return [r for r in self.requests if r[1] == '/translate']


class LibreTranslateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.fake = Fake()
        thread = threading.Thread(target=self.fake.serve_forever, daemon=True)
        thread.start()

        def stop():
            self.fake.shutdown()
            self.fake.server_close()
            thread.join(timeout=3)
        self.addCleanup(stop)
        self.translator = self.translator_for(self.fake.url)

    def translator_for(self, url):
        return LibreTranslateTranslator(lambda: (url, KEY))

    def session(self, component='locales', download=None):
        client = Mock(env_path=self.root / '.env')
        client.download.return_value = download or placeholder_download()
        workspace = Workspace(self.root / ('data-' + component), client)
        workspace.sync(component)
        return workspace

    def propose(self, workspace, translator=None):
        entries = workspace.work['entries']
        result = (translator or self.translator).propose('default', batch_context(workspace, entries))
        return entries, result

    def test_placeholders_and_markup_never_reach_the_engine_and_are_restored(self):
        w = self.session()
        entries, result = self.propose(w)
        wire = json.dumps(self.fake.translations()[0][2]['q'])
        self.assertNotIn('%{', wire)
        self.assertNotIn('<a', wire)
        self.assertNotIn('https://', wire)
        self.assertEqual(self.fake.translations()[0][2]['format'], 'text')
        self.assertEqual(self.fake.translations()[0][2]['api_key'], KEY)
        self.assertEqual(len(validate_batch(w, entries, result)), 3)
        greeting = next(e for e in result['entries'] if 'greeting' in w.work['entries'][[x['key'] for x in entries].index(e['key'])]['context'])
        self.assertEqual(greeting['target'], [GREETING.replace('Hello', 'Hola')])
        self.assertEqual(greeting['note'], '')

    def test_markdown_links_are_fully_protected_and_their_text_is_translated(self):
        self.fake.translate = lambda q: q.replace('See', 'Ver').replace('Regulation', 'Regulación')
        source = 'See [Regulation A2](regulations:regulation:A2) for the %{count}th time.'
        context = {'language': 'es_419', 'requested_entries': [{'key': 'k', 'source': [source], 'target_count': 1}]}
        result = self.translator.propose('default', context)
        self.assertEqual(self.fake.translations()[0][2]['q'], ['See XPH1X Regulation A2 XPH2X for the XPH3X th time.'])
        self.assertEqual(result['entries'], [{'key': 'k', 'target': ['Ver [Regulación A2](regulations:regulation:A2) for the %{count}th time.'], 'note': ''}])
        self.fake.translate = lambda q: q.replace('XPH1X ', '「')
        fallback = self.translator.propose('default', context)['entries'][0]
        self.assertEqual(fallback['target'], [source])
        self.assertIn('English kept', fallback['note'])

    def test_reordered_markup_keeps_english_but_reordered_placeholders_are_translated(self):
        def swapped(q):
            return q.replace('XPH1X', '@').replace('XPH2X', 'XPH1X').replace('@', 'XPH2X')
        self.fake.translate = swapped
        for source in ('Read the [rules](https://x.org/r) first', 'Click <a href="x">here</a> now'):
            context = {'language': 'es_419', 'requested_entries': [{'key': 'k', 'source': [source], 'target_count': 1}]}
            entry = self.translator.propose('default', context)['entries'][0]
            self.assertEqual(entry['target'], [source])
            self.assertIn('English kept', entry['note'])
        context = {'language': 'es_419', 'requested_entries': [{'key': 'k', 'source': ['%{count} of %{total}'], 'target_count': 1}]}
        self.assertEqual(self.translator.propose('default', context)['entries'], [{'key': 'k', 'target': ['%{total} of %{count}'], 'note': ''}])

    def test_a_dropped_sentinel_keeps_english_for_that_entry_only(self):
        self.fake.translate = lambda q: spanish(q.replace('XPH2X', '')) if 'rules' in q else spanish(q)
        w = self.session()
        entries, result = self.propose(w)
        validate_batch(w, entries, result)
        by_context = {item['context']: e for item, e in zip(entries, result['entries'])}
        bad = by_context['greeting']
        self.assertEqual(bad['target'], [GREETING])
        self.assertTrue(bad['note'].startswith('Machine translation changed placeholders or markup; English kept. Raw output: '))
        self.assertEqual(by_context['save']['target'], ['  Guardar changes  '])
        self.assertEqual(by_context['count']['note'], '')

    def test_plural_layouts_pass_validation(self):
        for component, download in (('locales', None), ('regulations', regulations_download())):
            with self.subTest(component):
                w = self.session(component, download)
                entries, result = self.propose(w)
                plural = next(e for item, e in zip(entries, result['entries']) if len(item['original']) == 3)
                self.assertEqual(len(plural['target']), 3)
                self.assertEqual(len(validate_batch(w, entries, result)), len(entries))
        sent = [q for _, _, body in self.fake.translations() for q in body['q']]
        self.assertIn('One XPH1X cube', sent)
        self.assertIn('Many XPH1X cubes', sent)

    def test_language_resolution(self):
        cases = [('pt_BR', ['en', 'pt-BR'], ('pt-BR', False)), ('pt_BR', ['en', 'pb'], ('pb', False)), ('es_419', ['en', 'es'], ('es', True)), ('zh_Hans', ['en', 'zh'], ('zh', False)), ('zh_Hant', ['en', 'zh', 'zt'], ('zt', False)),
                 ('zh_CN', ['en', 'zh-Hans', 'zh-Hant'], ('zh-Hans', False)), ('zh_TW', ['en', 'zh-Hans', 'zh-Hant'], ('zh-Hant', False)), ('zh_CN', ['en', 'zh'], ('zh', False))]
        for language, targets, expected in cases:
            with self.subTest(language, targets=targets):
                self.fake.languages = [{'code': 'en', 'name': 'English', 'targets': targets}]
                self.assertEqual(self.translator.prepare(language), expected)

    def test_unavailable_languages_raise_before_any_translation(self):
        for language, targets in (('zh_Hant', ['en', 'zh']), ('hr', ['en', 'es'])):
            with self.subTest(language):
                self.fake.languages = [{'code': 'en', 'name': 'English', 'targets': targets}]
                with self.assertRaisesRegex(ReviewError, f'no English → {language} model'):
                    self.translator.prepare(language)
        self.fake.languages = [{'code': 'es', 'name': 'Spanish', 'targets': ['en']}]
        with self.assertRaises(ReviewError):
            self.translator.prepare('es_419')
        self.assertEqual(self.fake.translations(), [])

    def test_prose_percent_in_a_format_flagged_entry_keeps_english_and_the_session_finishes(self):
        english, target = polib.POFile(), polib.POFile()
        english.metadata = {'Language': 'en', 'Content-Type': 'text/plain; charset=UTF-8', 'Plural-Forms': 'nplurals=2; plural=(n != 1);'}
        target.metadata = dict(english.metadata, Language='es_419')
        for index, text in enumerate(['Hello %s', 'At least 50% of attempts for %s.']):
            english.append(polib.POEntry(msgctxt=f'r{index}', msgid=text, msgstr=text, flags=['python-format']))
            target.append(polib.POEntry(msgctxt=f'r{index}', msgid=text, msgstr='', flags=['python-format']))
        self.fake.translate = lambda q: q.replace('Hello', 'Hola').replace('of attempts', 'de los intentos')
        w = self.session('regulations', {'english': str(english), 'target': str(target), 'metadata': {}, 'warnings': []})
        state = generate_proposals(w, {'libretranslate': self.translator}, 'libretranslate', 'default')
        self.assertTrue(state['workspace']['ready'])
        self.assertEqual(sorted(forms(entry)[0] for entry in w.review.proposed.by_key.values()), ['At least 50% of attempts for %s.', 'Hola %s'])

    def test_a_proxy_in_the_environment_is_not_used_for_a_local_server(self):
        with patch.dict(os.environ, {'http_proxy': 'http://127.0.0.1:9', 'no_proxy': ''}):
            self.assertEqual(self.translator.prepare('es_419'), ('es-419', False))

    def test_base_fallback_is_noted_on_every_entry(self):
        self.fake.languages = [{'code': 'en', 'name': 'English', 'targets': ['en', 'es']}]
        w = self.session()
        entries, result = self.propose(w)
        self.assertEqual({e['note'] for e in result['entries']}, {'Translated with the generic es model; no dedicated es_419 model exists.'})
        validate_batch(w, entries, result)

    def test_server_errors_surface_their_text_and_never_the_key(self):
        self.translator.prepare('es_419')
        for status, text in ((403, 'Invalid API key'), (500, 'Cannot translate text: model crashed')):
            with self.subTest(status):
                self.fake.override = lambda h, m, p, status=status, text=text: h.reply(status, {'error': text}) or True
                w = self.session()
                with self.assertRaises(ReviewError) as caught:
                    self.propose(w)
                self.assertIn(text, str(caught.exception))
                self.assertIn(str(status), str(caught.exception))
                self.assertNotIn(KEY, str(caught.exception))

    def test_unreachable_redirecting_oversized_and_invalid_servers_raise(self):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            closed = probe.getsockname()[1]
        overrides = {
            'redirect': lambda h, m, p: h.reply(302, None, raw=b'', headers=[('Location', 'http://127.0.0.1:9/')]) or True,
            'oversized': lambda h, m, p: h.reply(200, None, raw=b'[' + b' ' * MAX_PROVIDER_JSON_BYTES + b']') or True,
            'not json': lambda h, m, p: h.reply(200, None, raw=b'<html>nope</html>') or True,
            'wrong shape': lambda h, m, p: h.reply(200, {'translatedText': 'x'}) or True,
            'list instead of object': lambda h, m, p: p == '/translate' and (h.reply(200, ['x']) or True),
        }
        with self.assertRaises(ReviewError) as caught:
            self.translator_for(f'http://127.0.0.1:{closed}').prepare('es')
        self.assertNotIn(KEY, str(caught.exception))
        with self.assertRaises(ReviewError):
            self.translator_for('file:///etc/passwd').prepare('es')
        for name, override in overrides.items():
            with self.subTest(name):
                self.fake.override = override
                with self.assertRaises(ReviewError) as caught:
                    self.propose(self.session()) if name in ('wrong shape', 'list instead of object') else self.translator.prepare('es_419')
                self.assertNotIn(KEY, str(caught.exception))

    def test_generate_proposals_resumes_and_checks_the_language_first(self):
        w = self.session()
        translators = {'libretranslate': self.translator}
        state = generate_proposals(w, translators, 'libretranslate', 'default', maximum=1)
        self.assertFalse(state['workspace']['ready'])
        self.assertEqual(len(self.fake.translations()), 1)
        state = generate_proposals(w, translators, 'libretranslate', 'default', maximum=10)
        self.assertTrue(state['workspace']['ready'])
        self.assertEqual(len(self.fake.translations()), 2)
        self.assertEqual(state['counts']['accepted'], 0)

        other = self.session('regulations', regulations_download())
        self.fake.languages = [{'code': 'en', 'name': 'English', 'targets': ['ja']}]
        before = len(self.fake.translations())
        with self.assertRaisesRegex(ReviewError, 'no English'):
            generate_proposals(other, translators, 'libretranslate', 'default')
        self.assertEqual(len(self.fake.translations()), before)

    def test_whitespace_survives_and_lowercased_sentinels_are_restored(self):
        self.fake.translate = lambda q: spanish(q).replace('XPH', 'xPH')
        w = self.session()
        entries, result = self.propose(w)
        validate_batch(w, entries, result)
        by_context = {item['context']: e for item, e in zip(entries, result['entries'])}
        self.assertEqual(by_context['save']['target'], ['  Guardar changes  '])
        self.assertEqual(by_context['greeting']['target'], [GREETING.replace('Hello', 'Hola')])

    def test_connections_store_a_clean_address_and_expose_only_a_boolean(self):
        connections = Connections(self.root / '.env')
        self.assertFalse(connections.status()['libretranslate'])
        self.assertIsNone(libretranslate_endpoint(connections)())
        connections.configure({'LIBRETRANSLATE_URL': 'http://127.0.0.1:5000/', 'LIBRETRANSLATE_API_KEY': KEY})
        self.assertEqual(libretranslate_endpoint(connections)(), ('http://127.0.0.1:5000', KEY))
        self.assertIs(connections.status()['libretranslate'], True)
        self.assertNotIn('5000', json.dumps(connections.status()))
        self.assertNotIn(KEY, json.dumps(connections.status()))
        for bad in ('file:///etc/passwd', 'ftp://x'):
            with self.assertRaises(ReviewError):
                connections.configure({'LIBRETRANSLATE_URL': bad})
        connections.configure({'LIBRETRANSLATE_URL': ''})
        self.assertFalse(connections.status()['libretranslate'])
        self.assertIn('libretranslate', ai_translators(connections, Mock()))

    def test_a_saved_api_key_is_not_sent_to_a_different_server(self):
        (self.root / '.env').write_text('LIBRETRANSLATE_URL=http://a.example/\nLIBRETRANSLATE_API_KEY=' + KEY + '\n')
        connections = Connections(self.root / '.env')
        connections.configure({'LIBRETRANSLATE_URL': 'http://a.example'})
        self.assertEqual(libretranslate_endpoint(connections)(), ('http://a.example', KEY))
        connections.configure({'LIBRETRANSLATE_URL': 'http://b.example'})
        self.assertEqual(libretranslate_endpoint(connections)(), ('http://b.example', ''))
        connections.configure({'LIBRETRANSLATE_URL': 'http://c.example', 'LIBRETRANSLATE_API_KEY': 'other-key-456'})
        self.assertEqual(libretranslate_endpoint(connections)(), ('http://c.example', 'other-key-456'))
        connections.configure({'LIBRETRANSLATE_URL': 'http://a.example'})
        self.assertEqual(libretranslate_endpoint(connections)(), ('http://a.example', ''))


# A fake `libretranslate` command with the behaviour ManagedEngine depends on. Like the real command, it downloads the --load-only model only when started with --update-models or when fewer than two models are installed, and it serves every installed model whatever --load-only says. The tests that expect a restart, or expect none, rely on this.
ENGINE = textwrap.dedent('''\
    #!PYTHON
    import json, os, sys, time
    from http.server import BaseHTTPRequestHandler, HTTPServer
    here = os.path.dirname(os.path.abspath(__file__))
    args = sys.argv[1:]
    arg = lambda name: args[args.index(name) + 1]
    code = arg('--load-only').split(',')[1]
    with open(os.path.join(here, 'calls.jsonl'), 'a') as f:
        f.write(json.dumps({'argv': args, 'pid': os.getpid()}) + '\\n')
    path = os.path.join(here, 'models.json')
    installed = json.load(open(path))
    if '--update-models' in args or len(installed) < 2:
        if code == 'hr' or os.path.exists(os.path.join(here, 'offline')):
            # Like the real command, a failed download prints the "Cannot update models" line without flushing and the server still starts. The code `hr` always fails to download, and an `offline` file next to the fake command makes every download fail.
            print("Cannot update models (normal if you're offline): Unavailable language codes: hr.")
        elif code not in installed:
            installed.append(code)
            json.dump(installed, open(path, 'w'))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *a):
            print('request', self.path, file=sys.stderr, flush=True)

        def send(self, payload):
            data = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self.send([{'code': 'en', 'name': 'English', 'targets': ['en'] + installed}])

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            self.send({'translatedText': [q.replace('Hello', 'Hola') for q in body['q']]})

    if code == 'slow':
        time.sleep(60)
    HTTPServer(('127.0.0.1', int(arg('--port'))), Handler).serve_forever()
''').replace('PYTHON', sys.executable, 1)


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class ManagedEngineTests(unittest.TestCase):
    session = LibreTranslateTests.session

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.command = self.root / 'libretranslate'
        self.command.write_text(ENGINE)
        self.command.chmod(0o755)
        self.install('es', 'fr')
        self.engines = []

    def install(self, *codes):
        (self.root / 'models.json').write_text(json.dumps(list(codes)))

    def installed(self):
        return json.loads((self.root / 'models.json').read_text())

    def calls(self):
        path = self.root / 'calls.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def translator(self, url=None):
        engine = ManagedEngine(str(self.command))
        self.engines.append(engine)
        self.addCleanup(self.no_process_left)
        return LibreTranslateTranslator(lambda: (url, None) if url else None, engine)

    def no_process_left(self):
        for engine in self.engines:
            engine.stop()
        self.assertEqual([c['pid'] for c in self.calls() if alive(c['pid'])], [])

    def test_the_engine_starts_on_demand_and_is_reused(self):
        translator, messages = self.translator(), []
        w = self.session()
        translators = {'libretranslate': translator}
        state = generate_proposals(w, translators, 'libretranslate', 'default', progress=lambda event: messages.append(event['message']), maximum=1)
        self.assertEqual(messages.count(STARTING), 1)
        self.assertFalse(state['workspace']['ready'])
        state = generate_proposals(w, translators, 'libretranslate', 'default', progress=lambda event: messages.append(event['message']), maximum=10)
        self.assertTrue(state['workspace']['ready'])
        self.assertEqual(messages.count(STARTING), 1)
        self.assertEqual(len(self.calls()), 1)
        argv = self.calls()[0]['argv']
        self.assertEqual(argv[argv.index('--host') + 1], '127.0.0.1')
        self.assertEqual(argv[argv.index('--load-only') + 1], 'en,es')
        self.assertNotIn('--update-models', argv)
        self.assertIn('--disable-web-ui', argv)
        self.assertIn('--disable-files-translation', argv)
        self.assertTrue(w.work['entries'] and state['counts']['pending'])
        self.assertTrue(alive(self.calls()[0]['pid']))

    def test_a_missing_model_restarts_once_with_update_models_and_translates(self):
        translator = self.translator()
        self.assertEqual(translator.prepare('es_419'), ('es', True))
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(translator.prepare('ja'), ('ja', False))
        first, second = self.calls()
        self.assertIn('--update-models', second['argv'])
        self.assertIn('en,ja', second['argv'])
        self.assertFalse(alive(first['pid']))
        self.assertEqual(self.installed(), ['es', 'fr', 'ja'])
        context = {'language': 'ja', 'requested_entries': [{'key': 'k', 'source': ['Hello'], 'target_count': 1}]}
        self.assertEqual(translator.propose('default', context)['entries'], [{'key': 'k', 'target': ['Hola'], 'note': ''}])
        self.assertEqual(translator.prepare('es_419'), ('es', True))
        self.assertEqual(len(self.calls()), 2)

    def test_an_unavailable_language_raises_with_the_engine_message_and_leaves_nothing_running(self):
        translator = self.translator()
        with self.assertRaises(ReviewError) as caught:
            translator.prepare('hr')
        self.assertEqual(str(caught.exception), "The local Argos engine has no English → hr model and could not download one. Cannot update models (normal if you're offline): Unavailable language codes: hr.")
        self.assertEqual(len(self.calls()), 2)
        self.assertIn('--update-models', self.calls()[1]['argv'])
        self.assertFalse(translator.engine.alive())
        self.assertEqual([c['pid'] for c in self.calls() if alive(c['pid'])], [])

    def test_a_launcher_that_buffers_the_engine_message_still_gives_a_clear_error(self):
        # `python -E` ignores the PYTHONUNBUFFERED that ManagedEngine sets, so this launcher loses the "Cannot update models" line the way a pipx install does. The error raised by prepare() must still say what failed.
        self.command.write_text(ENGINE.replace(sys.executable, sys.executable + ' -E', 1))
        with self.assertRaises(ReviewError) as caught:
            self.translator().prepare('hr')
        self.assertEqual(str(caught.exception), 'The local Argos engine could not get an English → hr model. Argos may not offer this language, or the download failed.')

    def test_a_failed_regional_download_falls_back_to_the_installed_base_model(self):
        self.install('pt', 'es')
        (self.root / 'offline').touch()
        translator = self.translator()
        self.assertEqual(translator.prepare('pt_BR'), ('pt', True))
        self.assertEqual(len(self.calls()), 2)
        self.assertTrue(translator.engine.alive())

    def test_a_request_after_close_raises_cleanly(self):
        translator = self.translator()
        translator.engine.close()
        with self.assertRaisesRegex(ReviewError, 'stopped unexpectedly'):
            translator.prepare('es_419')
        self.assertEqual(self.calls(), [])

    def test_closing_during_a_slow_start_returns_at_once_and_starts_nothing_else(self):
        translator, outcome = self.translator(), []

        def prepare():
            try:
                translator.prepare('slow')
            except ReviewError as exc:
                outcome.append(exc)
        thread = threading.Thread(target=prepare, daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        while not self.calls() and time.monotonic() < deadline:
            time.sleep(0.05)
        started = time.monotonic()
        translator.engine.close()
        self.assertLess(time.monotonic() - started, 3)
        thread.join(10)
        self.assertEqual(len(outcome), 1)
        self.assertEqual(len(self.calls()), 1)
        self.assertFalse(alive(self.calls()[0]['pid']))

    def test_stop_ends_the_process_and_is_safe_to_repeat(self):
        translator = self.translator()
        translator.engine.stop()
        translator.prepare('es_419')
        pid = self.calls()[0]['pid']
        self.assertTrue(alive(pid))
        translator.engine.stop()
        translator.engine.stop()
        self.assertFalse(alive(pid))
        self.assertFalse(translator.engine.alive())

    def test_a_configured_address_wins_and_the_engine_never_starts(self):
        fake = Fake()
        thread = threading.Thread(target=fake.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (fake.shutdown(), fake.server_close(), thread.join(timeout=3)))
        translator = self.translator(fake.url)
        self.assertEqual(translator.status(), {'configured': True, 'installed': True})
        translator.check_access()
        self.assertEqual(translator.prepare('es_419'), ('es-419', False))
        self.assertEqual(self.calls(), [])
        self.assertFalse(translator.engine.alive())

    def test_without_an_address_or_the_command_the_install_hint_is_raised(self):
        for engine in (None, ManagedEngine(str(self.root / 'missing'))):
            translator = LibreTranslateTranslator(lambda: None, engine)
            self.assertEqual(translator.status(), {'configured': False, 'installed': False})
            for call in (translator.check_access, translator.models, lambda: translator.prepare('es')):
                with self.assertRaises(ReviewError) as caught:
                    call()
                self.assertEqual(str(caught.exception), INSTALL_HINT)
        self.assertEqual(self.translator().status(), {'configured': False, 'installed': True})

    def test_regional_models_are_fetched_while_other_regions_use_the_base_language(self):
        self.install('pt', 'es')
        translator = self.translator()
        self.assertEqual(translator.prepare('pt_BR'), ('pt-BR', False))
        self.assertIn('en,pt-BR', self.calls()[-1]['argv'])
        self.assertIn('--update-models', self.calls()[-1]['argv'])
        self.assertEqual(self.installed(), ['pt', 'es', 'pt-BR'])
        calls = len(self.calls())
        self.assertEqual(translator.prepare('es_419'), ('es', True))
        self.assertEqual(len(self.calls()), calls)
        context = {'language': 'es_419', 'requested_entries': [{'key': 'k', 'source': ['Hello'], 'target_count': 1}]}
        self.assertEqual(translator.propose('default', context)['entries'][0]['note'], 'Translated with the generic es model; no dedicated es_419 model exists.')


if __name__ == '__main__':
    unittest.main()
