"""Pure tests for Combine-parts helpers and WHOLE source crops.

Does not call create_app() or touch the live database.
"""
import unittest
from types import SimpleNamespace

from app.hierarchy import descendants, is_stem
from app.ingestor import construct_qid, parse_filename
from app.pdf_import import whole_source_crops
from app.question_combine import (
    CombineError,
    choose_mode,
    collapse_maximal_stems,
    is_root_question,
    subtree_preorder,
)


def _node(nid, qid, *, parent=None, children=None, qno_end=None, part=None):
    n = SimpleNamespace(
        id=nid, qid=qid, parent_id=getattr(parent, 'id', None),
        parent=parent, children=list(children or []),
        qno_end=qno_end, part=part, qno=1,
    )
    if parent is not None:
        parent.children.append(n)
    return n


class WholeFilenameTests(unittest.TestCase):
    def test_root_whole_parses(self):
        p = parse_filename('ICT_DSE_2023_P1B_Q1_EN_WHOLE.png')
        self.assertEqual(p['type'], 'WHOLE')
        self.assertEqual(p['qno'], 'Q1')
        self.assertEqual(p['part'], 1)
        self.assertEqual(construct_qid(p), 'ICT_DSE_2023_P1B_Q1')

    def test_whole_page_two(self):
        p = parse_filename('ICT_DSE_2023_P1B_Q2_ENO_WHOLE_2.png')
        self.assertEqual(p['type'], 'WHOLE')
        self.assertEqual(p['part'], 2)

    def test_part_qid_whole_still_parses(self):
        # Ingest skips these; the regex must still match so the skip can fire.
        p = parse_filename('ICT_DSE_2023_P1B_Q1c_EN_WHOLE.png')
        self.assertEqual(p['type'], 'WHOLE')
        self.assertEqual(p['qno'], 'Q1c')


class CombinePlannerTests(unittest.TestCase):
    def setUp(self):
        self.root = _node(1, 'ICT_DSE_2023_P1B_Q1')
        self.a = _node(2, 'ICT_DSE_2023_P1B_Q1a', parent=self.root, part='a')
        self.c = _node(3, 'ICT_DSE_2023_P1B_Q1c', parent=self.root, part='c')
        self.ci = _node(4, 'ICT_DSE_2023_P1B_Q1ci', parent=self.c, part='i')
        self.cii = _node(5, 'ICT_DSE_2023_P1B_Q1cii', parent=self.c, part='ii')

    def test_is_root_and_stem(self):
        self.assertTrue(is_root_question(self.root))
        self.assertFalse(is_root_question(self.c))
        self.assertTrue(is_stem(self.root))
        self.assertTrue(is_stem(self.c))
        self.assertFalse(is_stem(self.ci))

    def test_choose_mode_restore_vs_reconstruct(self):
        self.assertEqual(choose_mode(self.root, has_whole=True), 'restore')
        self.assertEqual(choose_mode(self.root, has_whole=False), 'reconstruct')
        self.assertEqual(choose_mode(self.c, has_whole=True), 'reconstruct')
        self.assertEqual(choose_mode(self.c, has_whole=False), 'reconstruct')

    def test_range_stem_refused(self):
        rng = _node(9, 'ECON_DSE_2023_P1_Q23-24', qno_end=24)
        _node(10, 'ECON_DSE_2023_P1_Q23', parent=rng)
        with self.assertRaises(CombineError) as ctx:
            choose_mode(rng, has_whole=True)
        self.assertEqual(ctx.exception.status, 409)

    def test_leaf_refused(self):
        with self.assertRaises(CombineError):
            choose_mode(self.ci, has_whole=False)

    def test_collapse_keeps_ancestor(self):
        out = collapse_maximal_stems([self.root, self.c, self.ci])
        self.assertEqual([n.id for n in out], [1])

    def test_collapse_keeps_unrelated(self):
        other = _node(20, 'ICT_DSE_2023_P1B_Q2')
        _node(21, 'ICT_DSE_2023_P1B_Q2a', parent=other, part='a')
        out = collapse_maximal_stems([self.c, other])
        self.assertEqual({n.id for n in out}, {3, 20})

    def test_subtree_preorder_root(self):
        qids = [n.qid.rsplit('_', 1)[-1] for n in subtree_preorder(self.root)]
        self.assertEqual(qids, ['Q1', 'Q1a', 'Q1c', 'Q1ci', 'Q1cii'])

    def test_subtree_preorder_nested(self):
        qids = [n.qid.rsplit('_', 1)[-1] for n in subtree_preorder(self.c)]
        self.assertEqual(qids, ['Q1c', 'Q1ci', 'Q1cii'])
        self.assertEqual(len(descendants(self.c)), 2)


class WholeSourceCropsTests(unittest.TestCase):
    def test_root_split_collects_unique_source_pages(self):
        plan = {'que': [
            {'label': '1', 'page': 0, 'box': [0.1, 0.1, 0.9, 0.3],
             'source_page': 0, 'source_box': [0.1, 0.1, 0.9, 0.9]},
            {'label': '1a', 'page': 0, 'box': [0.1, 0.3, 0.9, 0.5],
             'source_page': 0, 'source_box': [0.1, 0.1, 0.9, 0.9]},
            {'label': '1b', 'page': 1, 'box': [0.1, 0.0, 0.9, 0.4],
             'source_page': 1, 'source_box': [0.1, 0.0, 0.9, 0.8]},
        ]}
        crops = whole_source_crops(plan, 'que', '1')
        self.assertEqual(len(crops), 2)
        self.assertEqual(crops[0]['page'], 0)
        self.assertEqual(crops[1]['page'], 1)

    def test_unsplit_root_returns_empty(self):
        plan = {'que': [
            {'label': '2', 'page': 0, 'box': [0, 0, 1, 1]},
        ]}
        self.assertEqual(whole_source_crops(plan, 'que', '2'), [])

    def test_nested_stem_returns_empty(self):
        plan = {'que': [
            {'label': '1c', 'page': 0, 'box': [0.1, 0.4, 0.9, 0.5],
             'source_page': 0, 'source_box': [0.1, 0.4, 0.9, 0.9]},
            {'label': '1ci', 'page': 0, 'box': [0.1, 0.5, 0.9, 0.7],
             'source_page': 0, 'source_box': [0.1, 0.4, 0.9, 0.9]},
        ]}
        self.assertEqual(whole_source_crops(plan, 'que', '1c'), [])

    def test_range_stem_returns_empty(self):
        plan = {'que': [
            {'label': '23-24', 'page': 0, 'box': [0, 0, 1, 0.4],
             'source_page': 0, 'source_box': [0, 0, 1, 0.4]},
            {'label': '23', 'page': 0, 'box': [0, 0.4, 1, 0.6],
             'source_page': 0, 'source_box': [0, 0, 1, 0.4]},
        ]}
        self.assertEqual(whole_source_crops(plan, 'que', '23-24'), [])


if __name__ == '__main__':
    unittest.main()
