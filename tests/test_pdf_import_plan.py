"""Pure tests for PDF-import plan labels, grouping, and crop mapping.

Does not call create_app() or touch the live database.
"""
import unittest

from app.pdf_import import (
    _group_plan,
    _replace_group,
    _resolve_sol_commit_label,
    _split_parent_labels,
    _stitch_continuations,
    coerce_plan_item,
    expected_part_labels_for,
    full_width_child_box,
    map_crop_box_to_page,
    original_group_parts,
    plan_item_label,
    sanitize_plan,
)


class PlanLabelHelpersTests(unittest.TestCase):
    def test_coerce_legacy_int_qno(self):
        out = coerce_plan_item({'page': 0, 'qno': 5, 'box': [0, 0, 1, 1]})
        self.assertEqual(out['label'], '5')
        self.assertEqual(out['qno'], 5)

    def test_coerce_range_label(self):
        out = coerce_plan_item({'page': 0, 'qno': 23, 'label': '23-24',
                                'box': [0, 0, 1, 1]})
        self.assertEqual(out['label'], '23-24')
        self.assertEqual(out['qno'], 23)

    def test_plan_item_label_from_qno(self):
        self.assertEqual(plan_item_label({'qno': 3}), '3')
        self.assertEqual(plan_item_label({'label': '3a'}), '3a')
        self.assertIsNone(plan_item_label({'label': 'Table 1'}))

    def test_map_crop_box_to_page(self):
        parent = [0.1, 0.2, 0.5, 0.8]
        self.assertEqual(map_crop_box_to_page(parent, [0, 0, 1, 1]), parent)
        mapped = map_crop_box_to_page(parent, [0, 0, 1, 0.5])
        self.assertAlmostEqual(mapped[0], 0.1)
        self.assertAlmostEqual(mapped[1], 0.2)
        self.assertAlmostEqual(mapped[2], 0.5)
        self.assertAlmostEqual(mapped[3], 0.5)

    def test_full_width_child_box_keeps_parent_x(self):
        parent = [0.1, 0.2, 0.5, 0.8]
        out = full_width_child_box(parent, [0.3, 0.25, 0.9, 0.5])
        self.assertAlmostEqual(out[0], 0.1)
        self.assertAlmostEqual(out[2], 0.5)
        self.assertAlmostEqual(out[1], 0.2 + 0.25 * 0.6)
        self.assertAlmostEqual(out[3], 0.2 + 0.5 * 0.6)

    def test_group_plan_by_label_stems_first(self):
        plan = {
            'que': [
                {'page': 0, 'label': '3a', 'box': [0, 0.4, 1, 0.7]},
                {'page': 0, 'label': '3', 'box': [0, 0.1, 1, 0.4]},
                {'page': 0, 'qno': 4, 'box': [0, 0.7, 1, 0.9]},
            ],
            'sol': [],
        }
        groups = _group_plan(plan)
        labels = [g[2] for g in groups]
        self.assertEqual(labels, ['3', '3a', '4'])

    def test_stitch_copies_label(self):
        plan = {'que': [
            {'page': 0, 'qno': 3, 'label': '3', 'box': [0, 0.5, 1, 1],
             '_cn': True},
            {'page': 1, 'qno': None, 'label': None, 'box': [0, 0, 1, 0.3],
             '_cp': True},
        ], 'sol': []}
        _stitch_continuations(plan)
        self.assertEqual(plan['que'][1]['label'], '3')
        self.assertEqual(plan['que'][1]['qno'], 3)
        self.assertTrue(plan['que'][1]['cont'])
        self.assertNotIn('cont', plan['que'][0])

    def test_sanitize_keeps_cont_flag_only_when_set(self):
        clean = sanitize_plan({'que': [
            {'page': 1, 'label': '3', 'box': [0, 0, 1, 0.3], 'cont': True},
            {'page': 1, 'label': '4', 'box': [0, 0.3, 1, 0.6], 'cont': False},
        ], 'sol': []})
        self.assertTrue(clean['que'][0]['cont'])
        self.assertNotIn('cont', clean['que'][1])

    def test_stitch_inherits_head_x_span(self):
        plan = {'que': [
            {'page': 0, 'qno': 3, 'label': '3', 'box': [0.1, 0.5, 0.9, 1],
             '_cn': True},
            # continuation boxed only the indented body
            {'page': 1, 'qno': None, 'label': None, 'box': [0.25, 0.05, 0.8, 0.3],
             '_cp': True},
        ], 'sol': []}
        _stitch_continuations(plan)
        self.assertEqual(plan['que'][1]['box'], [0.1, 0.05, 0.9, 0.3])

    def test_sol_unmatched_part_dropped_whole_attaches_to_stem(self):
        que = {'3', '3a'}
        self.assertEqual(_resolve_sol_commit_label('3a', que), '3a')
        self.assertEqual(_resolve_sol_commit_label('3', que), '3')
        self.assertIsNone(_resolve_sol_commit_label('3b', que))
        self.assertEqual(_resolve_sol_commit_label('3', {'3a'}), '3')

    def test_sanitize_keeps_part_label(self):
        clean = sanitize_plan({
            'que': [{'page': 0, 'label': '3a', 'qno': 3,
                     'box': [0.1, 0.1, 0.9, 0.5], 'role': 'part'}],
            'sol': [],
        })
        self.assertEqual(clean['que'][0]['label'], '3a')
        self.assertEqual(clean['que'][0]['qno'], 3)
        self.assertEqual(clean['que'][0]['role'], 'part')

    def test_sanitize_generic_keeps_free_text(self):
        clean = sanitize_plan(
            {'que': [{'page': 0, 'label': 'Table 1', 'box': [0, 0, 1, 1]}],
             'sol': []},
            generic=True)
        self.assertEqual(clean['que'][0]['label'], 'Table 1')

    def test_expected_part_labels(self):
        que = [
            {'label': '3', 'box': [0, 0, 1, 0.2], 'page': 0},
            {'label': '3a', 'box': [0, 0.2, 1, 0.5], 'page': 0},
            {'label': '3ci', 'box': [0, 0.5, 1, 0.8], 'page': 0},
        ]
        self.assertEqual(expected_part_labels_for('3', que),
                         ['stem', 'a', 'ci'])

    def test_split_parent_labels_skip_already_split(self):
        items = [
            {'page': 0, 'label': '3', 'role': 'stem', 'source_label': '3',
             'box': [0, 0, 1, 0.3]},
            {'page': 0, 'label': '3a', 'role': 'part', 'source_label': '3',
             'box': [0, 0.3, 1, 0.6]},
            {'page': 0, 'label': '4', 'box': [0, 0.6, 1, 0.9]},
        ]
        self.assertEqual(_split_parent_labels(items), ['4'])
        self.assertEqual(_split_parent_labels(items, {'3'}), ['3'])

    def test_original_group_parts_prefers_source_box(self):
        items = [
            {'page': 0, 'label': '3a', 'source_label': '3',
             'source_page': 0, 'source_box': [0.1, 0.1, 0.9, 0.8],
             'box': [0.1, 0.4, 0.9, 0.8]},
            {'page': 0, 'label': '3', 'source_label': '3',
             'source_page': 0, 'source_box': [0.1, 0.1, 0.9, 0.8],
             'box': [0.1, 0.1, 0.9, 0.4]},
        ]
        parts = original_group_parts(items, '3')
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0]['box'], [0.1, 0.1, 0.9, 0.8])

    def test_replace_group_swaps_children(self):
        items = [
            {'page': 0, 'label': '3', 'box': [0, 0, 1, 0.5]},
            {'page': 0, 'label': '4', 'box': [0, 0.5, 1, 1]},
        ]
        new = [{'page': 0, 'label': '3a', 'role': 'part', 'source_label': '3',
                'box': [0, 0.2, 1, 0.5]}]
        out = _replace_group(items, '3', new)
        labels = [plan_item_label(it) for it in out]
        self.assertEqual(labels, ['4', '3a'])


