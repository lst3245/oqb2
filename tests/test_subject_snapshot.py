"""Pure tests for subject restore points (payload codec, restore planning,
preview summary, tag-change detection).

Does not call create_app() or touch the live database.
"""
import json
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from app.subject_snapshot import (
    LINK_FIELDS,
    QUESTION_FIELDS,
    SnapshotError,
    decode_state,
    empty_state,
    encode_state,
    plan_has_changes,
    plan_restore,
    question_tags_differ,
    select_prunable,
    summarize,
)


def _q(qid, **kw):
    row = {'qid': qid}
    for f in QUESTION_FIELDS:
        row[f] = kw.get(f)
    for f in LINK_FIELDS:
        row[f] = tuple(sorted(kw.get(f, ())))
    return row


def _state():
    """MATC-like subject: two topics, three subtopics, one chapter."""
    s = empty_state('MATC')
    s['topics'] = {
        1: {'name': '01 Numbers', 'sort_order': 1, 'description': None},
        2: {'name': '02 Algebra', 'sort_order': 2, 'description': 'eqns'},
    }
    s['subtopics'] = {
        10: {'topic_id': 1, 'name': 'Integers', 'hidden': False, 'sort_order': 1, 'description': None},
        11: {'topic_id': 1, 'name': 'Fractions', 'hidden': True, 'sort_order': 2, 'description': None},
        20: {'topic_id': 2, 'name': 'Linear', 'hidden': False, 'sort_order': 1, 'description': None},
    }
    s['chapters'] = {5: {'name': 'Ch 1', 'sort_order': 1, 'description': None}}
    s['subchapters'] = {50: {'chapter_id': 5, 'name': '1.1', 'hidden': False, 'sort_order': 1,
                             'description': None}}
    s['questions'] = {
        100: _q('MATC_Q1', major_topic_id=1, major_subtopic_id=10, level=2, q_type='MC',
                section='A', correct_percentage=55, minor_topic_ids=(2,), subtopic_ids=(20,)),
        101: _q('MATC_Q2', major_topic_id=2, major_subtopic_id=20, chapter_id=5,
                subchapter_id=50, level=3, q_type='CQ'),
    }
    return s


def _copy(state):
    return decode_state(encode_state(state))


class CodecTests(unittest.TestCase):
    def test_round_trip(self):
        s = _state()
        back = decode_state(encode_state(s))
        for kind in ('topics', 'subtopics', 'chapters', 'subchapters', 'questions'):
            self.assertEqual(back[kind], s[kind], kind)
        self.assertEqual(back['subject_id'], 'MATC')
        self.assertEqual(set(back['fields']), set(QUESTION_FIELDS + LINK_FIELDS))

    def test_unicode_names_kept(self):
        s = _state()
        s['topics'][1]['name'] = '數與代數'
        payload = encode_state(s)
        self.assertIn('數與代數', payload)
        self.assertEqual(decode_state(payload)['topics'][1]['name'], '數與代數')

    def test_bad_payloads_raise(self):
        with self.assertRaises(SnapshotError):
            decode_state('not json')
        with self.assertRaises(SnapshotError):
            decode_state(json.dumps({'v': 999, 'subject_id': 'X'}))

    def test_older_payload_without_a_column_leaves_it_alone(self):
        data = json.loads(encode_state(_state()))
        idx = data['question_columns'].index('correct_percentage')
        data['question_columns'].pop(idx)
        for row in data['questions']:
            row.pop(idx)
        point = decode_state(json.dumps(data))
        self.assertNotIn('correct_percentage', point['fields'])

        live = _copy(_state())
        live['questions'][100]['correct_percentage'] = 80
        plan = plan_restore(live, point)
        self.assertFalse(plan['questions']['changed'])
        self.assertNotIn('correct_percentage', plan['questions']['scalar_fields'])


