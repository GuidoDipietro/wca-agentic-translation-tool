"""Optional local Chrome test; does not install or download browsers."""
import os
import json
from http.server import BaseHTTPRequestHandler
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer

from test_review import fixture
from test_workspace import downloads
from test_locales import locale_download
from unittest.mock import Mock
from workspace import Workspace
from demo import DemoClient
from core import Review, ReviewError
from server import make_handler
from connections import Connections
from locales import convert_units


@unittest.skipUnless(os.environ.get('RUN_BROWSER_TESTS') == '1', 'Optional: RUN_BROWSER_TESTS=1, Chrome and Node >=22')
class BrowserTests(unittest.TestCase):
    def test_local_review_in_chrome(self):
        self.run_browser(False)

    def test_workspace_sync_and_agent_workflow(self):
        self.run_browser(True)

    def test_locales_yaml_workflow(self):
        self.run_browser('locales')

    def test_payload_json_workflow(self):
        self.run_browser('payload')

    def test_interface_language_switch_preserves_work(self):
        self.run_browser('i18n')

    def test_glossary_configuration(self):
        self.run_browser('glossary')

    def test_demo_stages_and_24_hour_times(self):
        self.run_browser('demo')

    def test_automatic_multilingual_flow(self):
        self.run_browser('automatic')

    def test_chatgpt_cross_site_callback_in_chrome(self):
        self.run_browser('oauth')

    def test_stale_draft_recovery_and_session_isolation(self):
        self.run_browser('recovery')

    def run_browser(self, workspace_mode):
        chrome=shutil.which('google-chrome') or shutil.which('chromium')
        node=shutil.which('node')
        if not chrome or not node:
            self.skipTest('Chrome or Node unavailable')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            original=root/'original.po'; proposed=root/'propuesto.po'
            original.write_text(fixture(),encoding='utf-8')
            proposed.write_text(fixture('Buenas %s',{0:'Un rompecabezas',1:'Muchos rompecabezas'}),encoding='utf-8')
            fake_translator=None
            auth=None
            provider=None
            provider_thread=None
            connections=Connections(root/'.env',demo=workspace_mode in ('demo','i18n','recovery','glossary'))
            if workspace_mode=='automatic':
                native='fr:\n  stable: Déjà traduit\n  greeting: ""\n  count:\n    one: ""\n    many: ""\n    other: ""\n'
                units=[{'context':'stable','source':['Already translated'],'target':['Déjà traduit']},{'context':'greeting','source':['Hello, %{name}'],'target':['']},{'context':'count','source':['One cube','%{count} cubes'],'target':['','','']}]
                english,target,mapping=convert_units(units,native,'fr')
                client=Mock();client.env_path=root/'.env';client.download.return_value=dict(downloads(target,english),native_yaml=native,yaml_mapping=mapping)
                client.translations.return_value=[{'code':'fr','name':'French','total':3,'translated':1},{'code':'es_419','name':'Spanish','total':3,'translated':3}]
                controller=Workspace(root/'data',client)
                connections.configure({'WEBLATE_TOKEN':'fake-test-key'})
                auth=Mock();auth.status.return_value={'active':'test-account','accounts':[{'id':'test-account','email':'test@example.org','connected':True,'can_translate':True}],'error':None}
                fake_translator=Mock();fake_translator.models.return_value=[{'id':'test-model','name':'Test model'}]
                fake_translator.propose.side_effect=lambda provider,model,context,progress=None: {'entries':[{'key':e['key'],'target':['Bonjour, %{name}'] if e['context']=='greeting' else ['Un cube','%{count} cubes','%{count} cubes'],'note':'French test'} for e in context['requested_entries']]}
            elif workspace_mode=='oauth':
                client=Mock();client.env_path=root/'.env'
                controller=Workspace(root/'data',client)
                auth=Mock()
                auth.status.return_value={'active':None,'accounts':[],'error':None}
                def complete(query):
                    self.assertEqual(query,{'state':'browser-state','client_id':'browser-client','code':'browser-code'})
                    auth.status.return_value={'active':'browser-account','accounts':[{'id':'browser-account','email':'test@example.org','connected':True,'can_translate':True}],'error':None}
                auth.complete.side_effect=complete
                def disconnect():
                    auth.status.return_value={'active':'browser-account','accounts':[{'id':'browser-account','email':'test@example.org','connected':False,'can_translate':False}],'error':None}
                auth.disconnect.side_effect=disconnect
                fake_translator=Mock();fake_translator.models.return_value=[{'id':'test-model','name':'Test model'}]
            elif workspace_mode in ('demo','i18n','recovery','glossary'):
                controller=Workspace(root/'data',DemoClient())
                if workspace_mode == 'glossary':
                    from glossary_store import GlossaryStore
                    defaults = root / 'defaults'
                    defaults.mkdir()
                    (defaults / 'es_419.json').write_text(json.dumps({'version': 1, 'language': 'es_419', 'entries': [
                        {'source': ['Trainee Delegate', 'Trainee Delegates'], 'preferred': ['Referencia inicial'], 'forbidden': []},
                        {'source': ['Cube'], 'preferred': ['Cubo'], 'forbidden': []},
                    ]}))
                    controller.glossaries = GlossaryStore(root / 'data', defaults)
            elif workspace_mode:
                text=fixture().replace('#, fuzzy\n','')
                new=fixture()+'\nmsgctxt \"new\"\nmsgid \"New key\"\nmsgstr \"\"\n'
                client=Mock();client.env_path=root/'.env'
                client.download.side_effect=([locale_download(pending=False),locale_download(),locale_download()] if workspace_mode=='locales' else [downloads(text,text),downloads(new,new),downloads(new,new)]) + [ReviewError('Test synchronization failure'),locale_download() if workspace_mode=='locales' else downloads(new,new)]
                if workspace_mode == 'payload':
                    from payload import convert_units as convert_payload
                    def payload_data(pending):
                        units = [{'context': 'stable', 'source': ['Untouched'], 'target': ['Sin tocar']}, {'context': 'greeting', 'source': ['Hello %s'], 'target': ['Hola %s'], 'fuzzy': pending}]
                        if pending:
                            units.append({'context': 'new', 'source': ['New key'], 'target': ['']})
                        native = json.dumps({u['context']: {'message': u['target'][0]} for u in units})
                        english, target, mapping = convert_payload(units, native)
                        return dict(downloads(target, english), native_json=native, json_mapping=mapping)
                    client.download.side_effect = [payload_data(False), payload_data(True), payload_data(True), ReviewError('Test synchronization failure'), payload_data(True)]
                client.translations.return_value=[{'code':'es_419','name':'Spanish','total':3,'translated':1}]
                controller=Workspace(root/'data',client)
            else:
                controller=Review(original,proposed,root/'data')
            server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(controller,connections=connections,auth=auth,translator=fake_translator))
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            if workspace_mode=='oauth':
                class ProviderHandler(BaseHTTPRequestHandler):
                    def do_GET(self):
                        if self.path.startswith('/authorize'):
                            body=b'<html><body><a id="provider-continue" href="/continue">Confirm test connection</a></body></html>'
                            self.send_response(200)
                            self.send_header('Content-Type','text/html; charset=utf-8')
                            self.send_header('Content-Length',str(len(body)))
                            self.end_headers();self.wfile.write(body)
                        elif self.path=='/continue':
                            self.send_response(303)
                            self.send_header('Location',f'http://127.0.0.1:{server.server_port}/auth/callback?state=browser-state&client_id=browser-client&code=browser-code')
                            self.send_header('Content-Length','0');self.end_headers()
                        else:
                            self.send_response(404);self.end_headers()
                    def log_message(self,*args):pass
                provider=ThreadingHTTPServer(('127.0.0.1',0),ProviderHandler)
                provider_thread=threading.Thread(target=provider.serve_forever,daemon=True);provider_thread.start()
                auth.start.return_value=f'http://localhost:{provider.server_port}/authorize'
            profile=root/'chrome'
            proc=subprocess.Popen([chrome,'--headless=new','--no-sandbox','--disable-gpu','--disable-dev-shm-usage','--no-first-run','--disable-background-networking','--disable-component-update','--remote-debugging-port=0',f'--user-data-dir={profile}','about:blank'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                port_file=profile/'DevToolsActivePort'
                for _ in range(150):
                    if port_file.exists():break
                    if proc.poll() is not None: self.fail('Chrome closed during startup')
                    time.sleep(.1)
                self.assertTrue(port_file.exists(),'Chrome did not open DevTools')
                debug_port=int(port_file.read_text().splitlines()[0])
                result=subprocess.run([node,str(Path(__file__).with_name('glossary_smoke.mjs' if workspace_mode=='glossary' else 'recovery_smoke.mjs' if workspace_mode=='recovery' else 'i18n_smoke.mjs' if workspace_mode=='i18n' else 'oauth_smoke.mjs' if workspace_mode=='oauth' else 'automatic_smoke.mjs' if workspace_mode=='automatic' else 'demo_smoke.mjs' if workspace_mode=='demo' else 'workspace_smoke.mjs' if workspace_mode else 'browser_smoke.mjs')),f'http://127.0.0.1:{server.server_port}',f'http://127.0.0.1:{debug_port}', workspace_mode if workspace_mode in ('locales', 'payload') else 'regulations'],text=True,capture_output=True,timeout=50)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                print(result.stdout.strip())
                if workspace_mode=='oauth':
                    self.assertEqual([call.args for call in auth.start.call_args_list],[(server.server_port,None),(server.server_port,'browser-account'),(server.server_port,None)])
                    self.assertEqual(auth.complete.call_count,3)
                    for call in auth.complete.call_args_list:
                        self.assertEqual(call.args,({'state':'browser-state','client_id':'browser-client','code':'browser-code'},))
            finally:
                proc.terminate()
                try:proc.wait(timeout=8)
                except subprocess.TimeoutExpired:proc.kill();proc.wait()
                if provider:
                    provider.shutdown();provider.server_close();provider_thread.join(timeout=3)
                server.shutdown();server.server_close();thread.join(timeout=3)
