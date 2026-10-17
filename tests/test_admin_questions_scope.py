"""Pure tests for the single-subject Question Management helpers (ADR-016)."""
import unittest

from app.question_scope import (
    DEFAULT_PAGE_SIZE,
    dashboard_qid_pattern,
    ids_subject_violation,
    page_slice,
    parse_list_args,
    qid_search_pattern,
    root_sort_key,
    subjects_of,
)


class SubjectGuardTests(unittest.TestCase):
    ROWS = [(1, 'ICT'), (2, 'ICT'), (3, 'BAFS')]

    def test_all_inside_subject_passes(self):
        self.assertIsNone(ids_subject_violation([(1, 'ICT'), (2, 'ICT')], 'ICT'))

    def test_rows_outside_subject_are_refused(self):
        problem = ids_subject_violation(self.ROWS, 'ICT')
        self.assertEqual(problem['error_code'], 'outside_subject')
        self.assertEqual(problem['outside_count'], 1)
        self.assertEqual(problem['outside_subjects'], {'BAFS': 1})
        self.assertIn('not in ICT', problem['message'])

    def test_wrong_subject_refuses_everything(self):
        problem = ids_subject_violation([(1, 'ICT')], 'ECON')
        self.assertEqual(problem['outside_count'], 1)

    def test_without_subject_mixed_sets_are_refused(self):
        problem = ids_subject_violation(self.ROWS)
        self.assertEqual(problem['error_code'], 'mixed_subjects')
        self.assertEqual(problem['subjects'], {'BAFS': 1, 'ICT': 2})

    def test_without_subject_single_subject_passes(self):
        self.assertIsNone(ids_subject_violation([(1, 'ICT'), (2, 'ICT')]))
        self.assertIsNone(ids_subject_violation([]))

    def test_subjects_of_largest_first(self):
        self.assertEqual(list(subjects_of(self.ROWS).items()), [('ICT', 2), ('BAFS', 1)])


class ListArgsTests(unittest.TestCase):
    def test_defaults(self):
        f = parse_list_args({})
        self.assertEqual(f['subject'], '')
        self.assertEqual((f['sort'], f['dir'], f['page'], f['page_size']), ('qid', 'asc', 1, DEFAULT_PAGE_SIZE))
        self.assertEqual((f['tree_scope'], f['verified'], f['has_whole']), ('all', None, False))
        self.assertEqual((f['qids'], f['selected_ids'], f['years']), ([], [], []))

    def test_values_are_normalised(self):
        f = parse_list_args({
            'subject': ' ICT ', 'qids': 'A_1, B_2,,', 'selected_ids': '5,x,7', 'years': '2025,abc,2012',
            'source': 'dse', 'paper': 'p1b', 'q_type': 'mc', 'verified': '0', 'tree_scope': 'LEAVES',
            'has_whole': 'on', 'check_status': 'Issues', 'sort': 'YEAR', 'dir': 'DESC', 'page': '3', 'page_size': '200',
        })
        self.assertEqual(f['subject'], 'ICT')
        self.assertEqual(f['qids'], ['A_1', 'B_2'])
        self.assertEqual(f['selected_ids'], [5, 7])
        self.assertEqual(f['years'], [2025, 2012])
        self.assertEqual((f['source'], f['paper'], f['q_type']), ('DSE', 'P1B', 'MC'))
        self.assertIs(f['verified'], False)
        self.assertEqual((f['tree_scope'], f['has_whole'], f['check_status']), ('leaves', True, 'issues'))
        self.assertEqual((f['sort'], f['dir'], f['page'], f['page_size']), ('year', 'desc', 3, 200))

    def test_bad_values_fall_back_to_no_filter(self):
        f = parse_list_args({'q_type': 'essay', 'tree_scope': 'forest', 'check_status': 'meh',
                             'sort': 'selection_order', 'page': '-4', 'page_size': '5', 'verified': 'maybe'})
        self.assertEqual((f['q_type'], f['tree_scope'], f['check_status']), ('', 'all', ''))
        self.assertEqual((f['sort'], f['page'], f['page_size'], f['verified']), ('qid', 1, DEFAULT_PAGE_SIZE, None))

    def test_qid_search_pattern(self):
        self.assertEqual(qid_search_pattern('2025_P1B'), '%2025_P1B%')
        self.assertEqual(qid_search_pattern('*_Q7*'), '%_Q7%')
        self.assertEqual(qid_search_pattern('ICT%'), 'ICT%')

    def test_dashboard_qid_pattern_loose(self):
        self.assertEqual(dashboard_qid_pattern('2025 q1', False), '%2025%Q1%')
        self.assertEqual(dashboard_qid_pattern('Q16-17', False), '%Q16%17%')
        self.assertIsNone(dashboard_qid_pattern('  ', False))
        self.assertIsNone(dashboard_qid_pattern('-', False))

    def test_dashboard_qid_pattern_wildcard_wins_in_loose(self):
        self.assertEqual(dashboard_qid_pattern('*-*', False), '%-%')
        self.assertEqual(dashboard_qid_pattern('*_Q16-17', False), '%_Q16-17')

    def test_dashboard_qid_pattern_strict(self):
        self.assertEqual(dashboard_qid_pattern('ECON_DSE_2012_P1_Q16-17', True), '%ECON_DSE_2012_P1_Q16-17%')
        self.assertEqual(dashboard_qid_pattern('MATC_DSE_2024*', True), 'MATC_DSE_2024%')


class RootSortAndPagingTests(unittest.TestCase):
    ROOTS = [
        dict(id=1, source='DSE', year=2025, paper='P1A', qno=3, q_type='MC', created_at='2026-01-02'),
        dict(id=2, source='DSE', year=2012, paper='P1A', qno=10, q_type='CQ', created_at='2026-03-01'),
        dict(id=3, source='DSE', year=2025, paper='P1A', qno=1, q_type=None, created_at='2025-12-31'),
    ]

    def _order(self, sort, reverse=False):
        return [r['id'] for r in sorted(self.ROOTS, key=root_sort_key(sort), reverse=reverse)]

    def test_sorts(self):
        self.assertEqual(self._order('qid'), [2, 3, 1])
        self.assertEqual(self._order('qno'), [3, 1, 2])
        self.assertEqual(self._order('year', reverse=True), [1, 3, 2])
        self.assertEqual(self._order('q_type'), [2, 1, 3])
        self.assertEqual(self._order('created_at'), [3, 1, 2])

    def test_page_slice_clamps(self):
        items = list(range(1, 8))
        self.assertEqual(page_slice(items, 1, 3), ([1, 2, 3], 1, 3))
        self.assertEqual(page_slice(items, 3, 3), ([7], 3, 3))
        self.assertEqual(page_slice(items, 9, 3), ([7], 3, 3))
        self.assertEqual(page_slice([], 2, 50), ([], 1, 1))


if __name__ == '__main__':
    unittest.main()
