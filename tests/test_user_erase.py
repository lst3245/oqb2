"""Admin -> Users -> Delete is a full erase (app/user_erase.py) on oqb_sso_test:
rows, references, files on disk, guards, rollback, path confinement, the
preview endpoint and JSON-only errors. Runs only under tools/run_tests.py."""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from dbcase import DbTestCase, _password_hash

ISSUER = 'https://sms.erase.test'


def _touch(path, data=b'x'):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as fh:
        fh.write(data)
    return path


class EraseCase(DbTestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix='oqb_erase_')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.user_root = os.path.join(self.tmp, 'Storage', 'User')
        self.legacy_root = os.path.join(self.tmp, 'output')
        os.makedirs(self.user_root)
        os.makedirs(self.legacy_root)
        self.app.config.update(USER_PATH=self.user_root, OUTPUT_PATH=self.legacy_root)
        self.login('super')

    # ------------------------------------------------------------ helpers
    def fresh(self):
        from app import db
        db.session.rollback()

    def post_delete(self, uid):
        self.fresh()
        resp = self.client.post(f'/admin/users/{uid}/delete')
        self.fresh()
        return resp

    def get_preview(self, uid):
        self.fresh()
        resp = self.client.get(f'/admin/users/{uid}/delete-preview')
        self.fresh()
        return resp

    def home(self, name):
        return os.path.join(self.user_root, name)

    def add(self, *rows):
        from app import db
        db.session.add_all(rows)
        db.session.flush()
        return rows[0] if len(rows) == 1 else rows

    def snapshot(self):
        """Row count of every table + every user reference column per value."""
        from sqlalchemy import func, select
        from app import db, user_erase
        self.fresh()
        out = {t.name: db.session.execute(select(func.count()).select_from(t)).scalar()
               for t in db.metadata.sorted_tables}
        for table, col in user_erase.NULL_REFS + user_erase.LATE_NULL_REFS:
            rows = db.session.execute(select(table.c[col], func.count()).group_by(table.c[col])).all()
            out[f'{table.name}.{col}'] = sorted(rows, key=lambda r: (r[0] is None, r[0] or 0))
        return out

    def seed_victim(self, name='teacher_a', other='admin_matc'):
        """Everything a user can own or be named in, plus another user's data
        that must survive. Returns a dict of ids / paths."""
        from app import db
        from app.models import (AuthAuditLog, ExternalIdentity, FileSection, FileShare, GeneratedFile,
                                PromptOverride, PromptVariant, Question, SavedFilter,
                                SavedGenerationProfile, SavedQuestionSet, SmsSnapshot, SmsSubjectMap,
                                SmsTrustIssuer, SmsTrustKey, SubjectPromptNote, SubjectRestorePoint,
                                SystemSetting, TagCorrection, UserSmsLevel)
        uid, oid, nid = self.users[name], self.users[other], self.users['nogrants']
        s_latest = self.add(FileSection(user_id=uid, name='Latest', is_default=True))
        s_extra = self.add(FileSection(user_id=uid, name='Exams'))
        s_other = self.add(FileSection(user_id=oid, name='Latest', is_default=True))
        gen = os.path.join(self.home(name), 'generated')
        f_docx = self.add(GeneratedFile(user_id=uid, display_name='P1', filename='paper1.docx',
                                        status='completed', section_id=s_latest.id))
        f_zip = self.add(GeneratedFile(user_id=uid, display_name='B', filename='bundle.zip',
                                       status='failed', section_id=s_extra.id))
        f_legacy = self.add(GeneratedFile(user_id=uid, display_name='L', filename='legacy.docx',
                                          status='completed', section_id=s_latest.id))
        f_other = self.add(GeneratedFile(user_id=oid, display_name='O', filename='other.docx',
                                         status='completed', section_id=s_other.id))
        # another user's file sitting in the victim's section (SET NULL, not deleted)
        f_stray = self.add(GeneratedFile(user_id=oid, display_name='S', filename='stray.docx',
                                         status='completed', section_id=s_extra.id))
        paths = {
            'docx': _touch(os.path.join(gen, 'paper1.docx')),
            'docx_pdf': _touch(os.path.join(gen, 'paper1.pdf')),
            'zip': _touch(os.path.join(gen, 'bundle.zip')),
            'zip_pdf': _touch(os.path.join(gen, 'bundle.pdf.zip')),
            'legacy': _touch(os.path.join(self.legacy_root, 'legacy.docx')),
            'legacy_pdf': _touch(os.path.join(self.legacy_root, 'legacy.pdf')),
            'notes': _touch(os.path.join(self.home(name), 'notes', 'n.txt')),
        }
        keep = {
            'other_legacy': _touch(os.path.join(self.legacy_root, 'other.docx')),
            'other_home': _touch(os.path.join(self.home(other), 'generated', 'keep.docx')),
        }
        self.add(
            SavedFilter(user_id=uid, name='f1', filter_data='{}'),
            SavedFilter(user_id=uid, name='f2', filter_data='{}', is_shared=True),
            SavedFilter(user_id=oid, name='of', filter_data='{}'),
            SavedGenerationProfile(user_id=uid, name='g1', options_data='{}', is_shared=True),
            SavedQuestionSet(user_id=uid, name='q1', subject='MATC', question_ids='[]'),
            SavedQuestionSet(user_id=uid, name='q2', subject='MATC', question_ids='[]', is_shared=True),
            FileShare(file_id=f_docx.id, shared_by_user_id=uid, shared_with_user_id=oid),
            FileShare(file_id=f_other.id, shared_by_user_id=oid, shared_with_user_id=uid),
            FileShare(section_id=s_extra.id, shared_by_user_id=uid, shared_with_user_id=nid),
            FileShare(file_id=f_stray.id, shared_by_user_id=oid, shared_with_user_id=nid),
            TagCorrection(question_id=self.questions['MATC'], subject_id='MATC', field='level', user_id=uid),
            SystemSetting(key='ERASE_TEST', value='1', updated_by=uid),
            PromptOverride(key='erase_test', content='x', updated_by=uid),
            SubjectPromptNote(subject_id='MATC', feature='tag', updated_by=uid),
            PromptVariant(prompt_key='erase_test', name='v', updated_by=uid),
            SubjectRestorePoint(subject_id='MATC', user_id=uid, action='manual', payload='{}'),
            ExternalIdentity(user_id=uid, issuer=ISSUER, sub='sub-victim', link_method='admin',
                             linked_by_user_id=self.users['super']),
            ExternalIdentity(user_id=oid, issuer=ISSUER, sub='sub-other', link_method='admin',
                             linked_by_user_id=uid),
            UserSmsLevel(user_id=uid, issuer=ISSUER, sms_subject_uuid='', level='viewer', source='handoff'),
            SmsSnapshot(issuer=ISSUER, snapshot_id='s1', jti='j1', iat=1, status='applied',
                        decided_by_user_id=uid),
            SmsSubjectMap(sms_uuid='u-math', oqb_subject_id='MATC', confirmed_by_user_id=uid),
        )
        issuer = self.add(SmsTrustIssuer(issuer=ISSUER, updated_by_user_id=uid))
        self.add(SmsTrustKey(issuer_id=issuer.id, kid='k1', public_pem='-', fingerprint='f' * 64,
                             pinned_by_user_id=uid))
        q = db.session.get(Question, self.questions['MATC'])
        q.verified_by = uid
        self.add(AuthAuditLog(event='password_login', outcome='ok', user_id=uid),
                 AuthAuditLog(event='account_state', outcome='ok', user_id=uid, actor_user_id=uid))
        db.session.commit()
        return {'uid': uid, 'oid': oid, 'paths': paths, 'keep': keep,
                'file_ids': [f_docx.id, f_zip.id, f_legacy.id], 'other_file_ids': [f_other.id, f_stray.id],
                'section_ids': [s_latest.id, s_extra.id], 'stray_id': f_stray.id, 'other_section': s_other.id}

    def assert_no_orphans(self, s):
        from sqlalchemy import func, or_, select
        from app import db, user_erase
        from app.models import FileShare, User
        self.fresh()
        uid = s['uid']
        self.assertIsNone(db.session.get(User, uid))
        for table, col in user_erase.NULL_REFS + user_erase.LATE_NULL_REFS:
            n = db.session.execute(select(func.count()).select_from(table).where(table.c[col] == uid)).scalar()
            self.assertEqual(n, 0, f'{table.name}.{col}')
        for _key, table in user_erase.OWNED + user_erase.LINKS:
            n = db.session.execute(select(func.count()).select_from(table).where(table.c.user_id == uid)).scalar()
            self.assertEqual(n, 0, table.name)
        for name in ('generated_files', 'file_sections'):
            table = db.metadata.tables[name]
            n = db.session.execute(select(func.count()).select_from(table).where(table.c.user_id == uid)).scalar()
            self.assertEqual(n, 0, name)
        sh = FileShare.__table__
        n = db.session.execute(select(func.count()).select_from(sh).where(or_(
            sh.c.shared_by_user_id == uid, sh.c.shared_with_user_id == uid,
            sh.c.file_id.in_(s['file_ids']), sh.c.section_id.in_(s['section_ids'])))).scalar()
        self.assertEqual(n, 0, 'file_shares')


