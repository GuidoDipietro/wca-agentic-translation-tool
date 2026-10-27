import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from core import Catalog, ReviewError
from locales import convert_units, yaml_load
from workspace import Workspace, write_json
from test_workspace import downloads


def locale_download(pending=True, plural_pending=False):
    native='''# conservar este comentario
es-419:
  stable: 'Sin tocar'
  count:
    one: Un cubo
    many: Varios cubos
    other: Otros cubos
'''
    if not pending:
        native+='  notifications:\n    copied: Copiado al portapapeles\n  delegates_page:\n    copy_email: Copiar correo\n'
    units=[{'context':'stable','source':['Untouched'],'target':['Sin tocar']}, {'context':'count','source':['One cube','Many cubes'],'target':['Un cubo','Varios cubos','Otros cubos'],'fuzzy':plural_pending}, {'context':'notifications->copied','source':['Copied to clipboard'],'target':['' if pending else 'Copiado al portapapeles']}, {'context':'delegates_page->copy_email','source':['Copy email'],'target':['' if pending else 'Copiar correo']}]
    english,target,mapping=convert_units(units,native)
    return dict(downloads(target,english),native_yaml=native,yaml_mapping=mapping)


class LocalesTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.client=Mock();self.client.env_path=self.root/'.env'
        self.w=Workspace(self.root/'data',self.client)

    def sync(self, data=None):
        self.client.download.return_value=data or locale_download()
        return self.w.sync('locales')

    def load(self):
        payload={'version':1,'session_id':self.w.active_id,'base_sha256':self.w.work['base_sha256'],'entries':[{'key':e['key'],'target':['Revisado uno','Revisado muchos','Revisado otros'] if e['plural_forms'] else ['Texto revisado'],'note':'Comprobado'} for e in self.w.work['entries']]}
        write_json(self.w.directory(self.w.active_id)/'proposals.json',payload)
        return self.w.load_proposals()

    def test_detects_missing_keys_and_preserves_three_plural_forms(self):
        state=self.sync()
        self.assertEqual(state['workspace']['component'],'locales')
        self.assertEqual(state['workspace']['candidate_count'],2)
        cat=Catalog.read(self.w.directory(self.w.active_id)/'base.po')
        self.assertEqual(cat.nplurals,3)
        self.assertEqual(self.w.work['output_format'],'yml')

    def test_export_only_accepted_yaml_keeps_other_values_comments_and_base(self):
        data=locale_download();self.sync(data);state=self.load();r=self.w.review
        a,b=[e['key'] for e in state['entries']]
        r.decide(a,{'status':'accepted'},0);r.decide(b,{'status':'rejected'},1)
        result=r.export(2)
        self.assertTrue(result['ok']);self.assertEqual(result['filename'],'es-419_locales_reviewed.yaml')
        _,parsed,lang=yaml_load(result['content'])
        self.assertEqual(parsed[lang]['notifications']['copied'],'Texto revisado')
        self.assertNotIn('delegates_page',parsed[lang])
        self.assertEqual(parsed[lang]['stable'],'Sin tocar')
        self.assertEqual(parsed[lang]['count']['many'],'Varios cubos')
        self.assertIn('# conservar este comentario',result['content'])
        self.assertEqual((self.w.directory(self.w.active_id)/'downloaded.yml').read_text(),data['native_yaml'])

    def test_suggestions_yaml_contains_only_approved_changes(self):
        self.sync();state=self.load();r=self.w.review
        r.decide(state['entries'][0]['key'],{'status':'accepted'},0)
        result=r.export(1,confirm_pending=True,mode='suggestions')
        self.assertTrue(result['ok']);self.assertEqual(result['filename'],'es-419_locales_suggestions.yaml')
        _,data,lang=yaml_load(result['content'])
        self.assertEqual(dict(data[lang]),{'notifications':{'copied':'Texto revisado'}})
        self.assertNotIn('stable',data[lang]);self.assertNotIn('count',data[lang])

    def test_pending_export_is_byte_identical(self):
        data=locale_download();self.sync(data);self.load()
        result=self.w.review.export(0,confirm_pending=True)
        self.assertTrue(result['ok']);self.assertEqual(result['content'],data['native_yaml'])

    def test_plural_review_exports_all_three_named_forms(self):
        self.sync(locale_download(plural_pending=True));state=self.load();r=self.w.review
        plural=next(e for e in state['entries'] if e['context']=='count')
        self.assertEqual(plural['plural_forms'],['one','many','other'])
        r.decide(plural['key'],{'status':'accepted'},0)
        result=r.export(1,confirm_pending=True)
        self.assertTrue(result['ok']);_,data,lang=yaml_load(result['content'])
        self.assertEqual(dict(data[lang]['count']),{'one':'Revisado uno','many':'Revisado muchos','other':'Revisado otros'})

    def test_recent_yaml_conflict_and_preservation(self):
        data=locale_download();self.sync(data);state=self.load();r=self.w.review;k=state['entries'][0]['key']
        r.decide(k,{'status':'accepted'},0)
        recent=data['native_yaml']+'  notifications:\n    copied: Cambio remoto\n  remote_extra: No perder\n'
        r.set_recent(recent,1)
        self.assertFalse(r.export(2,confirm_pending=True)['ok'])
        r.decide(k,{'status':'accepted','resolution':'chosen'},2)
        result=r.export(3,confirm_pending=True)
        self.assertTrue(result['ok']);self.assertIn('No perder',result['content'])
        self.w.open(self.w.active_id)
        self.assertEqual(self.w.review.document['native_recent'],recent)

    def test_recent_yaml_rejects_missing_keys_and_changed_plural_scheme_atomically(self):
        data=locale_download(pending=False,plural_pending=True)
        self.sync(data);self.load();r=self.w.review
        native=data['native_yaml']
        r.set_recent(native,0)
        before=copy.deepcopy(r.document)
        saved=r.state_path.read_bytes()
        invalid=[
            'es-419:\n  unrelated: Otro componente\n',
            native.replace("  stable: 'Sin tocar'\n",''),
            native.replace('    many: Varios cubos\n',''),
            native.replace('    other: Otros cubos\n','    two: Dos cubos\n'),
            native.replace('    other: Otros cubos\n','    other: Otros cubos\n    two: Dos cubos\n'),
            native.replace("  stable: 'Sin tocar'","  stable:\n    nested: Nuevo tipo"),
        ]
        for text in invalid:
            with self.subTest(text=text):
                with self.assertRaises(ReviewError):r.set_recent(text,1)
                self.assertEqual(r.document,before)
                self.assertEqual(r.state_path.read_bytes(),saved)
                self.assertEqual(r.document['native_recent'],native)

    def test_recent_yaml_allows_translation_of_keys_missing_from_the_initial_download(self):
        data=locale_download();self.sync(data);self.load();r=self.w.review
        recent=data['native_yaml']+'  notifications:\n    copied: Copiado\n'
        r.set_recent(recent,0)
        self.assertEqual(r.document['native_recent'],recent)

    def test_recent_yaml_preserves_list_positions(self):
        native='es-419:\n  days:\n    - Uno\n    - Dos\n'
        units=[{'context':f'days->{i}','source':[source],'target':[target],'fuzzy':True}
               for i,(source,target) in enumerate([('One','Uno'),('Two','Dos')])]
        english,target,mapping=convert_units(units,native)
        self.sync(dict(downloads(target,english),native_yaml=native,yaml_mapping=mapping));self.load()
        r=self.w.review
        for text in ['es-419:\n  days:\n    - Uno\n', 'es-419:\n  days:\n    "0": Uno\n    "1": Dos\n']:
            with self.subTest(text=text):
                with self.assertRaises(ReviewError):r.set_recent(text,0)
                self.assertEqual(r.document['revision'],0)
        r.set_recent(native.replace('Dos','Segundo'),0)
        self.assertEqual(r.document['revision'],1)

    def test_component_baselines_and_progress_are_separate(self):
        self.client.download.return_value=downloads();self.w.sync('regulations');regs=self.w.active_id
        state=self.sync(locale_download(pending=False))
        self.assertEqual(state['workspace']['candidate_count'],0)
        self.assertEqual({s['component'] for s in state['workspace']['sessions']},{'regulations','locales'})
        self.w.open(regs)
        self.assertEqual(self.w.snapshot()['workspace']['component'],'regulations')

    def test_bad_yaml_and_api_mismatch_are_rejected(self):
        for text in ['en:\n  x: text\n','es-419:\n  x: one\n  x: two\n']:
            with self.assertRaises(ReviewError):yaml_load(text)
        with self.assertRaises(ReviewError):convert_units([{'context':'key','source':['Key'],'target':['Mismatch']}],'es-419:\n  key: Other\n')

    def test_placeholder_changes_block_yaml_export(self):
        native='es-419:\n  greeting: ""\n'
        en,target,mapping=convert_units([{'context':'greeting','source':['Hello %{name}'],'target':['']}],native)
        self.sync(dict(downloads(target,en),native_yaml=native,yaml_mapping=mapping));self.load()
        r=self.w.review;r.decide(r.changed[0],{'status':'accepted'},0)
        self.assertFalse(r.export(1)['ok'])