class PageRangeTests(unittest.TestCase):
    def test_blank_means_all(self):
        from app.pdf_import import parse_page_range
        self.assertIsNone(parse_page_range('', 12))
        self.assertIsNone(parse_page_range('  ', 12))
        self.assertIsNone(parse_page_range(None, 12))

    def test_list_and_range(self):
        from app.pdf_import import parse_page_range
        self.assertEqual(parse_page_range('1-3,5,8-9', 10), [0, 1, 2, 4, 7, 8])

    def test_clamps_to_pdf_length(self):
        from app.pdf_import import parse_page_range
        self.assertEqual(parse_page_range('1-5', 3), [0, 1, 2])

    def test_out_of_range_is_error(self):
        from app.pdf_import import parse_page_range
        with self.assertRaises(ValueError):
            parse_page_range('20', 5)
        with self.assertRaises(ValueError):
            parse_page_range('abc', 5)


class SplitPipelineHelperTests(unittest.TestCase):
    def test_single_pass1_box_is_parent(self):
        from app.pdf_import import pick_pass1_parent_box
        self.assertEqual(
            pick_pass1_parent_box([{'box': [0.1, 0.2, 0.9, 0.8]}]),
            [0.1, 0.2, 0.9, 0.8])

    def test_zero_or_many_pass1_boxes_use_full_image(self):
        from app.pdf_import import pick_pass1_parent_box
        self.assertEqual(pick_pass1_parent_box([]), [0.0, 0.0, 1.0, 1.0])
        self.assertEqual(
            pick_pass1_parent_box([
                {'box': [0.1, 0.1, 0.9, 0.4]},
                {'box': [0.1, 0.4, 0.9, 0.8]},
            ]),
            [0.0, 0.0, 1.0, 1.0])

    def test_merge_unions_same_label_stem_first(self):
        from app.pdf_import import merge_part_boxes_by_label
        out = merge_part_boxes_by_label([
            {'label': 'a', 'box': [0.1, 0.5, 0.9, 0.7]},
            {'label': 'stem', 'box': [0.1, 0.0, 0.9, 0.3]},
            {'label': 'a', 'box': [0.1, 0.8, 0.9, 1.0]},
        ])
        self.assertEqual([x['label'] for x in out], ['stem', 'a'])
        self.assertEqual(out[1]['box'], [0.1, 0.5, 0.9, 1.0])

    def test_map_page_box_to_stitch(self):
        from app.pdf_import import map_page_box_to_stitch
        # Second page of two equal 100x200 pages stitched to 100x400.
        box = map_page_box_to_stitch(100, 200, 100, 400, 200, [0, 0, 1, 0.5])
        self.assertAlmostEqual(box[0], 0.0)
        self.assertAlmostEqual(box[1], 0.5)
        self.assertAlmostEqual(box[2], 1.0)
        self.assertAlmostEqual(box[3], 0.75)

    def test_split_png_skips_pass1_by_default(self):
        import inspect
        from app.pdf_import import split_question_png
        self.assertFalse(
            inspect.signature(split_question_png).parameters['find_parent'].default)


