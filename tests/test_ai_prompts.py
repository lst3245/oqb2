"""Unit tests for the AI prompt resolver: variant resolution, the
(key, endpoint_id) cache, format-block composition, and the JSON parser
contracts that must survive the format-rule extraction.

These tests patch ``ai_prompts._load_resolved`` instead of touching the
database, so they run against any environment (including a pre-init DB).
"""
import unittest
from unittest import mock

from app import ai_prompts


class FormatRegistryTests(unittest.TestCase):
    def test_format_keys_resolve_to_registry_items(self):
        """Every declared format_key must exist in the registry."""
        for key, spec in ai_prompts.PROMPTS_REGISTRY.items():
            fkey = spec.get('format_key')
            if fkey:
                self.assertIn(fkey, ai_prompts.PROMPTS_REGISTRY,
                              f'{key} points at missing format item {fkey}')

    def test_feature_format_items_exist(self):
        for fkey in ('CHECK_FORMAT', 'MD_FORMAT', 'SOLVE_GEN_FORMAT',
                     'SOLVE_CHECK_FORMAT', 'TAG_FORMAT', 'EXPLAIN_FORMAT'):
            self.assertIn(fkey, ai_prompts.PROMPTS_REGISTRY)
            self.assertEqual(ai_prompts.PROMPTS_REGISTRY[fkey]['role'], 'format')

    def test_system_defaults_no_longer_contain_json_contract(self):
        """The STRICT JSON contracts moved out of the system defaults."""
        for key in ('CHECK_SYSTEM', 'SOLVE_CHECK_SYSTEM', 'TAG_SYSTEM'):
            base = ai_prompts.PROMPTS_REGISTRY[key]['default']
            self.assertNotIn('STRICT JSON only', base,
                             f'{key} still embeds its JSON contract')


class ResolverTests(unittest.TestCase):
    def setUp(self):
        ai_prompts.invalidate_cache()

    def tearDown(self):
        ai_prompts.invalidate_cache()

    def test_unknown_key_raises(self):
        with self.assertRaises(KeyError):
            ai_prompts.get_prompt('NOT_A_KEY')

    def test_default_fallback_when_db_unavailable(self):
        with mock.patch.object(ai_prompts, '_load_resolved', return_value=None):
            self.assertEqual(ai_prompts.get_prompt('CHECK_SYSTEM'),
                             ai_prompts.PROMPTS_REGISTRY['CHECK_SYSTEM']['default'])

    def test_endpoint_specific_resolution_and_cache_keying(self):
        def fake_load(key, endpoint_id=None):
            if endpoint_id == 7:
                return 'EP7 VARIANT'
            return 'ACTIVE VARIANT'

        with mock.patch.object(ai_prompts, '_load_resolved', side_effect=fake_load):
            self.assertEqual(ai_prompts.get_prompt('CHECK_SYSTEM', 7), 'EP7 VARIANT')
            self.assertEqual(ai_prompts.get_prompt('CHECK_SYSTEM'), 'ACTIVE VARIANT')
            self.assertEqual(ai_prompts.get_prompt('CHECK_SYSTEM', 3), 'ACTIVE VARIANT')

        # Cached per (key, endpoint): values survive without DB access.
        with mock.patch.object(ai_prompts, '_load_resolved',
                               side_effect=AssertionError('cache miss')):
            self.assertEqual(ai_prompts.get_prompt('CHECK_SYSTEM', 7), 'EP7 VARIANT')
            self.assertEqual(ai_prompts.get_prompt('CHECK_SYSTEM'), 'ACTIVE VARIANT')

    def test_invalidate_single_key_drops_all_endpoints(self):
        with mock.patch.object(ai_prompts, '_load_resolved', return_value='X'):
            ai_prompts.get_prompt('CHECK_SYSTEM', 1)
            ai_prompts.get_prompt('CHECK_SYSTEM', 2)
            ai_prompts.get_prompt('MD_SYSTEM')
        ai_prompts.invalidate_cache('CHECK_SYSTEM')
        keys = list(ai_prompts._PROMPT_CACHE)
        self.assertFalse(any(k[0] == 'CHECK_SYSTEM' for k in keys))
        self.assertTrue(any(k[0] == 'MD_SYSTEM' for k in keys))


