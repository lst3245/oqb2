"""Bulk Edit preview plan. Pure: no database and no create_app()."""
import unittest
from types import SimpleNamespace

from app.batch_edit import (
    BatchEditError, apply_resolved, parse_batch_form, parse_batch_question_ids,
    preview_for_questions, resolve_batch, view_from_question,
)


class _Form:
    def __init__(self, data=None, lists=None):
        self.data = data or {}
        self.lists = lists or {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def getlist(self, key):
        return list(self.lists.get(key, []))


def _flags(**on):
    base = {
        'update_level': '0',
        'update_q_type': '0',
        'update_section': '0',
        'update_correct_pct': '0',
        'update_topics': '0',
        'update_chapters': '0',
    }
    base.update(on)
    return base


def _topic(id_, name, topic_id=None):
    return SimpleNamespace(id=id_, name=name, topic_id=topic_id)


def _question(**kw):
    defaults = dict(
        level=1, q_type='MC', section='A', correct_percentage=40,
        major_topic_id=1, major_topic=_topic(1, 'Algebra'),
        major_subtopic_id=None, major_subtopic=None,
        minor_topics=[], subtopics=[],
        chapter_id=None, chapter=None,
        subchapter_id=None, subchapter=None,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


class ParseTests(unittest.TestCase):
    def test_question_ids_keep_order_and_drop_duplicates(self):
        form = _Form(lists={'question_ids': ['3', '1', '3', 'x', '2']})
        self.assertEqual(parse_batch_question_ids(form), [3, 1, 2])

    def test_missing_ids(self):
        with self.assertRaises(BatchEditError) as caught:
            parse_batch_question_ids(_Form())
        self.assertEqual(str(caught.exception), 'No questions selected')

    def test_unticked_fields_are_ignored(self):
        parsed = parse_batch_form(_Form(_flags(level='9', q_type='CQ')))
        self.assertFalse(any(parsed['flags'].values()))
        self.assertIsNone(parsed['level'])
        self.assertIsNone(parsed['q_type'])

    def test_empty_level_clears(self):
        parsed = parse_batch_form(_Form(_flags(update_level='1', level='')))
        self.assertIsNone(parsed['level'])

    def test_bad_level(self):
        with self.assertRaises(BatchEditError):
            parse_batch_form(_Form(_flags(update_level='1', level='high')))

    def test_percentage_out_of_range_clears_with_a_warning(self):
        parsed = parse_batch_form(_Form(_flags(
            update_correct_pct='1', correct_percentage='150')))
        self.assertIsNone(parsed['correct_percentage'])
        self.assertIn('0 and 100', parsed['correct_percentage_warning'])

    def test_percentage_zero_is_kept(self):
        parsed = parse_batch_form(_Form(_flags(
            update_correct_pct='1', correct_percentage='0')))
        self.assertEqual(parsed['correct_percentage'], 0)
        self.assertIsNone(parsed['correct_percentage_warning'])

    def test_blank_percentage_clears_without_a_warning(self):
        parsed = parse_batch_form(_Form(_flags(
            update_correct_pct='1', correct_percentage='  ')))
        self.assertIsNone(parsed['correct_percentage'])
        self.assertIsNone(parsed['correct_percentage_warning'])

    def test_section_keeps_internal_spaces(self):
        parsed = parse_batch_form(_Form(_flags(update_section='1', section=' B ')))
        self.assertEqual(parsed['section'], ' B ')

    def test_duplicate_minor_topics_collapse(self):
        parsed = parse_batch_form(_Form(
            _flags(update_topics='1', major_topic_id='', major_subtopic_id=''),
            lists={'minor_topic_ids': ['5', '5', '6', ''], 'subtopic_ids': []},
        ))
        self.assertEqual(parsed['minor_topic_ids'], [5, 6])


class ResolveAndPreviewTests(unittest.TestCase):
    def _topics_form(self, **extra):
        data = _flags(
            update_topics='1',
            major_topic_id='3',
            major_subtopic_id='9',
        )
        data.update(extra)
        return _Form(data, lists={
            'minor_topic_ids': extra.get('minor_topic_ids', []),
            'subtopic_ids': extra.get('subtopic_ids', []),
        })

    def test_subtopic_outside_the_major_topic_is_cleared(self):
        parsed = parse_batch_form(self._topics_form())
        resolved = resolve_batch(
            parsed,
            topics={3: 'Statistics'},
            subtopics={9: {'name': 'Indices', 'topic_id': 1}},
            chapters={},
            subchapters={},
        )
        self.assertEqual(resolved['major_topic_id'], 3)
        self.assertIsNone(resolved['major_subtopic_id'])
        self.assertTrue(any('cleared' in note for note in resolved['warnings']))

    def test_subtopic_of_the_major_topic_is_kept(self):
        parsed = parse_batch_form(self._topics_form())
        resolved = resolve_batch(
            parsed,
            topics={3: 'Statistics'},
            subtopics={9: {'name': 'Dispersion', 'topic_id': 3}},
            chapters={},
            subchapters={},
        )
        self.assertEqual(resolved['major_subtopic'], (9, 'Dispersion'))
        self.assertFalse(resolved['warnings'])

    def test_missing_minor_topic_is_skipped(self):
        parsed = parse_batch_form(self._topics_form(
            major_subtopic_id='', minor_topic_ids=['3', '8']))
        resolved = resolve_batch(
            parsed,
            topics={3: 'Statistics'},
            subtopics={},
            chapters={},
            subchapters={},
        )
        self.assertEqual(resolved['minor_topics'], [(3, 'Statistics')])
        self.assertTrue(any('minor topic' in note for note in resolved['warnings']))

    def test_preview_lists_only_questions_that_change(self):
        parsed = parse_batch_form(_Form(_flags(
            update_level='1', level='2',
            update_topics='1', major_topic_id='3', major_subtopic_id='',
        ), lists={'minor_topic_ids': [], 'subtopic_ids': []}))
        resolved = resolve_batch(
            parsed,
            topics={3: 'Statistics'},
            subtopics={},
            chapters={},
            subchapters={},
        )
        changing = _question(
            level=1,
            major_topic_id=1, major_topic=_topic(1, 'Algebra'),
            minor_topics=[_topic(2, 'Geometry')],
        )
        already = _question(
            level=2,
            major_topic_id=3, major_topic=_topic(3, 'Statistics'),
            minor_topics=[],
        )
        same_set_reordered = _question(
            level=2,
            major_topic_id=3, major_topic=_topic(3, 'Statistics'),
            minor_topics=[],
        )
        payload = preview_for_questions([
            (10, 'Q-CHANGE', view_from_question(changing)),
            (11, 'Q-SAME', view_from_question(already)),
            (12, 'Q-ORDER', view_from_question(same_set_reordered)),
        ], resolved)
        self.assertEqual(payload['changed_count'], 1)
        self.assertEqual(payload['unchanged_count'], 2)
        self.assertEqual([q['qid'] for q in payload['unchanged']], ['Q-SAME', 'Q-ORDER'])
        fields = {change['field']: change for change in payload['questions'][0]['changes']}
        self.assertEqual(fields['level']['before'], '1')
        self.assertEqual(fields['level']['after'], '2')
        self.assertEqual(fields['major_topic']['before'], 'Algebra')
        self.assertEqual(fields['major_topic']['after'], 'Statistics')
        self.assertEqual(fields['minor_topics']['removed'], ['Geometry'])
        self.assertEqual(fields['minor_topics']['added'], [])
        self.assertTrue(fields['minor_topics']['after_empty'])
        self.assertNotIn('q_type', fields)
        self.assertEqual(payload['updating'], ['Level', 'Topics & subtopics'])

    def test_reordering_the_same_minor_topics_is_not_a_change(self):
        parsed = parse_batch_form(_Form(
            _flags(update_topics='1', major_topic_id='1', major_subtopic_id=''),
            lists={'minor_topic_ids': ['6', '5'], 'subtopic_ids': []},
        ))
        resolved = resolve_batch(
            parsed,
            topics={1: 'Algebra', 5: 'Graphs', 6: 'Indices'},
            subtopics={},
            chapters={},
            subchapters={},
        )
        question = _question(
            major_topic_id=1, major_topic=_topic(1, 'Algebra'),
            minor_topics=[_topic(5, 'Graphs'), _topic(6, 'Indices')],
        )
        payload = preview_for_questions(
            [(1, 'Q1', view_from_question(question))], resolved)
        self.assertEqual(payload['changed_count'], 0)

    def test_zero_percent_to_empty_is_a_change(self):
        parsed = parse_batch_form(_Form(_flags(
            update_correct_pct='1', correct_percentage='')))
        resolved = resolve_batch(parsed, {}, {}, {}, {})
        question = _question(correct_percentage=0)
        payload = preview_for_questions(
            [(1, 'Q1', view_from_question(question))], resolved)
        change = payload['questions'][0]['changes'][0]
        self.assertEqual(change['field'], 'correct_percentage')
        self.assertEqual(change['before'], '0%')
        self.assertTrue(change['after_empty'])

    def test_unticked_bundle_does_not_show(self):
        parsed = parse_batch_form(_Form(_flags(update_level='1', level='3')))
        resolved = resolve_batch(parsed, {}, {}, {}, {})
        question = _question(level=3, major_topic_id=1, major_topic=_topic(1, 'Algebra'))
        payload = preview_for_questions(
            [(1, 'Q1', view_from_question(question))], resolved)
        self.assertEqual(payload['changed_count'], 0)


class ApplyTests(unittest.TestCase):
    def test_apply_writes_only_the_ticked_bundle(self):
        question = _question(level=1, q_type='MC', section='A')
        question.minor_topics = [_topic(2, 'Geometry')]
        question.subtopics = []
        resolved = {
            'flags': {
                'level': False, 'q_type': False, 'section': False,
                'correct_percentage': False, 'topics': True, 'chapters': False,
            },
            'level': 3,
            'q_type': 'CQ',
            'section': None,
            'correct_percentage': None,
            'major_topic_id': 3,
            'major_subtopic_id': None,
            'chapter_id': 9,
            'subchapter_id': None,
        }
        topic = _topic(3, 'Statistics')
        apply_resolved(question, resolved, [topic], [])
        self.assertEqual(question.level, 1)
        self.assertEqual(question.q_type, 'MC')
        self.assertEqual(question.section, 'A')
        self.assertEqual(question.major_topic_id, 3)
        self.assertIsNone(question.major_subtopic_id)
        self.assertEqual(question.minor_topics, [topic])
        self.assertEqual(question.chapter_id, None)


if __name__ == '__main__':
    unittest.main()