class MissingStemFillTests(unittest.TestCase):
    PARENT = [0.1, 0.2, 0.9, 0.8]

    def test_strip_above_first_part(self):
        from app.pdf_import import missing_stem_box
        out = missing_stem_box(self.PARENT, [('a', [0.1, 0.35, 0.9, 0.5]),
                                             ('b', [0.1, 0.5, 0.9, 0.8])])
        self.assertEqual(out, [0.1, 0.2, 0.9, 0.35])

    def test_thin_strip_is_not_a_stem(self):
        from app.pdf_import import missing_stem_box
        self.assertIsNone(missing_stem_box(self.PARENT, [('a', [0.1, 0.21, 0.9, 0.5])]))

    def test_model_stem_or_no_parts_leaves_it(self):
        from app.pdf_import import missing_stem_box
        self.assertIsNone(missing_stem_box(self.PARENT, [('stem', [0.1, 0.2, 0.9, 0.3]),
                                                         ('a', [0.1, 0.3, 0.9, 0.5])]))
        self.assertIsNone(missing_stem_box(self.PARENT, []))

    def _run_group(self, kind, parent_label, parts, replies, ink=True):
        from unittest import mock
        from app import pdf_import
        calls = iter(replies)
        with mock.patch.object(pdf_import, '_write_split_crop', return_value='__nope__.png'), \
                mock.patch.object(pdf_import, 'page_png_path', return_value='__page__.png'), \
                mock.patch.object(pdf_import, '_strip_has_ink', return_value=ink), \
                mock.patch.object(pdf_import, 'detect_parts',
                                  side_effect=lambda *a, **k: (next(calls), '')):
            children, _raws = pdf_import._split_one_group(
                'tok', kind, parent_label, parts, config=None, image_max_dim=100)
        return children

    PARTS_ONLY = [{'label': 'a', 'box': [0, 0.4, 1, 0.7]},
                  {'label': 'b', 'box': [0, 0.7, 1, 1.0]}]

    def test_group_fills_stem_on_first_page_only(self):
        parts = [{'page': 0, 'box': [0.1, 0.5, 0.9, 1.0]},
                 {'page': 1, 'box': [0.1, 0.0, 0.9, 0.5]}]
        page2 = [{'label': 'c', 'box': [0, 0.3, 1, 1.0]}]
        children = self._run_group('que', '5', parts, [self.PARTS_ONLY, page2])
        stems = [c for c in children if c['label'] == '5']
        self.assertEqual(len(stems), 1)
        self.assertEqual(stems[0]['page'], 0)
        self.assertAlmostEqual(stems[0]['box'][1], 0.5)
        self.assertAlmostEqual(stems[0]['box'][3], 0.5 + 0.4 * 0.5)
        self.assertEqual(stems[0]['source_label'], '5')
        self.assertTrue(stems[0].get('_stem_filled'))
        self.assertEqual(stems[0]['role'], 'stem')

    def test_group_no_fill_for_sol_nested_or_blank_strip(self):
        parts = [{'page': 0, 'box': [0.1, 0.2, 0.9, 0.8]}]
        for kind, label, ink in (('sol', '5', True), ('que', '4d', True), ('que', '5', False)):
            children = self._run_group(kind, label, parts, [self.PARTS_ONLY], ink=ink)
            self.assertFalse(any(c.get('_stem_filled') for c in children), (kind, label, ink))

    def test_has_ink_ignores_margin_number(self):
        from app import pdf_layout
        if not pdf_layout.numpy_available():
            self.skipTest('NumPy not installed')
        import numpy as np
        gray = np.full((200, 400), 255, dtype=np.uint8)
        box = [0.0, 0.0, 1.0, 0.25]
        self.assertFalse(pdf_layout.has_ink(gray, box))
        gray[10:30, 5:25] = 0          # question number inside the left 8%
        self.assertFalse(pdf_layout.has_ink(gray, box))
        gray[10:30, 60:300] = 0        # a line of text
        self.assertTrue(pdf_layout.has_ink(gray, box))


