"""Tests for question hierarchy grammar, sort keys, and render-plan expansion."""
import unittest
from types import SimpleNamespace

from app.hierarchy import (
    HierarchyError,
    QNO_TOKEN_RE,
    breadcrumb_parts,
    build_qid,
    compose_part_label,
    derive_roles,
    format_qno_token,
    label_is_ancestor,
    next_part_label,
    group_for_dashboard,
    normalize_part_box_label,
    paginate_by_root,
    normalize_plan_label,
    parse_qid,
    parse_qno_token,
    part_segments,
    part_sort_key,
    rename_shape_ok,
    resolve_render_plan,
    rewrite_descendant_token,
    seq_owner_id,
    sort_key,
    split_part_path,
    token_fits_under,
)
from app.ingestor import construct_qid, parse_filename
from app.utils import SORT_FIELDS, apply_multi_sort


def _q(**kw):
    defaults = dict(
        parent=None, parent_id=None, children=[], part=None, qno_end=None,
        subject='ECON', source='DSE', year=2023, paper='P1', qid='',
        qno=1, id=None,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _link(parent, *children):
    for c in children:
        c.parent = parent
        c.parent_id = parent.id
        parent.children.append(c)


class TokenGrammarTests(unittest.TestCase):
    def test_plain_and_range_and_parts(self):
        self.assertEqual(parse_qno_token('5').token, 'Q5')
        self.assertEqual(parse_qno_token('Q5').token, 'Q5')
        self.assertEqual(parse_qno_token(5).token, 'Q5')
        p = parse_qno_token('23-24')
        self.assertEqual((p.qno, p.qno_end, p.part_path), (23, 24, None))
        self.assertEqual(p.token, 'Q23-24')
        p = parse_qno_token('Q3ci')
        self.assertEqual(p.part_path, 'ci')
        self.assertEqual(p.own_part, 'i')
        self.assertEqual(p.parent_part_path, 'c')

    def test_rejects_range_plus_part_and_inverted_range(self):
        self.assertIsNone(parse_qno_token('Q23-24a'))
        self.assertIsNone(parse_qno_token('Q24-23'))
        self.assertIsNone(parse_qno_token('Q0'))
        self.assertIsNone(parse_qno_token('QUE'))
        self.assertIsNone(parse_qno_token(''))

    def test_format_round_trip(self):
        self.assertEqual(format_qno_token(3, None, 'ci'), 'Q3ci')
        self.assertEqual(format_qno_token(23, 24, None), 'Q23-24')
        with self.assertRaises(HierarchyError):
            format_qno_token(23, 24, 'a')

    def test_qid_parse_and_build(self):
        p = parse_qid('ECON_DSE_2023_P1_Q23-24')
        self.assertEqual(p.subject, 'ECON')
        self.assertEqual(p.qno.token, 'Q23-24')
        self.assertEqual(p.prefix, 'ECON_DSE_2023_P1')
        self.assertEqual(
            build_qid('MATC', 'QB', None, None, 'BOOK1', 'Q3a'),
            'MATC_QB_BOOK1_Q3a',
        )
        self.assertIsNone(parse_qid('MATC_DSE_2024_P1_Q5_EN_QUE'))  # filename, not QID
        self.assertIsNone(parse_qid('matc_DSE_2024_P1_Q5'))  # subject must be upper

    def test_token_search_skips_que(self):
        m = QNO_TOKEN_RE.search('MATC_DSE_2024_P1_Q5_EN_QUE')
        self.assertEqual(m.group(1), 'Q5')
        m = QNO_TOKEN_RE.search('ECON_DSE_2023_P1_Q23-24_ENO_QUE')
        self.assertEqual(m.group(1), 'Q23-24')
        self.assertIsNone(QNO_TOKEN_RE.search('QUE'))


class PlanLabelTests(unittest.TestCase):
    def test_normalize_plan_label(self):
        self.assertEqual(normalize_plan_label(5), '5')
        self.assertEqual(normalize_plan_label('Q23-24'), '23-24')
        self.assertEqual(normalize_plan_label('3a'), '3a')
        self.assertIsNone(normalize_plan_label('stem'))
        self.assertIsNone(normalize_plan_label(''))

    def test_normalize_part_box_label(self):
        self.assertEqual(normalize_part_box_label('stem'), 'stem')
        self.assertEqual(normalize_part_box_label('preamble'), 'stem')
        self.assertEqual(normalize_part_box_label('(a)'), 'a')
        self.assertEqual(normalize_part_box_label('3a'), 'a')
        self.assertEqual(normalize_part_box_label('3'), 'stem')
        self.assertEqual(normalize_part_box_label('ci'), 'ci')
        self.assertIsNone(normalize_part_box_label('??'))

    def test_compose_part_label(self):
        self.assertEqual(compose_part_label('3', 'stem'), '3')
        self.assertEqual(compose_part_label('3', 'a'), '3a')
        self.assertEqual(compose_part_label('3', 'ci'), '3ci')
        self.assertEqual(compose_part_label('3', '3a'), '3a')
        self.assertEqual(compose_part_label('3c', 'i'), '3ci')
        self.assertIsNone(compose_part_label('23-24', 'a'))
        self.assertEqual(compose_part_label('23-24', 'stem'), '23-24')


class PartSegmentationTests(unittest.TestCase):
    def test_letter_roman_and_root_roman(self):
        self.assertEqual(split_part_path('ci'), ('c', 'i'))
        self.assertEqual(split_part_path('cii'), ('c', 'ii'))
        self.assertEqual(split_part_path('civ'), ('c', 'iv'))
        self.assertEqual(split_part_path('i'), ('', 'i'))
        self.assertEqual(split_part_path('ii'), ('', 'ii'))
        self.assertEqual(split_part_path('a'), ('', 'a'))
        self.assertEqual(part_segments('ci'), ['c', 'i'])
        self.assertEqual(part_segments('cii'), ['c', 'ii'])
        self.assertEqual(part_segments('ii'), ['ii'])

    def test_part_sort_order(self):
        letters = [part_sort_key(x) for x in ('a', 'b', 'c')]
        self.assertEqual(letters, sorted(letters))
        romans = [part_sort_key(x) for x in ('i', 'ii', 'iii', 'iv', 'v')]
        self.assertEqual(romans, sorted(romans))
        # letters (kind 0) before romans (kind 1) if mixed at one level
        self.assertLess(part_sort_key('a'), part_sort_key('i'))


class RewriteAndRenameShapeTests(unittest.TestCase):
    def test_root_rename_rewrites_parts(self):
        self.assertEqual(rewrite_descendant_token('Q3a', 'Q3', 'Q5'), 'Q5a')
        self.assertEqual(rewrite_descendant_token('Q3ci', 'Q3', 'Q5'), 'Q5ci')
        self.assertEqual(rewrite_descendant_token('Q3c', 'Q3c', 'Q3d'), 'Q3d')
        self.assertEqual(rewrite_descendant_token('Q3ci', 'Q3c', 'Q3d'), 'Q3di')

    def test_range_rename_shifts_children(self):
        self.assertEqual(rewrite_descendant_token('Q23', 'Q23-24', 'Q25-26'), 'Q25')
        self.assertEqual(rewrite_descendant_token('Q24', 'Q23-24', 'Q25-26'), 'Q26')
        with self.assertRaises(HierarchyError):
            rewrite_descendant_token('Q23', 'Q23-24', 'Q25-27')

    def test_shape_guards(self):
        self.assertIsNone(rename_shape_ok('Q3', 'Q5', False))
        self.assertIsNotNone(rename_shape_ok('Q3', 'Q3a', False))
        self.assertIsNotNone(rename_shape_ok('Q3', 'Q5-6', True))
        self.assertIsNone(rename_shape_ok('Q3c', 'Q3d', True))
        self.assertIsNotNone(rename_shape_ok('Q3c', 'Q3ci', True))


class SortKeyTests(unittest.TestCase):
    def test_stem_before_children_and_roman_order(self):
        root = _q(id=1, qno=3, qid='ECON_DSE_2023_P1_Q3', part=None, parent_id=None)
        a = _q(id=2, qno=3, qid='ECON_DSE_2023_P1_Q3a', part='a')
        c = _q(id=3, qno=3, qid='ECON_DSE_2023_P1_Q3c', part='c')
        ci = _q(id=4, qno=3, qid='ECON_DSE_2023_P1_Q3ci', part='i')
        civ = _q(id=5, qno=3, qid='ECON_DSE_2023_P1_Q3civ', part='iv')
        _link(root, a, c)
        _link(c, ci, civ)
        ordered = sorted([civ, ci, a, c, root], key=sort_key)
        self.assertEqual(
            [q.qid for q in ordered],
            [
                'ECON_DSE_2023_P1_Q3',
                'ECON_DSE_2023_P1_Q3a',
                'ECON_DSE_2023_P1_Q3c',
                'ECON_DSE_2023_P1_Q3ci',
                'ECON_DSE_2023_P1_Q3civ',
            ],
        )

    def test_range_stem_before_its_mc_children(self):
        stem = _q(id=10, qno=23, qno_end=24, qid='ECON_DSE_2023_P1_Q23-24', parent_id=None)
        q23 = _q(id=11, qno=23, qid='ECON_DSE_2023_P1_Q23')
        q24 = _q(id=12, qno=24, qid='ECON_DSE_2023_P1_Q24')
        _link(stem, q23, q24)
        ordered = sorted([q24, q23, stem], key=sort_key)
        self.assertEqual(
            [q.qid for q in ordered],
            [
                'ECON_DSE_2023_P1_Q23-24',
                'ECON_DSE_2023_P1_Q23',
                'ECON_DSE_2023_P1_Q24',
            ],
        )

    def test_apply_multi_sort_qid_field_uses_hierarchy_key(self):
        root = _q(id=1, qno=3, qid='ECON_DSE_2023_P1_Q3')
        a = _q(id=2, qno=3, qid='ECON_DSE_2023_P1_Q3a', part='a')
        _link(root, a)
        result = apply_multi_sort([a, root], [{'field': 'qid', 'direction': 'asc'}])
        self.assertEqual([q.id for q in result], [1, 2])


class RenderPlanTests(unittest.TestCase):
    def setUp(self):
        self.root = _q(id=1, qno=3, qid='ECON_DSE_2023_P1_Q3')
        self.a = _q(id=2, qno=3, qid='ECON_DSE_2023_P1_Q3a', part='a')
        self.b = _q(id=3, qno=3, qid='ECON_DSE_2023_P1_Q3b', part='b')
        _link(self.root, self.a, self.b)
        self.other = _q(id=9, qno=9, qid='ECON_DSE_2023_P1_Q9')

    def test_selected_leaf_inserts_stem_once(self):
        plan = resolve_render_plan([self.a, self.b], mode='selected')
        self.assertEqual(
            [(it.role, it.question.id) for it in plan],
            [('stem', 1), ('leaf', 2), ('leaf', 3)],
        )
        self.assertEqual(plan[1].seq_owner_id, 1)  # part shares the root
        self.assertEqual(plan[2].seq_owner_id, 1)

    def test_non_contiguous_reemits_stem(self):
        plan = resolve_render_plan([self.a, self.other, self.b], mode='selected')
        roles = [(it.role, it.question.id) for it in plan]
        self.assertEqual(roles, [
            ('stem', 1), ('leaf', 2),
            ('leaf', 9),
            ('stem', 1), ('leaf', 3),
        ])
        self.assertEqual(seq_owner_id(self.other), 9)

    def test_selected_stem_expands_to_whole(self):
        plan = resolve_render_plan([self.root], mode='selected')
        self.assertEqual(
            [(it.role, it.question.id) for it in plan],
            [('stem', 1), ('leaf', 2), ('leaf', 3)],
        )

    def test_whole_mode_from_one_leaf(self):
        plan = resolve_render_plan([self.a], mode='whole')
        self.assertEqual(
            [(it.role, it.question.id) for it in plan],
            [('stem', 1), ('leaf', 2), ('leaf', 3)],
        )

    def test_range_mc_leaves_own_their_seq(self):
        stem = _q(id=10, qno=23, qno_end=24, qid='ECON_DSE_2023_P1_Q23-24')
        q23 = _q(id=11, qno=23, qid='ECON_DSE_2023_P1_Q23', part=None)
        q24 = _q(id=12, qno=24, qid='ECON_DSE_2023_P1_Q24', part=None)
        _link(stem, q23, q24)
        plan = resolve_render_plan([q24], mode='selected')
        self.assertEqual(
            [(it.role, it.question.id, it.seq_owner_id) for it in plan],
            [('stem', 10, None), ('leaf', 12, 12)],
        )

    def test_needs_prev_parts_pulls_earlier_siblings_as_background(self):
        c = _q(id=4, qno=3, qid='ECON_DSE_2023_P1_Q3c', part='c',
               needs_prev_parts=True)
        _link(self.root, c)
        plan = resolve_render_plan([c], mode='selected')
        self.assertEqual(
            [(it.role, it.question.id) for it in plan],
            [('stem', 1), ('stem', 2), ('stem', 3), ('leaf', 4)],
        )
        # unflagged sibling keeps ancestors-only behaviour
        plan_b = resolve_render_plan([self.b], mode='selected')
        self.assertEqual([(it.role, it.question.id) for it in plan_b],
                         [('stem', 1), ('leaf', 3)])

    def test_needs_prev_parts_not_duplicated_when_sibling_selected(self):
        c = _q(id=4, qno=3, qid='ECON_DSE_2023_P1_Q3c', part='c',
               needs_prev_parts=True)
        _link(self.root, c)
        plan = resolve_render_plan([self.a, c], mode='selected')
        self.assertEqual(
            [(it.role, it.question.id) for it in plan],
            [('stem', 1), ('leaf', 2), ('stem', 3), ('leaf', 4)],
        )


class DerivedRoleTests(unittest.TestCase):
    def test_label_is_ancestor(self):
        self.assertTrue(label_is_ancestor('5', '5a'))
        self.assertTrue(label_is_ancestor('5', '5di'))
        self.assertTrue(label_is_ancestor('5d', '5di'))
        self.assertFalse(label_is_ancestor('5d', '5d'))
        self.assertFalse(label_is_ancestor('5a', '5b'))
        self.assertFalse(label_is_ancestor('5', '6a'))
        self.assertTrue(label_is_ancestor('23-24', '24'))
        self.assertFalse(label_is_ancestor('23-24', '24a'))
        self.assertFalse(label_is_ancestor('5', '23-24'))

    def test_derive_roles_nested(self):
        roles = derive_roles(['4', '4a', '4b', '4d', '4di', '4dii', '7', 'Q8a'])
        self.assertEqual(roles['4'], 'stem')
        self.assertEqual(roles['4a'], 'part')
        self.assertEqual(roles['4d'], 'stem')
        self.assertEqual(roles['4di'], 'part')
        self.assertEqual(roles['7'], 'question')
        self.assertEqual(roles['8a'], 'part')
        self.assertNotIn('Q8a', roles)

    def test_derive_roles_range(self):
        roles = derive_roles(['23-24', '23', '24'])
        self.assertEqual(roles['23-24'], 'stem')
        self.assertEqual(roles['23'], 'question')

    def test_next_part_label(self):
        self.assertEqual(next_part_label('4a'), '4b')
        self.assertEqual(next_part_label('4di'), '4dii')
        self.assertEqual(next_part_label('4dix'), '4dx')
        self.assertEqual(next_part_label('4'), '5')
        self.assertEqual(next_part_label('Q4b'), '4c')
        self.assertIsNone(next_part_label('23-24'))
        self.assertIsNone(next_part_label('4z'))
        self.assertIsNone(next_part_label('4dx'))
        self.assertIsNone(next_part_label('nope'))


class DashboardGroupTests(unittest.TestCase):
    def setUp(self):
        self.root = _q(id=1, qno=3, qid='ECON_DSE_2023_P1_Q3')
        self.a = _q(id=2, qno=3, qid='ECON_DSE_2023_P1_Q3a', part='a')
        self.b = _q(id=3, qno=3, qid='ECON_DSE_2023_P1_Q3b', part='b')
        _link(self.root, self.a, self.b)
        self.other = _q(id=9, qno=9, qid='ECON_DSE_2023_P1_Q9')

    def test_standalone_is_ungrouped(self):
        groups = group_for_dashboard([self.other])
        self.assertEqual(len(groups), 1)
        self.assertIsNone(groups[0]['stem'])
        self.assertEqual(groups[0]['leaves'], [self.other])

    def test_consecutive_parts_wrap_under_root(self):
        groups = group_for_dashboard([self.a, self.b, self.other])
        self.assertEqual(len(groups), 2)
        self.assertIs(groups[0]['stem'], self.root)
        self.assertEqual(groups[0]['leaves'], [self.a, self.b])
        self.assertIsNone(groups[1]['stem'])
        self.assertEqual(groups[1]['leaves'], [self.other])

    def test_stem_row_is_header_not_leaf(self):
        groups = group_for_dashboard([self.root, self.a, self.b])
        self.assertEqual(len(groups), 1)
        self.assertIs(groups[0]['stem'], self.root)
        self.assertEqual(groups[0]['leaves'], [self.a, self.b])

    def test_non_contiguous_parts_split_groups(self):
        groups = group_for_dashboard([self.a, self.other, self.b])
        self.assertEqual(len(groups), 3)
        self.assertEqual(groups[0]['leaves'], [self.a])
        self.assertEqual(groups[1]['leaves'], [self.other])
        self.assertEqual(groups[2]['leaves'], [self.b])

    def test_breadcrumb_root_then_parts(self):
        crumbs = breadcrumb_parts(self.a)
        self.assertEqual(
            [(c['id'], c['label']) for c in crumbs],
            [(1, 'Q3'), (2, '(a)')],
        )

    def test_token_fits_under_parts_and_range(self):
        self.assertTrue(token_fits_under(
            'ECON_DSE_2023_P1_Q3a', 'ECON_DSE_2023_P1_Q3'))
        self.assertTrue(token_fits_under(
            'ECON_DSE_2023_P1_Q3ci', 'ECON_DSE_2023_P1_Q3c'))
        self.assertTrue(token_fits_under(
            'ECON_DSE_2023_P1_Q23', 'ECON_DSE_2023_P1_Q23-24'))
        self.assertFalse(token_fits_under(
            'ECON_DSE_2023_P1_Q5', 'ECON_DSE_2023_P1_Q3'))
        self.assertFalse(token_fits_under(
            'ECON_DSE_2023_P1_Q3', 'ECON_DSE_2023_P1_Q3'))


class PaginateByRootTests(unittest.TestCase):
    """Dashboard pagination counts whole questions; a root never straddles pages."""

    def setUp(self):
        self.root = _q(id=1, qno=3, qid='ECON_DSE_2023_P1_Q3')
        self.a = _q(id=2, qno=3, qid='ECON_DSE_2023_P1_Q3a', part='a')
        self.b = _q(id=3, qno=3, qid='ECON_DSE_2023_P1_Q3b', part='b')
        self.c = _q(id=4, qno=3, qid='ECON_DSE_2023_P1_Q3c', part='c')
        _link(self.root, self.a, self.b, self.c)
        self.q9 = _q(id=9, qno=9, qid='ECON_DSE_2023_P1_Q9')
        self.q10 = _q(id=10, qno=10, qid='ECON_DSE_2023_P1_Q10')

    def test_root_with_three_parts_is_one_slot(self):
        items, total, parts = paginate_by_root([self.a, self.b, self.c, self.q9, self.q10], 1, 2)
        self.assertEqual(total, 3)
        self.assertEqual(parts, 5)
        self.assertEqual(items, [self.a, self.b, self.c, self.q9])
        items2, _, _ = paginate_by_root([self.a, self.b, self.c, self.q9, self.q10], 2, 2)
        self.assertEqual(items2, [self.q10])

    def test_part_only_match_counts_as_one_question(self):
        items, total, parts = paginate_by_root([self.b, self.q9], 1, 20)
        self.assertEqual((total, parts), (2, 2))
        self.assertEqual(items, [self.b, self.q9])

    def test_interleaved_parts_snap_together(self):
        items, total, _ = paginate_by_root([self.a, self.q9, self.b], 1, 20)
        self.assertEqual(total, 2)
        self.assertEqual(items, [self.a, self.b, self.q9])
        self.assertEqual(len(group_for_dashboard(items)), 2)

    def test_explicit_stem_row_shares_bucket_and_leads(self):
        items, total, parts = paginate_by_root([self.a, self.root, self.q9], 1, 20)
        self.assertEqual((total, parts), (2, 2))
        self.assertEqual(items, [self.root, self.a, self.q9])

    def test_out_of_range_page_is_empty(self):
        items, total, _ = paginate_by_root([self.q9], 3, 20)
        self.assertEqual(total, 1)
        self.assertEqual(items, [])


class FilenameGrammarTests(unittest.TestCase):
    def test_legacy_and_new_tokens_parse(self):
        p = parse_filename('MATC_DSE_2024_P1_Q5_EN_QUE.png')
        self.assertEqual(p['qno'], 'Q5')
        self.assertEqual(construct_qid(p), 'MATC_DSE_2024_P1_Q5')
        p = parse_filename('ECON_DSE_2023_P1_Q3ci_ENO_QUE.png')
        self.assertEqual(p['qno'], 'Q3ci')
        self.assertEqual(construct_qid(p), 'ECON_DSE_2023_P1_Q3ci')
        p = parse_filename('ECON_DSE_2023_P1_Q23-24_ENO_QUE.png')
        self.assertEqual(p['qno'], 'Q23-24')
        self.assertEqual(construct_qid(p), 'ECON_DSE_2023_P1_Q23-24')
        p = parse_filename('MATC_DSE_2024_P1_Q5_EN_QUE_2.png')
        self.assertEqual(p['part'], 2)  # IMG multi-file, not a sub-question
        p = parse_filename('MATC_QB_BOOK1_Q3a_CH_SOL.md')
        self.assertEqual(construct_qid(p), 'MATC_QB_BOOK1_Q3a')

    def test_que_suffix_does_not_confuse_qno(self):
        self.assertIsNone(parse_filename('MATC_DSE_2024_P1_QUE_EN_QUE.png'))


class SplitBoxNormalizeTests(unittest.TestCase):
    """Pure validation for Split-into-parts boxes (no live DB)."""

    def test_requires_stem_and_part(self):
        from app.question_split import _normalize_boxes
        qid = 'ECON_DSE_2023_P1_Q3'
        box = [0.1, 0.1, 0.9, 0.4]
        with self.assertRaises(ValueError):
            _normalize_boxes([{'label': 'a', 'box': box}], qid)
        with self.assertRaises(ValueError):
            _normalize_boxes([{'label': 'stem', 'box': box}], qid)
        out = _normalize_boxes([
            {'label': 'stem', 'box': box},
            {'label': 'a', 'box': [0.1, 0.4, 0.9, 0.7]},
            {'label': 'ci', 'box': [0.1, 0.7, 0.9, 0.95]},
        ], qid)
        self.assertEqual([x['token'] for x in out], ['Q3', 'Q3a', 'Q3ci'])

    def test_rejects_range_stem(self):
        from app.question_split import _normalize_boxes
        with self.assertRaises(ValueError):
            _normalize_boxes(
                [{'label': 'stem', 'box': [0, 0, 1, 1]},
                 {'label': 'a', 'box': [0, 0, 1, 1]}],
                'ECON_DSE_2023_P1_Q23-24',
            )


class ExistingQnoSortStillNumeric(unittest.TestCase):
    """Stand-alone rows with no parent_id keep numeric qno ordering."""

    def test_qno_field_is_tuple_starting_with_integer(self):
        q = SimpleNamespace(qid='MATC_DSE_2024_P1_Q10', qno=10, year=2024)
        key = SORT_FIELDS['qno']['key'](q)
        self.assertEqual(key[0], 10)
        self.assertFalse(SORT_FIELDS['qno']['natural'])


if __name__ == '__main__':
    unittest.main()
