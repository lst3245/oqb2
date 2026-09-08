"""Pure-logic tests for the PDF import agent (no Flask app, no LLM, no DB)."""
import unittest

from app.pdf_agent import (
    Budget,
    BudgetExhausted,
    apply_verify_fixes,
    assign_unlabelled_from_outline,
    labels_on_other_pages,
    check_outline,
    compare_parts,
    expected_relative_labels,
    fix_overlaps,
    flatten_part_tree,
    mark_depends_prev,
    merge_outline,
    outline_questions_on_page,
    question_items,
    reconcile_pages,
    relative_labels_present,
)


def _q(label, pages, parts=None, deps=None, marks=None):
    return {'label': label, 'pages': list(pages), 'parts': parts or [],
            'depends_prev': deps or [], 'marks': marks}


class OutlineHelpersTests(unittest.TestCase):
    def test_flatten_and_expected(self):
        tree = [{'label': 'a', 'parts': [{'label': 'i'}, {'label': 'ii'}]},
                {'label': 'b', 'parts': []},
                {'label': 'd', 'parts': [{'label': 'i'}]}]
        self.assertEqual(flatten_part_tree(tree), ['a', 'ai', 'aii', 'b', 'd', 'di'])
        self.assertEqual(expected_relative_labels(_q('4', [1], tree)),
                         ['stem', 'a', 'ai', 'aii', 'b', 'd', 'di'])
        self.assertEqual(expected_relative_labels(_q('7', [2])), [])

    def test_questions_on_page(self):
        outline = {'questions': [_q('1', [2]), _q('2', [2, 3]), _q('3', [4])]}
        self.assertEqual(outline_questions_on_page(outline, 2), ['1', '2'])
        self.assertEqual(outline_questions_on_page(outline, 3), ['2'])
        self.assertEqual(outline_questions_on_page(outline, 9), [])

    def test_merge_outline_unions_pages_and_parts(self):
        a = {'pages': [{'page': 1, 'kind': 'instructions'}, {'page': 2, 'kind': 'question'}],
             'questions': [_q('1', [2], [{'label': 'a', 'parts': []}], marks=5)],
             'paper': {'year': None, 'paper': 'P1', 'section': None}}
        b = {'pages': [{'page': 3, 'kind': 'question'}],
             'questions': [_q('1', [3], [{'label': 'a', 'parts': [{'label': 'i'}]},
                                         {'label': 'b', 'parts': []}], deps=['b']),
                           _q('2', [3])],
             'paper': {'year': 2023, 'paper': None, 'section': None}}
        out = merge_outline(a, b)
        self.assertEqual([p['page'] for p in out['pages']], [1, 2, 3])
        q1 = out['questions'][0]
        self.assertEqual(q1['pages'], [2, 3])
        self.assertEqual(flatten_part_tree(q1['parts']), ['a', 'ai', 'b'])
        self.assertEqual(q1['marks'], 5)
        self.assertEqual(q1['depends_prev'], ['b'])
        self.assertEqual([q['label'] for q in out['questions']], ['1', '2'])
        self.assertEqual(out['paper'], {'year': 2023, 'paper': 'P1', 'section': None})
        self.assertEqual(merge_outline(None, None)['questions'], [])

    def test_check_outline_flags_gaps_and_order(self):
        outline = {
            'pages': [{'page': 1, 'kind': 'instructions'}, {'page': 2, 'kind': 'question'},
                      {'page': 3, 'kind': 'question'}, {'page': 4, 'kind': 'question'}],
            'questions': [_q('1', [2]), _q('3', [3, 4]), _q('4', [1])],
        }
        msgs = [m for _s, m, _l in check_outline(outline, 4)]
        self.assertTrue(any('missing from the outline: 2' in m for m in msgs))
        self.assertTrue(any('Q4 is placed on page 1' in m for m in msgs))
        self.assertTrue(any('Q4 starts on page 1, before' in m for m in msgs))
        clean = {'pages': [{'page': 1, 'kind': 'question'}],
                 'questions': [_q('1', [1]), _q('2', [1])]}
        self.assertEqual(check_outline(clean, 1), [])
        self.assertEqual(check_outline({'questions': []}, 3)[0][0], 'error')