class SharedBlockSplitTests(unittest.TestCase):
    """A ``stem`` box printed after a part is the shared block before the
    next part (ADR-015): ICT 2025 P1B Q7's second passage before (c)."""

    _run_group = MissingStemFillTests._run_group

    def _labels(self, children):
        return [c['label'] for c in sorted(children, key=lambda c: (c['page'], c['box'][1]))]

    def test_stem_on_continuation_page_becomes_block(self):
        parts = [{'page': 0, 'box': [0.1, 0.1, 0.9, 1.0]},
                 {'page': 1, 'box': [0.1, 0.0, 0.9, 0.9]}]
        page1 = [{'label': 'stem', 'box': [0, 0.0, 1, 0.3]},
                 {'label': 'a', 'box': [0, 0.3, 1, 0.6]},
                 {'label': 'b', 'box': [0, 0.6, 1, 1.0]}]
        page2 = [{'label': 'stem', 'box': [0, 0.0, 1, 0.4]},
                 {'label': 'c', 'box': [0, 0.4, 1, 0.7]},
                 {'label': 'd', 'box': [0, 0.7, 1, 1.0]}]
        children = self._run_group('que', '7', parts, [page1, page2])
        self.assertEqual(self._labels(children), ['7', '7a', '7b', '7~c', '7c', '7d'])
        roles = {c['label']: c['role'] for c in children}
        self.assertEqual(roles['7~c'], 'stem')
        self.assertFalse(any('_pi' in c for c in children))

    def test_stem_below_a_part_on_same_page(self):
        parts = [{'page': 0, 'box': [0.1, 0.0, 0.9, 1.0]}]
        boxes = [{'label': 'stem', 'box': [0, 0.0, 1, 0.2]},
                 {'label': 'a', 'box': [0, 0.2, 1, 0.4]},
                 {'label': 'stem', 'box': [0, 0.4, 1, 0.6]},
                 {'label': 'b', 'box': [0, 0.6, 1, 1.0]}]
        children = self._run_group('que', '7', parts, [boxes])
        self.assertEqual(self._labels(children), ['7', '7a', '7~b', '7b'])

    def test_stem_spilling_over_before_a_stays_stem(self):
        parts = [{'page': 0, 'box': [0.1, 0.8, 0.9, 1.0]},
                 {'page': 1, 'box': [0.1, 0.0, 0.9, 0.9]}]
        page1 = [{'label': 'stem', 'box': [0, 0.0, 1, 1.0]}]
        page2 = [{'label': 'stem', 'box': [0, 0.0, 1, 0.3]},
                 {'label': 'a', 'box': [0, 0.3, 1, 1.0]}]
        children = self._run_group('que', '7', parts, [page1, page2])
        self.assertEqual(self._labels(children), ['7', '7', '7a'])

    def test_nested_and_sol(self):
        parts = [{'page': 0, 'box': [0.1, 0.0, 0.9, 1.0]}]
        boxes = [{'label': 'i', 'box': [0, 0.0, 1, 0.3]},
                 {'label': 'ii', 'box': [0, 0.3, 1, 0.5]},
                 {'label': 'stem', 'box': [0, 0.5, 1, 0.7]},
                 {'label': 'iii', 'box': [0, 0.7, 1, 1.0]}]
        children = self._run_group('que', '7d', parts, [boxes])
        self.assertIn('7d~iii', self._labels(children))
        sol = [{'label': 'a', 'box': [0, 0.0, 1, 0.4]},
               {'label': '~c', 'box': [0, 0.4, 1, 0.6]},
               {'label': 'c', 'box': [0, 0.6, 1, 1.0]}]
        children = self._run_group('sol', '7', parts, [sol])
        self.assertEqual(self._labels(children), ['7a', '7', '7c'])

    def test_split_tool_relative_labels(self):
        from app.pdf_import import _reclassify_relative_stems
        boxes = [{'label': 'stem', 'box': [0, 0.0, 1, 0.2]},
                 {'label': 'a', 'box': [0, 0.2, 1, 0.4]},
                 {'label': 'stem', 'box': [0, 0.4, 1, 0.6]},
                 {'label': 'b', 'box': [0, 0.6, 1, 1.0]}]
        self.assertEqual([b['label'] for b in _reclassify_relative_stems(boxes)],
                         ['stem', 'a', '~b', 'b'])

    def test_group_plan_orders_block_before_its_parts(self):
        plan = {'que': [{'page': 1, 'label': l, 'box': [0, i / 10, 1, (i + 1) / 10]}
                        for i, l in enumerate(['7d', '7c', '7~c', '7b', '7a', '7'])],
                'sol': []}
        self.assertEqual([g[2] for g in _group_plan(plan)],
                         ['7', '7a', '7b', '7~c', '7c', '7d'])
        que = [{'label': l, 'box': [0, 0, 1, 1]} for l in ('7', '7a', '7~c', '7c')]
        self.assertEqual(expected_part_labels_for('7', que), ['stem', 'a', 'c'])