# ---------------------------------------------------------------- (a) full erase

class FullEraseTests(EraseCase):
    def test_erase_removes_everything_and_keeps_the_rest(self):
        from app import db
        from app.models import (AuthAuditLog, ExternalIdentity, FileShare, GeneratedFile, Question,
                                SavedFilter, SubjectRestorePoint, User)
        s = self.seed_victim()
        resp = self.post_delete(s['uid'])
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True)[:400])
        body = resp.get_json()
        self.assertIs(body['success'], True)
        self.assertEqual(body['files_failed'], 0)
        self.assertEqual(body['files_refused'], 0)
        self.assertNotIn('message', body)
        self.assertEqual(body['files_deleted'], 7)
        self.assert_no_orphans(s)

        c = body['counts']
        self.assertEqual((c['generated_files'], c['file_sections'], c['saved_filters'],
                          c['saved_filters_shared'], c['saved_generation_profiles'],
                          c['saved_question_sets'], c['saved_question_sets_shared'], c['file_shares'],
                          c['subject_permissions'], c['sms_identities'], c['sms_levels']),
                         (3, 2, 2, 1, 1, 2, 1, 3, 2, 1, 1))
        for key in ('tag_corrections.user_id', 'system_settings.updated_by', 'prompt_overrides.updated_by',
                    'subject_prompt_notes.updated_by', 'prompt_variants.updated_by', 'questions.verified_by',
                    'subject_restore_points.user_id', 'external_identities.linked_by_user_id',
                    'sms_snapshots.decided_by_user_id', 'sms_subject_map.confirmed_by_user_id',
                    'sms_trust_issuers.updated_by_user_id', 'sms_trust_keys.pinned_by_user_id'):
            self.assertEqual(body['nulled'][key], 1, key)

        # files and the user's folder are gone; other users' files stay
        for path in s['paths'].values():
            self.assertFalse(os.path.exists(path), path)
        self.assertFalse(os.path.exists(self.home('teacher_a')))
        for path in s['keep'].values():
            self.assertTrue(os.path.exists(path), path)
        self.assertTrue(os.path.isdir(self.user_root))

        # other users' rows survive; references are NULL, not deleted
        self.assertEqual(sorted(r.id for r in GeneratedFile.query.filter(
            GeneratedFile.id.in_(s['other_file_ids']))), sorted(s['other_file_ids']))
        self.assertIsNone(db.session.get(GeneratedFile, s['stray_id']).section_id)
        self.assertEqual(SavedFilter.query.filter_by(user_id=s['oid']).count(), 1)
        self.assertEqual(FileShare.query.filter_by(file_id=s['stray_id']).count(), 1)
        other_link = ExternalIdentity.query.filter_by(user_id=s['oid']).one()
        self.assertIsNone(other_link.linked_by_user_id)
        self.assertEqual(SubjectRestorePoint.query.count(), 1)
        self.assertIsNone(db.session.get(Question, self.questions['MATC']).verified_by)
        self.assertIsNotNone(db.session.get(User, s['oid']))

        # audit: old rows kept, one user_deleted row with counts, no secrets
        rows = AuthAuditLog.query.filter_by(user_id=s['uid']).order_by(AuthAuditLog.id).all()
        self.assertEqual([r.event for r in rows], ['password_login', 'account_state', 'user_deleted'])
        row = rows[-1]
        self.assertEqual((row.outcome, row.actor_user_id), ('ok', self.users['super']))
        detail = json.loads(row.detail)
        self.assertEqual(detail['username'], 'teacher_a')
        self.assertEqual(detail['deleted'], c)
        self.assertEqual(detail['nulled']['questions.verified_by'], 1)
        self.assertNotIn('password', row.detail)

    def test_blank_user_erases(self):
        resp = self.post_delete(self.users['nogrants'])
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()['files_deleted'], 0)
        self.assert_no_orphans({'uid': self.users['nogrants'], 'file_ids': [], 'section_ids': []})

    def test_erased_sms_user_session_ends(self):
        from dbcase import sign_in
        s = self.seed_victim()
        victim = self.app.test_client()
        sign_in(victim, self.user('teacher_a'))
        self.assertEqual(victim.get('/user/files').status_code, 200)
        self.assertEqual(self.post_delete(s['uid']).status_code, 200)
        self.assertIn('/login', victim.get('/user/files').headers.get('Location', ''))


