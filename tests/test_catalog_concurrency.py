"""Las listas remotas no bloquean la lectura ni la escritura de revisiones."""
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from connections import Connections
from core import ReviewError
from server import make_handler
from weblate import Client
from workspace import Workspace,write_json
from test_locales import locale_download


class CatalogConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name)
        self.entered=threading.Event();self.release=threading.Event()
        self.client=Mock();self.client.env_path=root/'.env';self.client.download.return_value=locale_download()
        self.workspace=Workspace(root/'data',self.client);self.workspace.sync('locales')
        write_json(self.workspace.directory(self.workspace.active_id)/'proposals.json',{
            'version':1,'session_id':self.workspace.active_id,'base_sha256':self.workspace.work['base_sha256'],
            'entries':[{'key':item['key'],'target':['Texto revisado'],'note':''} for item in self.workspace.work['entries']]})
        self.workspace.load_proposals()
        self.translator=Mock()
        self.server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(self.workspace,Connections(root/'.env'),translator=self.translator))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.release.set();self.server.shutdown();self.server.server_close();self.thread.join(timeout=3)

    def wait_catalog(self,result):
        self.entered.set()
        if not self.release.wait(8):raise ReviewError('No se liberó el catálogo de prueba')
        return result

    def request(self,method,path,data=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=6)
        try:
            conn.request(method,path,body=json.dumps(data) if data is not None else None,
                         headers={'Content-Type':'application/json'} if data is not None else {})
            response=conn.getresponse();return response.status,json.loads(response.read())
        finally:conn.close()

    def exercise(self,path):
        result=[]
        worker=threading.Thread(target=lambda:result.append(self.request('GET',path)),daemon=True)
        worker.start()
        try:
            self.assertTrue(self.entered.wait(3))
            # Simula recargar mientras la pestaña anterior sigue buscando opciones.
            status,state=self.request('GET','/api/state');self.assertEqual(status,200,state)
            entry=state['entries'][0]
            status,accepted=self.request('POST','/api/decision',{
                'session_id':state['workspace']['active_id'],'workspace_revision':state['workspace']['revision'],
                'revision':state['revision'],'key':entry['key'],'decision':{'status':'accepted'}})
            self.assertEqual(status,200,accepted);self.assertEqual(accepted['counts']['accepted'],1)
            self.assertFalse(self.release.is_set(),'La decisión no debe esperar a la red')
            status,reloaded=self.request('GET','/api/state');self.assertEqual(status,200)
            self.assertEqual(reloaded['counts']['accepted'],1)
        finally:
            self.release.set();worker.join(timeout=5)
        self.assertFalse(worker.is_alive());self.assertEqual(result[0][0],200)

    def test_model_lookup_does_not_block_reload_or_first_approval(self):
        self.translator.models.side_effect=lambda provider:self.wait_catalog([{'id':'test-model','name':'Test'}])
        self.exercise('/api/models?provider=chatgpt')

    def test_language_lookup_does_not_block_reload_or_first_approval(self):
        self.client.translations.side_effect=lambda component:self.wait_catalog([{'code':'es_419','name':'Spanish','total':4,'translated':2}])
        self.exercise('/api/languages?component=locales')

    def test_project_summary_does_not_block_review(self):
        self.workspace.client=Client(Path(self.tmp.name)/'.env')
        result={'language':'es_419','total':10,'pending':5,'outside_supported':3}
        with patch.object(Client,'project_status',side_effect=lambda language:self.wait_catalog(result)):
            self.exercise('/api/project-status?language=es_419')
