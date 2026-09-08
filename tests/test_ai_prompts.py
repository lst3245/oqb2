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

    def test_pdf_part_user_appends_rendered_contract(self):
        text = ai_prompts.build_pdf_part_user_text('QUE', coord_order='xyxy')
        self.assertIn(ai_prompts.FORMAT_EMPHASIS_HEADER, text)
        self.assertNotIn('{{box_array}}', text)
        self.assertIn('[x1, y1, x2, y2]', text)
        sol = ai_prompts.build_pdf_part_user_text(
            'SOL', expected_labels=['stem', 'a', 'ci'], coord_order='xyxy')
        self.assertIn('stem, a, ci', sol)

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

    def test_parse_question_boxes_keeps_range_and_part_labels(self):
        out = ai_prompts.parse_question_boxes(
            '[{"qno": "23-24", "box": [40, 70, 960, 330], '
            '"continues_prev": false, "continues_next": false},'
            ' {"qno": 5, "box": [40, 400, 960, 700]},'
            ' {"qno": "3a", "box": [40, 720, 960, 900]}]')
        self.assertEqual(len(out), 3)
        self.assertEqual(out[0]['qno'], 23)
        self.assertEqual(out[0]['label'], '23-24')
        self.assertEqual(out[1]['qno'], 5)
        self.assertEqual(out[1]['label'], '5')
        self.assertEqual(out[2]['qno'], 3)
        self.assertEqual(out[2]['label'], '3a')

    def test_parse_question_boxes_salvages_range_string(self):
        # Trailing garbage so json.loads fails and salvage runs.
        raw = '[{"qno": "23-24", "box": [40, 70, 960, 330],}]'
        out = ai_prompts.parse_question_boxes(raw)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['qno'], 23)
        self.assertEqual(out[0]['label'], '23-24')

    def test_parse_part_boxes_stem_and_letters(self):
        out = ai_prompts.parse_part_boxes(
            '[{"label": "stem", "box": [0, 0, 1000, 200]},'
            ' {"label": "a", "box": [0, 200, 1000, 500]},'
            ' {"label": "ci", "box": [0, 500, 1000, 800]},'
            ' {"label": "3b", "box": [0, 800, 1000, 950]}]')
        self.assertEqual([b['label'] for b in out], ['stem', 'a', 'ci', 'b'])
        self.assertTrue(all(0 <= v <= 1 for b in out for v in b['box']))

    def test_parse_part_boxes_drops_invalid_labels(self):
        out = ai_prompts.parse_part_boxes(
            '[{"label": "", "box": [0, 0, 100, 100]},'
            ' {"label": "??", "box": [0, 0, 100, 100]}]')
        self.assertEqual(out, [])

    def test_parse_part_boxes_nested_letters_kept(self):
        out = ai_prompts.parse_part_boxes(
            '[{"label": "stem", "box": [0, 0, 1000, 100]},'
            ' {"label": "d", "box": [0, 100, 1000, 300]},'
            ' {"label": "di", "box": [0, 300, 1000, 500]},'
            ' {"label": "dii", "box": [0, 500, 1000, 700]}]')
        self.assertEqual([b['label'] for b in out], ['stem', 'd', 'di', 'dii'])

    def test_pdf_part_prompts_render_with_expected_note(self):
        with mock.patch.object(ai_prompts, '_load_resolved', return_value=None):
            sys_q = ai_prompts.build_pdf_part_system('QUE', ['stem', 'a', 'di'])
            self.assertIn('stem, a, di', sys_q)
            self.assertIn('NESTED PARTS', sys_q)
            sys_q2 = ai_prompts.build_pdf_part_system('QUE')
            self.assertNotIn('{{', sys_q2)
            page_sys = ai_prompts.build_pdf_box_system('QUE', expected_labels=['5', '6'])
            self.assertIn('question(s) 5, 6', page_sys)
            page_sys_plain = ai_prompts.build_pdf_box_system('QUE')
            self.assertNotIn('{{', page_sys_plain)
            user = ai_prompts.build_pdf_box_user_text('SOL', expected_labels=['7'])
            self.assertIn('question(s) 7', user)

    def test_parse_agent_outline(self):
        raw = ('```json\n{"pages": [{"page": 1, "kind": "instructions"}, '
               '{"page": 2, "kind": "question"}, {"page": 3, "kind": "Question "}],'
               ' "questions": [{"label": "Q1", "pages": [2, 3], "marks": 12,'
               ' "parts": [{"label": "(a)", "parts": [{"label": "i"}, {"label": "ii"}]},'
               ' {"label": "b"}, {"label": "b"}], "depends_prev": ["b"]},'
               ' {"label": "23-24", "pages": [3], "parts": []},'
               ' {"label": "bad label", "pages": [3]}],'
               ' "paper": {"year": "2023", "paper": "p1", "section": null}}\n```')
        out = ai_prompts.parse_agent_outline(raw)
        self.assertEqual([p['kind'] for p in out['pages']],
                         ['instructions', 'question', 'question'])
        self.assertEqual([q['label'] for q in out['questions']], ['1', '23-24'])
        q1 = out['questions'][0]
        self.assertEqual(q1['pages'], [2, 3])
        self.assertEqual(q1['marks'], 12)
        self.assertEqual([p['label'] for p in q1['parts']], ['a', 'b'])
        self.assertEqual([p['label'] for p in q1['parts'][0]['parts']], ['i', 'ii'])
        self.assertEqual(q1['depends_prev'], ['b'])
        self.assertEqual(out['paper'], {'year': 2023, 'paper': 'P1', 'section': None})
        self.assertIsNone(ai_prompts.parse_agent_outline('no json here'))

    def test_parse_agent_verify(self):
        raw = ('{"ok": true, "issues": [{"box": 2, "label": "a", "problem": '
               '"wrong extent", "fix": "extend_bottom", "note": "marks cut"},'
               ' {"box": null, "label": "c", "problem": "missing", "fix": "redetect"},'
               ' {"box": 3, "label": "b", "problem": "weird", "fix": "teleport"}],'
               ' "depends_prev": ["c", "stem", "(b)"]}')
        out = ai_prompts.parse_agent_verify(raw)
        self.assertFalse(out['ok'])  # fixes pending override the model's ok
        self.assertEqual([i['problem'] for i in out['issues']],
                         ['wrong_extent', 'missing', 'extra'])
        self.assertEqual([i['fix'] for i in out['issues']],
                         ['extend_bottom', 'redetect', 'none'])
        self.assertEqual(out['depends_prev'], ['c', 'b'])
        clean = ai_prompts.parse_agent_verify('{"ok": true, "issues": []}')
        self.assertTrue(clean['ok'])
        self.assertIsNone(ai_prompts.parse_agent_verify(''))

    def test_pdf_agent_verify_user_includes_page_note(self):
        with mock.patch.object(ai_prompts, '_load_resolved', return_value=None):
            text = ai_prompts.build_pdf_agent_verify_user_text(
                [(1, 'stem'), (2, 'a')], ['stem', 'a'],
                page_note='Parts on other pages (d, e) are not missing.')
            self.assertIn('Parts on other pages (d, e)', text)
            self.assertIn('1=stem, 2=a', text)
            self.assertNotIn('{{page_note}}', text)
            plain = ai_prompts.build_pdf_agent_verify_user_text(
                [(1, 'a')], ['a'])
            self.assertNotIn('{{', plain)


if __name__ == '__main__':
    unittest.main()
