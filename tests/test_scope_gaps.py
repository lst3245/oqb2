"""Stage-1 scope fixes, exercised through the routes against oqb_sso_test."""
import json
import os
import shutil
import uuid

from dbcase import PASSWORD, DbTestCase

XHR = {'X-Requested-With': 'XMLHttpRequest'}


def _restore_points(subject_id):
    from app.models import SubjectRestorePoint
    return SubjectRestorePoint.query.filter_by(subject_id=subject_id).count()


class TaxonomyScopeTests(DbTestCase):
    def test_subject_admin_cannot_add_topic_in_other_subject(self):
        from app.models import Topic
        self.login('admin_matc')
        before = Topic.query.filter_by(subject_id='ICT').count()
        resp = self.client.post('/admin/topics/add', data={'subject_id': 'ICT', 'name': 'X'}, headers=XHR)
        self.assert_denied_json(resp)
        self.assertEqual(Topic.query.filter_by(subject_id='ICT').count(), before)
        self.assertEqual(_restore_points('ICT'), 0)

    def test_subject_admin_adds_topic_in_own_subject(self):
        self.login('admin_matc')
        resp = self.client.post('/admin/topics/add', data={'subject_id': 'MATC', 'name': 'New'}, headers=XHR)
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True)[:300])

    def test_add_subtopic_under_foreign_topic_denied(self):
        self.login('admin_matc')
        resp = self.client.post('/admin/subtopics/add',
                                data={'topic_id': self.topics['ICT'], 'name': 'X'}, headers=XHR)
        self.assert_denied_json(resp)

    def test_delete_foreign_chapter_denied(self):
        from app import db
        from app.models import Chapter
        self.login('admin_matc')
        resp = self.client.post(f"/admin/chapters/{self.chapters['ICT']}/delete", headers=XHR)
        self.assert_denied_json(resp)
        self.assertIsNotNone(db.session.get(Chapter, self.chapters['ICT']))

    def test_reorder_foreign_topics_denied(self):
        self.login('admin_matc')
        resp = self.api_post('/admin/topics/reorder', {'topic_ids': [self.topics['ICT']]})
        self.assert_denied_json(resp)
        self.assertEqual(_restore_points('ICT'), 0)

    def test_reorder_mixed_subjects_denied(self):
        self.login('admin_matc')
        resp = self.api_post('/admin/subchapters/reorder',
                             {'subchapter_ids': [self.subchapters['MATC'], self.subchapters['ECON']]})
        self.assert_denied_json(resp)

    def test_reorder_own_topics_allowed(self):
        self.login('admin_matc')
        resp = self.api_post('/admin/topics/reorder', {'topic_ids': [self.topics['MATC']]})
        self.assertEqual(resp.status_code, 200)

    def test_super_admin_reorders_anything(self):
        self.login('super')
        resp = self.api_post('/admin/chapters/reorder', {'chapter_ids': [self.chapters['ECON']]})
        self.assertEqual(resp.status_code, 200)


class BatchEditScopeTests(DbTestCase):
    def test_batch_update_outside_subject_denied(self):
        from app import db
        from app.models import Question
        self.login('admin_matc')
        resp = self.client.post('/admin/questions/batch-update', headers=XHR, data={
            'question_ids': [str(self.questions['ICT'])], 'update_level': '1', 'level': '2'})
        self.assertEqual(resp.status_code, 403)
        self.assertIsNone(db.session.get(Question, self.questions['ICT']).level)
        self.assertEqual(_restore_points('ICT'), 0)

    def test_batch_update_rejects_foreign_topic(self):
        from app import db
        from app.models import Question
        self.login('admin_matc')
        resp = self.client.post('/admin/questions/batch-update', headers=XHR, data={
            'question_ids': [str(self.questions['MATC'])], 'update_topics': '1',
            'major_topic_id': str(self.topics['ICT'])})
        self.assertEqual(resp.status_code, 400, resp.get_data(as_text=True)[:300])
        self.assertIsNone(db.session.get(Question, self.questions['MATC']).major_topic_id)
        self.assertEqual(_restore_points('MATC'), 0)

    def test_batch_update_own_subject_works(self):
        from app import db
        from app.models import Question
        self.login('admin_matc')
        resp = self.client.post('/admin/questions/batch-update', headers=XHR, data={
            'question_ids': [str(self.questions['MATC'])], 'update_topics': '1',
            'major_topic_id': str(self.topics['MATC'])})
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True)[:300])
        db.session.expire_all()
        self.assertEqual(db.session.get(Question, self.questions['MATC']).major_topic_id, self.topics['MATC'])