# ---------------------------------------------------------------- (b) guards

class GuardTests(EraseCase):
    def test_cannot_erase_yourself(self):
        before = self.snapshot()
        resp = self.post_delete(self.users['super'])
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()['reason'], 'self')
        self.assertEqual(self.snapshot(), before)

    def test_last_active_super_admin(self):
        from app import db, user_erase
        u = self.user('breakglass')
        u.active = False
        db.session.commit()
        before = self.snapshot()
        with self.assertRaises(user_erase.EraseRefused) as ctx:
            user_erase.erase_user(self.user('super'), None)
        self.assertEqual((ctx.exception.reason, ctx.exception.status), ('last_super_admin', 409))
        self.assertEqual(self.snapshot(), before)
        # with another active super admin it would proceed: preview is not blocked
        u = self.user('breakglass')
        u.active = True
        db.session.commit()
        self.assertIsNone(user_erase.preview(self.user('super'), None)['refusal'])

    def test_running_job_refuses(self):
        from app import db
        from app.models import GeneratedFile
        s = self.seed_victim()
        for status in ('pending', 'generating'):
            with self.subTest(status=status):
                gf = GeneratedFile(user_id=s['uid'], display_name='R', filename=f'run_{status}.docx',
                                   status=status)
                db.session.add(gf)
                db.session.commit()
                before = self.snapshot()
                resp = self.post_delete(s['uid'])
                self.assertEqual(resp.status_code, 409)
                body = resp.get_json()
                self.assertEqual(body['reason'], 'job_running')
                self.assertIn('still generating', body['error'])
                self.assertEqual(self.snapshot(), before)
                for path in s['paths'].values():
                    self.assertTrue(os.path.exists(path), path)
                db.session.delete(db.session.get(GeneratedFile, gf.id))
                db.session.commit()

    def test_not_found_is_json(self):
        resp = self.post_delete(999999)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.mimetype, 'application/json')
        self.assertTrue(resp.get_json()['error'])

    def test_super_admin_only(self):
        from dbcase import sign_in
        client = self.app.test_client()
        sign_in(client, self.user('admin_matc'))
        uid = self.users['nogrants']
        self.assertEqual(client.post(f'/admin/users/{uid}/delete', headers={'Accept': 'application/json'}).status_code, 403)
        self.assertEqual(client.get(f'/admin/users/{uid}/delete-preview', headers={'Accept': 'application/json'}).status_code, 403)
        self.assertIsNotNone(self.user('nogrants'))

    def test_csrf_required(self):
        self.client.auto_csrf = False
        try:
            resp = self.client.post(f'/admin/users/{self.users["nogrants"]}/delete')
        finally:
            self.client.auto_csrf = True
        self.assertEqual(resp.status_code, 400)
        self.fresh()
        self.assertIsNotNone(self.user('nogrants'))


