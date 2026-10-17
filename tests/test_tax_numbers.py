"""Pure tests for app.tax_numbers (no app, no DB)."""
import unittest
from types import SimpleNamespace
from unittest import mock

from app import tax_numbers


class Topic(SimpleNamespace):
    pass


class Subtopic(SimpleNamespace):
    pass


class Chapter(SimpleNamespace):
    pass


NUMBERS = {'MATC': ({1: '01'}, {10: '1.1'})}


def fake_subject_numbers(subject_id):
    return NUMBERS.get(subject_id, ({}, {}))


@mock.patch.object(tax_numbers, 'subject_numbers', side_effect=fake_subject_numbers)
class NumberedNameTests(unittest.TestCase):
    def test_topic_gets_prefix(self, _):
        t = Topic(id=1, subject_id='MATC', name='Basic Algebra', name_zh=None)
        self.assertEqual(tax_numbers.numbered_name(t, 'en'), '01 Basic Algebra')

    def test_subtopic_uses_parent_subject(self, _):
        t = Topic(id=1, subject_id='MATC', name='Basic Algebra', name_zh=None)
        s = Subtopic(id=10, topic=t, name='Law of Indices', name_zh='指數定律')
        self.assertEqual(tax_numbers.numbered_name(s, 'zh'), '1.1 指數定律')

    def test_unnumbered_topic_keeps_name(self, _):
        t = Topic(id=2, subject_id='MATC', name='Hidden', name_zh=None)
        self.assertEqual(tax_numbers.numbered_name(t, 'en'), 'Hidden')

    def test_subject_without_prefix(self, _):
        t = Topic(id=1, subject_id='PHY', name='Heat', name_zh=None)
        self.assertEqual(tax_numbers.numbered_name(t, 'en'), 'Heat')

    def test_other_nodes_pass_through(self, sn):
        c = Chapter(id=1, subject_id='MATC', name='Ch 1', name_zh=None)
        self.assertEqual(tax_numbers.numbered_name(c, 'en'), 'Ch 1')
        sn.assert_not_called()

    def test_none(self, _):
        self.assertIsNone(tax_numbers.numbered_name(None))


class PrefixedTests(unittest.TestCase):
    def test_prefixed(self):
        self.assertEqual(tax_numbers.prefixed('01', 'A'), '01 A')
        self.assertEqual(tax_numbers.prefixed(None, 'A'), 'A')
        self.assertIsNone(tax_numbers.prefixed('01', None))


if __name__ == '__main__':
    unittest.main()