class DashboardScopeTests(DbTestCase):
    def test_subtopics_of_unreadable_topic_denied(self):
        self.login('viewer_ict')
        resp = self.client.get(f"/dashboard/api/subtopics?topic_ids={self.topics['MATC']}")
        self.assert_denied_json(resp)

    def test_subtopics_of_readable_topic(self):
        self.login('viewer_ict')
        resp = self.client.get(f"/dashboard/api/subtopics?topic_ids={self.topics['ICT']}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([s['id'] for s in resp.get_json()], [self.subtopics['ICT']])

    def test_subchapters_of_unreadable_chapter_denied(self):
        self.login('viewer_ict')
        resp = self.client.get(f"/dashboard/api/subchapters?chapter_ids={self.chapters['ECON']}")
        self.assert_denied_json(resp)

    def test_unknown_parent_ids_return_empty(self):
        self.login('viewer_ict')
        resp = self.client.get('/dashboard/api/subtopics?topic_ids=987654')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), [])

    def test_filtered_query_is_clamped_without_subject(self):
        from flask_login import login_user
        from app.dashboard import _build_filtered_query
        with self.app.test_request_context('/'):
            login_user(self.user('viewer_ict'))
            subjects = {q.subject for q in _build_filtered_query({}).all()}
        self.assertEqual(subjects, {'ICT'})

    def test_filtered_query_unclamped_for_super_admin(self):
        from flask_login import login_user
        from app.dashboard import _build_filtered_query
        with self.app.test_request_context('/'):
            login_user(self.user('super'))
            subjects = {q.subject for q in _build_filtered_query({}).all()}
        self.assertEqual(subjects, {'MATC', 'ICT', 'ECON'})


class GeneratorScopeTests(DbTestCase):
    def test_create_document_needs_user_level(self):
        self.login('teacher_a')  # MATC user, ICT viewer
        resp = self.client.post('/generate/create', headers=XHR, data={
            'question_ids': [str(self.questions['ICT'])], 'display_name': 'x'})
        self.assertEqual(resp.status_code, 403)

    def test_generate_page_drops_viewer_only_questions(self):
        self.login('teacher_a')
        resp = self.client.get(f"/generate/?question_ids={self.questions['ICT']}")
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/dashboard', resp.headers['Location'])


