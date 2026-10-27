import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'app'))
from core import ReviewError, Catalog
from locales import convert_units, yaml_load, yaml_from_units
from languages import plural_names
from workspace import Workspace, write_json
from translation import validate_batch
from test_workspace import downloads


class MultilingualTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.client=Mock();self.client.env_path=self.root/'.env'
        self.w=Workspace(self.root/'data',self.client)

    def session(self, code, names):
        native=f'{code}:\n  cubes:\n'+''.join(f'    {name}: ""\n' for name in names)
        unit={'context':'cubes','source':['1 cube','%{count} cubes'],'target':['']*len(names)}
        english,target,mapping=convert_units([unit],native,code)
        self.client.download.return_value=dict(downloads(target,english),native_yaml=native,yaml_mapping=mapping)
        return self.w.sync('locales',language=code)

    def test_plural_schemes_include_non_spanish_languages(self):
        self.assertEqual(plural_names('ja'),['other'])
        self.assertEqual(plural_names('ar'),['zero','one','two','few','many','other'])
        self.assertEqual(plural_names('pl'),['one','few','many','other'])

    def test_arabic_zero_form_uses_plural_source_markers(self):
        state=self.session('ar',plural_names('ar'))
        item=self.w.work['entries'][0]
        targets=['مكعب واحد' if name=='one' else '%{count} مكعبات' for name in item['plural_forms']]
        valid=validate_batch(self.w,[item],{'entries':[{'key':item['key'],'target':targets,'note':'Arabic'}]})
        write_json(self.w.directory(self.w.active_id)/'proposals.json',{'version':1,'session_id':self.w.active_id,'base_sha256':self.w.work['base_sha256'],'entries':list(valid.values())})
        self.w.load_proposals();r=self.w.review;r.decide(item['key'],{'status':'accepted'},0)
        result=r.export(1)
        self.assertTrue(result['ok'],result)
        _,tree,root=yaml_load(result['content'],'ar')
        self.assertEqual(root,'ar');self.assertEqual(len(tree[root]['cubes']),6)
        self.assertTrue(result['filename'].startswith('ar_'))

    def test_japanese_single_plural_form_uses_other(self):
        self.session('ja',['other']);item=self.w.work['entries'][0]
        self.assertEqual(len(item['original']),1)
        validate_batch(self.w,[item],{'entries':[{'key':item['key'],'target':['%{count} 個'],'note':''}]})

    def test_histories_are_separate_for_language_and_component(self):
        self.session('fr',['one','many','other']);first=self.w.active_id
        self.session('ja',['other']);second=self.w.active_id
        self.assertEqual({s['language'] for s in self.w.sessions()},{'fr','ja'})
        self.assertNotEqual(first,second)
        self.w.open(first);self.assertEqual(self.w.snapshot()['workspace']['language'],'fr')
        self.assertIn('fr',self.w.snapshot()['workspace']['prompt'])

    def test_server_language_alias_preserves_native_yaml_root(self):
        _,data,root=yaml_load('zh-CN:\n  title: 测试\n','zh_Hans',['zh_CN'])
        self.assertEqual(root,'zh-CN')
        with self.assertRaises(ReviewError):yaml_load('pt-PT:\n  title: Teste\n','pt_BR')

    def test_unknown_or_inconsistent_plural_scheme_is_rejected(self):
        with self.assertRaises(ReviewError):plural_names('ar',number=2)
        with self.assertRaises(ReviewError):yaml_load('ja:\n  x: text\n','fr')

    def test_units_reconstruct_import_contexts_with_numeric_keys_and_plurals(self):
        units=[{'context':'events->333','source':['3x3x3 Cube'],'target':['3x3x3']},
               {'context':'rounds->0->name','source':['Final'],'target':['Finala']},
               {'context':'cubes','source':['One cube','%{count} cubes'],'target':['Kubo bat','%{count} kubo']}]
        native=yaml_from_units(units,'eu','config/locales/eu.yml')
        _,data,root=yaml_load(native,'eu')
        self.assertEqual(data[root]['events']['333'],'3x3x3')
        self.assertEqual(data[root]['rounds']['0']['name'],'Finala')
        self.assertEqual(list(data[root]['cubes']),['one','other'])
        english,target,mapping=convert_units(units,native,'eu')
        self.assertEqual(len(Catalog(target).entries),3)
        self.assertIn('do not replace',native)

    def test_reconstruction_rejects_ambiguous_contexts_and_plural_counts(self):
        unit={'context':'title','source':['Title'],'target':['Titre']}
        for units in [[unit,unit],[unit,dict(unit,context='title->child')],
                      [dict(unit,context='empty->')],
                      [{'context':'count','source':['One','Many'],'target':['one','many']}]]:
            with self.assertRaises(ReviewError):yaml_from_units(units,'ar')
        with self.assertRaises(ReviewError):yaml_from_units([unit],'fr','other.yml')

    def test_english_and_unsafe_codes_are_not_translation_targets(self):
        for value in ['en','../../other','es_419?token=x']:
            with self.assertRaises(ReviewError):self.w.sync('locales',language=value)