# ---------------------------------------------------------------- (c) rollback

class RollbackTests(EraseCase):
    def _fail_on(self, predicate):
        from app import user_erase
        real = user_erase._exec

        def wrapper(stmt):
            if predicate(stmt):
                raise RuntimeError('injected failure')
            return real(stmt)
        return mock.patch.object(user_erase, '_exec', side_effect=wrapper)

    def _assert_untouched(self, s, before, resp):
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.mimetype, 'application/json')
        self.assertIn('Could not delete the user', resp.get_json()['error'])
        self.assertEqual(self.snapshot(), before)
        for path in s['paths'].values():
            self.assertTrue(os.path.exists(path), path)
        for path in s['keep'].values():
            self.assertTrue(os.path.exists(path), path)

    def test_failure_after_generated_files_deleted(self):
        s = self.seed_victim()
        before = self.snapshot()
        with self._fail_on(lambda st: st.is_delete and st.table.name == 'file_sections'):
            resp = self.post_delete(s['uid'])
        self._assert_untouched(s, before, resp)

    def test_failure_at_the_user_row(self):
        s = self.seed_victim()
        before = self.snapshot()
        with self._fail_on(lambda st: st.is_delete and st.table.name == 'users'):
            resp = self.post_delete(s['uid'])
        self._assert_untouched(s, before, resp)

    def test_failure_in_the_audit_row(self):
        from app import auth_audit
        s = self.seed_victim()
        before = self.snapshot()
        with mock.patch.object(auth_audit, 'record', side_effect=RuntimeError('audit down')):
            resp = self.post_delete(s['uid'])
        self._assert_untouched(s, before, resp)
        # and it works afterwards
        self.assertEqual(self.post_delete(s['uid']).status_code, 200)
        self.assert_no_orphans(s)

    def test_file_delete_failure_after_commit_is_not_an_error(self):
        s = self.seed_victim()
        real_remove = os.remove
        target = os.path.normcase(s['paths']['legacy'])

        def flaky(path, *a, **kw):
            if os.path.normcase(str(path)) == target:
                raise PermissionError('locked')
            return real_remove(path, *a, **kw)
        with mock.patch('app.user_erase.os.remove', side_effect=flaky), \
                self.assertLogs(self.app.logger, 'WARNING') as logs:
            resp = self.post_delete(s['uid'])
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body['files_failed'], 1)
        self.assertIn('could not be removed', body['message'])
        self.assertTrue(any('legacy.docx' in line for line in logs.output))
        self.assertTrue(os.path.exists(s['paths']['legacy']))
        self.assert_no_orphans(s)


