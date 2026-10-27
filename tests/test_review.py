import copy
import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch

import polib
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from core import Catalog, Review, ReviewError, forms, key


def fixture(target='Hola %s', plural=None, unchanged='Sin cambios'):
    po = polib.POFile()
    po.metadata = {'Language': 'es_419', 'Content-Type': 'text/plain; charset=UTF-8', 'Plural-Forms': 'nplurals=2; plural=(n != 1);'}
    po.header = 'Encabezado que debe conservarse'
    po.append(polib.POEntry(msgid='Hello %s', msgstr=target, msgctxt='greeting', comment='Comentario del desarrollador', tcomment='Comentario del traductor', occurrences=[('source.md', '7')], flags=['fuzzy']))
    po.append(polib.POEntry(msgid='One cube', msgid_plural='Many cubes', msgstr_plural=plural or {0:'Un cubo',1:'Varios cubos'}, msgctxt='plural'))
    po.append(polib.POEntry(msgid='Unchanged', msgstr=unchanged))
    po.append(polib.POEntry(msgid='Old', msgstr='Antiguo', obsolete=True))
    return str(po)


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.original = self.root / 'original.po'
        self.proposed = self.root / 'propuesto.po'
        self.original.write_text(fixture(), encoding='utf-8')
        self.proposed.write_text(fixture('Buenas %s', {0:'Un rompecabezas',1:'Muchos rompecabezas'}), encoding='utf-8')
        self.data = self.root / 'datos'
        self.review = self.load()
        self.singular, self.plural = self.review.changed

    def load(self):
        return Review(self.original, self.proposed, self.data)

    def decide(self, entry_key, status, **kwargs):
        self.review.decide(entry_key, dict(status=status, **kwargs), self.review.document['revision'])

    def export(self, **kwargs):
        return self.review.export(self.review.document['revision'], **kwargs)

    def test_comparison_and_stable_keys(self):
        self.assertEqual(len(self.review.changed), 2)
        proposed = polib.pofile(str(self.proposed))
        proposed.reverse()
        proposed.save(str(self.proposed))
        reordered = self.load()
        self.assertEqual(reordered.changed, self.review.changed)
        self.assertEqual(reordered.snapshot()['counts']['pending'], 2)

    def test_pending_export_requires_confirmation_and_exact_original(self):
        self.assertTrue(self.export()['confirmation_required'])
        result = self.export(confirm_pending=True)
        self.assertTrue(result['ok'])
        self.assertEqual(result['content'], self.original.read_text(encoding='utf-8'))

    def test_accept_reject_and_metadata_preserved(self):
        before = self.original.read_bytes(), self.proposed.read_bytes()
        self.decide(self.singular, 'accepted')
        self.decide(self.plural, 'rejected')
        result = self.export()
        self.assertTrue(result['ok'])
        self.assertEqual(result['content'], fixture().replace('msgstr "Hola %s"', 'msgstr "Buenas %s"'))
        self.assertEqual(before, (self.original.read_bytes(), self.proposed.read_bytes()))
        output = Catalog(result['content'])
        self.assertEqual(output.by_key[self.singular].flags, ['fuzzy'])
        self.assertEqual(output.po.metadata, self.review.original.po.metadata)
        self.assertEqual(len(output.po), len(self.review.original.po))

    def test_plural_edition_multiline_and_escapes_roundtrip(self):
        values = ['Un "cubo"\nsegunda línea\\fin', 'Muchos\tcubos']
        self.decide(self.plural, 'edited', values=values)
        result = self.export(confirm_pending=True)
        self.assertTrue(result['ok'])
        output = Catalog(result['content'])
        self.assertEqual(forms(output.by_key[self.plural]), values)
        self.assertEqual(forms(output.by_key[self.singular]), ['Hola %s'])

    def test_crlf_preserved(self):
        text = fixture().replace('\n','\r\n')
        cat = Catalog(text)
        output = cat.render({self.singular:['Buenos días %s']})
        self.assertEqual(output, text.replace('msgstr "Hola %s"', 'msgstr "Buenos días %s"'))

    def test_persistence_and_drafts_are_not_approvals(self):
        self.decide(self.singular, 'pending', draft=['Borrador %s'])
        loaded = self.load()
        self.assertEqual(loaded.document['decisions'][self.singular]['draft'], ['Borrador %s'])
        self.assertEqual(loaded.snapshot()['counts']['pending'], 2)
        result = self.export(confirm_pending=True)
        self.assertEqual(result['content'], fixture())
        self.decide(self.singular, 'edited', values=['Mi texto %s'])
        self.assertEqual(self.load().document['decisions'][self.singular]['values'], ['Mi texto %s'])

    def test_placeholders_block_export(self):
        for target in ['Sin marcador', 'Cambiado %d', 'Duplicado %s %s']:
            self.decide(self.singular,'edited',values=[target])
            result = self.export(confirm_pending=True)
            self.assertFalse(result['ok'])
            self.assertTrue(any('placeholders' in err['message'] for err in result['errors']))
        self.assertFalse((self.data/'es-419_regulations_reviewed.po').exists())

    def test_named_placeholders_and_reference_warnings(self):
        from core import PLACEHOLDER, tokens
        self.assertEqual(sum(tokens(PLACEHOLDER, '%{name} %(count)d {value} {{id}} %1$s %%').values()), 6)
        self.decide(self.singular,'edited',values=['Ver [guía](https://example.org) %s'])
        result = self.export(confirm_pending=True)
        self.assertFalse(result['ok'])
        self.assertTrue(result['warnings'])
        self.assertTrue(self.export(confirm_pending=True,confirm_warnings=True)['ok'])

    def test_percentages_are_not_printf_placeholders(self):
        from core import PLACEHOLDER, FORMAT_PLACEHOLDER, tokens
        self.assertEqual(tokens(PLACEHOLDER, '50 % de los lugares y 100 % de los lugares'), {})
        self.assertEqual(tokens(FORMAT_PLACEHOLDER, '% d'), {'% d': 1})

    def test_empty_translation_blocks_export(self):
        self.decide(self.singular,'edited',values=[''])
        self.assertFalse(self.export(confirm_pending=True)['ok'])

    def test_changed_inputs_refused_on_restart_and_export(self):
        self.decide(self.singular, 'accepted')
        self.proposed.write_text(fixture('Otra propuesta %s'),encoding='utf-8')
        with self.assertRaises(ReviewError): self.load()
        with self.assertRaises(ReviewError): self.export(confirm_pending=True)

    def test_unknown_entry_and_wrong_plural_count_rejected(self):
        for entry_key, decision in [('missing',{'status':'accepted'}),(self.plural,{'status':'edited','values':['Solo una']}),(self.singular,{'status':'pending','draft':[None]})]:
            with self.assertRaises(ReviewError):
                self.review.decide(entry_key,decision,self.review.document['revision'])

    def test_duplicate_missing_and_invalid_po_rejected(self):
        with self.assertRaises(ReviewError): Catalog('msgid "broken\nmsgstr "x"\n')
        with self.assertRaises(ReviewError): Catalog(fixture() + '\nmsgctxt "greeting"\nmsgid "Hello %s"\nmsgstr "Otro"\n')
        self.proposed.write_text(fixture()+'\nmsgid "Added"\nmsgstr "Agregado"\n', encoding='utf-8')
        with self.assertRaises(ReviewError): self.load()

    def test_duplicate_context_and_source_is_rejected_even_if_plural_differs(self):
        text=fixture()+'\nmsgctxt "plural"\nmsgid "One cube"\nmsgid_plural "Other cubes"\nmsgstr[0] "Uno"\nmsgstr[1] "Otros"\n'
        with self.assertRaisesRegex(ReviewError,'Duplicate identifiers'):
            Catalog(text)

    def test_combined_fuzzy_and_translation_edits_keep_unapproved_bytes(self):
        text=fixture().replace('#, fuzzy','#, fuzzy, python-format')
        catalog=Catalog(text)
        result=catalog.render({self.singular:['Una propuesta más larga %s']},clear_fuzzy=[self.singular,self.singular])
        expected=text.replace('#, fuzzy, python-format','#, python-format').replace('msgstr "Hola %s"','msgstr "Una propuesta más larga %s"')
        self.assertEqual(result,expected)

    def test_incomplete_plural_rejected(self):
        with self.assertRaises(ReviewError): Catalog(fixture(plural={0:'Uno'}))

    def test_stale_revision_rejected(self):
        self.decide(self.singular,'accepted')
        with self.assertRaises(ReviewError): self.review.decide(self.plural,{'status':'accepted'},0)

    def test_corrupt_progress_not_overwritten(self):
        self.data.mkdir()
        path=self.data/'decisiones.json'
        path.write_text('{invalid',encoding='utf-8')
        with self.assertRaises(ReviewError): self.load()
        self.assertEqual(path.read_text(),'{invalid')

    def test_failed_save_does_not_commit_in_memory(self):
        before=copy.deepcopy(self.review.document)
        with patch('core.atomic_write',side_effect=OSError('Disco lleno')):
            with self.assertRaises(OSError): self.decide(self.singular,'accepted')
        self.assertEqual(self.review.document,before)

    def test_recent_conflict_resolution_preserves_others(self):
        self.decide(self.singular,'accepted')
        recent=fixture('Cambio ajeno %s', unchanged='Otro cambio ajeno')
        self.review.set_recent(recent,self.review.document['revision'])
        self.assertFalse(self.export(confirm_pending=True)['ok'])
        self.decide(self.singular,'accepted',resolution='chosen')
        result=self.export(confirm_pending=True)
        self.assertTrue(result['ok'])
        cat=Catalog(result['content'])
        self.assertEqual(forms(cat.by_key[self.singular]),['Buenas %s'])
        self.assertIn('Otro cambio ajeno',result['content'])
        self.assertEqual(self.load().recent.text,recent)

    def test_keep_recent_and_invalidate_resolution_after_new_upload(self):
        recent=fixture('Cambio ajeno %s')
        self.review.set_recent(recent,self.review.document['revision'])
        self.decide(self.singular,'rejected',resolution='current')
        self.assertEqual(self.export(confirm_pending=True)['content'],recent)
        self.review.set_recent(fixture('Cambio más reciente %s'),self.review.document['revision'])
        self.assertFalse(self.export(confirm_pending=True)['ok'])

    def test_recent_incompatible_identifiers_rejected(self):
        recent=fixture()+'\nmsgid "Added"\nmsgstr "Nuevo"\n'
        with self.assertRaises(ReviewError): self.review.set_recent(recent,0)
        self.assertIsNone(self.review.recent)

    def test_summary_counts_and_export_reparse(self):
        self.decide(self.singular,'accepted')
        self.decide(self.plural,'edited',values=['Uno','Muchos'])
        result=self.export()
        self.assertTrue(result['ok'])
        summary=json.loads((self.data/'resumen_exportacion.json').read_text())
        self.assertEqual(summary['counts'],{'accepted':1,'rejected':0,'edited':1,'pending':0})
        Catalog.read(self.data/'es-419_regulations_reviewed.po')


if __name__ == '__main__':
    unittest.main()
