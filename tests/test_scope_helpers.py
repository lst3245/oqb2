"""Pure tests for the scope helpers used by the stage-1 route fixes."""
import unittest

from app.authz import path_in_subjects
from app.batch_edit import resolved_taxonomy_ids, taxonomy_violations
from app.files_policy import strip_out_of_scope
from app import ai_tools, pdf_import
from app import sso_schema


class TaxonomyViolationTests(unittest.TestCase):
    OWNERS = {'topic': {1: 'MATC', 2: 'ICT'}, 'subtopic': {10: 'MATC'},
              'chapter': {5: 'MATC'}, 'subchapter': {50: 'ICT'}}

    def test_same_subject_passes(self):
        resolved = {'major_topic_id': 1, 'major_subtopic_id': 10, 'chapter_id': 5}
        self.assertEqual(taxonomy_violations({'MATC'}, resolved, self.OWNERS), [])

    def test_foreign_topic_is_flagged(self):
        resolved = {'major_topic_id': 2}
        self.assertTrue(taxonomy_violations({'MATC'}, resolved, self.OWNERS))

    def test_foreign_subchapter_is_flagged(self):
        resolved = {'subchapter_id': 50}
        self.assertTrue(taxonomy_violations({'MATC'}, resolved, self.OWNERS))

    def test_mixed_subject_selection_with_taxonomy_is_flagged(self):
        resolved = {'major_topic_id': 1}
        self.assertTrue(taxonomy_violations({'MATC', 'ICT'}, resolved, self.OWNERS))

    def test_mixed_selection_without_taxonomy_passes(self):
        self.assertEqual(taxonomy_violations({'MATC', 'ICT'}, {'level': 2}, self.OWNERS), [])

    def test_resolved_ids_are_grouped_by_kind(self):
        ids = resolved_taxonomy_ids({'major_topic_id': 1, 'chapter_id': 5, 'level': 3})
        self.assertIn(1, ids.get('topic', set()))
        self.assertIn(5, ids.get('chapter', set()))


class StripOutOfScopeTests(unittest.TestCase):
    ID_SUBJECT = {1: 'MATC', 2: 'ICT', 3: 'ECON'}
    QID_SUBJECT = {'A': 'MATC', 'B': 'ICT'}

    def test_list_ids_are_filtered(self):
        out = strip_out_of_scope({'question_ids': [1, 2, 3, 99], 'x': 1}, {'MATC'}, self.ID_SUBJECT)
        self.assertEqual(out['question_ids'], [1])
        self.assertEqual(out['x'], 1)

    def test_string_ids_keep_shape(self):
        out = strip_out_of_scope({'ids': '1,2,3'}, {'MATC', 'ECON'}, self.ID_SUBJECT)
        self.assertEqual(out['ids'], '1,3')

    def test_qids_use_qid_map(self):
        out = strip_out_of_scope({'qids': ['A', 'B', 'C']}, {'ICT'}, {}, self.QID_SUBJECT)
        self.assertEqual(out['qids'], ['B'])

    def test_input_is_not_mutated(self):
        payload = {'question_ids': [1, 2]}
        strip_out_of_scope(payload, set(), self.ID_SUBJECT)
        self.assertEqual(payload['question_ids'], [1, 2])

    def test_non_dict_passes_through(self):
        self.assertEqual(strip_out_of_scope('raw', {'MATC'}, {}), 'raw')


class PathInSubjectsTests(unittest.TestCase):
    def test_first_segment_must_be_a_subject(self):
        self.assertTrue(path_in_subjects('MATC/2024/p1.pdf', ['MATC']))
        self.assertTrue(path_in_subjects('matc\\p1.pdf', ['MATC']))
        self.assertFalse(path_in_subjects('ICT/p1.pdf', ['MATC']))

    def test_root_and_traversal_are_refused(self):
        self.assertFalse(path_in_subjects('', ['MATC']))
        self.assertFalse(path_in_subjects('../MATC/x.pdf', ['MATC']))
        self.assertFalse(path_in_subjects('p1.pdf', ['MATC']))


class JobRegistryOwnerTests(unittest.TestCase):
    def test_owner_is_recorded_and_dropped(self):
        for mod in (pdf_import, ai_tools):
            job_id, _ev = mod.new_job(owner_id=42)
            self.assertEqual(mod.job_owner(job_id), (True, 42))
            mod.finish_job(job_id)
            self.assertEqual(mod.job_owner(job_id), (False, None))

    def test_job_outside_a_request_has_no_owner(self):
        job_id, _ev = pdf_import.new_job()
        try:
            self.assertEqual(pdf_import.job_owner(job_id), (True, None))
        finally:
            pdf_import.finish_job(job_id)


class SchemaSqlTests(unittest.TestCase):
    def test_missing_columns_are_case_insensitive(self):
        have = {n.upper() for n, _ in sso_schema.USER_COLUMNS[:3]}
        missing = sso_schema.missing_columns(have)
        self.assertEqual(len(missing), len(sso_schema.USER_COLUMNS) - 3)

    def test_alter_is_one_additive_statement(self):
        sql = sso_schema.alter_users_sql(sso_schema.USER_COLUMNS)
        self.assertTrue(sql.startswith('ALTER TABLE users ADD COLUMN '))
        self.assertEqual(sql.count('ALTER TABLE'), 1)
        for word in ('DROP', 'MODIFY', 'CHANGE', 'UPDATE'):
            self.assertNotIn(word, sql.upper())


if __name__ == '__main__':
    unittest.main()
