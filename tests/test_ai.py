import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from connections import Connections
from core import ReviewError
from translation import generate_proposals, OpenAITranslator, validate_batch
from workspace import Workspace
from test_locales import locale_download


class AIWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name);client=Mock();client.env_path=root/'.env';client.download.return_value=locale_download()
        self.w=Workspace(root/'data',client);self.w.sync('locales')
        self.translator=Mock()
        self.calls=[]
        def propose(provider,model,context,progress=None):
            self.calls.append(context)
            return {'entries':[{'key':e['key'],'target':['Traducción de ejemplo'],'note':'Propuesta'} for e in context['requested_entries']]}
        self.translator.propose.side_effect=propose

    def test_automatic_generation_never_approves_and_uses_corpus_context(self):
        state=generate_proposals(self.w,self.translator,'openai','test-model')
        self.assertTrue(state['workspace']['ready']);self.assertEqual(state['counts']['pending'],2)
        self.assertEqual(state['counts']['accepted'],0)
        self.assertEqual(self.calls[0]['consistency_examples'], [])
        self.assertEqual(self.calls[0]['language'],'es_419')
        self.assertTrue(self.w.review.export(0)['confirmation_required'])

    def test_simulation_reference_is_only_for_human_review(self):
        self.w.work['entries'][0]['simulation_reference']=['Respuesta de referencia reservada']
        generate_proposals(self.w,self.translator,'openai','test-model')
        self.assertNotIn('simulation_reference',json.dumps(self.calls))
        self.assertNotIn('Respuesta de referencia reservada',json.dumps(self.calls))

    def test_interrupted_generation_resumes_without_repeating_validated_entries(self):
        state=generate_proposals(self.w,self.translator,'openai','test-model',maximum=1)
        self.assertFalse(state['workspace']['ready']);self.assertEqual(state['workspace']['ai_completed'],1)
        first=self.calls[0]['requested_entries'][0]['key']
        state=generate_proposals(self.w,self.translator,'openai','test-model',maximum=1)
        self.assertTrue(state['workspace']['ready']);self.assertNotEqual(self.calls[1]['requested_entries'][0]['key'],first)

    def test_wrong_keys_retry_once_then_leave_review_untouched(self):
        self.translator.propose.side_effect=lambda *a,**k:{'entries':[]}
        with self.assertRaises(ReviewError):generate_proposals(self.w,self.translator,'openai','test-model')
        self.assertEqual(self.translator.propose.call_count,2)
        self.assertFalse((self.w.directory(self.w.active_id)/'proposals.json').exists())
        self.assertEqual(self.w.review.snapshot()['counts']['accepted'],0)

    def test_existing_review_is_never_overwritten(self):
        generate_proposals(self.w,self.translator,'openai','test-model');count=self.translator.propose.call_count
        with self.assertRaises(ReviewError):generate_proposals(self.w,self.translator,'openai','test-model')
        self.assertEqual(self.translator.propose.call_count,count)

    def test_api_key_stays_server_side_and_is_optional(self):
        path=Path(self.tmp.name)/'.env';path.write_text('OPENAI_API_KEY=private-test-key\n')
        connections=Connections(path)
        self.assertNotIn('private-test-key',json.dumps(connections.status()))
        connections.configure({'OPENAI_API_KEY':'other-private-key'})
        self.assertEqual(connections.get('OPENAI_API_KEY'),'other-private-key')
        self.assertNotIn('other-private-key',json.dumps(connections.status()))
        with self.assertRaises(ReviewError):connections.configure({'OPENAI_API_KEY':'unsafe\rkey'})

    def test_complete_sse_response_is_parsed_without_unsupported_plan_fields(self):
        expected={'entries':[{'key':'test','target':['Texte'],'note':''}]}
        events=[{'type':'response.output_text.delta','delta':json.dumps(expected)},{'type':'response.completed','response':{'status':'completed'}}]
        response=Mock();response.__enter__=Mock(return_value=io.BytesIO(b''.join(line for e in events for line in [('data: '+json.dumps(e)+'\n').encode(),b'\n'])))
        response.__exit__=Mock(return_value=False)
        config=Mock();auth=Mock();auth.access_token.return_value='oauth-test-token'
        provider=OpenAITranslator(config,auth)
        with patch('translation.urllib.request.build_opener') as build:
            build.return_value.open.return_value=response
            self.assertEqual(provider.propose('chatgpt','test-model',{}),expected)
            body=json.loads(build.return_value.open.call_args.args[0].data)
            self.assertNotIn('max_output_tokens',body);self.assertFalse(body['store']);self.assertTrue(body['stream'])

    def test_stream_must_complete_before_proposals_are_accepted(self):
        events=[{'type':'response.output_text.delta','delta':'{"entries":[]}'},{'type':'response.failed'}]
        # The provider consumes SSE line by line without using a truncated response.
        response=Mock();response.__enter__=Mock(return_value=io.BytesIO(b''.join(line for e in events for line in [('data: '+json.dumps(e)+'\n').encode(),b'\n'])))
        response.__exit__=Mock(return_value=False)
        config=Mock();config.get.return_value='private-test-key'
        provider=OpenAITranslator(config,Mock())
        with patch('translation.urllib.request.build_opener') as build:
            build.return_value.open.return_value=response
            with self.assertRaises(ReviewError):provider.propose('openai','test-model',{})
            request=build.return_value.open.call_args.args[0]
            body=json.loads(request.data)
            self.assertFalse(body['store']);self.assertTrue(body['stream'])
            self.assertEqual(body['text']['format']['type'],'json_schema')
            self.assertNotIn('private-test-key',request.data.decode())