class FilesPolicyTests(DbTestCase):
    def _file(self, owner, question_ids):
        from app import db
        from app.models import GeneratedFile
        gf = GeneratedFile(user_id=self.users[owner], display_name='f', filename='f.docx',
                           status='completed',
                           generation_options=json.dumps({'question_ids': question_ids}))
        db.session.add(gf)
        db.session.commit()
        return gf

    def _share(self, gf, owner, recipient):
        from app import db
        from app.models import FileShare
        db.session.add(FileShare(file_id=gf.id, shared_by_user_id=self.users[owner],
                                 shared_with_user_id=self.users[recipient]))
        db.session.commit()

    def test_owner_and_super_admin_download(self):
        from app.files_policy import can_download
        gf = self._file('teacher_a', [self.questions['MATC']])
        self.assertTrue(can_download(gf, self.user('teacher_a')))
        self.assertTrue(can_download(gf, self.user('super')))

    def test_share_recipient_needs_every_subject(self):
        from app.files_policy import can_download, can_view
        gf = self._file('teacher_a', [self.questions['MATC'], self.questions['ICT']])
        self._share(gf, 'teacher_a', 'viewer_ict')
        self.assertTrue(can_view(gf, self.user('viewer_ict')))
        self.assertFalse(can_download(gf, self.user('viewer_ict')))

    def test_share_recipient_with_readable_subjects_downloads(self):
        from app.files_policy import can_download
        gf = self._file('teacher_a', [self.questions['ICT']])
        self._share(gf, 'teacher_a', 'viewer_ict')
        self.assertTrue(can_download(gf, self.user('viewer_ict')))

    def test_file_without_ids_is_owner_only(self):
        from app.files_policy import can_download
        gf = self._file('teacher_a', [])
        self._share(gf, 'teacher_a', 'viewer_ict')
        self.assertFalse(can_download(gf, self.user('viewer_ict')))

    def test_unshared_user_cannot_download(self):
        from app.files_policy import can_download
        gf = self._file('teacher_a', [self.questions['ICT']])
        self.assertFalse(can_download(gf, self.user('viewer_ict')))

    def test_generation_options_are_scrubbed_for_recipient(self):
        gf = self._file('teacher_a', [self.questions['MATC'], self.questions['ICT']])
        self._share(gf, 'teacher_a', 'viewer_ict')
        self.login('viewer_ict')
        resp = self.client.get(f'/user/files/{gf.id}/generation_options')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()['generation_options']['question_ids'], [self.questions['ICT']])


class ProfileAndSetScopeTests(DbTestCase):
    def test_viewer_cannot_save_profiles(self):
        self.login('viewer_ict')
        self.assert_denied_json(self.api_post('/user/profiles/save', {'name': 'p', 'filter_data': {'subject': 'ICT'}}))
        self.assert_denied_json(self.api_post('/user/gen-profiles/save', {'name': 'p', 'options_data': {'a': 1}}))

    def test_viewer_cannot_list_files(self):
        self.login('viewer_ict')
        self.assert_denied_json(self.client.get('/user/files/list?section_id=-1', headers=XHR))

    def test_shared_profile_needs_subject_access(self):
        from app import db
        from app.models import SavedFilter
        p = SavedFilter(user_id=self.users['admin_matc'], name='m', is_shared=True,
                        filter_data=json.dumps({'subject': 'MATC'}))
        db.session.add(p)
        db.session.commit()
        self.login('viewer_ict')
        self.assert_denied_json(self.client.get(f'/user/profiles/{p.id}/data',
                                                headers={'Sec-Fetch-Mode': 'cors'}))
        self.assertEqual(self.client.get(f'/user/profiles/{p.id}/data').status_code, 403)
        self.login('teacher_a')
        self.assertEqual(self.client.get(f'/user/profiles/{p.id}/data').status_code, 200)

    def test_set_save_needs_user_level(self):
        self.login('teacher_a')  # ICT viewer only
        resp = self.api_post('/user/sets/save', {'name': 's', 'subject': 'ICT',
                                                 'question_ids': [self.questions['ICT']]})
        self.assertEqual(resp.status_code, 403)
        resp = self.api_post('/user/sets/save', {'name': 's', 'subject': 'MATC',
                                                 'question_ids': [self.questions['MATC']]})
        self.assertEqual(resp.status_code, 200)


