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


if __name__ == '__main__':
    unittest.main()
