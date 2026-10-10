"""Shared-block ends (ADR-015 amendment): ``Q4~b`` with ``block_end='b'``
owns (b) only; later parts sit under the lettered parent again."""
import os
import tempfile
import unittest
from types import SimpleNamespace

from app.hierarchy import (
    block_covers,
    block_display_label,
    block_end_choices,
    block_end_error,
    breadcrumb_parts,
    derive_roles,
    earlier_siblings,
    effective_block_end,
    label_is_ancestor,
    normalize_block_end,
    parse_block_end_rows,
    part_position_label,
    part_sort_value,
    pick_block,
    plan_block_level,
    resolve_render_plan,
)
from app.pdf_import import apply_derived_roles, sanitize_plan, validate_plan_block_ends
from app.question_blocks import BlockError, new_block_members

P = 'ICT_DSE_2017_P2D_'


def _q(**kw):
    defaults = dict(
        parent=None, parent_id=None, part=None, qno_end=None, block_end=None,
        subject='ICT', source='DSE', year=2017, paper='P2D', qid='', qno=4,
        id=None, needs_prev_parts=False,
    )
    defaults.update(kw)
    q = SimpleNamespace(**defaults)
    q.children = []
    q.part_sort = part_sort_value(q.part) if q.part else None
    return q


def _link(parent, *children):
    for c in children:
        c.parent = parent
        c.parent_id = parent.id
        parent.children.append(c)


def _ids(plan):
    return [(it.role, it.question.id) for it in plan]


class CoverAndPickTests(unittest.TestCase):

    def test_no_end_is_unchanged(self):
        self.assertTrue(block_covers('b', 'e'))
        self.assertEqual(pick_block(['~b'], 'e'), '~b')
        self.assertEqual(pick_block(['~b'], 'e', {}), '~b')
        self.assertEqual(pick_block(['~b'], 'e', {'~b': None}), '~b')
        self.assertEqual(pick_block(['~c', '~e'], 'd'), '~c')
        self.assertEqual(pick_block(['~c', '~e'], 'f'), '~e')

    def test_end_bounds_the_block(self):
        self.assertTrue(block_covers('b', 'b', 'b'))
        self.assertFalse(block_covers('b', 'c', 'b'))
        self.assertTrue(block_covers('b', 'c', 'c'))
        self.assertEqual(pick_block(['~b'], 'b', {'~b': 'b'}), '~b')
        self.assertIsNone(pick_block(['~b'], 'c', {'~b': 'b'}))
        self.assertIsNone(pick_block(['~b'], 'a', {'~b': 'b'}))

    def test_earlier_block_never_resumes(self):
        ends = {'~b': 'b'}
        self.assertIsNone(pick_block(['~b', '~d'], 'c', ends))
        self.assertEqual(pick_block(['~b', '~d'], 'd', ends), '~d')
        self.assertEqual(pick_block(['~b', '~d'], 'e', ends), '~d')
        # an end on the later block does not hand its parts back to ~b
        ends = {'~d': 'd'}
        self.assertEqual(pick_block(['~b', '~d'], 'c', ends), '~b')
        self.assertIsNone(pick_block(['~b', '~d'], 'e', ends))

    def test_stale_end_behaves_like_none(self):
        # anchor renamed ~b -> ~c while end stayed 'b'
        self.assertIsNone(effective_block_end('~c', 'b'))
        self.assertEqual(pick_block(['~c'], 'e', {'~c': 'b'}), '~c')
        self.assertIsNone(effective_block_end('~c', 'ii'))
        self.assertIsNone(effective_block_end('~c', 'cii'))
        self.assertEqual(effective_block_end('~c', 'd'), 'd')
        self.assertEqual(effective_block_end('~ii', 'iii'), 'iii')

    def test_normalize(self):
        self.assertEqual(normalize_block_end(' (C) '), 'c')
        for blank in (None, '', '  ', 'none', 'NULL'):
            self.assertIsNone(normalize_block_end(blank))


