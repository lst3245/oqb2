"""Pure tests for app.review_service (no app, no DB): payload parsing,
item validation with fake questions, display rows and batch status."""
import json
import unittest
from types import SimpleNamespace

from app import question_edit_service as qes
from app import review_service as rs


def _tax():
    topics = [SimpleNamespace(id=1, name='Algebra', name_zh='代數'),
              SimpleNamespace(id=2, name='Geometry', name_zh='幾何')]
    subtopics = [SimpleNamespace(id=11, topic_id=1, name='Equations', name_zh=None)]
    return qes.Taxonomy(topics, subtopics, [], [])


def _current(**over):
    base = {f: None for f in qes.FIELDS}
    base.update(minor_topics=[], subtopics=[])
    base.update(over)
    return base


Q1 = SimpleNamespace(id=7, qid='MATC_DSE_2020_P1_Q1', subject='MATC')
Q2 = SimpleNamespace(id=8, qid='MATC_DSE_2020_P1_Q2', subject='MATC')
QX = SimpleNamespace(id=9, qid='PHY_DSE_2020_P1_Q1', subject='PHY')
BY_QID = {q.qid: q for q in (Q1, Q2, QX)}
BY_ID = {q.id: q for q in (Q1, Q2, QX)}


def _validate(items, current=None):
    def find(qid, pk):
        if qid:
            return BY_QID.get(qid)
        return BY_ID.get(int(pk)) if pk not in (None, '') else None
    return rs.validate_items(items, 'MATC', find=find, taxonomy=_tax(),
                             current=lambda q: current or _current(level=2, major_topic=1),
                             stem=lambda q: False)


def _messages(problems, level=None):
    return [p['message'] for p in problems if level is None or p['level'] == level]


class ParseTests(unittest.TestCase):
    def test_confidence(self):
        self.assertEqual(rs.parse_confidence(0.5), 0.5)
        self.assertEqual(rs.parse_confidence('80%'), 0.8)
        self.assertEqual(rs.parse_confidence(80), 0.8)
        self.assertEqual(rs.parse_confidence(1), 1.0)
        for blank in (None, '', True, False):
            self.assertIsNone(rs.parse_confidence(blank))
        for bad in ('high', -0.1, 150):
            with self.subTest(bad=bad), self.assertRaises(rs.ReviewError):
                rs.parse_confidence(bad)

    def test_payload_shapes(self):
        items = [{'qid': Q1.qid, 'changes': {'level': 3}}]
        self.assertEqual(rs.parse_payload(items)['items'], items)
        parsed = rs.parse_payload(json.dumps({'subject_id': ' matc ', 'title': ' T ', 'items': items}))
        self.assertEqual((parsed['subject'], parsed['title']), ('MATC', 'T'))
        self.assertEqual(rs.parse_payload({'subject': 'phy', 'items': items})['subject'], 'PHY')

    def test_payload_errors(self):
        for bad in ('{not json', '"text"', {'items': []}, {'items': 'x'}, {}, 5):
            with self.subTest(bad=bad), self.assertRaises(rs.ReviewError):
                rs.parse_payload(bad)
        with self.assertRaises(rs.ReviewError):
            rs.parse_payload({'items': [{}] * (rs.MAX_ITEMS + 1)})


