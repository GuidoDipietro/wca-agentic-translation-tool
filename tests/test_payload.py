import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from core import ReviewError
from payload import convert_units, json_load
from weblate import Client
from workspace import Workspace, write_json
from demo import DemoClient
from simulation import SimulationClient


def download():
    native = json.dumps({'pages#abc:title': {'message': '', 'description': 'Keep this'}, 'media#xyz:alt': {'message': 'Imagen'}}, indent=4)
    units = [{'context': 'pages#abc:title', 'source': ['Welcome'], 'target': ['']}, {'context': 'media#xyz:alt', 'source': ['Image'], 'target': ['Imagen']}]
    english, target, mapping = convert_units(units, native)
    return {'english': english, 'target': target, 'native_json': native, 'json_mapping': mapping, 'metadata': {'en': {'revision': 'a', 'total': 2}, 'es_419': {'revision': 'b'}}, 'warnings': []}, units


class PayloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.client = Mock(env_path=self.root / '.env')
        self.client.download.return_value = download()[0]
        self.w = Workspace(self.root / 'data', self.client)
        self.w.sync('payload')

    def proposals(self, value='Bienvenidos'):
        work = self.w.work
        write_json(self.w.directory(self.w.active_id) / 'proposals.json', {'version': 1, 'session_id': self.w.active_id, 'base_sha256': work['base_sha256'], 'entries': [{'key': e['key'], 'target': [value], 'note': ''} for e in work['entries']]})
        self.w.load_proposals()
        return self.w.review, work['entries'][0]['key']

    def test_workspace_exports_only_approved_json_and_preserves_metadata(self):
        self.assertEqual(self.w.work['output_format'], 'json')
        self.assertEqual(self.w.snapshot()['workspace']['output_format'], 'json')
        r, k = self.proposals()
        self.assertFalse(r.export(0, mode='changes')['ok'])
        r.decide(k, {'status': 'accepted'}, 0)
        result = r.export(1, mode='changes')
        self.assertTrue(result['ok'], result)
        self.assertEqual(json_load(result['content']), {'pages#abc:title': {'message': 'Bienvenidos', 'description': 'Keep this'}})
        self.assertEqual(result['filename'], 'es-419_payload_reviewed.json')
        restored = Workspace(self.root / 'data', self.client)
        self.assertEqual(restored.review.export(1, mode='changes')['content'], result['content'])

    def test_pending_full_export_preserves_original_bytes(self):
        r, _ = self.proposals()
        self.assertEqual(r.export(0, confirm_pending=True)['content'], download()[0]['native_json'])

    def test_recent_conflicts_block_export_and_extra_keys_are_preserved(self):
        r, k = self.proposals()
        r.decide(k, {'status': 'accepted'}, 0)
        data = json_load(download()[0]['native_json'])
        data['pages#abc:title']['message'] = 'Cambio remoto'
        data['extra'] = {'message': 'Conservar'}
        r.set_recent(json.dumps(data), 1)
        self.assertFalse(r.export(2)['ok'])
        r.decide(k, {'status': 'accepted', 'resolution': 'chosen'}, 2)
        result = r.export(3)
        self.assertTrue(result['ok'], result)
        self.assertEqual(json_load(result['content'])['extra'], data['extra'])
        with self.assertRaises(ReviewError):
            r.set_recent('{}', 3)

    def test_duplicate_keys_and_malformed_messages_are_rejected(self):
        for text in ['{"x":{"message":""},"x":{"message":""}}', '{"x":{"message":5}}', '[]']:
            with self.assertRaises(ReviewError): json_load(text)

    def test_conversion_rejects_mismatch_duplicate_context_and_plural(self):
        payload, units = download()
        variants = [units + [units[0]], [], [dict(units[0], target=['Diferente']), units[1]], [dict(units[0], source=['One', 'Many']), units[1]]]
        for value in variants:
            with self.assertRaises(ReviewError): convert_units(value, payload['native_json'])

    def test_download_uses_payload_paths_and_units_without_regulations(self):
        payload, units = download()
        client = Client(self.root / '.env', component='payload')
        client.metadata = Mock(side_effect=lambda code: payload['metadata'][code])
        client.units = Mock(return_value=units)
        client.get = Mock(side_effect=lambda path: payload['native_json'] if '/payload/' in path else '<martif/>')
        result = client.download()
        self.assertEqual(result['native_json'], payload['native_json'])
        self.assertEqual(result['json_mapping'], payload['json_mapping'])
        self.assertFalse(any('/regulations/' in c.args[0] for c in client.get.call_args_list))

    def test_demo_and_simulation_keep_native_json_in_sync(self):
        demo = DemoClient(delay=0)
        demo.component = 'payload'
        result = demo.download()
        self.assertTrue(result['demo'])
        self.assertEqual(len(json_load(result['native_json'])), 3)
        client = SimulationClient(self.root / '.env', component='payload')
        with patch.object(Client, 'download', return_value=download()[0]):
            simulated = client.download()
        self.assertEqual(json_load(simulated['native_json'])['media#xyz:alt']['message'], '')
        self.assertEqual(simulated['remote_json'], download()[0]['native_json'])

    def test_modified_base_json_is_rejected(self):
        r, k = self.proposals()
        r.native_path.write_text('{}')
        with self.assertRaises(ReviewError): r.export(0, confirm_pending=True)

    def test_new_untranslated_key_missing_from_json_can_be_reviewed_and_exported(self):
        payload, units = download()
        context = 'documents#6ae0511b319a6ff3dbd63ba0:title'
        units.append({'context': context, 'source': ['New document'], 'target': [''], 'state': 0})
        english, target, mapping = convert_units(units, payload['native_json'])
        payload.update(english=english, target=target, json_mapping=mapping)
        self.client.download.return_value = payload
        state = self.w.sync('payload')
        self.assertEqual(state['workspace']['candidate_count'], 2)
        r, _ = self.proposals()
        # Full export preserves the omission while the entry is pending.
        self.assertEqual(r.export(0, confirm_pending=True)['content'], payload['native_json'])
        r.set_recent(payload['native_json'], 0)
        item = next(e for e in self.w.work['entries'] if e['context'] == context)
        r.decide(item['key'], {'status': 'accepted'}, 1)
        result = r.export(2, confirm_pending=True, mode='changes')
        self.assertTrue(result['ok'], result)
        self.assertEqual(json_load(result['content']), {context: {'message': 'Bienvenidos'}})
        restored = Workspace(self.root / 'data', self.client)
        self.assertEqual(restored.review.export(2, confirm_pending=True, mode='changes')['content'], result['content'])

    def test_missing_nonempty_translation_is_still_rejected(self):
        payload, units = download()
        units.append({'context': 'missing', 'source': ['New document'], 'target': ['Documento nuevo']})
        with self.assertRaisesRegex(ReviewError, 'key is missing'):
            convert_units(units, payload['native_json'])

    def test_existing_translation_difference_is_reported_separately(self):
        payload, units = download()
        units[1]['target'] = ['Otro texto']
        with self.assertRaisesRegex(ReviewError, 'different translations'):
            convert_units(units, payload['native_json'])

    def test_real_download_accepts_missing_empty_key_and_keeps_native_file(self):
        payload, units = download()
        units.append({'context': 'documents#new:title', 'source': ['New title'], 'target': ['']})
        metadata = copy.deepcopy(payload['metadata'])
        metadata['en']['total'] = 3
        client = Client(self.root / '.env', component='payload')
        client.metadata = Mock(side_effect=lambda code: metadata[code])
        client.units = Mock(return_value=units)
        client.get = Mock(side_effect=lambda path: payload['native_json'] if '/payload/' in path else '<martif/>')
        result = client.download()
        self.assertEqual(result['native_json'], payload['native_json'])
        self.assertEqual(len(result['json_mapping']), 3)

    def test_extra_json_keys_are_preserved_but_never_proposed_or_exported_as_changes(self):
        payload, units = download()
        data = json_load(payload['native_json'])
        data['obsolete#id:title'] = {'message': 'Conservar intacto', 'description': 'Metadatos originales'}
        payload['native_json'] = json.dumps(data, indent=4)
        english, target, mapping = convert_units(units, payload['native_json'])
        payload.update(english=english, target=target, json_mapping=mapping)
        self.client.download.return_value = payload
        self.w.sync('payload')
        self.assertNotIn('obsolete#id:title', [e['context'] for e in self.w.work['entries']])
        r, k = self.proposals()
        self.assertEqual(r.export(0, confirm_pending=True)['content'], payload['native_json'])
        r.decide(k, {'status': 'accepted'}, 0)
        full = r.export(1)
        self.assertTrue(full['ok'], full)
        self.assertEqual(json_load(full['content'])['obsolete#id:title'], data['obsolete#id:title'])
        changes = r.export(1, mode='changes')
        self.assertTrue(changes['ok'], changes)
        self.assertEqual(set(json_load(changes['content'])), {'pages#abc:title'})

    def test_download_warns_about_extra_json_keys(self):
        payload, units = download()
        data = json_load(payload['native_json'])
        data['extra'] = {'message': ''}
        native = json.dumps(data)
        client = Client(self.root / '.env', component='payload')
        client.metadata = Mock(side_effect=lambda code: payload['metadata'][code])
        client.units = Mock(return_value=units)
        client.get = Mock(side_effect=lambda path: native if '/payload/' in path else '<martif/>')
        result = client.download()
        self.assertEqual(result['native_json'], native)
        self.assertTrue(any('1 keys' in warning for warning in result['warnings']))