class ValidationTests(unittest.TestCase):

    def test_valid(self):
        self.assertIsNone(block_end_error('~b', 'b'))
        self.assertIsNone(block_end_error('~b', 'e'))
        self.assertIsNone(block_end_error('~b', None))
        self.assertIsNone(block_end_error('~b', ''))
        self.assertIsNone(block_end_error('~b', 'c', ['~b', '~d']))
        self.assertIsNone(block_end_error('~ii', 'iv'))

    def test_invalid(self):
        self.assertIn('before the block', block_end_error('~c', 'b'))
        self.assertIn('letter', block_end_error('~c', 'ii'))
        self.assertIn('roman numeral', block_end_error('~ii', 'e'))
        self.assertIn('single part', block_end_error('~c', 'cii'))
        self.assertIsNone(block_end_error('~c', '(d)'))
        self.assertIn('next shared block', block_end_error('~b', 'd', ['~b', '~d']))
        self.assertIn('next shared block', block_end_error('~b', 'e', ['~b', '~d']))
        self.assertIn('Only a shared block', block_end_error('c', 'd'))

    def test_choices(self):
        members = ['a', 'b', 'c', 'd', 'e']
        self.assertEqual(block_end_choices('~b', members), ['b', 'c', 'd', 'e'])
        self.assertEqual(block_end_choices('~b', members, ['~b', '~d']), ['b', 'c'])
        self.assertEqual(block_end_choices('~d', members, ['~b', '~d']), ['d', 'e'])


class PlanBlockLevelTests(unittest.TestCase):
    """ICT_DSE_2017_P2D_Q4: the table before (b) is used by (b) only."""

    def setUp(self):
        self.root = _q(id=1, qid=P + 'Q4')
        self.a = _q(id=2, qid=P + 'Q4a', part='a', parent_id=1)
        self.blk = _q(id=3, qid=P + 'Q4~b', part='~b', parent_id=1)
        self.b = _q(id=4, qid=P + 'Q4b', part='b', parent_id=3)
        self.c = _q(id=5, qid=P + 'Q4c', part='c', parent_id=3)
        self.d = _q(id=6, qid=P + 'Q4d', part='d', parent_id=3)
        self.e = _q(id=7, qid=P + 'Q4e', part='e', parent_id=3)
        self.members = [self.a, self.b, self.c, self.d, self.e]

    def _moves(self, blocks, exclude_id=None):
        return [(m.qid[-3:], t) for m, t in
                plan_block_level(1, self.members, blocks, exclude_id=exclude_id)]

    def _apply(self, blocks, exclude_id=None):
        for m, t in plan_block_level(1, self.members, blocks, exclude_id=exclude_id):
            m.parent_id = t

    def test_no_end_no_moves(self):
        self.assertEqual(self._moves([self.blk]), [])

    def test_end_moves_later_parts_up(self):
        self.blk.block_end = 'b'
        self.assertEqual(self._moves([self.blk]), [('Q4c', 1), ('Q4d', 1), ('Q4e', 1)])
        self._apply([self.blk])
        self.assertEqual(self._moves([self.blk]), [])  # idempotent
        self.blk.block_end = None
        self.assertEqual(self._moves([self.blk]), [('Q4c', 3), ('Q4d', 3), ('Q4e', 3)])

    def test_end_survives_other_block_create_and_delete(self):
        self.blk.block_end = 'b'
        self._apply([self.blk])
        blk_d = _q(id=8, qid=P + 'Q4~d', part='~d', parent_id=1)
        self.assertEqual(self._moves([self.blk, blk_d]), [('Q4d', 8), ('Q4e', 8)])
        self._apply([self.blk, blk_d])
        self.assertEqual(self.c.parent_id, 1)
        # dissolving ~d: its parts go up, not back into ~b (ended at b)
        self.assertEqual(self._moves([self.blk, blk_d], exclude_id=8), [('Q4d', 1), ('Q4e', 1)])

    def test_stale_end_after_anchor_rename(self):
        self.blk.block_end = 'b'
        self._apply([self.blk])
        self.blk.part = '~c'  # renamed: end 'b' is now before the anchor
        moves = self._moves([self.blk])
        self.assertEqual(moves, [('Q4b', 1), ('Q4c', 3), ('Q4d', 3), ('Q4e', 3)])

    def test_new_block_members_respects_end(self):
        root = _q(id=1, qid=P + 'Q4')
        kids = [_q(id=i, part=p, qid=P + 'Q4' + p) for i, p in enumerate('abcde', start=2)]
        _link(root, *kids)
        self.assertEqual([m.part for m in new_block_members(root.children, 'b')],
                         ['b', 'c', 'd', 'e'])
        self.assertEqual([m.part for m in new_block_members(root.children, 'b', 'b')], ['b'])
        self.assertEqual([m.part for m in new_block_members(root.children, 'b', 'c')], ['b', 'c'])
        with self.assertRaises(BlockError):
            new_block_members(root.children, 'c', 'b')


