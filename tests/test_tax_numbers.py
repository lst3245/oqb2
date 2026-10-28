"""Pure tests for app.tax_numbers (no app, no DB)."""
import json
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app import tax_numbers


class Topic(SimpleNamespace):
    pass


class Subtopic(SimpleNamespace):
    pass


class Chapter(SimpleNamespace):
    pass


NUMBERS = {
    'MATC': ({1: '01'}, {10: '1.1'}),
    'PHYS': ({21: '21'}, {210: '21.1'}),
}


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

    def test_stored_number_in_the_name_is_not_repeated(self, _):
        t = Topic(id=21, subject_id='PHYS',
                  name='21 Astronomy — Orbital Motion (Paper 2)', name_zh='天文學')
        self.assertEqual(
            tax_numbers.numbered_name(t, 'en'),
            '21 Astronomy — Orbital Motion (Paper 2)',
        )
        # Chinese without a leading number still gets the prefix.
        self.assertEqual(tax_numbers.numbered_name(t, 'zh'), '21 天文學')
        self.assertEqual(
            tax_numbers.numbered_name(t, 'both'),
            '21 Astronomy — Orbital Motion (Paper 2) 天文學',
        )

    def test_chinese_name_that_already_has_the_number(self, _):
        t = Topic(id=21, subject_id='PHYS', name='21 Astronomy', name_zh='21 天文學')
        self.assertEqual(tax_numbers.numbered_name(t, 'zh'), '21 天文學')

    def test_subtopic_number_not_repeated(self, _):
        t = Topic(id=21, subject_id='PHYS', name='21 Astronomy', name_zh=None)
        s = Subtopic(id=210, topic=t, name='21.1 Escape Velocity', name_zh=None)
        self.assertEqual(tax_numbers.numbered_name(s, 'en'), '21.1 Escape Velocity')


class PrefixedTests(unittest.TestCase):
    def test_prefixed(self):
        self.assertEqual(tax_numbers.prefixed('01', 'A'), '01 A')
        self.assertEqual(tax_numbers.prefixed(None, 'A'), 'A')
        self.assertIsNone(tax_numbers.prefixed('01', None))
        self.assertEqual(tax_numbers.prefixed('21', ''), '21 ')

    def test_does_not_double_a_leading_number(self):
        name = '21 Astronomy — Orbital Motion (Paper 2)'
        self.assertEqual(tax_numbers.prefixed('21', name), name)
        self.assertEqual(tax_numbers.prefixed('01', '01 Basic Algebra'), '01 Basic Algebra')
        self.assertEqual(tax_numbers.prefixed('01', '1 Algebra'), '1 Algebra')
        self.assertEqual(tax_numbers.prefixed('1.1', '1.1 Law of Indices'), '1.1 Law of Indices')
        self.assertEqual(tax_numbers.prefixed('01.01', '1.1 Law of Indices'), '1.1 Law of Indices')
        self.assertEqual(tax_numbers.prefixed('21', '21. Astronomy'), '21. Astronomy')

    def test_different_number_is_still_prefixed(self):
        self.assertEqual(tax_numbers.prefixed('21', '21st century'), '21 21st century')
        self.assertEqual(tax_numbers.prefixed('1.1', '1.10 Laws'), '1.1 1.10 Laws')
        self.assertEqual(tax_numbers.prefixed('22', '21 Astronomy'), '22 21 Astronomy')
        self.assertEqual(tax_numbers.prefixed('01', 'Basic Algebra'), '01 Basic Algebra')


class TaxLabelJsTests(unittest.TestCase):
    """OQB.taxLabel must stay in step with prefixed (filter / tag editor)."""

    def test_tax_label_matches_prefixed(self):
        pairs = [
            ('21', '21 Astronomy — Orbital Motion (Paper 2)'),
            ('01', 'Basic Algebra'),
            ('01', '1 Algebra'),
            ('1.1', 'Law of Indices'),
            ('1.1', '1.1 Law of Indices'),
            ('01.01', '1.1 Law of Indices'),
            ('21', '21st century'),
            ('21', '21. Astronomy'),
            ('22', '21 Astronomy'),
            (None, 'Heat'),
            ('21', ''),
        ]
        expected = [tax_numbers.prefixed(number, name) for number, name in pairs]
        script = (Path(__file__).parents[1] / 'static' / 'js' / 'i18n.js').read_text(encoding='utf-8')
        payload = json.dumps([
            {'name': name, 'number': number} for number, name in pairs
        ])
        program = (
            'var window = {};\nvar document = { cookie: "" };\n'
            + script
            + '\nvar cases = ' + payload + ';\n'
            + 'var out = cases.map(function (c) { return window.OQB.taxLabel(c); });\n'
            + 'out.unshift(window.OQB.taxLabel(null));\n'
            + 'console.log(JSON.stringify(out));\n'
        )
        proc = subprocess.run(
            ['node', '-'], input=program, text=True, capture_output=True, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        got = json.loads(proc.stdout)
        self.assertEqual(got[0], '')
        self.assertEqual(got[1:], expected)


if __name__ == '__main__':
    unittest.main()
