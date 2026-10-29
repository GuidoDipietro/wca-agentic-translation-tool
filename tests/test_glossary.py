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

    def terminology_report(self, source, target, glossary):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            po = polib.POFile()
            po.metadata = {'Language': 'es_419', 'Content-Type': 'text/plain; charset=UTF-8'}
            po.append(polib.POEntry(msgid=source, msgstr=''))
            (root / 'base.po').write_text(str(po))
            po[0].msgstr = target
            (root / 'proposed.po').write_text(str(po))
            review = Review(root / 'base.po', root / 'proposed.po', root / 'review', glossary=glossary, source_checks=True)
            review.decide(review.changed[0], {'status': 'accepted'}, 0)
            return [w for w in review.validate_export(1)['warnings'] if w['code'] in ('preferred_term', 'forbidden_term')]

    def test_specific_profile_label_overrides_generic_solves(self):
        glossary = [term('solves', 'resoluciones'), term('Completed Solves', 'Soluciones completadas')]
        self.assertEqual(self.terminology_report('Completed Solves', 'Soluciones completadas', glossary), [])
        self.assertEqual(self.terminology_report('Solves', 'Resoluciones', glossary), [])
        warnings = self.terminology_report('Solves', 'Soluciones', glossary)
        self.assertEqual([w['code'] for w in warnings], ['preferred_term'])
        self.assertEqual(self.terminology_report('Completed Solves', 'Resoluciones completadas', glossary)[0]['code'], 'preferred_term')

    def test_specific_puzzle_cover_does_not_require_puzzle_in_translation(self):
        glossary = [term('puzzle', 'puzzle'), term('puzzle cover', 'tapacubos')]
        self.assertEqual(self.terminology_report('Lift the puzzle cover', 'Levantá el tapacubos', glossary), [])
        self.assertEqual(self.terminology_report('The puzzle cover and the puzzle', 'El tapacubos y el puzzle', glossary), [])
        warnings = self.terminology_report('The puzzle cover and the puzzle', 'El tapacubos', glossary)
        self.assertEqual([w['code'] for w in warnings], ['preferred_term'])

    def test_specific_preference_keeps_generic_and_specific_forbidden_checks(self):
        glossary = [term('timer', 'cronómetro', ['Stackmat']), term('speedsolving timer', 'cronómetro de speedsolving', ['Stackmat'])]
        self.assertEqual(self.terminology_report('Speedsolving timer', 'Cronómetro de speedsolving', glossary), [])
        warnings = self.terminology_report('Speedsolving timer', 'Cronómetro de speedsolving Stackmat', glossary)
        self.assertEqual([w['code'] for w in warnings], ['forbidden_term'])
        # A compound term without a ban must still inherit the generic source's ban.
        glossary[1]['forbidden'] = []
        self.assertEqual([w['code'] for w in self.terminology_report('Speedsolving timer', 'Cronómetro de speedsolving Stackmat', glossary)], ['forbidden_term'])

    def test_only_strictly_containing_preferred_phrases_override(self):
        glossary = [term('puzzle', 'puzzle'), {'source': ['puzzle cover'], 'preferred': [], 'forbidden': ['cubierta']}]
        warnings = self.terminology_report('puzzle cover', 'tapacubos', glossary)
        self.assertEqual([w['code'] for w in warnings], ['preferred_term'])
        # Overlap without containment must retain both preferences.
        glossary = [term('world championship', 'campeonato mundial'), term('championship podium', 'podio')]
        warnings = self.terminology_report('world championship podium', 'Podio', glossary)
        self.assertEqual([w['code'] for w in warnings], ['preferred_term'])