class RangeSplitTests(unittest.TestCase):
    """Pass 2 on a shared-stimulus range (31-32): stem + numbered questions."""

    PARTS = [{'page': 0, 'box': [0.1, 0.2, 0.9, 0.8]}]

    def _run(self, parent_label, reply, ink=True):
        from unittest import mock
        from app import pdf_import
        seen = {}

        def fake_detect(*a, **k):
            seen.update(k)
            return reply, ''

        with mock.patch.object(pdf_import, '_write_split_crop', return_value='__nope__.png'), \
                mock.patch.object(pdf_import, 'page_png_path', return_value='__page__.png'), \
                mock.patch.object(pdf_import, '_strip_has_ink', return_value=ink), \
                mock.patch.object(pdf_import, 'detect_parts', side_effect=fake_detect):
            children, _raws = pdf_import._split_one_group(
                'tok', 'que', parent_label, self.PARTS, config=None,
                image_max_dim=100, expected_labels=['stem', 'a'])
        return children, seen

    def test_range_becomes_stem_plus_questions(self):
        reply = [{'label': 'stem', 'box': [0, 0, 1, 0.3]},
                 {'label': '31', 'box': [0, 0.3, 1, 0.6]},
                 {'label': '32', 'box': [0, 0.6, 1, 1.0]}]
        children, seen = self._run('31-32', reply)
        self.assertEqual(seen.get('range_span'), (31, 32))
        self.assertIsNone(seen.get('expected_labels'))
        roles = {c['label']: c['role'] for c in children}
        self.assertEqual(roles, {'31-32': 'stem', '31': 'question', '32': 'question'})
        self.assertTrue(all(c['source_label'] == '31-32' for c in children))
        self.assertEqual([c['qno'] for c in children], [31, 31, 32])

    def test_range_fills_shared_stem(self):
        reply = [{'label': '31', 'box': [0, 0.4, 1, 0.7]},
                 {'label': '32', 'box': [0, 0.7, 1, 1.0]}]
        children, _seen = self._run('31-32', reply)
        stem = [c for c in children if c['label'] == '31-32']
        self.assertEqual(len(stem), 1)
        self.assertTrue(stem[0].get('_stem_filled'))

    def test_range_stem_only_is_no_split(self):
        children, _seen = self._run('31-32', [{'label': 'stem', 'box': [0, 0, 1, 1]}])
        self.assertIsNone(children)

    def test_plain_question_keeps_part_mode(self):
        _children, seen = self._run('5', [{'label': 'a', 'box': [0, 0, 1, 1]}])
        self.assertIsNone(seen.get('range_span'))
        self.assertEqual(seen.get('expected_labels'), ['stem', 'a'])

    def test_range_with_own_question_boxes_is_skipped(self):
        from app.pdf_import import range_has_own_questions
        pass1 = [{'page': 0, 'label': '31-32', 'box': [0, 0, 1, 0.2]},
                 {'page': 0, 'label': '31', 'box': [0, 0.2, 1, 0.5]}]
        self.assertTrue(range_has_own_questions(pass1, '31-32'))
        split = [{'page': 0, 'label': '31-32', 'source_label': '31-32', 'box': [0, 0, 1, 0.2]},
                 {'page': 0, 'label': '31', 'source_label': '31-32', 'box': [0, 0.2, 1, 0.5]},
                 {'page': 0, 'label': '33', 'box': [0, 0.5, 1, 0.8]}]
        self.assertFalse(range_has_own_questions(split, '31-32'))

    def test_page_resplit_sends_range_children_with_the_range(self):
        items = [{'page': 0, 'label': '31-32', 'source_label': '31-32', 'box': [0, 0, 1, 0.2]},
                 {'page': 0, 'label': '31', 'source_label': '31-32', 'box': [0, 0.2, 1, 0.5]},
                 {'page': 0, 'label': '32', 'source_label': '31-32', 'box': [0, 0.5, 1, 0.8]}]
        self.assertEqual(_split_parent_labels(items, {'31-32', '31', '32'}), ['31-32'])
        self.assertEqual(_split_parent_labels(items, {'31'}), ['31'])

    def test_replace_range_keeps_separate_boxes(self):
        items = [{'page': 0, 'label': '31-32', 'box': [0, 0, 1, 0.8]},
                 {'page': 0, 'label': '33', 'box': [0, 0.8, 1, 1]}]
        new = [{'page': 0, 'label': '31-32', 'source_label': '31-32', 'box': [0, 0, 1, 0.3]},
               {'page': 0, 'label': '31', 'source_label': '31-32', 'box': [0, 0.3, 1, 0.8]}]
        out = _replace_group(items, '31-32', new)
        self.assertEqual([plan_item_label(it) for it in out], ['33', '31-32', '31'])


if __name__ == '__main__':
    unittest.main()
