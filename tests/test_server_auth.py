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
        for path in ['/app.js', '/style.css', '/api/state', '/api/connections']:
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
        for path in ['/api/state', '/api/connections', '/api/models', '/api/languages', '/app.js', '/style.css']:
            with self.subTest(path=path):
                status, _, body = self.request('GET', path, NAVIGATION)
                self.assertEqual(status, 403)
                self.assertEqual(json.loads(body)['error'], 'Solicitud externa bloqueada.')

    def test_external_posts_cannot_start_login_or_change_settings(self):
        for path in ['/api/chatgpt/start', '/api/connections', '/api/sync', '/api/dismiss-check', '/api/validate-export', '/api/export', '/']:
            with self.subTest(path=path), patch.object(self.auth, 'start') as start:
                status, _, _ = self.request('POST', path, dict(NAVIGATION, **{'Content-Type': 'application/json'}), '{}')
                self.assertEqual(status, 403)
                start.assert_not_called()
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
