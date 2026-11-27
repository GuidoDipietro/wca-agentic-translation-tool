"""Native cross-process locks and local startup on Windows and POSIX."""
import http.client
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))

from filelock import exclusive_lock, release_lock
from openai_auth import ChatGPTAuth


# Each subprocess imports the repository module even if the environment also
# contains the unrelated PyPI package named filelock.
LOCK_PROCESS = textwrap.dedent('''\
    from pathlib import Path
    import sys
    sys.path.insert(0, sys.argv[1])
    from filelock import exclusive_lock
    with Path(sys.argv[2]).open('a') as stream:
        try:
            exclusive_lock(stream)
        except BlockingIOError:
            sys.exit(23)
        if len(sys.argv) > 3:
            Path(sys.argv[3]).write_text('locked', encoding='utf-8')
            sys.stdin.read()
''')


class FileLockTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.lock_path = self.root / '.lock'

    def run_command(self, command):
        return subprocess.run(command, capture_output=True, text=True,
                              encoding='utf-8', errors='replace', timeout=15)

    def try_lock(self, path=None):
        return self.run_command([sys.executable, '-c', LOCK_PROCESS,
                                 str(ROOT / 'app'), str(path or self.lock_path)])

    def stop_process(self, process):
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=15)

    def wait_until(self, process, ready):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                stdout, stderr = process.communicate(timeout=15)
                self.fail(f'Process exited before startup ({process.returncode}):\n{stdout}\n{stderr}')
            if ready():
                return
            time.sleep(.05)
        self.stop_process(process)
        stdout, stderr = process.communicate(timeout=15)
        self.fail(f'Process did not become ready:\n{stdout}\n{stderr}')

    def test_exclusive_lock_contends_and_explicit_unlock_releases(self):
        for content in ('', 'Existing lock-file contents\n'):
            with self.subTest(content=content):
                self.lock_path.write_text(content, encoding='utf-8')
                with self.lock_path.open('a') as stream:
                    exclusive_lock(stream)
                    result = self.try_lock()
                    self.assertEqual(result.returncode, 23, result.stdout + result.stderr)
                    release_lock(stream)
                    result = self.try_lock()
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_closing_file_releases_lock(self):
        with self.lock_path.open('a') as stream:
            exclusive_lock(stream)
            result = self.try_lock()
            self.assertEqual(result.returncode, 23, result.stdout + result.stderr)
        result = self.try_lock()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_locks_are_independent_between_data_directories(self):
        other = self.root / 'other'
        other.mkdir()
        with self.lock_path.open('a') as stream:
            exclusive_lock(stream)
            result = self.try_lock(other / '.lock')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_process_exit_and_force_kill_release_lock(self):
        for force_kill in (False, True):
            with self.subTest(force_kill=force_kill):
                ready = self.root / 'ready'
                ready.unlink(missing_ok=True)
                process = subprocess.Popen(
                    [sys.executable, '-c', LOCK_PROCESS, str(ROOT / 'app'),
                     str(self.lock_path), str(ready)],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding='utf-8', errors='replace')
                self.addCleanup(self.stop_process, process)
                self.wait_until(process, ready.exists)
                result = self.try_lock()
                self.assertEqual(result.returncode, 23, result.stdout + result.stderr)
                if force_kill:
                    process.kill()
                stdout, stderr = process.communicate(timeout=15)
                if not force_kill:
                    self.assertEqual(process.returncode, 0, stdout + stderr)
                result = self.try_lock()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def start_demo(self, data):
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        command = [sys.executable, str(ROOT / 'run.py'), '--demo',
                   '--data-dir', str(data), '--port', str(port)]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding='utf-8', errors='replace')
        self.addCleanup(self.stop_process, process)

        def ready():
            connection = http.client.HTTPConnection('127.0.0.1', port, timeout=.5)
            try:
                connection.request('GET', '/')
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, 200)
                return True
            except (OSError, http.client.HTTPException):
                return False
            finally:
                connection.close()

        self.wait_until(process, ready)
        return process, command

    def test_demo_server_and_glossary_import_share_lock_and_can_restart(self):
        data = self.root / 'data'
        candidate = self.root / 'candidate.json'
        candidate.write_text(json.dumps({
            'version': 1, 'language': 'es_419',
            'entries': [{'source': ['Cube'], 'preferred': ['Cubo'], 'forbidden': []}],
        }), encoding='utf-8')
        importer = [sys.executable, str(ROOT / 'app/glossary_store.py'),
                    str(candidate), '--language', 'es_419',
                    '--data-dir', str(data), '--import-file']
        server, command = self.start_demo(data)
        # The occupied port makes the lock error distinguishable from a bind
        # error: a second instance must reject the data folder before binding.
        second = self.run_command(command)
        self.assertNotEqual(second.returncode, 0)
        self.assertIn('Another instance is already using this data folder.', second.stderr)
        blocked = self.run_command(importer)
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn('Stop the server before importing a glossary.', blocked.stderr)

        self.stop_process(server)
        restarted, _ = self.start_demo(data)
        self.stop_process(restarted)
        imported = self.run_command(importer)
        self.assertEqual(imported.returncode, 0, imported.stdout + imported.stderr)
        self.assertIn('Imported: 1 terms', imported.stdout)

    def test_auth_file_can_be_saved_and_reopened(self):
        path = self.root / 'test-auth.json'
        auth = ChatGPTAuth(path)
        auth.save()
        reopened = ChatGPTAuth(path)
        self.assertEqual(reopened.data, auth.data)
        reopened.save()

    def test_auth_save_tolerates_unsupported_chmod(self):
        path = self.root / 'test-auth.json'
        auth = ChatGPTAuth(path)
        for error in (PermissionError('Permission bits unavailable'),
                      NotImplementedError('chmod unavailable')):
            with self.subTest(error=type(error).__name__):
                with patch.object(Path, 'chmod', side_effect=error):
                    auth.save()
                self.assertEqual(ChatGPTAuth(path).data, auth.data)


if __name__ == '__main__':
    unittest.main()
