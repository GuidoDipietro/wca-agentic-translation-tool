import copy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from core import ReviewError
from simulation import SimulationClient
from workspace import Workspace, write_json
from test_locales import locale_download


class SimulationTests(unittest.TestCase):
    def test_simulation_is_visible_before_the_first_sync(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace=Workspace(Path(tmp)/'simulation',SimulationClient(Path(tmp)/'.env'))
            state=workspace.snapshot()
            self.assertTrue(state['workspace']['simulation'])
            self.assertIsNone(state['workspace']['active_id'])
            self.assertFalse(state['workspace']['demo'])

    def test_real_snapshot_is_only_changed_in_isolated_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            original=locale_download(pending=False)
            before=copy.deepcopy(original)
            client=SimulationClient(Path(tmp)/'.env')
            w=Workspace(Path(tmp)/'simulation',client)
            with patch('weblate.Client.download',return_value=original):state=w.sync('locales')
            self.assertEqual(original,before)
            self.assertTrue(state['workspace']['simulation']);self.assertFalse(state['workspace']['demo'])
            self.assertEqual(state['workspace']['candidate_count'],3)
            folder=w.directory(w.active_id)
            self.assertEqual((folder/'remote_reference.yml').read_text(),original['native_yaml'])
            self.assertNotEqual((folder/'downloaded.yml').read_text(),original['native_yaml'])
            self.assertTrue(all(e.get('simulation_reference') for e in state['workspace'] and w.work['entries']))
            payload={'version':1,'session_id':w.active_id,'base_sha256':w.work['base_sha256'],'entries':[{'key':item['key'],'target':item['simulation_reference'],'note':'Reconstrucción de prueba'} for item in w.work['entries']]}
            write_json(folder/'proposals.json',payload)
            self.assertEqual(w.proposal_checks(),([],[]))
            command=[sys.executable,str(Path(__file__).resolve().parents[1]/'run.py'),'validate-proposals',w.active_id,'--data-dir',str(w.data_dir),'--simulate']
            result=subprocess.run(command,capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            w.load_proposals();r=w.review
            for k in r.changed:r.decide(k,{'status':'accepted'},r.document['revision'])
            output=r.export(r.document['revision'])
            self.assertTrue(output['ok']);self.assertTrue(output['filename'].startswith('SIMULATION_'))
            self.assertIn('SIMULATION',output['content'])
            with self.assertRaises(ReviewError):Workspace(w.data_dir)
            restored=Workspace(w.data_dir,SimulationClient(Path(tmp)/'.env'))
            self.assertEqual(restored.snapshot()['counts']['accepted'],3)