class ValidateItemsTests(unittest.TestCase):
    def test_valid_change(self):
        valid, problems = _validate([{'qid': Q1.qid, 'changes': [{'field': 'level', 'old': 2, 'new': 3}],
                                      'rationale': 'harder than it looks', 'confidence': 0.9}])
        self.assertEqual(problems, [])
        self.assertEqual(len(valid), 1)
        item = valid[0]
        self.assertEqual((item['question_id'], item['qid'], item['kind']), (7, Q1.qid, 'fields'))
        self.assertEqual(item['changes'], [{'field': 'level', 'old': 2, 'new': 3}])
        self.assertEqual((item['rationale'], item['confidence']), ('harder than it looks', 0.9))

    def test_question_id_and_names(self):
        valid, problems = _validate([{'question_id': 8, 'changes': {'major_topic': 'Geometry'}}])
        self.assertEqual(problems, [])
        self.assertEqual(valid[0]['qid'], Q2.qid)
        self.assertEqual(valid[0]['changes'], [{'field': 'major_topic', 'old': 1, 'new': 2}])

    def test_errors_drop_the_item(self):
        cases = [
            ('not an object', 'each item must be an object'),
            ({'changes': {'level': 3}}, 'qid is required'),
            ({'qid': 'MATC_NOPE', 'changes': {'level': 3}}, 'question not found'),
            ({'qid': QX.qid, 'changes': {'level': 3}}, 'is in PHY, not MATC'),
            ({'qid': Q1.qid, 'changes': {'level': 2}}, 'nothing to change'),
            ({'qid': Q1.qid, 'changes': {'colour': 'red'}}, 'unknown field'),
            ({'qid': Q1.qid, 'kind': 'freeform'}, 'needs a title or a rationale'),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                valid, problems = _validate([raw])
                self.assertEqual(valid, [])
                self.assertEqual(len(problems), 1)
                self.assertEqual(problems[0]['level'], 'error')
                self.assertIn(expected, problems[0]['message'])
                self.assertEqual(problems[0]['index'], 1)

    def test_warnings_keep_the_item(self):
        valid, problems = _validate([
            {'qid': Q1.qid, 'changes': [{'field': 'level', 'old': 1, 'new': 3}], 'confidence': 'sure'},
            {'qid': Q1.qid, 'changes': {'level': 3, 'major_topic': 1}},
        ])
        self.assertEqual(len(valid), 2)
        self.assertIsNone(valid[0]['confidence'])
        warnings = _messages(problems, 'warning')
        self.assertTrue(any('confidence' in w for w in warnings))
        self.assertTrue(any('"old" differs' in w and 'level' in w for w in warnings))
        self.assertTrue(any('already hold the new value' in w for w in warnings))
        self.assertTrue(any('more than once' in w for w in warnings))
        self.assertEqual(valid[1]['changes'], [{'field': 'level', 'old': 2, 'new': 3}])

    def test_freeform(self):
        valid, problems = _validate([
            {'qid': 'MATC_GONE', 'title': 'Image is blurry'},
            {'kind': 'freeform', 'rationale': 'Re-scan the 2019 paper'},
        ])
        self.assertEqual([v['kind'] for v in valid], ['freeform', 'freeform'])
        self.assertEqual(valid[0]['qid'], 'MATC_GONE')
        self.assertIsNone(valid[0]['question_id'])
        self.assertEqual(_messages(problems), ['question not found; kept as text'])


class DisplayTests(unittest.TestCase):
    def test_change_rows_flags(self):
        tax = _tax()
        changes = [{'field': 'level', 'old': 2, 'new': 3},
                   {'field': 'major_topic', 'old': 1, 'new': 2},
                   {'field': 'marks', 'old': 4, 'new': 5},
                   {'field': 'bogus', 'old': 1, 'new': 2}]
        rows = rs.change_rows(changes, _current(level=2, major_topic=2, marks=6), tax)
        self.assertEqual([r['field'] for r in rows], ['level', 'major_topic', 'marks'])
        level, topic, marks = rows
        self.assertEqual((level['stale'], level['noop']), (False, False))
        self.assertEqual((topic['stale'], topic['noop']), (False, True))
        self.assertEqual((marks['stale'], marks['noop']), (True, False))
        self.assertIn('Geometry', topic['new_label'])
        self.assertEqual(level['label'], 'Level')

    def test_change_rows_without_question(self):
        rows = rs.change_rows([{'field': 'level', 'old': 2, 'new': 3}], None, _tax())
        self.assertEqual((rows[0]['stale'], rows[0]['noop'], rows[0]['current']), (False, False, None))

    def test_next_batch_status(self):
        self.assertEqual(rs.next_batch_status('open', {'applied': 2, 'rejected': 1}), 'done')
        self.assertEqual(rs.next_batch_status('done', {'skipped': 1, 'applied': 1}), 'open')
        self.assertEqual(rs.next_batch_status('open', {'pending': 1}), 'open')
        self.assertEqual(rs.next_batch_status('archived', {'pending': 4}), 'archived')
        self.assertEqual(rs.next_batch_status('open', {}), 'done')

    def test_item_kind(self):
        self.assertEqual(rs.item_kind({'changes': {}}), 'fields')
        self.assertEqual(rs.item_kind({'title': 'x'}), 'freeform')
        self.assertEqual(rs.item_kind({'kind': 'FREEFORM', 'changes': {}}), 'freeform')


if __name__ == '__main__':
    unittest.main()
