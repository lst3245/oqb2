"""Tests for question multi-sort fields."""
import unittest
from types import SimpleNamespace

from app.utils import SORT_FIELDS, apply_multi_sort, enumerate_sort_groups


def _question(qid, qno, year):
    return SimpleNamespace(qid=qid, qno=qno, year=year)


def _node(id, name, sort_order, **kw):
    return SimpleNamespace(id=id, name=name, sort_order=sort_order, **kw)


def _tagged(qid, topic=None, subtopic=None):
    return SimpleNamespace(
        qid=qid, major_topic=topic, major_subtopic=subtopic,
        major_topic_id=topic.id if topic else None,
        major_subtopic_id=subtopic.id if subtopic else None,
        chapter=None, subchapter=None, chapter_id=None, subchapter_id=None,
    )


class TaxonomySavedOrderSortTests(unittest.TestCase):
    """Topic / subtopic sorts follow the admin list order, not the name."""

    def setUp(self):
        # Saved order: Zeta (0) before Alpha (1); alphabetical would invert it.
        self.zeta = _node(10, 'Zeta Algebra', 0)
        self.alpha = _node(11, 'Alpha Geometry', 1)
        self.zeta_b = _node(20, 'B Indices', 0, topic=self.zeta)
        self.zeta_a = _node(21, 'A Surds', 1, topic=self.zeta)
        self.alpha_a = _node(22, 'A Angles', 0, topic=self.alpha)
        self.questions = [
            _tagged('Q1', self.alpha, self.alpha_a),
            _tagged('Q2'),
            _tagged('Q3', self.zeta, self.zeta_a),
            _tagged('Q4', self.zeta, self.zeta_b),
        ]

    def test_topic_follows_sort_order_and_untagged_last(self):
        result = apply_multi_sort(self.questions, [{'field': 'topic', 'direction': 'asc'},
                                                   {'field': 'qid', 'direction': 'asc'}])
        self.assertEqual([q.qid for q in result], ['Q3', 'Q4', 'Q1', 'Q2'])

    def test_subtopic_orders_under_parent_topic(self):
        result = apply_multi_sort(self.questions, [{'field': 'subtopic', 'direction': 'asc'}])
        self.assertEqual([q.qid for q in result], ['Q4', 'Q3', 'Q1', 'Q2'])

    def test_ties_on_sort_order_break_by_id(self):
        self.alpha.sort_order = 0  # same as zeta; id 10 < 11
        result = apply_multi_sort(self.questions[:1] + self.questions[2:3],
                                  [{'field': 'topic', 'direction': 'asc'}])
        self.assertEqual([q.qid for q in result], ['Q3', 'Q1'])

    def test_sort_groups_default_to_saved_order(self):
        blocks = enumerate_sort_groups(self.questions, ['topic', 'subtopic'])
        self.assertEqual(
            [b['key'] for b in blocks],
            [[10, 20], [10, 21], [11, 22], [0, 0]],
        )
        self.assertEqual(blocks[-1]['labels']['topic'], '(No topic)')


class QuestionNumberSortTests(unittest.TestCase):
    def setUp(self):
        self.questions = [
            _question('MATC_DSE_2024_P1_Q10', 10, 2024),
            _question('MATC_DSE_2023_P1_Q2', 2, 2023),
            _question('MATC_DSE_2024_P1_Q2', 2, 2024),
        ]

    def test_question_number_field_uses_integer_qno(self):
        self.assertEqual(SORT_FIELDS['qno']['label'], 'Question Number')
        self.assertFalse(SORT_FIELDS['qno']['natural'])
        key = SORT_FIELDS['qno']['key'](self.questions[0])
        self.assertEqual(key[0], 10)

    def test_question_number_sorts_numerically(self):
        result = apply_multi_sort(
            self.questions,
            [{'field': 'qno', 'direction': 'asc'}],
        )
        self.assertEqual([q.qno for q in result], [2, 2, 10])

    def test_question_number_descending(self):
        result = apply_multi_sort(
            self.questions,
            [{'field': 'qno', 'direction': 'desc'}],
        )
        self.assertEqual([q.qno for q in result], [10, 2, 2])

    def test_question_number_as_secondary_criterion(self):
        result = apply_multi_sort(
            self.questions,
            [
                {'field': 'year', 'direction': 'asc'},
                {'field': 'qno', 'direction': 'desc'},
            ],
        )
        self.assertEqual(
            [q.qid for q in result],
            [
                'MATC_DSE_2023_P1_Q2',
                'MATC_DSE_2024_P1_Q10',
                'MATC_DSE_2024_P1_Q2',
            ],
        )


if __name__ == '__main__':
    unittest.main()