# ---------------------------------------------------------------- (d) paths

class PathConfinementTests(EraseCase):
    def _user(self, username):
        from app import db
        from app.models import User
        u = User(username=username, password_hash=_password_hash())
        db.session.add(u)
        db.session.commit()
        self.users[username] = u.id
        return u.id

    def test_filename_escaping_the_root_is_not_deleted(self):
        from app import db
        from app.models import GeneratedFile
        uid = self.users['teacher_a']
        outside = _touch(os.path.join(self.tmp, 'outside.docx'))
        sibling = _touch(os.path.join(self.user_root, 'admin_matc', 'generated', 'victim.docx'))
        _touch(os.path.join(self.home('teacher_a'), 'generated', 'ok.docx'))
        db.session.add_all([
            GeneratedFile(user_id=uid, display_name='a', filename='../../../outside.docx', status='completed'),
            GeneratedFile(user_id=uid, display_name='b', filename='..\\..\\admin_matc\\generated\\victim.docx',
                          status='completed'),
            GeneratedFile(user_id=uid, display_name='c', filename=os.path.abspath(outside), status='completed'),
            GeneratedFile(user_id=uid, display_name='d', filename='ok.docx', status='completed'),
        ])
        db.session.commit()
        resp = self.post_delete(uid)
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body['files_refused'], 3)
        self.assertTrue(os.path.exists(outside))
        self.assertTrue(os.path.exists(sibling))
        self.assertFalse(os.path.exists(self.home('teacher_a')))
        self.assertIn('could not be removed', body['message'])

    def test_dotdot_username_never_reaches_the_parent(self):
        uid = self._user('..')
        sentinel = _touch(os.path.join(self.tmp, 'Storage', 'keep.txt'))
        other = _touch(os.path.join(self.home('teacher_a'), 'keep.txt'))
        fallback = _touch(os.path.join(self.home(f'user_{uid}'), 'mine.txt'))
        resp = self.post_delete(uid)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(os.path.exists(sentinel))
        self.assertTrue(os.path.exists(other))
        self.assertTrue(os.path.isdir(self.user_root))
        self.assertFalse(os.path.exists(fallback))

    def test_folder_shared_by_two_accounts_is_kept(self):
        self._user('Mr_Chan')
        uid = self._user('Mr Chan')
        mine = _touch(os.path.join(self.home('Mr_Chan'), 'x.txt'))
        resp = self.post_delete(uid)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()['files_refused'], 1)
        self.assertTrue(os.path.exists(mine))

    def test_linked_home_is_not_followed(self):
        outside_dir = os.path.join(self.tmp, 'elsewhere')
        keep = _touch(os.path.join(outside_dir, 'precious.txt'))
        link = self.home('teacher_a')
        try:
            if os.name == 'nt':
                import _winapi
                _winapi.CreateJunction(outside_dir, link)
            else:
                os.symlink(outside_dir, link, target_is_directory=True)
        except (OSError, AttributeError) as exc:
            self.skipTest(f'cannot create a link here: {exc}')
        resp = self.post_delete(self.users['teacher_a'])
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()['files_refused'], 1)
        self.assertTrue(os.path.exists(keep))

    def test_legacy_file_named_by_another_user_is_kept(self):
        from app import db
        from app.models import GeneratedFile
        legacy = _touch(os.path.join(self.legacy_root, 'common.docx'))
        db.session.add_all([
            GeneratedFile(user_id=self.users['teacher_a'], display_name='a', filename='common.docx', status='completed'),
            GeneratedFile(user_id=self.users['admin_matc'], display_name='b', filename='common.docx', status='completed'),
        ])
        db.session.commit()
        resp = self.post_delete(self.users['teacher_a'])
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()['files_refused'], 1)
        self.assertTrue(os.path.exists(legacy))