class PlanTests(unittest.TestCase):
    def test_identical_states_plan_nothing(self):
        plan = plan_restore(_copy(_state()), _copy(_state()))
        self.assertFalse(plan_has_changes(plan))
        self.assertEqual(plan['conflicts'], [])

    def test_bulk_retag_is_reverted(self):
        point = _copy(_state())
        live = _copy(_state())
        for row in live['questions'].values():
            row.update(major_topic_id=2, major_subtopic_id=None, level=None,
                       minor_topic_ids=(), subtopic_ids=(10, 11))
        plan = plan_restore(live, point)
        q = plan['questions']
        self.assertEqual({c['id'] for c in q['changed']}, {100, 101})
        by_id = {u['id']: u for u in q['scalar_updates']}
        self.assertEqual(by_id[100]['major_topic_id'], 1)
        self.assertEqual(by_id[100]['major_subtopic_id'], 10)
        self.assertEqual(by_id[101]['level'], 3)
        self.assertEqual(sorted(q['links']['minor_topic_ids']['add']), [(100, 2)])
        self.assertIn((100, 10), q['links']['subtopic_ids']['remove'])
        self.assertIn((100, 20), q['links']['subtopic_ids']['add'])
        self.assertIn((101, 11), q['links']['subtopic_ids']['remove'])

    def test_rename_and_reorder_are_updates(self):
        point = _copy(_state())
        live = _copy(_state())
        live['topics'][2]['name'] = '07 Straight Lines'
        live['topics'][2]['sort_order'] = 9
        live['subtopics'][11]['hidden'] = False
        plan = plan_restore(live, point)
        upd = {u['id']: u for u in plan['taxonomy']['topics']['update']}
        self.assertEqual(upd[2]['set'], {'name': '02 Algebra', 'sort_order': 2})
        self.assertEqual(upd[2]['old']['name'], '07 Straight Lines')
        sub = plan['taxonomy']['subtopics']['update']
        self.assertEqual(sub, [{'id': 11, 'set': {'hidden': True}, 'old': {'hidden': False}}])

    def test_deleted_rows_are_reinserted_with_their_ids(self):
        point = _copy(_state())
        live = _copy(_state())
        del live['subtopics'][20]
        del live['topics'][2]
        for row in live['questions'].values():
            row['major_topic_id'] = None if row['major_topic_id'] == 2 else row['major_topic_id']
            row['major_subtopic_id'] = None if row['major_subtopic_id'] == 20 else row['major_subtopic_id']
            row['minor_topic_ids'] = ()
            row['subtopic_ids'] = ()
        plan = plan_restore(live, point)
        self.assertEqual([r['id'] for r in plan['taxonomy']['topics']['insert']], [2])
        ins = plan['taxonomy']['subtopics']['insert']
        self.assertEqual(ins, [{'id': 20, 'topic_id': 2, 'name': 'Linear', 'hidden': False,
                                'sort_order': 1, 'description': None}])
        by_id = {u['id']: u for u in plan['questions']['scalar_updates']}
        self.assertEqual(by_id[101]['major_topic_id'], 2)
        self.assertEqual(by_id[101]['major_subtopic_id'], 20)
        self.assertEqual(plan['questions']['dropped_refs'], [])

    def test_newer_unreferenced_rows_are_deleted(self):
        point = _copy(_state())
        live = _copy(_state())
        live['topics'][3] = {'name': 'New topic', 'sort_order': 3, 'description': None}
        live['subtopics'][30] = {'topic_id': 3, 'name': 'New sub', 'hidden': False,
                                 'sort_order': 1, 'description': None}
        live['questions'][100]['subtopic_ids'] = (20, 30)
        plan = plan_restore(live, point)
        self.assertEqual(plan['taxonomy']['subtopics']['delete'], [30])
        self.assertEqual(plan['taxonomy']['topics']['delete'], [3])
        self.assertIn((100, 30), plan['questions']['links']['subtopic_ids']['remove'])

    def test_newer_rows_used_by_newer_questions_are_kept(self):
        point = _copy(_state())
        live = _copy(_state())
        live['topics'][3] = {'name': 'New topic', 'sort_order': 3, 'description': None}
        live['subtopics'][30] = {'topic_id': 3, 'name': 'New sub', 'hidden': False,
                                 'sort_order': 1, 'description': None}
        live['questions'][102] = _q('MATC_Q3', subtopic_ids=(30,))
        plan = plan_restore(live, point)
        self.assertEqual(plan['taxonomy']['subtopics']['keep'], [30])
        # Topic 3 has no direct reference but keeps its surviving subtopic.
        self.assertEqual(plan['taxonomy']['topics']['keep'], [3])
        self.assertEqual(plan['taxonomy']['topics']['delete'], [])
        self.assertEqual(plan['questions']['added_since'], [102])
        self.assertNotIn(102, {c['id'] for c in plan['questions']['changed']})

    def test_external_reference_keeps_newer_row(self):
        point = _copy(_state())
        live = _copy(_state())
        live['chapters'][6] = {'name': 'Ch 2', 'sort_order': 2, 'description': None}
        plan = plan_restore(live, point, external_refs={'chapters': {6}})
        self.assertEqual(plan['taxonomy']['chapters']['keep'], [6])
        self.assertEqual(plan['taxonomy']['chapters']['delete'], [])

    def test_deleted_question_is_skipped(self):
        point = _copy(_state())
        live = _copy(_state())
        del live['questions'][101]
        plan = plan_restore(live, point)
        self.assertEqual(plan['questions']['skipped'], [101])
        self.assertFalse(plan['questions']['changed'])
        # Chapter 5 is still in the point, so nothing taxonomy-wise changes.
        self.assertFalse(plan_has_changes(plan))

    def test_foreign_id_is_a_conflict(self):
        point = _copy(_state())
        live = _copy(_state())
        del live['topics'][2]
        del live['subtopics'][20]
        plan = plan_restore(live, point, foreign={'topics': {2: 'PHYS'}})
        self.assertTrue(any('PHYS' in c for c in plan['conflicts']))
        self.assertEqual(plan['taxonomy']['topics']['insert'], [])

    def test_dangling_reference_is_dropped_not_inserted(self):
        point = _copy(_state())
        point['questions'][100]['minor_topic_ids'] = (2, 999)
        point['questions'][101]['chapter_id'] = 777
        live = _copy(_state())
        plan = plan_restore(live, point)
        dropped = {(pk, f, v) for pk, f, v in plan['questions']['dropped_refs']}
        self.assertIn((100, 'minor_topic_ids', 999), dropped)
        self.assertIn((101, 'chapter_id', 777), dropped)
        self.assertNotIn((100, 999), plan['questions']['links']['minor_topic_ids']['add'])
        by_id = {u['id']: u for u in plan['questions']['scalar_updates']}
        self.assertIsNone(by_id[101]['chapter_id'])

    def test_external_existing_reference_is_kept(self):
        point = _copy(_state())
        point['questions'][100]['minor_topic_ids'] = (2, 999)
        live = _copy(_state())
        plan = plan_restore(live, point, external_existing={'topics': {999}})
        self.assertIn((100, 999), plan['questions']['links']['minor_topic_ids']['add'])
        self.assertEqual(plan['questions']['dropped_refs'], [])


