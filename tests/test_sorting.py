"""Tests for question multi-sort fields."""
import unittest
from types import SimpleNamespace

from app.utils import SORT_FIELDS, apply_multi_sort


def _question(qid, qno, year):
    return SimpleNamespace(qid=qid, qno=qno, year=year)


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
        self.assertEqual(SORT_FIELDS['qno']['key'](self.questions[0]), 10)

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