# ---------------------------------------------------------------- (e) preview

class PreviewTests(EraseCase):
    def test_counts(self):
        s = self.seed_victim()
        before = self.snapshot()
        resp = self.get_preview(s['uid'])
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(self.snapshot(), before)
        c = body['counts']
        self.assertEqual((c['generated_files'], c['file_sections'], c['saved_filters'],
                          c['saved_generation_profiles'], c['saved_generation_profiles_shared'],
                          c['saved_question_sets'], c['file_shares'], c['subject_permissions']),
                         (3, 2, 2, 1, 1, 2, 3, 2))
        self.assertIs(body['sms_linked'], True)
        self.assertEqual(body['home_files'], 5)  # 4 generated + notes/n.txt
        self.assertEqual(body['in_progress'], 0)
        self.assertIsNone(body['blocked'])
        self.assertEqual(body['user'], {'id': s['uid'], 'username': 'teacher_a', 'active': True,
                                        'is_super_admin': False})
        self.assertNotIn('refusal', body)

    def test_blank_user_and_blocked_reasons(self):
        from app import db
        from app.models import GeneratedFile
        body = self.get_preview(self.users['nogrants']).get_json()
        self.assertEqual(body['counts']['generated_files'], 0)
        self.assertIs(body['sms_linked'], False)
        self.assertEqual(body['home_files'], 0)
        self.assertIn('own account', self.get_preview(self.users['super']).get_json()['blocked'])
        db.session.add(GeneratedFile(user_id=self.users['nogrants'], display_name='x', filename='x.docx',
                                     status='generating'))
        db.session.commit()
        body = self.get_preview(self.users['nogrants']).get_json()
        self.assertEqual(body['in_progress'], 1)
        self.assertIn('still generating', body['blocked'])

    def test_not_found_is_json(self):
        resp = self.get_preview(999999)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.mimetype, 'application/json')


# ---------------------------------------------------------------- (f) JSON errors + page

class JsonAndPageTests(EraseCase):
    def test_unexpected_error_is_json_500(self):
        from app import user_erase
        with mock.patch.object(user_erase, 'erase_user', side_effect=RuntimeError('boom')):
            resp = self.post_delete(self.users['nogrants'])
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.mimetype, 'application/json')
        self.assertNotIn('<html', resp.get_data(as_text=True).lower())
        with mock.patch.object(user_erase, 'preview', side_effect=RuntimeError('boom')):
            resp = self.get_preview(self.users['nogrants'])
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.mimetype, 'application/json')

    def test_refusals_are_json(self):
        resp = self.post_delete(self.users['super'])
        self.assertEqual(resp.mimetype, 'application/json')
        self.assertIn('error', resp.get_json())

    def test_users_page_has_disable_next_to_delete_and_the_erase_dialog(self):
        from app import db
        self.fresh()
        html = self.client.get('/admin/users').get_data(as_text=True)
        self.assertIn('id="eraseUserModal"', html)
        card = html.split(f'id="user-{self.users["teacher_a"]}"', 1)[1].split('class="card-body"', 1)[0]
        self.assertIn('toggle-active-btn', card)
        self.assertIn("setUserActive(", card)
        self.assertLess(card.index('toggle-active-btn'), card.index('deleteUser('))
        u = self.user('teacher_a')
        u.active = False
        db.session.commit()
        self.fresh()
        html = self.client.get('/admin/users').get_data(as_text=True)
        card = html.split(f'id="user-{self.users["teacher_a"]}"', 1)[1].split('class="card-body"', 1)[0]
        self.assertIn('setUserActive({}, true)'.format(self.users['teacher_a']), card)

    def test_template_strings_are_wrapped(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            'templates', 'admin_users.html')
        with open(path, encoding='utf-8') as fh:
            src = fh.read()
        self.assertIn("{{ t('Erase user permanently') }}", src)
        self.assertIn("{{ t('Accounts linked to SMS are recreated, empty, at their next SMS sign-in. "
                      "For staff who leave, use Disable: it blocks sign-in and keeps their files.') }}", src)


if __name__ == '__main__':
    unittest.main()
