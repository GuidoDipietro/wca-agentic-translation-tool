import http.client
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from core import ReviewError
from demo import DemoClient
from server import make_handler
from workspace import Workspace
from weblate import Client


class DemoTests(unittest.TestCase):
    def test_demo_workflow_has_no_network_or_credentials(self):
        with tempfile.TemporaryDirectory() as tmp, patch('workspace.load_token',side_effect=AssertionError('No leer credenciales')), patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('No usar la red')):
            w=Workspace(tmp,DemoClient(delay=0));events=[]
            state=w.sync('locales',progress=events.append)
            self.assertTrue(state['workspace']['demo']);self.assertFalse(state['workspace']['token_configured'])
            self.assertEqual(state['workspace']['candidate_count'],3)
            self.assertEqual([e['stage'] for e in events],[1,2,3,4,5,6,7])
            self.assertIn('DEMO',state['workspace']['prompt'])
            self.assertEqual(w.proposal_checks(),([],[]))
            w.load_proposals();r=w.review
            for k in r.changed:r.decide(k,{'status':'accepted'},r.document['revision'])
            for mode in ['full','suggestions']:
                result=r.export(r.document['revision'],mode=mode)
                self.assertTrue(result['ok']);self.assertTrue(result['filename'].startswith('DEMO_'))
                self.assertIn('DEMO',result['content']);self.assertNotIn('fuzzy',result['content'])
            self.assertEqual(Workspace(tmp,DemoClient(delay=0)).snapshot()['counts']['accepted'],3)
            with self.assertRaises(ReviewError):Workspace(tmp)

    def test_agent_can_validate_demo_from_cli_without_altering_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            w=Workspace(tmp,DemoClient(delay=0));state=w.sync('locales')
            self.assertIn('--demo',state['workspace']['prompt'])
            record=(Path(tmp)/'workspace.json').read_bytes()
            result=subprocess.run([sys.executable,str(Path(__file__).resolve().parents[1]/'run.py'),'validate-proposals',w.active_id,'--data-dir',tmp,'--demo'],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertEqual((Path(tmp)/'workspace.json').read_bytes(),record)

    def test_regulations_demo_has_safe_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            w=Workspace(tmp,DemoClient(delay=0));w.sync('regulations');w.load_proposals();r=w.review
            for k in r.changed:r.decide(k,{'status':'accepted'},r.document['revision'])
            self.assertTrue(r.export(r.document['revision'])['filename'].startswith('DEMO_'))

    def test_client_pagination_emits_progress(self):
        c=Client('/nonexistent');events=[];c.progress=events.append
        with patch.object(c,'get',return_value=json.dumps({'results':[{'id':1}],'count':1,'next':None})):
            c.units()
        self.assertEqual(events[0]['stage'],2);self.assertIn('1 / 1',events[0]['message'])


class StreamingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.entered=threading.Event();self.release=threading.Event();self.addCleanup(self.release.set)
        entered,release=self.entered,self.release
        class SlowClient(DemoClient):
            def download(self):
                self.progress({'stage':2,'total':7,'message':'Página de prueba en curso'})
                entered.set()
                if not release.wait(timeout=8):raise ReviewError('La prueba no liberó la descarga')
                return super().download()
        self.workspace=Workspace(self.tmp.name,SlowClient(delay=0))
        self.server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(self.workspace))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        def cleanup():
            self.release.set();self.server.shutdown();self.server.server_close();self.thread.join(timeout=3)
        self.addCleanup(cleanup)

    def connect(self):
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
        self.addCleanup(connection.close)
        return connection

    def start(self):
        conn=self.connect()
        conn.request('POST','/api/sync',json.dumps({'component':'locales','session_id':None,'workspace_revision':0}),{'Content-Type':'application/json'})
        resp=conn.getresponse();self.assertEqual(resp.status,200)
        self.assertIn('ndjson',resp.getheader('Content-Type'))
        self.assertEqual(json.loads(resp.readline())['stage'],0)
        self.assertTrue(self.entered.wait(3))
        event=json.loads(resp.readline());self.assertEqual(event['message'],'Página de prueba en curso')
        return resp

    def test_events_arrive_before_download_finishes_and_server_stays_responsive(self):
        resp=self.start();self.assertFalse(self.release.is_set())
        conn=self.connect();conn.request('GET','/');static=conn.getresponse();self.assertEqual(static.status,200);static.read()
        conn.request('GET','/api/state');state=conn.getresponse();self.assertEqual(state.status,200);self.assertIsNone(json.loads(state.read())['workspace']['active_id'])
        conn.request('POST','/api/sync','{}',{'Content-Type':'application/json'});busy=conn.getresponse();self.assertEqual(busy.status,409);busy.read()
        self.release.set();events=[json.loads(line) for line in resp.read().splitlines()]
        self.assertEqual(events[-2]['stage'],7);self.assertTrue(events[-1]['result']['workspace']['demo'])

    def test_error_is_streamed_and_previous_state_is_kept(self):
        with patch.object(self.workspace.client,'download',side_effect=ReviewError('Fallo de descarga controlado')):
            conn=self.connect();conn.request('POST','/api/sync',json.dumps({'session_id':None,'workspace_revision':0}),{'Content-Type':'application/json'})
            resp=conn.getresponse();events=[json.loads(line) for line in resp.read().splitlines()]
            self.assertEqual(events[-1]['error'],'Fallo de descarga controlado')
            self.assertIsNone(events[-1]['recovery']['workspace']['active_id'])
            self.assertIsNone(self.workspace.active_id)