class LabelRoleTests(unittest.TestCase):

    LABS = ['4', '4a', '4~b', '4b', '4c', '4d']

    def test_label_is_ancestor_with_end(self):
        ends = {'4~b': 'b'}
        self.assertTrue(label_is_ancestor('4~b', '4b', self.LABS, ends))
        self.assertFalse(label_is_ancestor('4~b', '4c', self.LABS, ends))
        self.assertFalse(label_is_ancestor('4~b', '4c', None, ends))
        self.assertFalse(label_is_ancestor('4~b', '4c', self.LABS, {'Q4~b': 'b'}))
        self.assertTrue(label_is_ancestor('4~b', '4c', self.LABS))

    def test_derive_roles_with_end(self):
        roles = derive_roles(self.LABS, {'4~b': 'b'})
        self.assertEqual(roles['4~b'], 'stem')
        self.assertEqual(roles['4'], 'stem')
        self.assertEqual(roles['4c'], 'part')
        self.assertEqual(derive_roles(self.LABS), roles)

    def test_q7_regression(self):
        labs = ['7', '7a', '7b', '7~c', '7c', '7d']
        self.assertTrue(label_is_ancestor('7~c', '7d', labs, {}))
        self.assertEqual(derive_roles(labs, {})['7~c'], 'stem')


class RenderAndLabelTests(unittest.TestCase):
    """After ``set_block_end(Q4~b, 'b')`` the tree is Q4 → a, ~b → b, c, d."""

    def setUp(self):
        self.root = _q(id=1, qid=P + 'Q4')
        self.a = _q(id=2, qid=P + 'Q4a', part='a')
        self.blk = _q(id=3, qid=P + 'Q4~b', part='~b', block_end='b')
        self.b = _q(id=4, qid=P + 'Q4b', part='b')
        self.c = _q(id=5, qid=P + 'Q4c', part='c')
        self.d = _q(id=6, qid=P + 'Q4d', part='d', needs_prev_parts=True)
        _link(self.root, self.a, self.blk, self.c, self.d)
        _link(self.blk, self.b)

    def test_part_outside_end_renders_without_block(self):
        self.assertEqual(_ids(resolve_render_plan([self.c], mode='selected')),
                         [('stem', 1), ('leaf', 5)])
        self.assertEqual(_ids(resolve_render_plan([self.b], mode='selected')),
                         [('stem', 1), ('stem', 3), ('leaf', 4)])

    def test_needs_prev_shows_block_only_as_background_for_b(self):
        # the block is an earlier sibling of (d); the render plan expands it
        self.assertEqual([n.id for n in earlier_siblings(self.d)], [2, 3, 5])
        self.assertEqual(_ids(resolve_render_plan([self.d], mode='selected')),
                         [('stem', 1), ('stem', 2), ('stem', 3), ('stem', 4), ('stem', 5), ('leaf', 6)])

    def test_labels(self):
        self.assertEqual(block_display_label('~b'), 'before (b)')
        self.assertEqual(block_display_label('~b', 'b'), 'before (b), (b) only')
        self.assertEqual(block_display_label('~b', 'd'), 'before (b), up to (d)')
        self.assertEqual(block_display_label('~c', 'b'), 'before (c)')
        crumbs = breadcrumb_parts(self.b)
        self.assertEqual([c['label'] for c in crumbs], ['Q4', 'before (b), (b) only', '(b)'])
        self.assertEqual(part_position_label(self.b), '(b)')
        self.assertEqual(part_position_label(self.blk), 'before (b), (b) only')


