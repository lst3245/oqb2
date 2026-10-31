"""Pure tests for app.question_edit_service (no app, no DB)."""
import unittest
from types import SimpleNamespace

from app import question_edit_service as qes


def _tax():
    topics = [SimpleNamespace(id=1, name='Algebra', name_zh='代數'),
              SimpleNamespace(id=2, name='Geometry', name_zh='幾何')]
    subtopics = [SimpleNamespace(id=11, topic_id=1, name='Equations', name_zh='方程'),
                 SimpleNamespace(id=12, topic_id=1, name='Inequalities', name_zh=None),
                 SimpleNamespace(id=21, topic_id=2, name='Circles', name_zh='圓')]
    chapters = [SimpleNamespace(id=5, name='Chapter A', name_zh=None),
                SimpleNamespace(id=6, name='Chapter B', name_zh=None)]
    subchapters = [SimpleNamespace(id=51, chapter_id=5, name='A1', name_zh=None),
                   SimpleNamespace(id=61, chapter_id=6, name='B1', name_zh=None)]
    return qes.Taxonomy(topics, subtopics, chapters, subchapters)


def _current(**over):
    base = {f: None for f in qes.FIELDS}
    base.update(minor_topics=[], subtopics=[])
    base.update(over)
    return base


class FieldNameTests(unittest.TestCase):
    def test_aliases(self):
        self.assertEqual(qes.field_name('major_topic_id'), 'major_topic')
        self.assertEqual(qes.field_name('Type'), 'q_type')
        self.assertEqual(qes.field_name('minor_topic_ids'), 'minor_topics')
        self.assertIsNone(qes.field_name('qid'))


class NormalizeValueTests(unittest.TestCase):
    def test_scalars(self):
        self.assertEqual(qes.normalize_value('level', '2'), 2)
        self.assertEqual(qes.normalize_value('q_type', 'mc'), 'MC')
        self.assertEqual(qes.normalize_value('marks', '4.0'), 4)
        self.assertEqual(qes.normalize_value('correct_percentage', 55), 55)
        self.assertIsNone(qes.normalize_value('answer', '   '))

    def test_bad_scalars(self):
        for field, raw in (('level', 4), ('q_type', 'LQ'), ('marks', '2.5'),
                           ('correct_percentage', 101), ('section', 'x' * 51)):
            with self.subTest(field=field), self.assertRaises(qes.EditError):
                qes.normalize_value(field, raw)

    def test_taxonomy_refs(self):
        tax = _tax()
        self.assertEqual(qes.normalize_value('major_topic', 'algebra', tax), 1)
        self.assertEqual(qes.normalize_value('major_topic', '幾何', tax), 2)
        self.assertEqual(qes.normalize_value('major_topic', {'id': 2}, tax), 2)
        self.assertEqual(qes.normalize_value('major_topic', '1', tax), 1)
        self.assertEqual(qes.normalize_value('minor_topics', ['Geometry', 1, 1], tax), [1, 2])
        self.assertEqual(qes.normalize_value('minor_topics', None, tax), [])
        with self.assertRaises(qes.EditError):
            qes.normalize_value('major_topic', 99, tax)
        with self.assertRaises(qes.EditError):
            qes.normalize_value('chapter', 'No such chapter', tax)


