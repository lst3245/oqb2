"""Unit tests for section heading / split labels: numbering and MC/CQ suffix."""
import unittest
from types import SimpleNamespace

from app import generator
from app.utils import number_taxonomy


def _node(id, name, sort_order=0, **kw):
    return SimpleNamespace(id=id, name=name, sort_order=sort_order, **kw)


def _q(topic=None, subtopic=None, q_type='MC', chapter=None):
    return SimpleNamespace(
        major_topic=topic, major_subtopic=subtopic, chapter=chapter,
        subchapter=None, q_type=q_type,
    )


class NumberTaxonomyTests(unittest.TestCase):
    def test_saved_order_and_hidden_skip(self):
        topics = [_node(2, 'Geometry', 1), _node(1, 'Algebra', 0)]
        subtopics = [
            _node(12, 'Surds', 2, topic_id=1, hidden=False),
            _node(11, 'Textbook Ch 1', 0, topic_id=1, hidden=True),
            _node(10, 'Indices', 1, topic_id=1, hidden=False),
            _node(20, 'Angles', 0, topic_id=2, hidden=False),
        ]
        t_nums, s_nums = number_taxonomy(topics, subtopics)
        self.assertEqual(t_nums, {1: '01', 2: '02'})
        self.assertEqual(s_nums, {10: '1.1', 12: '1.2', 20: '2.1'})

    def test_padding_toggles(self):
        topics = [_node(1, 'Algebra', 0)]
        subtopics = [_node(10, 'Indices', 0, topic_id=1, hidden=False)]
        t_nums, s_nums = number_taxonomy(topics, subtopics, pad_topic=False, pad_subtopic=True)
        self.assertEqual(t_nums, {1: '1'})
        self.assertEqual(s_nums, {10: '01.01'})

    def test_hidden_topic_skipped_with_children(self):
        topics = [_node(1, 'Old syllabus', 0, hidden=True), _node(2, 'Algebra', 1, hidden=False)]
        subtopics = [_node(10, 'A', 0, topic_id=1, hidden=False),
                     _node(20, 'B', 0, topic_id=2, hidden=False)]
        t_nums, s_nums = number_taxonomy(topics, subtopics)
        self.assertEqual(t_nums, {2: '01'})
        self.assertEqual(s_nums, {20: '1.1'})


class TypeSuffixTests(unittest.TestCase):
    def test_suffix_values(self):
        self.assertEqual(generator._type_suffix([_q(q_type='CQ'), _q(q_type='MC')]), 'MC CQ')
        self.assertEqual(generator._type_suffix([_q(q_type='MC')]), 'MC')
        self.assertEqual(generator._type_suffix([_q(q_type=None)]), '')

    def test_suffix_per_consecutive_run(self):
        alg = _node(1, 'Algebra')
        geo = _node(2, 'Geometry')
        qs = [_q(alg, q_type='MC'), _q(alg, q_type='CQ'), _q(geo, q_type='CQ'), _q(alg, q_type='MC')]
        self.assertEqual(
            generator._section_run_suffixes(qs, {'topic': True}),
            ['MC CQ', 'MC CQ', 'CQ', 'MC'],
        )


class FormatSectionLabelTests(unittest.TestCase):
    def setUp(self):
        self.topic = _node(1, 'Basic Algebra')
        self.sub = _node(10, 'Law of Indices')
        self.numbering = {'topic': {1: '01'}, 'subtopic': {10: '1.1'}}

    def test_numbered_with_suffix(self):
        label = generator._format_section_label(
            _q(self.topic, self.sub), {'topic': True, 'subtopic': True},
            self.numbering, 'MC CQ',
        )
        self.assertEqual(label, '01 Basic Algebra - 1.1 Law of Indices MC CQ')

    def test_plain_label_unchanged(self):
        label = generator._format_section_label(
            _q(self.topic, self.sub, chapter=_node(5, 'Ch 3')),
            {'subtopic': True, 'chapter': True},
        )
        self.assertEqual(label, 'Law of Indices | Ch 3')

    def test_suffix_attaches_to_topic_when_no_subtopic_field(self):
        label = generator._format_section_label(
            _q(self.topic, self.sub), {'topic': True}, None, 'CQ',
        )
        self.assertEqual(label, 'Basic Algebra CQ')

    def test_split_groups_use_missing_and_group_suffix(self):
        qs = [_q(self.topic, self.sub, 'MC'), _q(self.topic, None, 'CQ'), _q(self.topic, self.sub, 'CQ')]
        groups = generator._split_questions_into_groups(
            qs, {'topic': True, 'subtopic': True},
            {'numbering': self.numbering, 'type_suffix': True},
        )
        self.assertEqual(
            [label for label, _ in groups],
            ['01 Basic Algebra - 1.1 Law of Indices MC CQ', '01 Basic Algebra - Unknown CQ'],
        )
        self.assertEqual([len(g) for _, g in groups], [2, 1])

    def test_split_without_options_matches_old_label(self):
        groups = generator._split_questions_into_groups(
            [_q(None, None)], {'topic': True, 'chapter': True},
        )
        self.assertEqual(groups[0][0], 'Unknown _ Unknown')


if __name__ == '__main__':
    unittest.main()