class SummaryTests(unittest.TestCase):
    def test_summary_names_and_counts(self):
        point = _copy(_state())
        live = _copy(_state())
        live['topics'][2]['name'] = '07 Straight Lines'
        live['questions'][100]['major_topic_id'] = 2
        live['questions'][100]['correct_percentage'] = None
        plan = plan_restore(live, point)
        summary = summarize(plan, live, point)
        self.assertTrue(summary['has_changes'])
        self.assertEqual(summary['questions_changed'], 1)
        self.assertEqual(summary['taxonomy']['topics']['rename'],
                         [{'from': '07 Straight Lines', 'to': '02 Algebra'}])
        fields = {fc['field']: fc['count'] for fc in summary['field_counts']}
        self.assertEqual(fields, {'Major topic': 1, 'Correct %': 1})
        sample = summary['samples'][0]
        self.assertEqual(sample['qid'], 'MATC_Q1')
        major = next(c for c in sample['changes'] if c['field'] == 'Major topic')
        self.assertEqual(major, {'field': 'Major topic', 'from': '07 Straight Lines',
                                 'to': '01 Numbers'})
        pct = next(c for c in sample['changes'] if c['field'] == 'Correct %')
        self.assertEqual(pct, {'field': 'Correct %', 'from': '—', 'to': '55'})

    def test_sample_limit(self):
        point = _copy(_state())
        live = _copy(_state())
        for row in live['questions'].values():
            row['level'] = 1
        summary = summarize(plan_restore(live, point), live, point, sample_limit=1)
        self.assertEqual(len(summary['samples']), 1)
        self.assertEqual(summary['samples_truncated'], 1)