class NormalizeChangesTests(unittest.TestCase):
    def test_list_and_dict_shapes(self):
        tax = _tax()
        out, warn = qes.normalize_changes([{'field': 'major_topic', 'old': 'Algebra', 'new': 'Geometry'}],
                                          tax, _current(major_topic=1))
        self.assertEqual(out, [{'field': 'major_topic', 'old': 1, 'new': 2}])
        self.assertEqual(warn, [])
        out, _ = qes.normalize_changes({'level': 3, 'chapter': {'old': None, 'new': 'Chapter B'}},
                                       tax, _current())
        self.assertEqual([c['field'] for c in out], ['level', 'chapter'])
        self.assertEqual(out[1]['new'], 6)

    def test_old_defaults_to_current_and_bad_old_warns(self):
        tax = _tax()
        out, warn = qes.normalize_changes({'level': 2}, tax, _current(level=1))
        self.assertEqual(out[0]['old'], 1)
        out, warn = qes.normalize_changes([{'field': 'chapter', 'old': 'Gone', 'new': 5}], tax,
                                          _current(chapter=6))
        self.assertEqual(out[0]['old'], 6)
        self.assertEqual(len(warn), 1)

    def test_child_checked_against_new_parent(self):
        tax = _tax()
        out, _ = qes.normalize_changes({'major_topic': 'Geometry', 'major_subtopic': 'Circles'},
                                       tax, _current(major_topic=1))
        self.assertEqual({c['field']: c['new'] for c in out}, {'major_topic': 2, 'major_subtopic': 21})
        with self.assertRaises(qes.EditError):
            qes.normalize_changes({'major_subtopic': 'Circles'}, tax, _current(major_topic=1))
        with self.assertRaises(qes.EditError):
            qes.normalize_changes({'subchapter': 'A1'}, tax, _current())

    def test_rejections(self):
        tax = _tax()
        for raw in ({'bogus': 1}, [{'field': 'level'}], {}, 'level=2',
                    [{'field': 'level', 'new': 1}, {'field': 'level', 'new': 2}]):
            with self.subTest(raw=raw), self.assertRaises(qes.EditError):
                qes.normalize_changes(raw, tax, _current())

    def test_stems_refuse_leaf_fields(self):
        tax = _tax()
        with self.assertRaises(qes.EditError):
            qes.normalize_changes({'major_topic': 1}, tax, _current(), stem=True)
        out, _ = qes.normalize_changes({'comment': 'shared stem'}, tax, _current(), stem=True)
        self.assertEqual(out[0]['new'], 'shared stem')


class PlanAfterTests(unittest.TestCase):
    def test_new_parent_clears_child(self):
        tax = _tax()
        cur = _current(major_topic=1, major_subtopic=11, chapter=5, subchapter=51)
        after = qes.plan_after(cur, {'major_topic': 2, 'chapter': 5}, tax)
        self.assertEqual(after['major_topic'], 2)
        self.assertIsNone(after['major_subtopic'])
        self.assertEqual(after['subchapter'], 51)

    def test_child_outside_parent_dropped(self):
        tax = _tax()
        after = qes.plan_after(_current(major_topic=1), {'major_subtopic': 21}, tax)
        self.assertIsNone(after['major_subtopic'])
        after = qes.plan_after(_current(major_topic=1), {'major_subtopic': 12}, tax)
        self.assertEqual(after['major_subtopic'], 12)

    def test_stem_keeps_leaf_fields(self):
        after = qes.plan_after(_current(level=1), {'level': 3, 'comment': 'x'}, _tax(), stem=True)
        self.assertEqual(after['level'], 1)
        self.assertEqual(after['comment'], 'x')


class StaleAndLabelTests(unittest.TestCase):
    def test_stale_fields(self):
        changes = [{'field': 'level', 'old': 1, 'new': 2},
                   {'field': 'minor_topics', 'old': [2, 1], 'new': [1]}]
        self.assertEqual(qes.stale_fields(changes, _current(level=1, minor_topics=[1, 2])), [])
        self.assertEqual(qes.stale_fields(changes, _current(level=2, minor_topics=[1])),
                         ['level', 'minor_topics'])

    def test_changed_fields(self):
        before = _current(level=1, minor_topics=[1])
        after = dict(before, level=2, minor_topics=[1])
        self.assertEqual(qes.changed_fields(before, after), ['level'])

    def test_labels_and_corrections(self):
        tax = _tax()
        self.assertEqual(qes.label('major_topic', 2, tax, 'en'), 'Geometry')
        self.assertEqual(qes.label('minor_topics', [1, 2], tax, 'en'), 'Algebra, Geometry')
        self.assertEqual(qes.label('level', None, tax), '')
        labels = qes.correction_labels({'major_topic': 1, 'minor_topics': [2], 'level': 3, 'chapter': None},
                                       tax, ['major_topic', 'minor_topics', 'level', 'chapter'])
        self.assertEqual(labels, {'major_topic': 'Algebra', 'minor_topics': ['Geometry'],
                                  'level': '3', 'chapter': None})


if __name__ == '__main__':
    unittest.main()
