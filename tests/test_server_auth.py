"""Regresiones del regreso OAuth y del bloqueo de solicitudes externas."""
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from connections import Connections
from openai_auth import ChatGPTAuth
from server import make_handler
from workspace import Workspace


NAVIGATION = {'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Dest': 'document'}


class AuthNavigationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        client = Mock()
        client.env_path = root / '.env'
        self.workspace = Workspace(root / 'data', client)
        self.auth = ChatGPTAuth(root / 'auth.json')
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.workspace, Connections(root / '.env'), self.auth))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def request(self, method, path, headers=None, body=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read().decode()
        finally:
            conn.close()

    def test_external_top_level_navigation_can_open_the_app(self):
        status, headers, body = self.request('GET', '/', NAVIGATION)
        self.assertEqual(status, 200)
        self.assertIn('WCA Translation Studio', body)
        self.assertIn('no-store', headers['Cache-Control'])
        self.assertIsNone(self.workspace.active_id)

    def test_slow_request_body_times_out_and_releases_the_operation_lock(self):
        with patch('server.LOCAL_REQUEST_TIMEOUT',0.1):
            connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
            try:
                connection.putrequest('POST','/api/connections')
                connection.putheader('Content-Type','application/json')
                connection.putheader('Content-Length','100')
                connection.endheaders(b'{')
                response=connection.getresponse()
                self.assertEqual(response.status,408)
                self.assertIn('timed out',response.read().decode())
            finally:
                connection.close()
        self.assertEqual(self.request('GET','/api/state')[0],200)

    def test_oauth_callback_redirect_can_load_the_app_and_connections(self):
        query = '/auth/callback?state=test-state&client_id=test-client&code=test-code'
        with patch.object(self.auth, 'complete') as complete:
            status, headers, _ = self.request('GET', query, NAVIGATION)
        self.assertEqual(status, 303)
        self.assertEqual(headers['Location'], '/')
        complete.assert_called_once_with({'state': 'test-state', 'client_id': 'test-client', 'code': 'test-code'})
        # El navegador conserva cross-site durante toda la cadena de redirecciones.
        status, _, body = self.request('GET', headers['Location'], NAVIGATION)
        self.assertEqual(status, 200)
        self.assertIn('/app.js', body)
        for path in ['/app.js', '/style.css', '/i18n.js', '/translations.json', '/USER_GUIDE.md', '/USER_GUIDE.es.md', '/api/state', '/api/connections']:
            status, _, _ = self.request('GET', path, {'Sec-Fetch-Site': 'same-origin'})
            self.assertEqual(status, 200, path)

    def test_bad_oauth_state_still_fails_before_any_token_exchange(self):
        with patch('openai_auth.http_json', side_effect=AssertionError('No consultar al proveedor')):
            status, headers, _ = self.request('GET', '/auth/callback?state=wrong&code=test-code&client_id=test-client', NAVIGATION)
        self.assertEqual(status, 303)
        self.assertIsNotNone(self.auth.last_error)
        self.assertFalse(self.auth.status()['accounts'])
        self.assertEqual(self.request('GET', headers['Location'], NAVIGATION)[0], 200)

    def test_external_api_and_resource_requests_remain_blocked(self):
        for path in ['/api/state', '/api/connections', '/api/models', '/api/languages', '/app.js', '/style.css', '/i18n.js', '/translations.json', '/USER_GUIDE.md', '/USER_GUIDE.es.md']:
            with self.subTest(path=path):
                status, _, body = self.request('GET', path, NAVIGATION)
                self.assertEqual(status, 403)
                self.assertEqual(json.loads(body)['error'], 'External request blocked.')

    def test_external_posts_cannot_start_login_or_change_settings(self):
        for path in ['/api/chatgpt/start', '/api/connections', '/api/sync', '/api/dismiss-check', '/api/validate-export', '/api/export', '/']:
            with self.subTest(path=path), patch.object(self.auth, 'start') as start:
                status, _, _ = self.request('POST', path, dict(NAVIGATION, **{'Content-Type': 'application/json'}), '{}')
                self.assertEqual(status, 403)
                start.assert_not_called()
        self.assertFalse(self.auth.pending)
        self.assertIsNone(self.workspace.active_id)

    def test_untrusted_posts_are_forbidden_while_an_operation_is_running(self):
        entered = threading.Event()
        release = threading.Event()
        results = []

        def configure(values):
            entered.set()
            if not release.wait(10):
                raise AssertionError('Test operation was not released')

        worker = threading.Thread(target=lambda: results.append(self.request(
            'POST', '/api/connections', {'Content-Type': 'application/json'}, '{"values":{}}'
        )), daemon=True)
        with patch.object(Connections, 'configure', side_effect=configure), patch.object(self.auth, 'start') as start:
            worker.start()
            try:
                self.assertTrue(entered.wait(3), 'The local operation must hold the lock')
                for extra in [NAVIGATION, {'Origin': 'https://other.example'}, {'Host': 'other.example'}]:
                    with self.subTest(headers=extra):
                        status, _, _ = self.request('POST', '/api/chatgpt/start',
                            dict(extra, **{'Content-Type': 'application/json'}), '{}')
                        self.assertEqual(status, 403)
                status, _, _ = self.request('POST', '/api/chatgpt/start',
                    {'Content-Type': 'application/json'}, '{}')
                self.assertEqual(status, 409, 'Trusted requests must still respect the operation lock')
                start.assert_not_called()
                self.assertFalse(release.is_set())
            finally:
                release.set()
                worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0], 200)
        self.assertFalse(self.auth.pending)
        self.assertIsNone(self.workspace.active_id)

    def test_frames_and_fetch_requests_cannot_use_the_navigation_exception(self):
        for mode, destination in [('navigate', 'iframe'), ('cors', 'empty'), ('no-cors', 'document')]:
            with self.subTest(mode=mode, destination=destination):
                headers = dict(NAVIGATION, **{'Sec-Fetch-Mode': mode, 'Sec-Fetch-Dest': destination})
                self.assertEqual(self.request('GET', '/', headers)[0], 403)
        self.assertEqual(self.request('GET', '/', {'Sec-Fetch-Site': 'cross-site'})[0], 403)

    def test_foreign_host_and_origin_are_still_rejected(self):
        for extra in [{'Host': 'other.example'}, {'Origin': 'https://other.example'}]:
            with self.subTest(extra=extra):
                self.assertEqual(self.request('GET', '/', dict(NAVIGATION, **extra))[0], 403)
        with patch.object(self.auth, 'complete') as complete:
            headers = dict(NAVIGATION, Host='other.example')
            self.assertEqual(self.request('GET', '/auth/callback?state=test', headers)[0], 403)
            complete.assert_not_called()