class PruneTests(unittest.TestCase):
    NOW = datetime(2026, 10, 6, 12, 0)

    def test_keeps_newest_n_counting_pending(self):
        pts = [(i, self.NOW - timedelta(minutes=10 - i)) for i in range(1, 6)]
        # All on one day: the first of the day (1) is an anchor.
        self.assertEqual(select_prunable(pts, self.NOW, keep=2, pending=1), [2, 3, 4])

    def test_busy_day_keeps_first_point_of_each_day(self):
        yesterday = self.NOW - timedelta(days=1)
        pts = [(1, yesterday.replace(hour=1)), (2, yesterday.replace(hour=9))]
        pts += [(10 + i, self.NOW.replace(hour=1, minute=i)) for i in range(20)]
        dropped = select_prunable(pts, self.NOW, keep=5)
        self.assertNotIn(1, dropped)    # first of yesterday
        self.assertIn(2, dropped)
        self.assertNotIn(10, dropped)   # first of today
        self.assertEqual(len(dropped), len(pts) - 5 - 2)

    def test_anchors_expire_and_protect_wins(self):
        old = self.NOW - timedelta(days=40)
        pts = [(1, old), (2, self.NOW), (3, self.NOW)]
        self.assertEqual(select_prunable(pts, self.NOW, keep=1), [1])
        self.assertEqual(select_prunable(pts, self.NOW, keep=1, protect={1}), [])


class TagChangeTests(unittest.TestCase):
    def _orm(self, row, pk=100):
        return SimpleNamespace(
            id=pk,
            minor_topics=[SimpleNamespace(id=i) for i in row['minor_topic_ids']],
            subtopics=[SimpleNamespace(id=i) for i in row['subtopic_ids']],
            **{f: row[f] for f in QUESTION_FIELDS},
        )

    def test_unchanged_question(self):
        s = _state()
        self.assertFalse(question_tags_differ(s, self._orm(s['questions'][100])))

    def test_link_order_does_not_matter(self):
        s = _state()
        s['questions'][100]['subtopic_ids'] = (10, 20)
        q = self._orm(s['questions'][100])
        q.subtopics = list(reversed(q.subtopics))
        self.assertFalse(question_tags_differ(s, q))

    def test_scalar_and_link_changes_detected(self):
        s = _state()
        q = self._orm(s['questions'][100])
        q.correct_percentage = 60
        self.assertTrue(question_tags_differ(s, q))
        q = self._orm(s['questions'][100])
        q.minor_topics = []
        self.assertTrue(question_tags_differ(s, q))

    def test_unknown_question_counts_as_changed(self):
        s = _state()
        self.assertTrue(question_tags_differ(s, self._orm(s['questions'][100], pk=555)))


if __name__ == '__main__':
    unittest.main()
