import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from core import Catalog, ReviewError, forms
from workspace import Workspace, write_json
from weblate import Client, SameOriginRedirect, WeblateHTTPError, load_token
from test_review import fixture


def downloads(target=None, english=None):
    return {'english': english or fixture(), 'target': target or fixture(), 'glossary': None, 'metadata': {'en':{'revision':'source1'},'es_419':{'revision':'target1'}}, 'warnings': []}


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.client=Mock();self.client.env_path=self.root/'.env'
        self.workspace=Workspace(self.root/'data',self.client)

    def sync(self, data):
        self.client.download.return_value=data
        return self.workspace.sync()

    def proposals(self, transform=None):
        work=self.workspace.work
        entries=[]
        for item in work['entries']:
            target=['Texto %s'] if item['context']=='greeting' else ['Un rompecabezas','Muchos rompecabezas']
            entries.append({'key':item['key'],'target':target,'note':'Revisión preparada'})
        payload={'version':1,'session_id':work['session_id'],'base_sha256':work['base_sha256'],'entries':entries}
        if transform:transform(payload)
        write_json(self.workspace.directory(self.workspace.active_id)/'proposals.json',payload)
        return payload

    def test_first_sync_at_100_percent_has_no_work(self):
        # The first fixture message is fuzzy: remove the flag to simulate 100% reviewed.
        text=fixture().replace('#, fuzzy\n','')
        result=self.sync(downloads(text,text))
        self.assertEqual(result['workspace']['candidate_count'],0)
        self.assertEqual(result['counts']['pending'],0)
        self.assertEqual(result['total_entries'],3)
        self.assertTrue((self.workspace.directory(self.workspace.active_id)/'base.po').exists())
        restored=Workspace(self.root/'data',self.client)
        self.assertEqual(restored.active_id,self.workspace.active_id)

    def test_prompt_work_and_token_never_exposed(self):
        self.client.env_path.write_text('WEBLATE_TOKEN=super-secret-test')
        state=self.sync(downloads())
        self.assertEqual(state['workspace']['candidate_count'],1)
        prompt=state['workspace']['prompt']
        self.assertIn('proposals.json',prompt)
        self.assertIn('Do not read .env',prompt)
        self.assertNotIn('super-secret-test',json.dumps(state))
        self.assertTrue(state['workspace']['token_configured'])

    def test_new_key_missing_in_target_is_added_and_exported(self):
        english=fixture().replace('#, fuzzy\n','')+'\nmsgctxt "new"\nmsgid "New %s"\nmsgstr ""\n'
        target=fixture().replace('#, fuzzy\n','')
        state=self.sync(downloads(target,english))
        self.assertEqual(state['workspace']['candidate_count'],1)
        item=self.workspace.work['entries'][0]
        self.assertIn('needs_translation',item['reasons'])
        self.proposals(lambda p:p['entries'][0].update(target=['Nuevo %s']))
        self.workspace.load_proposals()
        review=self.workspace.review
        review.decide(item['key'],{'status':'accepted'},0)
        result=review.export(1)
        self.assertTrue(result['ok'])
        self.assertIn('msgstr "Nuevo %s"',result['content'])
        self.assertEqual(len(Catalog(result['content']).entries),4)

    def test_source_changed_uses_context_and_previous_translation(self):
        old=fixture().replace('#, fuzzy\n','')
        self.sync(downloads(old,old))
        new=old.replace('Hello %s','Hello again %s')
        state=self.sync(downloads(new,new))
        self.assertEqual(state['workspace']['candidate_count'],1)
        item=self.workspace.work['entries'][0]
        self.assertEqual(item['previous_source'],['Hello %s'])
        self.assertEqual(item['previous_translation'],['Hola %s'])
        self.assertIn('source_changed',item['reasons'])
        self.assertEqual(len(self.workspace.sessions()),2)

    def test_load_proposals_preserves_progress_and_reopens_sessions(self):
        self.sync(downloads());first=self.workspace.active_id
        self.proposals();state=self.workspace.load_proposals()
        item=state['entries'][0]
        review=self.workspace.review
        review.decide(item['key'],{'status':'accepted'},0)
        self.workspace.load_proposals()
        self.assertEqual(self.workspace.review.snapshot()['counts']['accepted'],1)
        self.sync(downloads())
        self.workspace.open(first)
        self.assertEqual(self.workspace.review.snapshot()['counts']['accepted'],1)
        self.proposals(lambda p:p['entries'][0].update(target=['Otra %s']))
        with self.assertRaises(ReviewError):self.workspace.load_proposals()

    def test_bad_proposal_contract_rejected(self):
        self.sync(downloads())
        for transform in [lambda p:p.update(session_id='other'),lambda p:p.update(base_sha256='bad'),lambda p:p.update(entries=[]),lambda p:p['entries'].append(copy.deepcopy(p['entries'][0])),lambda p:p['entries'][0].update(target=['two','forms']),lambda p:p['entries'][0].update(key='unknown')]:
            self.proposals(transform)
            with self.assertRaises(ReviewError):self.workspace.validated_proposals()

    def test_unchanged_proposal_still_requires_review_and_clears_fuzzy_on_approval(self):
        self.sync(downloads())
        self.proposals(lambda p:p['entries'][0].update(target=['Hola %s']))
        self.workspace.load_proposals()
        review=self.workspace.review
        self.assertEqual(len(review.changed),1)
        item=review.changed[0]
        result=review.export(0,confirm_pending=True)
        self.assertIn('fuzzy',Catalog(result['content']).by_key[item].flags)
        review.decide(item,{'status':'accepted'},0)
        result=review.export(1)
        self.assertTrue(result['ok'])
        self.assertNotIn('fuzzy',Catalog(result['content']).by_key[item].flags)
        self.assertIn('#, fuzzy', (self.workspace.directory(self.workspace.active_id)/'base.po').read_text())

    def test_fuzzy_clear_keeps_other_flags_and_unreviewed_entries(self):
        text=fixture().replace('#, fuzzy','#, fuzzy, python-format')
        self.sync(downloads(text,text));self.proposals();self.workspace.load_proposals()
        r=self.workspace.review;k=r.changed[0];r.decide(k,{'status':'accepted'},0)
        output=r.export(1)
        self.assertTrue(output['ok'])
        self.assertEqual(Catalog(output['content']).by_key[k].flags,['python-format'])

    def test_agent_validation_detects_bad_placeholders_without_changing_progress(self):
        self.sync(downloads());self.proposals(lambda p:p['entries'][0].update(target=['Sin variable']))
        errors, warnings=self.workspace.proposal_checks()
        self.assertTrue(errors)
        self.assertFalse((self.workspace.directory(self.workspace.active_id)/'review.po').exists())
        self.assertEqual(self.workspace.review.snapshot()['counts']['accepted'],0)

    def test_modified_work_contract_is_rejected(self):
        self.sync(downloads());folder=self.workspace.directory(self.workspace.active_id)
        work=json.loads((folder/'work.json').read_text());work['entries']=[]
        write_json(folder/'work.json',work)
        with self.assertRaises(ReviewError):self.workspace.open(self.workspace.active_id)

    def test_stale_session_and_path_traversal_rejected(self):
        self.sync(downloads());old={'session_id':self.workspace.active_id,'workspace_revision':self.workspace.generation}
        self.sync(downloads())
        with self.assertRaises(ReviewError):self.workspace.assert_session(old)
        with self.assertRaises(ReviewError):self.workspace.open('../anything')

    def test_sessions_are_sorted_by_timestamp_not_random_suffix(self):
        from datetime import datetime as real_datetime, timezone
        with patch('workspace.datetime') as clock, patch('workspace.uuid.uuid4') as identifier:
            clock.now.side_effect=[real_datetime(2026,1,1,12,0,0,100000,tzinfo=timezone.utc),real_datetime(2026,1,1,12,0,0,900000,tzinfo=timezone.utc)]
            identifier.side_effect=[type('ID',(),{'hex':'ffffffff0000'})(),type('ID',(),{'hex':'000000000000'})()]
            self.sync(downloads());first=self.workspace.active_id
            self.sync(downloads());second=self.workspace.active_id
        self.assertEqual([s['id'] for s in self.workspace.sessions()],[second,first])

    def test_failed_download_keeps_active_session(self):
        self.sync(downloads());before=self.workspace.active_id
        self.client.download.side_effect=ReviewError('Sin permiso')
        with self.assertRaises(ReviewError):self.workspace.sync()
        self.assertEqual(self.workspace.active_id,before)
        self.assertEqual(len(self.workspace.sessions()),1)

    def test_glossary_is_used_for_checks(self):
        data=downloads()
        data['glossary']='''<martif><text><body><termEntry><langSet xml:lang="en"><tig><term>Hello</term></tig></langSet><langSet xml:lang="es-419"><tig><term>Hola</term></tig><tig><term>Buenas</term><termNote type="administrativeStatus">forbidden</termNote></tig></langSet></termEntry></body></text></martif>'''
        self.sync(data);self.proposals(lambda p:p['entries'][0].update(target=['Buenas %s']));self.workspace.load_proposals()
        r=self.workspace.review;r.decide(r.changed[0],{'status':'accepted'},0)
        result=r.export(1)
        self.assertTrue(any('forbidden' in item['message'] for item in result['warnings']))
        self.assertFalse(result['ok'])

    def test_modified_snapshot_rejected(self):
        self.sync(downloads());folder=self.workspace.directory(self.workspace.active_id)
        (folder/'base.po').write_text(fixture('Cambio %s'))
        with self.assertRaises(ReviewError):self.workspace.open(self.workspace.active_id)


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.env=Path(self.tmp.name)/'.env'
        self.client=Client(self.env)

    def http_error(self,code,headers=None):
        return urllib.error.HTTPError('https://translate.worldcubeassociation.org/api/test/',code,'Error',headers or {},io.BytesIO(b'sensitive server body'))

    def test_env_is_not_shell_and_environment_overrides(self):
        with patch.dict(os.environ,{},clear=True):
            self.env.write_text('IGNORED=abc\nWEBLATE_TOKEN="literal-$(not-run)"\n')
            self.assertEqual(load_token(self.env),'literal-$(not-run)')
            with patch.dict(os.environ,{'WEBLATE_TOKEN':'env-token'}):self.assertEqual(load_token(self.env),'env-token')

    def test_403_is_clear_and_never_contains_token_or_response_body(self):
        self.env.write_text('WEBLATE_TOKEN=secret-test')
        self.client.opener=Mock();self.client.opener.open.side_effect=self.http_error(403)
        with self.assertRaises(ReviewError) as caught:self.client.get('/api/test/')
        self.assertIn('403',str(caught.exception));self.assertNotIn('secret-test',str(caught.exception));self.assertNotIn('sensitive',str(caught.exception))
        req=self.client.opener.open.call_args.args[0]
        self.assertEqual(req.get_method(),'GET')
        self.assertEqual(req.get_header('Authorization'),'Token secret-test')

    def test_invalid_token_does_not_leak_in_error(self):
        self.env.write_text('WEBLATE_TOKEN="secret\nline"')
        with self.assertRaises(ReviewError) as caught:load_token(self.env)
        self.assertNotIn('secret',str(caught.exception))
        with patch.dict(os.environ,{'WEBLATE_TOKEN':'secret\rvalue'}):
            with self.assertRaises(ReviewError) as caught:load_token(self.env)
            self.assertNotIn('secret',str(caught.exception))

    def test_429_retries_and_respects_long_delay(self):
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False);response.read.return_value=b'ok'
        self.client.opener=Mock();self.client.opener.open.side_effect=[self.http_error(429,{'Retry-After':'2'}),response]
        with patch('weblate.time.sleep') as sleep:
            self.assertEqual(self.client.get('/api/test/'),'ok');sleep.assert_called_once_with(2)
        self.client.opener.open.side_effect=self.http_error(429,{'Retry-After':'120'})
        with patch('weblate.time.sleep') as sleep:
            with self.assertRaises(ReviewError):self.client.get('/api/test/')
            sleep.assert_not_called()

    def test_external_redirect_rejected(self):
        with self.assertRaises(ReviewError):SameOriginRedirect().redirect_request(None,None,302,'',{},'https://other.example/steal')

    def test_changed_remote_revision_rejected(self):
        self.client.metadata=Mock(side_effect=[{'revision':'a'},{'revision':'b'},{'revision':'changed'},{'revision':'b'}]*2)
        self.client.get=Mock(return_value=fixture())
        with self.assertRaisesRegex(ReviewError,'both downloads'):self.client.download()
        self.assertEqual(self.client.metadata.call_count,8)
        self.assertEqual(self.client.get.call_count,4)

    def test_revision_change_retries_the_whole_download_with_fresh_files(self):
        self.client.metadata=Mock(side_effect=[{'revision':r} for r in ('a','b','changed','b','changed','b','changed','b')])
        fresh=fixture('Nueva traducción %s')
        self.client.get=Mock(side_effect=[fixture(),fixture(),fresh,fixture(),ReviewError('Sin glosario')])
        events=[];self.client.progress=events.append
        result=self.client.download()
        self.assertEqual(result['target'],fresh)
        self.assertEqual(result['metadata']['en']['revision'],'changed')
        self.assertTrue(any('Retrying' in e['message'] for e in events))
        self.assertEqual(self.client.get.call_count,5)

    def test_permission_failure_is_not_retried_as_a_revision_change(self):
        self.client.metadata=Mock(return_value={'revision':'a'})
        self.client.get=Mock(side_effect=WeblateHTTPError(403,'/api/file/','No permitido'))
        with self.assertRaises(WeblateHTTPError):self.client.download()
        self.assertEqual(self.client.get.call_count,1)

    def test_project_status_includes_payload_pending_in_supported_components(self):
        self.client.get=Mock(side_effect=[json.dumps([{'code':'es_419','total':3588,'translated':3085}]),
                                          json.dumps({'total':2259,'translated':2259}),json.dumps({'total':826,'translated':826}),json.dumps({'total':503,'translated':0})])
        result=self.client.project_status('es-419')
        self.assertEqual(result,{'language':'es_419','total':3588,'pending':503,'outside_supported':0})
        self.assertTrue(any('/payload/' in call.args[0] for call in self.client.get.call_args_list))

    def test_project_status_subtracts_pending_in_supported_components(self):
        self.client.get=Mock(side_effect=[json.dumps({'results':[{'code':'es_419','total':100,'translated':70}],'next':None}),
                                          json.dumps({'total':50,'translated':40}),json.dumps({'total':40,'translated':25}),json.dumps({'total':5,'translated':5})])
        self.assertEqual(self.client.project_status('es_419')['outside_supported'],5)

    def test_project_status_does_not_misattribute_pending_without_component_access(self):
        self.client.get=Mock(side_effect=[json.dumps([{'code':'fr','total':100,'translated':70}]),
                                          WeblateHTTPError(404,'/api/translations/wca/locales/fr/','No existe')])
        result=self.client.project_status('fr')
        self.assertEqual(result['pending'],30)
        self.assertIsNone(result['outside_supported'])

    def test_project_status_rejects_external_pagination(self):
        self.client.get=Mock(return_value=json.dumps({'results':[],'next':'https://other.example/api/stats'}))
        with self.assertRaisesRegex(ReviewError,'outside the allowed server'):self.client.project_status('es_419')
        self.assertEqual(self.client.get.call_count,1)

    def test_english_export_403_uses_paginated_source_units(self):
        self.client.metadata=Mock(return_value={'revision':'a'})
        units1={'results':[{'id':1,'source':['Hello %s'],'context':'greeting','note':'Context','location':'source.md:7','flags':'python-format'}], 'next':'https://translate.worldcubeassociation.org/api/translations/wca/regulations/en/units/?page=2'}
        units2={'results':[{'id':2,'source':['One cube','Many cubes'],'context':'plural'}], 'next':None}
        target=fixture()
        self.client.get=Mock(side_effect=[target,WeblateHTTPError(403,'/api/source/file/','Sin permiso'),json.dumps(units1),json.dumps(units2),ReviewError('No glossary')])
        result=self.client.download()
        english=Catalog(result['english'])
        self.assertEqual(result['target'],target)
        self.assertEqual(len(english.entries),2)
        self.assertEqual(english.entries[0].occurrences,[('source.md','7')])
        self.assertEqual(forms(english.entries[1]),['One cube','Many cubes'])
        self.assertTrue(any('source units' in text for text in result['warnings']))

    def test_other_english_export_errors_are_not_hidden(self):
        self.client.metadata=Mock(return_value={'revision':'a'})
        self.client.get=Mock(side_effect=[fixture(),WeblateHTTPError(401,'/api/source/file/','Token inválido')])
        with self.assertRaises(WeblateHTTPError):self.client.download()

    def test_locales_file_forbidden_falls_back_only_to_accessible_units(self):
        self.client.component='locales';self.client.language='eu'
        self.client.metadata=Mock(return_value={'revision':'a','total':2,'filename':'config/locales/eu.yml','language':{'aliases':[]}})
        units={'results':[{'context':'title','source':['Title'],'target':['']}, {'context':'count','source':['One','Many'],'target':['Bat','Batzuk']}], 'next':None}
        self.client.get=Mock(side_effect=[WeblateHTTPError(403,'/api/file/','No permitido'),json.dumps(units),ReviewError('No glossary')])
        result=self.client.download()
        self.assertEqual(len(Catalog(result['target']).entries),2)
        self.assertIn('reconstructed',result['warnings'][0])
        self.assertIn('do not replace',result['native_yaml'])

    def test_locales_fallback_does_not_ignore_auth_or_units_permissions(self):
        self.client.component='locales';self.client.language='eu'
        self.client.metadata=Mock(return_value={'revision':'a'})
        for failures in [[WeblateHTTPError(401,'/api/file/','Inválido')],
                         [WeblateHTTPError(403,'/api/file/','No permitido'),WeblateHTTPError(403,'/api/units/','No permitido')]]:
            self.client.get=Mock(side_effect=failures)
            with self.assertRaises(WeblateHTTPError):self.client.download()

    def test_source_units_reject_external_pagination(self):
        self.client.get=Mock(return_value=json.dumps({'results':[],'next':'https://other.example/api/steal'}))
        with self.assertRaises(ReviewError):self.client.english_from_units()

    def test_metadata_keeps_permission_error_detail(self):
        self.client.get=Mock(side_effect=WeblateHTTPError(403,'/api/metadata/','No permitido'))
        with self.assertRaises(WeblateHTTPError):self.client.metadata('en')

    def test_glossary_access_failure_is_reported_not_silent(self):
        self.client.metadata=Mock(return_value={'revision':'a'})
        self.client.get=Mock(side_effect=[fixture(),fixture(),ReviewError('HTTP 403')])
        result=self.client.download()
        self.assertIsNone(result['glossary']);self.assertIn('403',result['warnings'][0])


if __name__=='__main__':unittest.main()