class ReconcileTests(unittest.TestCase):
    def test_missing_and_extra(self):
        outline = {'questions': [_q('1', [1]), _q('2', [1, 2]), _q('3', [2])]}
        items = [{'page': 0, 'label': '1', 'box': [0, 0, 1, .3]},
                 {'page': 0, 'label': '2', 'box': [0, .3, 1, 1]},
                 {'page': 1, 'label': '2', 'box': [0, 0, 1, .2]},   # continuation
                 {'page': 1, 'label': '9', 'box': [0, .5, 1, 1]}]
        rec = reconcile_pages(outline, items, 2)
        self.assertEqual(rec['missing'], [('3', 1)])
        self.assertEqual(rec['extra'], [('9', 1)])

    def test_assign_unlabelled_from_outline(self):
        outline = {'questions': [_q('4', [1, 2]), _q('5', [2])]}
        items = [{'page': 1, 'label': None, 'qno': None, 'box': [0, 0, 1, .2]},
                 {'page': 1, 'label': '5', 'qno': 5, 'box': [0, .3, 1, 1]}]
        self.assertEqual(assign_unlabelled_from_outline(items, outline), 1)
        self.assertEqual(items[0]['label'], '4')
        self.assertEqual(items[0]['qno'], 4)
        # ambiguous (two spanning questions) → untouched
        outline2 = {'questions': [_q('4', [1, 2]), _q('5', [1, 2])]}
        items2 = [{'page': 1, 'label': None, 'box': [0, 0, 1, .2]}]
        self.assertEqual(assign_unlabelled_from_outline(items2, outline2), 0)