class OwnerBoundStagingTests(DbTestCase):
    def test_cancel_other_users_job_denied(self):
        from app import ai_tools, pdf_import
        for mod, url in ((pdf_import, '/admin/pdf-import/cancel'), (ai_tools, '/admin/questions/ai/cancel')):
            job_id, ev = mod.new_job(owner_id=self.users['super'])
            try:
                self.login('admin_matc')
                self.assert_denied_json(self.api_post(url, {'job_id': job_id}))
                self.assertFalse(ev.is_set())
                self.login('super')
                self.assertEqual(self.api_post(url, {'job_id': job_id}).status_code, 200)
                self.assertTrue(ev.is_set())
            finally:
                mod.finish_job(job_id)

    def test_cancel_own_job_allowed(self):
        from app import pdf_import
        job_id, ev = pdf_import.new_job(owner_id=self.users['admin_matc'])
        try:
            self.login('admin_matc')
            self.assertEqual(self.api_post('/admin/pdf-import/cancel', {'job_id': job_id}).status_code, 200)
            self.assertTrue(ev.is_set())
        finally:
            pdf_import.finish_job(job_id)

    def _toolbox_session(self, owner_id):
        root = os.path.join(self.app.config['SYSTEM_PATH'], '.toolbox')
        token = uuid.uuid4().hex
        path = os.path.join(root, token)
        os.makedirs(os.path.join(path, 'sources'), exist_ok=True)
        with open(os.path.join(path, 'session.json'), 'w', encoding='utf-8') as f:
            json.dump({'owner_user_id': owner_id, 'sources': {}, 'pages': []}, f)
        self.addCleanup(shutil.rmtree, path, True)
        return token

    def test_toolbox_session_is_owner_bound(self):
        mine = self._toolbox_session(self.users['admin_matc'])
        theirs = self._toolbox_session(self.users['super'])
        legacy = self._toolbox_session(None)
        self.login('admin_matc')
        self.assertEqual(self.api_post('/admin/toolbox/pdf/session-restore', {'token': mine}).status_code, 200)
        self.assert_denied_json(self.api_post('/admin/toolbox/pdf/session-restore', {'token': theirs}))
        self.assert_denied_json(self.api_post('/admin/toolbox/pdf/session-restore', {'token': legacy}))
        listed = {s['token'] for s in self.client.get('/admin/toolbox/pdf/sessions').get_json()['sessions']}
        self.assertIn(mine, listed)
        self.assertNotIn(theirs, listed)
        self.assertNotIn(legacy, listed)

    def test_toolbox_delete_skips_foreign_sessions(self):
        theirs = self._toolbox_session(self.users['super'])
        self.login('admin_matc')
        resp = self.api_post('/admin/toolbox/pdf/sessions/delete', {'tokens': [theirs]})
        self.assertEqual(resp.get_json()['deleted'], 0)
        self.assertTrue(os.path.isdir(os.path.join(self.app.config['SYSTEM_PATH'], '.toolbox', theirs)))

    def test_super_admin_sees_legacy_sessions(self):
        legacy = self._toolbox_session(None)
        self.login('super')
        self.assertEqual(self.api_post('/admin/toolbox/pdf/session-restore', {'token': legacy}).status_code, 200)

    def test_smart_import_upload_is_owner_bound(self):
        from app import smart_import
        token = smart_import.create_upload(owner_id=self.users['super'])
        try:
            with open(os.path.join(smart_import.upload_dir(token), 'x.png'), 'wb') as f:
                f.write(b'x')
            self.login('admin_matc')
            resp = self.client.get(f'/admin/import/uploaded-file?token={token}&path=x.png')
            self.assertEqual(resp.status_code, 404)
            self.login('super')
            resp = self.client.get(f'/admin/import/uploaded-file?token={token}&path=x.png')
            self.assertEqual(resp.status_code, 200)
            resp.close()
        finally:
            smart_import.discard_upload(token)

    def test_pdf_source_scope(self):
        from flask_login import login_user
        from app import authz
        with self.app.test_request_context('/'):
            login_user(self.user('admin_matc'))
            self.assertTrue(authz.pdf_source_allowed('MATC/2024/p1.pdf'))
            self.assertFalse(authz.pdf_source_allowed('ICT/p1.pdf'))
            self.assertFalse(authz.pdf_source_allowed('loose.pdf'))
        with self.app.test_request_context('/'):
            login_user(self.user('super'))
            self.assertTrue(authz.pdf_source_allowed('loose.pdf'))


