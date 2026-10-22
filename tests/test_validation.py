import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from core import Review, ReviewError, Catalog, forms
from locales import convert_units, yaml_load
from workspace import Workspace, write_json
from test_review import fixture
from test_workspace import downloads


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.original=self.root/'base.po';self.proposed=self.root/'proposal.po'
        self.original.write_text(fixture());self.proposed.write_text(fixture('Buenas %s',{0:'Uno',1:'Muchos'}))
        self.review=Review(self.original,self.proposed,self.root/'review',source_checks=True)
        self.singular,self.plural=self.review.changed

    def decide(self,key,status='edited',values=None):
        decision={'status':status}
        if values is not None:decision['values']=values
        self.review.decide(key,decision,self.review.document['revision'])

    def report(self):return self.review.validate_export(self.review.document['revision'])

    def dismiss(self,issue,dismissed=True):
        self.review.dismiss_check(issue['id'],self.review.document['revision'],dismissed)

    def test_validation_does_not_write_an_export(self):
        self.decide(self.singular,'accepted')
        self.assertTrue(self.report()['ok'])
        self.assertFalse(list((self.root/'review').glob('*_reviewed.po')))

    def test_preflight_does_not_parse_or_serialize_the_whole_catalog(self):
        self.decide(self.singular,'accepted')
        with patch.object(Catalog,'read',side_effect=AssertionError('No volver a parsear')), patch.object(Catalog,'render',side_effect=AssertionError('No serializar en preflight')):
            self.assertTrue(self.report()['ok'])
        with patch.object(self.review.original,'render',wraps=self.review.original.render) as render:
            result=self.review.export(self.review.document['revision'],mode='changes',confirm_pending=True)
            self.assertTrue(result['ok']);render.assert_called_once()

    def test_fast_preflight_still_detects_same_size_input_changes(self):
        self.decide(self.singular,'accepted')
        original=self.original.read_text()
        modified=original.replace('Hola %s','Chau %s')
        self.assertEqual(len(original),len(modified));self.original.write_text(modified)
        with self.assertRaises(ReviewError):self.report()

    def test_dismissal_is_explicit_persistent_and_audited(self):
        self.decide(self.singular,values=['Hola sin variable'])
        issue=self.report()['errors'][0]
        self.assertEqual(issue['code'],'placeholders');self.dismiss(issue)
        self.review=Review(self.original,self.proposed,self.root/'review',source_checks=True)
        report=self.report();self.assertTrue(report['ok']);self.assertEqual(len(report['dismissed']),1)
        result=self.review.export(self.review.document['revision'],mode='changes',confirm_pending=True)
        self.assertTrue(result['ok']);self.assertEqual(result['dismissed'][0]['id'],issue['id'])
        summary=json.loads((self.root/'review/resumen_exportacion.json').read_text())
        self.assertIn('dismissed_at',summary['dismissed'][0])

    def test_editing_the_translation_invalidates_the_old_dismissal(self):
        self.decide(self.singular,values=['Hola sin variable'])
        issue=self.report()['errors'][0];self.dismiss(issue)
        self.decide(self.singular,values=['Otro texto sin variable'])
        self.assertFalse(self.report()['ok']);self.assertFalse(self.report()['dismissed'])
        with self.assertRaises(ReviewError):self.dismiss(issue)

    def test_dismissal_can_be_restored(self):
        self.decide(self.singular,values=['Hola sin variable'])
        issue=self.report()['errors'][0];self.dismiss(issue);self.dismiss(issue,False)
        self.assertEqual(self.report()['errors'][0]['id'],issue['id'])

    def test_only_the_selected_plural_form_is_dismissed(self):
        self.decide(self.plural,values=['Un cubo {extra}','Muchos cubos {extra}'])
        issues=self.report()['errors'];self.assertEqual(len(issues),2)
        self.dismiss(issues[0])
        report=self.report();self.assertEqual(len(report['errors']),1)
        self.assertNotEqual(report['errors'][0]['form'],report['dismissed'][0]['form'])

    def test_empty_translation_and_structure_errors_cannot_be_dismissed(self):
        for target,code in [('', 'empty'),('Hola %s\x01','characters')]:
            self.decide(self.singular,values=[target])
            issue=next(item for item in self.report()['errors'] if item['code']==code)
            self.assertFalse(issue['dismissible'])
            with self.assertRaises(ReviewError):self.dismiss(issue)

    def test_unknown_checks_and_stale_revisions_are_rejected(self):
        self.decide(self.singular,values=['Hola sin variable']);issue=self.report()['errors'][0]
        with self.assertRaises(ReviewError):self.review.dismiss_check(issue['id'],0)
        with self.assertRaises(ReviewError):self.review.dismiss_check('inventado',self.review.document['revision'])

    def test_unresolved_recent_conflicts_cannot_be_dismissed(self):
        self.decide(self.singular,'accepted')
        self.review.set_recent(fixture('Cambio remoto %s'),self.review.document['revision'])
        issue=next(item for item in self.report()['errors'] if item['code']=='conflict')
        with self.assertRaises(ReviewError):self.dismiss(issue)

    def test_po_changes_only_include_approved_entries(self):
        self.decide(self.singular,'accepted');self.decide(self.plural,'rejected')
        result=self.review.export(self.review.document['revision'],mode='changes')
        self.assertTrue(result['ok'])
        catalog=Catalog(result['content']);self.assertEqual(set(catalog.by_key),{self.singular})
        self.assertEqual(forms(catalog.by_key[self.singular]),['Buenas %s'])
        self.assertNotIn('fuzzy',catalog.by_key[self.singular].flags)
        self.assertEqual(catalog.po.metadata,self.review.original.po.metadata)

    def test_no_approved_changes_does_not_create_an_empty_download(self):
        self.assertFalse(self.report()['ok'])
        self.assertEqual(self.report()['errors'][0]['code'],'no_changes')

    def test_yaml_partial_preserves_numeric_maps_and_list_positions(self):
        native='es-419:\n  events:\n    "333": ""\n  days:\n    - ""\n    - Sin tocar\n  stable: Sin tocar\n'
        units=[{'context':'events->333','source':['Cube'],'target':['']},
               {'context':'days->0','source':['First'],'target':['']},
               {'context':'days->1','source':['Second'],'target':['Sin tocar']},
               {'context':'stable','source':['Stable'],'target':['Sin tocar']}]
        english,target,mapping=convert_units(units,native)
        client=Mock();client.env_path=self.root/'.env'
        client.download.return_value=dict(downloads(target,english),native_yaml=native,yaml_mapping=mapping)
        workspace=Workspace(self.root/'workspace',client);workspace.sync('locales')
        folder=workspace.directory(workspace.active_id)
        write_json(folder/'proposals.json',{'version':1,'session_id':workspace.active_id,'base_sha256':workspace.work['base_sha256'],'entries':[{'key':item['key'],'target':['Primero' if item['context']=='days->0' else '3x3x3'],'note':''} for item in workspace.work['entries']]})
        workspace.load_proposals();review=workspace.review
        for key in review.changed:review.decide(key,{'status':'accepted'},review.document['revision'])
        result=review.export(review.document['revision'],mode='changes')
        self.assertTrue(result['ok']);self.assertEqual(result['filename'],'es-419_reviewed.yml')
        _,data,lang=yaml_load(result['content'])
        self.assertEqual(data[lang]['events']['333'],'3x3x3')
        self.assertEqual(data[lang]['days'],['Primero','Sin tocar'])
        self.assertNotIn('stable',data[lang])
