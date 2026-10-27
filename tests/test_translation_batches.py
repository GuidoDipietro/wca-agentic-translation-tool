import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import polib
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from core import ReviewError
from translation import (OpenAITranslator, adaptive_batches, batch_context, compact_json,
                         consistency_examples, estimated_batch_tokens, generate_proposals,
                         relevant_glossary, wire_context, BATCH_TOKEN_BUDGET, InvalidTemporaryIDs)
from workspace import Workspace, read_json


class BatchOptimizationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        english, target = polib.POFile(), polib.POFile()
        english.metadata = {'Language': 'en', 'Content-Type': 'text/plain; charset=UTF-8'}
        target.metadata = dict(english.metadata, Language='es_419')
        for index in range(70):
            context = f'documents#{index:024x}:title'
            english.append(polib.POEntry(msgctxt=context, msgid=f'Document title {index}', msgstr=f'Document title {index}'))
            target.append(polib.POEntry(msgctxt=context, msgid=f'Document title {index}', msgstr=''))
        client = Mock(env_path=self.root / '.env')
        client.download.return_value = {'english': str(english), 'target': str(target), 'metadata': {}, 'warnings': []}
        self.w = Workspace(self.root / 'data', client)
        self.w.sync('regulations')

    def test_short_titles_take_two_requests_for_forty_entries(self):
        translator = Mock()
        translator.propose.side_effect = lambda provider, model, context, progress=None: {'entries': [{'key': e['key'], 'target': ['Título'], 'note': ''} for e in context['requested_entries']]}
        state = generate_proposals(self.w, translator, 'openai', 'test-model')
        self.assertEqual([len(call.args[2]['requested_entries']) for call in translator.propose.call_args_list], [32, 8])
        self.assertEqual(state['workspace']['ai_completed'], 40)
        self.assertEqual(state['counts']['accepted'], 0)
        self.assertFalse(state['workspace']['ready'])
        state = generate_proposals(self.w, translator, 'openai', 'test-model')
        self.assertTrue(state['workspace']['ready'])
        self.assertEqual(state['counts']['pending'], 70)

    def test_larger_texts_and_nonlatin_texts_shrink_batches_without_truncation(self):
        for text in ['Long paragraph ' * 300, '漢字かな' * 600]:
            entries = copy.deepcopy(self.w.work['entries'][:12])
            for e in entries: e['source'] = [text]
            batches = list(adaptive_batches(self.w, entries))
            self.assertGreater(len(batches), 1)
            self.assertEqual([e['key'] for b, _ in batches for e in b], [e['key'] for e in entries])
            for batch, context in batches:
                self.assertTrue(len(batch) == 1 or estimated_batch_tokens(context) <= BATCH_TOKEN_BUDGET)
                self.assertEqual(context['requested_entries'][0]['source'], [text])

    def test_context_keeps_plural_order_and_notes_but_omits_internal_fields(self):
        entry = dict(self.w.work['entries'][0], plural_forms=['one', 'many', 'other'], original=['', '', ''], comment='Preserve the exception.', simulation_reference=['secret reference'])
        context = batch_context(self.w, [entry])
        wire, mapping = wire_context(context)
        item = wire['requested_entries'][0]
        self.assertEqual(item['key'], '1')
        self.assertEqual(item['context'], 'documents:title')
        self.assertEqual(item['target_count'], 3)
        self.assertEqual(item['plural_forms'], ['one', 'many', 'other'])
        self.assertEqual(item['comment'], 'Preserve the exception.')
        self.assertEqual(mapping, {'1': entry['key']})
        self.assertNotIn('simulation_reference', compact_json(wire))
        self.assertNotIn('reasons', item)
        self.assertNotIn('original', item)

    def test_only_relevant_glossary_and_examples_are_sent(self):
        batch = self.w.work['entries'][:1]
        wanted = {'source': ['document'], 'preferred': ['documento'], 'forbidden': ['papel']}
        unrelated = {'source': ['cube'], 'preferred': ['cubo'], 'forbidden': []}
        self.assertEqual(relevant_glossary([wanted, unrelated], batch), [wanted, unrelated])
        def entry(source, value, fuzzy=False):
            return polib.POEntry(msgid=source, msgstr=value, flags=['fuzzy'] if fuzzy else [])
        review = Mock()
        review.original.entries = [entry('The cube', 'El cubo'), entry('Document details', 'Detalles'), entry('Document title', 'Título dudoso', True)]
        examples = consistency_examples(review, batch)
        self.assertEqual([e['source'] for e in examples], ['Document details'])
        review.original.entries = [entry('Document ' + str(i), 'Texto' * 900) for i in range(20)]
        examples = consistency_examples(review, batch)
        self.assertLessEqual(sum(len(compact_json(e)) for e in examples), 6000)
        self.assertLessEqual(len(examples), 8)

    def test_completed_batch_survives_failure_and_resume_does_not_repeat_it(self):
        translator = Mock()
        def propose(provider, model, context, progress=None):
            return {'entries': [{'key': e['key'], 'target': ['Título'], 'note': ''} for e in context['requested_entries']]}
        translator.propose.side_effect = [propose(None, None, batch_context(self.w, self.w.work['entries'][:32])), ReviewError('Interrupción simulada')]
        with self.assertRaises(ReviewError): generate_proposals(self.w, translator, 'openai', 'test-model')
        cache = read_json(self.w.directory(self.w.active_id) / 'generation.json')
        self.assertEqual(len(cache['entries']), 32)
        translator.propose.side_effect = propose
        translator.propose.reset_mock()
        generate_proposals(self.w, translator, 'openai', 'test-model')
        resent = {e['key'] for c in translator.propose.call_args_list for e in c.args[2]['requested_entries']}
        self.assertFalse(resent.intersection(cache['entries']))

    def sse_response(self, result):
        events = [{'type': 'response.output_text.delta', 'delta': json.dumps(result)}, {'type': 'response.completed', 'response': {'status': 'completed'}}]
        response = Mock()
        response.__enter__ = Mock(return_value=io.BytesIO(b''.join(line for e in events for line in [('data: ' + json.dumps(e) + '\n').encode(), b'\n'])))
        response.__exit__ = Mock(return_value=False)
        return response

    def wire_propose(self, result, context):
        auth = Mock()
        auth.access_token.return_value = 'fake-test-token'
        with patch('translation.urllib.request.build_opener') as build:
            build.return_value.open.return_value = self.sse_response(result)
            restored = OpenAITranslator(Mock(), auth).propose('chatgpt', 'test-model', context)
            body = json.loads(build.return_value.open.call_args.args[0].data)
        return restored, json.loads(body['input'][0]['content'])

    def test_short_wire_ids_restore_exact_keys_and_reject_unknown_duplicate_or_missing_ids(self):
        context = batch_context(self.w, self.w.work['entries'][:2])
        entries = [{'key': str(i), 'target': ['Título'], 'note': ''} for i in (2, 1)]
        restored, wire = self.wire_propose({'entries': entries}, context)
        self.assertEqual([e['key'] for e in restored['entries']], [e['key'] for e in reversed(self.w.work['entries'][:2])])
        self.assertEqual([e['key'] for e in wire['requested_entries']], ['1', '2'])
        for bad in [entries[:1], [entries[0], entries[0]], [dict(entries[0], key='999'), entries[1]]]:
            with self.assertRaises(InvalidTemporaryIDs): self.wire_propose({'entries': bad}, context)

    def test_real_translator_retries_bad_wire_ids_and_keeps_completed_batches(self):
        good=lambda count:{'entries':[{'key':str(i),'target':['Título'],'note':''} for i in range(1,count+1)]}
        bad={'entries':[{'key':'999','target':['Título'],'note':''}]}
        auth=Mock();auth.access_token.return_value='fake-test-token'
        translator=OpenAITranslator(Mock(),auth)
        with patch('translation.urllib.request.build_opener') as build:
            build.return_value.open.side_effect=[self.sse_response(value) for value in (good(32),bad,good(8))]
            state=generate_proposals(self.w,translator,'chatgpt','test-model')
            self.assertEqual(build.return_value.open.call_count,3)
            last=json.loads(build.return_value.open.call_args.args[0].data)
            self.assertIn('validation_feedback',json.loads(last['input'][0]['content']))
        self.assertEqual(state['workspace']['ai_completed'],40)
        self.assertFalse(state['workspace']['ready'])
        self.assertEqual(len(read_json(self.w.directory(self.w.active_id)/'generation.json')['entries']),40)

    def test_real_translator_stops_after_one_wire_id_retry_and_preserves_progress(self):
        good={'entries':[{'key':str(i),'target':['Título'],'note':''} for i in range(1,33)]}
        bad={'entries':[{'key':'999','target':['Título'],'note':''}]}
        auth=Mock();auth.access_token.return_value='fake-test-token'
        with patch('translation.urllib.request.build_opener') as build:
            build.return_value.open.side_effect=[self.sse_response(value) for value in (good,bad,bad)]
            with self.assertRaises(InvalidTemporaryIDs):
                generate_proposals(self.w,OpenAITranslator(Mock(),auth),'chatgpt','test-model')
            self.assertEqual(build.return_value.open.call_count,3)
        folder=self.w.directory(self.w.active_id)
        self.assertEqual(len(read_json(folder/'generation.json')['entries']),32)
        self.assertFalse((folder/'review.po').exists())


    def test_invalid_temporary_ids_get_one_retry_without_losing_progress(self):
        translator = Mock()
        context = batch_context(self.w, self.w.work['entries'][:1])
        translator.propose.side_effect = [InvalidTemporaryIDs('IDs inválidos'), {'entries': [{'key': context['requested_entries'][0]['key'], 'target': ['Título'], 'note': ''}]}]
        generate_proposals(self.w, translator, 'openai', 'test-model', maximum=1)
        self.assertEqual(translator.propose.call_count, 2)
        self.assertEqual(len(read_json(self.w.directory(self.w.active_id) / 'generation.json')['entries']), 1)
