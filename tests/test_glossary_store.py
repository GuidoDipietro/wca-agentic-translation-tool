"""Language catalogs, precedence, validation and immutable session snapshots."""
import copy
import fcntl
import http.client
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from core import ReviewError
from glossary_store import GlossaryStore, encode, merge_terms, parse_document, terms_tbx
from workspace import Workspace, glossary_terms
from server import make_handler
from connections import Connections
from test_workspace import downloads
from translation import batch_context


def term(source='Trainee Delegate', preferred='Delegado en prácticas'):
    return {'source': [source], 'preferred': [preferred], 'forbidden': ['Delegado en entrenamiento']}


def document(entries=None, language='es_419'):
    return {'version': 1, 'language': language, 'entries': [term()] if entries is None else entries}


class GlossaryStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = GlossaryStore(self.root / 'data', self.root / 'defaults')

    def bundled(self, value):
        self.store.defaults.mkdir(exist_ok=True)
        (self.store.defaults / 'es_419.json').write_text(encode(value))

    def test_defaults_personal_replacement_aliases_and_mode_isolation(self):
        self.assertEqual(self.store.snapshot('es-419')['origin'], 'empty')
        self.bundled(document())
        initial = self.store.snapshot('es-419')
        self.assertEqual(initial['origin'], 'bundled')
        changed = document([term(preferred='Otra referencia')])
        self.store.save('ES-419', changed, initial['revision'])
        restored = GlossaryStore(self.root / 'data', self.store.defaults)
        self.assertEqual(restored.snapshot('es_419')['entries'], changed['entries'])
        self.bundled(document([term(preferred='Nuevo predeterminado')]))
        self.assertEqual(restored.snapshot('es_419')['entries'], changed['entries'])
        self.assertEqual(self.store.snapshot('fr')['entries'], [])
        self.assertEqual(GlossaryStore(self.root / 'demo', self.store.defaults).snapshot('es_419')['origin'], 'bundled')
        empty = self.store.save('es_419', document([]), restored.snapshot('es_419')['revision'])
        self.assertEqual(empty['entries'], [])
        self.assertEqual(empty['origin'], 'local')

    def test_stale_edit_and_bad_import_do_not_overwrite(self):
        original = self.store.snapshot('es_419')
        self.store.save('es_419', document(), original['revision'])
        with self.assertRaises(ReviewError):
            self.store.save('es_419', document([]), original['revision'])
        current = self.store.snapshot('es_419')
        with self.assertRaises(ReviewError):
            self.store.save('es_419', document(language='fr'), current['revision'])
        self.assertEqual(self.store.snapshot('es_419'), current)

    def test_invalid_documents(self):
        bad = [[], {}, document(language='../auth'), document(language='fr'), document([term(), term(source='TRAINEE DELEGATE')]), document([{'source': [], 'preferred': ['x'], 'forbidden': []}]), document([{'source': ['x'], 'preferred': [], 'forbidden': []}]), document([{'source': ['x'], 'preferred': ['x'], 'forbidden': ['X']}]), document([term(source='a\nb')])]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ReviewError):
                parse_document(json.dumps(value), 'es_419')
        for value in ('{', ' ' * (2 * 1024 * 1024 + 1)):
            with self.assertRaises(ReviewError):
                parse_document(value, 'es_419')
        for code in ('../auth', '/tmp/es', None):
            with self.assertRaises(ReviewError):
                self.store.snapshot(code)

    def test_merge_replaces_remote_variants_and_preserves_other_terms(self):
        remote = [term(preferred='Remoto'), term('Cube', 'Cubo')]
        remote[0]['source'].append('Trainee Delegates')
        configured = [term('trainee delegates', 'Referencia')]
        original = copy.deepcopy(remote)
        self.assertEqual(merge_terms(remote, configured), [remote[1], configured[0]])
        self.assertEqual(remote, original)
        self.assertEqual(glossary_terms(terms_tbx(configured, 'es_419')), configured)

    def test_session_generation_review_and_reopening_use_snapshot(self):
        client = Mock()
        client.env_path = self.root / '.env'
        client.download.return_value = downloads()
        client.download.return_value['glossary'] = terms_tbx([term(preferred='Remoto')], 'es_419')
        workspace = Workspace(self.root / 'data', client)
        workspace.glossaries = self.store
        configured = document([{'source': ['Hello %s'], 'preferred': ['Hola %s'], 'forbidden': ['Buenas %s'], 'note': 'Saludo de referencia'}])
        self.store.save('es_419', configured, self.store.snapshot('es_419')['revision'])
        workspace.sync()
        session_id = workspace.active_id
        original = copy.deepcopy(workspace.work['glossary'])
        self.assertEqual(original, [term(preferred='Remoto'), configured['entries'][0]])
        context = batch_context(workspace, workspace.work['entries'])
        self.assertEqual(context['glossary'][-1]['note'], 'Saludo de referencia')
        self.assertEqual(workspace.review.glossary, original)
        folder = workspace.directory(session_id)
        self.assertTrue((folder / 'weblate-glossary.tbx').exists())
        self.assertIn('glossary.json', workspace.manifest['hashes'])
        self.assertEqual(json.loads((folder / 'glossary.json').read_text())['entries'], original)
        self.store.save('es_419', document([]), self.store.snapshot('es_419')['revision'])
        workspace.open(session_id)
        self.assertEqual(workspace.work['glossary'], original)
        workspace.sync()
        self.assertEqual(workspace.work['glossary'], [term(preferred='Remoto')])
        workspace.open(session_id)
        self.assertEqual(workspace.review.glossary, original)
        (folder / 'glossary.json').write_text('{}')
        with self.assertRaises(ReviewError):
            workspace.open(session_id)

    def test_cli_validate_import_and_server_lock(self):
        path = self.root / 'candidate.json'
        path.write_text(encode(document()))
        cmd = [sys.executable, str(Path(__file__).resolve().parents[1] / 'app/glossary_store.py'), str(path), '--language', 'es-419', '--data-dir', str(self.root / 'data')]
        self.assertEqual(subprocess.run(cmd, capture_output=True).returncode, 0)
        self.assertFalse(self.store.directory.exists())
        self.assertEqual(subprocess.run(cmd + ['--import-file'], capture_output=True).returncode, 0)
        self.assertEqual(self.store.snapshot('es_419')['entries'], document()['entries'])
        with (self.root / 'data/.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = subprocess.run(cmd + ['--import-file'], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Stop the server', result.stderr)


class GlossaryAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        workspace = Workspace(root / 'data', Mock())
        workspace.glossaries = GlossaryStore(root / 'data', root / 'defaults')
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(workspace, Connections(root / '.env')))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def request(self, method, data=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request(method, '/api/glossary?language=es-419', body=json.dumps(data) if data else None, headers=headers or {'Content-Type': 'application/json'})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_crud_without_active_session_and_conflicting_tabs(self):
        status, initial = self.request('GET')
        self.assertEqual(status, 200)
        data = {'language': 'es_419', 'document': document(), 'revision': initial['revision']}
        status, saved = self.request('POST', data)
        self.assertEqual(status, 200)
        self.assertEqual(saved['entries'], document()['entries'])
        self.assertEqual(self.request('POST', data)[0], 400)
        data['revision'] = saved['revision']
        data['document'] = document([])
        self.assertEqual(self.request('POST', data)[1]['entries'], [])
        self.assertEqual(self.request('GET')[1]['entries'], [])

    def test_external_requests_blocked_and_language_mismatch_rejected(self):
        self.assertEqual(self.request('GET', headers={'Sec-Fetch-Site': 'cross-site'})[0], 403)
        self.assertEqual(self.request('POST', {'language': 'es_419'}, {'Content-Type': 'application/json', 'Origin': 'https://example.org'})[0], 403)
        initial = self.request('GET')[1]
        self.assertEqual(self.request('POST', {'language': 'es_419', 'document': document(language='fr'), 'revision': initial['revision']})[0], 400)