class PartCheckTests(unittest.TestCase):
    def setUp(self):
        self.items = [
            {'page': 0, 'label': '4', 'box': [.1, .10, .9, .20]},
            {'page': 0, 'label': '4a', 'box': [.1, .20, .9, .40]},
            {'page': 0, 'label': '4b', 'box': [.1, .40, .9, .60]},
            {'page': 0, 'label': '4d', 'box': [.1, .60, .9, .70]},
            {'page': 0, 'label': '4di', 'box': [.1, .70, .9, .90]},
            {'page': 0, 'label': '5', 'box': [.1, .90, .9, 1.0]},
        ]

    def test_question_items_and_relative_labels(self):
        q4 = question_items(self.items, '4')
        self.assertEqual([it['label'] for it in q4], ['4', '4a', '4b', '4d', '4di'])
        self.assertEqual(relative_labels_present(q4, '4'), ['stem', 'a', 'b', 'd', 'di'])
        self.assertEqual(relative_labels_present(question_items(self.items, '4d'), '4d'),
                         ['stem', 'i'])

    def test_compare_parts(self):
        missing, extra = compare_parts(['stem', 'a', 'b', 'c'], ['stem', 'a', 'b', 'd'])
        self.assertEqual(missing, ['c'])
        self.assertEqual(extra, ['d'])
        self.assertEqual(compare_parts(['stem', 'a'], ['a']), ([], []))
        # Q5 (a) has no intro — children present means the grouping letter is fine
        missing, extra = compare_parts(
            ['stem', 'a', 'ai', 'aii', 'aiii', 'b'],
            ['stem', 'ai', 'aii', 'aiii', 'b'])
        self.assertEqual(missing, [])
        self.assertEqual(extra, [])
        # roman 'i' is not an ancestor of 'ii'
        missing, extra = compare_parts(['i', 'ii'], ['ii'])
        self.assertEqual(missing, ['i'])

    def test_fix_overlaps_trims_upper_and_drops_degenerate(self):
        items = [{'page': 0, 'label': '4a', 'box': [.1, .20, .9, .50]},
                 {'page': 0, 'label': '4b', 'box': [.1, .40, .9, .60]},
                 {'page': 0, 'label': '4c', 'box': [.1, .60, .9, .602]}]
        pool = list(items)
        changed = fix_overlaps(items, pool=pool)
        self.assertEqual(changed, 2)
        self.assertAlmostEqual(items[0]['box'][3], .40)
        self.assertEqual([it['label'] for it in items], ['4a', '4b'])
        self.assertEqual(len(pool), 2)
        # small overlap tolerated
        items2 = [{'page': 0, 'label': '4a', 'box': [.1, .20, .9, .41]},
                  {'page': 0, 'label': '4b', 'box': [.1, .40, .9, .60]}]
        self.assertEqual(fix_overlaps(items2), 0)

    def test_apply_verify_fixes(self):
        q4 = question_items(self.items, '4')
        legend = list(enumerate(q4, 1))
        verify = {'ok': False, 'depends_prev': ['b'], 'issues': [
            {'box': 3, 'label': 'b', 'problem': 'mislabelled', 'fix': 'relabel',
             'new_label': 'c', 'note': ''},
            {'box': 2, 'label': 'a', 'problem': 'wrong_extent', 'fix': 'extend_bottom',
             'new_label': None, 'note': 'marks cut'},
            {'box': None, 'label': 'e', 'problem': 'missing', 'fix': 'redetect',
             'new_label': None, 'note': 'below the table'},
            {'box': 1, 'label': 'stem', 'problem': 'overlaps', 'fix': 'none',
             'new_label': None, 'note': 'slightly'},
        ]}
        res = apply_verify_fixes(self.items, '4', verify, legend)
        self.assertEqual(res['changed'], 1)  # relabel; extend_bottom is a no-op (already touching)
        self.assertEqual(self.items[2]['label'], '4c')
        self.assertEqual(len(res['redetect']), 1)
        self.assertIn('below the table', res['redetect'][0])
        self.assertEqual(len(res['unresolved']), 1)
        self.assertEqual(res['depends'], ['b'])

    def test_apply_verify_fixes_ignores_missing_on_other_pages(self):
        items = [
            {'page': 3, 'label': '2', 'box': [.1, .10, .9, .20]},
            {'page': 3, 'label': '2a', 'box': [.1, .25, .9, .40]},
            {'page': 4, 'label': '2d', 'box': [.1, .18, .9, .30]},
            {'page': 4, 'label': '2e', 'box': [.1, .32, .9, .50]},
        ]
        legend = [(1, items[0]), (2, items[1])]
        verify = {'ok': False, 'depends_prev': [], 'issues': [
            {'box': None, 'label': 'd', 'problem': 'missing', 'fix': 'redetect',
             'note': 'Part (d) is listed in the outline but is missing from the crop.'},
            {'box': None, 'label': 'e', 'problem': 'missing', 'fix': 'redetect',
             'note': 'Part (e) is listed in the outline but is missing from the crop.'},
        ]}
        elsewhere = labels_on_other_pages(items, '2', 3)
        self.assertEqual(elsewhere, ['d', 'e'])
        res = apply_verify_fixes(items, '2', verify, legend, ignore_missing=elsewhere)
        self.assertEqual(res['redetect'], [])
        self.assertEqual(res['changed'], 0)
        self.assertEqual([it['label'] for it in items], ['2', '2a', '2d', '2e'])

    def test_apply_verify_fixes_drop_and_extend(self):
        items = [{'page': 0, 'label': '4', 'box': [.1, .10, .9, .20]},
                 {'page': 0, 'label': '4a', 'box': [.1, .25, .9, .40]},
                 {'page': 0, 'label': '4b', 'box': [.1, .40, .9, .60]}]
        legend = list(enumerate(items, 1))
        verify = {'ok': False, 'depends_prev': [], 'issues': [
            {'box': 2, 'label': 'a', 'problem': 'wrong_extent', 'fix': 'extend_top',
             'new_label': None, 'note': ''},
            {'box': 3, 'label': 'b', 'problem': 'not_a_part', 'fix': 'drop',
             'new_label': None, 'note': ''}]}
        res = apply_verify_fixes(items, '4', verify, legend)
        self.assertEqual(res['changed'], 2)
        self.assertAlmostEqual(items[1]['box'][1], .20)
        self.assertEqual([it['label'] for it in items], ['4', '4a'])

    def test_mark_depends_prev_skips_first_parts(self):
        n = mark_depends_prev(self.items, '4', ['a', 'b', 'di', 'zz'])
        self.assertEqual(n, 1)
        self.assertTrue(self.items[2].get('depends_prev'))
        self.assertFalse(self.items[1].get('depends_prev'))
        self.assertFalse(self.items[4].get('depends_prev'))


class BudgetTests(unittest.TestCase):
    def test_budget(self):
        b = Budget(max_calls=3)
        b.charge(2)
        self.assertEqual(b.remaining, 1)
        with self.assertRaises(BudgetExhausted):
            b.charge(2)
        b.charge(1)
        self.assertEqual(b.remaining, 0)


if __name__ == '__main__':
    unittest.main()
