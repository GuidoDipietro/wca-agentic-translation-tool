import copy
from pathlib import Path
import sys
import tempfile
import unittest

import polib
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from core import Review
from glossary import contains, source_matches, variants
from translation import relevant_glossary
from workspace import glossary_terms


def term(source, preferred, forbidden=()):
    return {'source': [source], 'preferred': [preferred], 'forbidden': list(forbidden)}


class GlossaryTests(unittest.TestCase):
    def test_small_glossary_is_complete_and_original_is_unchanged(self):
        glossary = [term('delegate', 'delegado'), term('cube', 'cubo')]
        original = copy.deepcopy(glossary)
        selected = relevant_glossary(glossary, [{'source': ['Nothing related']}])
        self.assertEqual(len(selected), 2)
        self.assertIn('delegates', selected[0]['source'])
        self.assertIn('delegadas', selected[0]['preferred'])
        self.assertEqual(glossary, original)

    def test_large_glossary_keeps_relevant_and_essential_terms(self):
        glossary = [term('delegate', 'delegado'), term('document', 'documento')]
        glossary += [term(f'unrelated{i}', 'x' * 100) for i in range(100)]
        selected = relevant_glossary(glossary, [{'source': ['Read the document']}])
        self.assertEqual([t['source'][0] for t in selected], ['delegate', 'document'])

    def test_large_glossary_matches_variants_and_does_not_cross_entry_boundaries(self):
        glossary = [term('competitor', 'competidor'), term('world championship', 'campeonato mundial')]
        glossary += [term(f'unrelated{i}', 'x' * 100) for i in range(100)]
        selected = relevant_glossary(glossary, [{'source': ['Competitors of the world']}, {'source': ['championship winners']}])
        self.assertEqual([t['source'][0] for t in selected], ['competitor'])
        self.assertTrue(source_matches(glossary[0], 'COMPETITORS'))
        self.assertFalse(source_matches(glossary[0], 'noncompetitors'))

    def test_other_languages_do_not_receive_spanish_variants(self):
        selected = relevant_glossary([term('delegate', 'delegado')], [{'source': ['Delegates']}], 'fr')
        self.assertEqual(selected[0]['preferred'], ['delegado'])
        self.assertEqual(variants(['competición'], 'es_419'), ['competición', 'competiciones'])
        self.assertFalse(contains('competidoras', variants(['competencia'], 'es_419')))

    def report(self, translation):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            po = polib.POFile()
            po.metadata = {'Language': 'es_419', 'Content-Type': 'text/plain; charset=UTF-8'}
            po.append(polib.POEntry(msgid='Contact the delegates', msgstr=''))
            (root / 'base.po').write_text(str(po))
            po[0].msgstr = translation
            (root / 'proposed.po').write_text(str(po))
            review = Review(root / 'base.po', root / 'proposed.po', root / 'review', glossary=[term('delegate', 'delegado', ['organizador'])], source_checks=True)
            review.decide(review.changed[0], {'status': 'accepted'}, 0)
            return review.validate_export(1)

    def test_validation_accepts_explicit_gender_and_number_variants(self):
        report = self.report('Contactá a las delegadas')
        self.assertFalse(any(w['code'] in ('preferred_term', 'forbidden_term') for w in report['warnings']))

    def test_validation_flags_forbidden_variants_and_missing_preference(self):
        report = self.report('Contactá a las organizadoras')
        # Las advertencias conservan el control individual y la huella del texto.
        self.assertEqual({w['code'] for w in report['warnings']}, {'forbidden_term', 'preferred_term'})
        self.assertTrue(all(w['dismissible'] for w in report['warnings']))

    def test_tbx_explicit_forms_remain_grouped(self):
        tbx = '<martif><text><body><termEntry><langSet xml:lang="en"><tig><term>delegate</term></tig><tig><term>delegates</term></tig></langSet><langSet xml:lang="es-419"><tig><term>delegado</term></tig><tig><term>delegadas</term></tig></langSet></termEntry></body></text></martif>'
        terms = glossary_terms(tbx)
        self.assertEqual(terms[0]['source'], ['delegate', 'delegates'])
        self.assertEqual(terms[0]['preferred'], ['delegado', 'delegadas'])