class FormatCompositionTests(unittest.TestCase):
    def setUp(self):
        ai_prompts.invalidate_cache()
        patcher = mock.patch.object(ai_prompts, '_load_resolved', return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(ai_prompts.invalidate_cache)

    def test_system_prompt_appends_format_block(self):
        reg = ai_prompts.PROMPTS_REGISTRY
        composed = ai_prompts.system_prompt('CHECK_SYSTEM')
        self.assertTrue(composed.startswith(reg['CHECK_SYSTEM']['default']))
        self.assertTrue(composed.endswith(reg['CHECK_FORMAT']['default']))

    def test_system_prompt_without_format_key_unchanged(self):
        self.assertEqual(ai_prompts.system_prompt('PDF_PAPER_NAME_SYSTEM'),
                         ai_prompts.PROMPTS_REGISTRY['PDF_PAPER_NAME_SYSTEM']['default'])

    def test_user_builders_append_format_with_header(self):
        text = ai_prompts.build_check_user_text('EN', 'ENO', 'QUE')
        self.assertIn(ai_prompts.FORMAT_EMPHASIS_HEADER, text)
        self.assertTrue(text.endswith(
            ai_prompts.PROMPTS_REGISTRY['CHECK_FORMAT']['default']))

        md = ai_prompts.build_md_user_text('ENO', 'QUE')
        self.assertIn(ai_prompts.FORMAT_EMPHASIS_HEADER, md)
        self.assertIn('[FIGURE', md)  # MD format rules present in user turn

    def test_tag_user_includes_json_contract(self):
        text = ai_prompts.build_tag_user_text('Math', ['q_type'], '(taxonomy)')
        self.assertIn('"q_type"', text)
        self.assertIn(ai_prompts.FORMAT_EMPHASIS_HEADER, text)

    def test_pdf_box_user_appends_rendered_contract(self):
        text = ai_prompts.build_pdf_box_user_text('QUE', 'xyxy')
        self.assertIn(ai_prompts.FORMAT_EMPHASIS_HEADER, text)
        # The contract's order placeholders must be filled, not literal.
        self.assertNotIn('{{box_array}}', text)
        self.assertIn('[x1, y1, x2, y2]', text)

    def test_explain_initial_user_appends_math_rules(self):
        text = ai_prompts.build_explain_initial_user_text()
        self.assertIn(ai_prompts.FORMAT_EMPHASIS_HEADER, text)
        self.assertIn('$$', text)


class ParserContractTests(unittest.TestCase):
    """The relocated JSON contracts must still match the parsers."""

    def test_parse_check_result_ok_and_issues(self):
        self.assertEqual(ai_prompts.parse_check_result('{"status": "ok"}'),
                         {'status': 'ok', 'issues': []})
        out = ai_prompts.parse_check_result(
            '{"status": "issues", "issues": [{"location": "L1", '
            '"description": "wrong sign", "severity": "major"}]}')
        self.assertEqual(out['status'], 'issues')
        self.assertEqual(out['issues'][0]['severity'], 'major')

    def test_parse_tag_result_shape(self):
        out = ai_prompts.parse_tag_result(
            '{"q_type": "MC", "level": 2, "section": "A", '
            '"major_topic": "Algebra", "major_subtopic": "Quadratics", '
            '"minor_topics": [], "subtopics": [], '
            '"chapter": null, "subchapter": null}')
        self.assertEqual(out['q_type'], 'MC')
        self.assertEqual(out['level'], 2)
        self.assertEqual(out['major_topic'], 'Algebra')

    def test_parse_figure_boxes_contract(self):
        out = ai_prompts.parse_figure_boxes(
            '[{"caption": "right triangle", "box": [40, 70, 960, 330]}]')
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['caption'], 'right triangle')


if __name__ == '__main__':
    unittest.main()