class DecoratorTests(DbTestCase):
    def test_subject_decorators_fail_closed(self):
        from flask_login import login_user
        from werkzeug.exceptions import Forbidden
        from app.utils import subject_access_required, subject_admin_required

        @subject_access_required
        def view(**kw):
            return 'ok'

        @subject_admin_required
        def admin_view(**kw):
            return 'ok'

        with self.app.test_request_context('/'):
            login_user(self.user('teacher_a'))
            with self.assertRaises(Forbidden):
                view()
            self.assertEqual(view(subject_id='MATC'), 'ok')
            with self.assertRaises(Forbidden):
                admin_view(subject_id='MATC')
        with self.app.test_request_context('/'):
            login_user(self.user('super'))
            self.assertEqual(view(), 'ok')


class AccountStateTests(DbTestCase):
    def test_register_is_super_admin_only(self):
        self.login('admin_matc')
        self.assertEqual(self.client.get('/register').status_code, 403)

    def test_register_never_grants_admin(self):
        from app.models import User
        self.login('super')
        resp = self.client.post('/register', data={'username': 'newbie', 'password': 'x1', 'is_admin': 'on'})
        self.assertEqual(resp.status_code, 302)
        u = User.query.filter_by(username='newbie').one()
        self.assertFalse(u.is_admin)
        self.assertFalse(u.is_super_admin)

    def test_password_login_records_method(self):
        from app import db
        resp = self.client.post('/login', data={'username': 'teacher_a', 'password': PASSWORD})
        self.assertEqual(resp.status_code, 302)
        db.session.expire_all()
        u = self.user('teacher_a')
        self.assertEqual(u.last_login_method, 'password')
        self.assertIsNotNone(u.last_login_at)

    def test_disabled_account_cannot_log_in(self):
        from app import db
        u = self.user('teacher_a')
        u.active = False
        db.session.commit()
        resp = self.client.post('/login', data={'username': 'teacher_a', 'password': PASSWORD})
        self.assertEqual(resp.status_code, 200)
        with self.client.session_transaction() as sess:
            self.assertNotIn('_user_id', sess)

    def test_disabling_ends_existing_session(self):
        from app import db
        self.login('teacher_a')
        self.assertEqual(self.client.get('/user/sets').status_code, 200)
        self.user('teacher_a').active = False
        db.session.commit()
        self.assertEqual(self.client.get('/user/sets').status_code, 302)

    def test_session_version_bump_logs_out(self):
        from app import db
        self.login('teacher_a')
        self.assertEqual(self.client.get('/user/sets').status_code, 200)
        self.user('teacher_a').bump_session_version()
        db.session.commit()
        self.assertEqual(self.client.get('/user/sets').status_code, 302)

    def test_legacy_plain_id_cookie_is_rejected(self):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.users['teacher_a'])
            sess['_fresh'] = True
        self.assertEqual(self.client.get('/user/sets').status_code, 302)

    def test_admin_password_change_bumps_version(self):
        from app import db
        self.login('super')
        before = self.user('teacher_a').session_version
        resp = self.client.post(f"/admin/users/{self.users['teacher_a']}/edit",
                                data={'username': 'teacher_a', 'password': 'new-pw'}, headers=XHR)
        self.assertEqual(resp.status_code, 200)
        db.session.expire_all()
        self.assertEqual(self.user('teacher_a').session_version, before + 1)

    def test_own_password_change_keeps_current_session(self):
        self.login('super')
        resp = self.client.post(f"/admin/users/{self.users['super']}/edit",
                                data={'password': 'new-pw'}, headers=XHR)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.client.get('/user/sets').status_code, 200)

    def test_unusable_password_never_matches(self):
        u = self.user('nogrants')
        u.password_hash = '!sms-only'
        self.assertFalse(u.check_password(''))
        self.assertFalse(u.check_password('!sms-only'))

    def test_schema_ready_flag(self):
        self.assertTrue(self.app.config.get('SMS_SCHEMA_READY'))
