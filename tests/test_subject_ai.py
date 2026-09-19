"""Pure-logic tests for the Subject AI tuning layer: tag comparison helpers,
the extended Auto Tag parser (confidence / reasons) and the subject
instruction block rendering. No DB, no ``create_app()``."""
import unittest

from app import ai_prompts
from app.subject_ai_service import (normalize_label, label_text,
                                    compare_labels, display_to_labels)


class NormalizeLabelTests(unittest.TestCase):
    def test_empty_forms_collapse_to_none(self):
        for v in (None, '', '   ', [], ['', ' '], ()):
            self.assertIsNone(normalize_label(v), repr(v))

    def test_lists_become_sorted_unique_tuples(self):
        self.assertEqual(normalize_label(['b', 'a', 'b ']), ('a', 'b'))

    def test_scalars_are_stripped_strings(self):
        self.assertEqual(normalize_label(2), '2')
        self.assertEqual(normalize_label(' MC '), 'MC')

    def test_label_text(self):
        self.assertEqual(label_text(None), '')
        self.assertEqual(label_text(['y', 'x']), 'x, y')
        self.assertEqual(label_text('Algebra'), 'Algebra')


class CompareLabelsTests(unittest.TestCase):
    def test_scalar_agreement_is_case_insensitive(self):
        out = compare_labels({'major_topic': 'Algebra'},
                             {'major_topic': 'algebra'}, ['major_topic'])
        self.assertTrue(out['major_topic']['agree'])
        self.assertTrue(out['major_topic']['applicable'])

    def test_missing_existing_is_not_applicable(self):
        out = compare_labels({'chapter': None}, {'chapter': 'Ch 1'}, ['chapter'])
        self.assertFalse(out['chapter']['applicable'])
        self.assertFalse(out['chapter']['agree'])
        self.assertEqual(out['chapter']['suggested'], 'Ch 1')

    def test_list_fields_use_set_equality(self):
        out = compare_labels({'minor_topics': ['A', 'B']},
                             {'minor_topics': ['b', 'a']}, ['minor_topics'])
        self.assertTrue(out['minor_topics']['agree'])
        out = compare_labels({'minor_topics': ['A', 'B']},
                             {'minor_topics': ['A']}, ['minor_topics'])
        self.assertFalse(out['minor_topics']['agree'])
        self.assertEqual(out['minor_topics']['existing'], 'A, B')

    def test_level_display_is_stringified(self):
        labels = display_to_labels({'level': 2, 'q_type': 'MC'}, ['level', 'q_type', 'section'])
        self.assertEqual(labels, {'level': '2', 'q_type': 'MC', 'section': None})


class TagParserDiagnosticsTests(unittest.TestCase):
    def test_confidence_and_reasons_are_parsed_and_clamped(self):
        out = ai_prompts.parse_tag_result(
            '{"q_type": "MC", "major_topic": "Algebra", '
            '"confidence": {"q_type": 1.7, "major_topic": "0.4", "bogus": 0.1}, '
            '"reasons": {"major_topic": " solves a quadratic ", "bogus": "x"}}')
        self.assertEqual(out['confidence'], {'q_type': 1.0, 'major_topic': 0.4})
        self.assertEqual(out['reasons'], {'major_topic': 'solves a quadratic'})

    def test_scalar_confidence_applies_to_every_field(self):
        out = ai_prompts.parse_tag_result('{"q_type": "CQ", "confidence": 0.5}')
        self.assertEqual(out['confidence']['q_type'], 0.5)
        self.assertEqual(out['confidence']['chapter'], 0.5)

    def test_missing_diagnostics_are_empty_dicts(self):
        out = ai_prompts.parse_tag_result('{"q_type": "CQ"}')
        self.assertEqual(out['confidence'], {})
        self.assertEqual(out['reasons'], {})


class SubjectInstructionsBlockTests(unittest.TestCase):
    def test_blank_inputs_render_nothing(self):
        self.assertEqual(ai_prompts.format_subject_instructions('', None), '')

    def test_note_and_patterns_get_headers(self):
        block = ai_prompts.format_subject_instructions('Level 3 = two chapters.',
                                                       '- Major Topic: X -> Y (3 times).')
        self.assertIn(ai_prompts.SUBJECT_INSTRUCTIONS_HEADER, block)
        self.assertIn(ai_prompts.CORRECTION_PATTERNS_HEADER, block)
        self.assertIn('Level 3 = two chapters.', block)
        self.assertTrue(block.startswith('\n') and block.endswith('\n'))

    def test_default_tag_user_declares_the_slot(self):
        spec = ai_prompts.PROMPTS_REGISTRY['TAG_USER']
        self.assertIn('subject_instructions', spec['variables'])
        self.assertIn('{{subject_instructions}}', spec['default'])

    def test_system_prompt_with_body_keeps_format_block(self):
        text = ai_prompts.system_prompt_with_body('TAG_SYSTEM', 'My own rules.')
        self.assertTrue(text.startswith('My own rules.'))
        self.assertIn('STRICT JSON', text)


if __name__ == '__main__':
    unittest.main()