class PdfImportPlanTests(unittest.TestCase):

    def _item(self, label, **kw):
        return dict(page=0, label=label, box=[0, 0, 1, 0.1], **kw)

    def test_sanitize_keeps_valid_end_and_drops_bad(self):
        raw = {'que': [self._item('4'), self._item('4a'), self._item('4~b', block_end='(B)'),
                       self._item('4b'), self._item('4c'), self._item('4c', block_end='d')],
               'sol': [self._item('4~b', block_end='b')]}
        clean = sanitize_plan(raw)
        que = {it['label']: it for it in clean['que']}
        self.assertEqual(que['4~b']['block_end'], 'b')
        self.assertNotIn('block_end', que['4c'])
        self.assertNotIn('block_end', clean['sol'][0])
        self.assertEqual(que['4~b']['role'], 'stem')
        self.assertEqual(que['4c']['role'], 'part')

    def test_validate_drops_end_past_next_block(self):
        items = [{'label': '4~b', 'block_end': 'd'}, {'label': '4~d'},
                 {'label': '4~d', 'block_end': 'c'}, {'label': '5~c', 'block_end': 'e'}]
        validate_plan_block_ends(items)
        self.assertNotIn('block_end', items[0])
        self.assertNotIn('block_end', items[2])
        self.assertEqual(items[3]['block_end'], 'e')

    def test_apply_derived_roles_reads_ends(self):
        items = [{'label': '4'}, {'label': '4~b', 'block_end': 'b'}, {'label': '4b'}, {'label': '4c'}]
        apply_derived_roles(items)
        self.assertEqual([it['role'] for it in items], ['stem', 'stem', 'part', 'part'])


class CsvAndCliTests(unittest.TestCase):

    def test_parse_rows(self):
        rows = parse_block_end_rows([
            'block_qid,end_part', '', '# comment',
            'ICT_DSE_2017_P2D_Q4~b, b', 'ICT_DSE_2019_P2C_Q2~c,(F)', 'ICT_X_Q1~b,none',
            'ICT_X_Q2~b',
        ])
        self.assertEqual(rows, [('ICT_DSE_2017_P2D_Q4~b', 'b'), ('ICT_DSE_2019_P2C_Q2~c', 'f'),
                                ('ICT_X_Q1~b', None), ('ICT_X_Q2~b', None)])

    def test_cli_usage_errors_never_open_the_app(self):
        from click.testing import CliRunner
        from cli import cli
        runner = CliRunner()
        res = runner.invoke(cli, ['set-block-end'])
        self.assertEqual(res.exit_code, 2)
        self.assertIn('BLOCK_QID END_PART', res.output)
        fd, path = tempfile.mkstemp(suffix='.csv')
        os.close(fd)
        try:
            res = runner.invoke(cli, ['set-block-end', 'ICT_X_Q4~b', 'b', '--from-csv', path])
            self.assertEqual(res.exit_code, 2)
            self.assertIn('not both', res.output)
        finally:
            os.remove(path)


if __name__ == '__main__':
    unittest.main()
