"""Admin → Users table: search, filters, paging, access summary, the
permissions panel, and the toolbox save-to-server erase guard.
Runs only under tools/run_tests.py (oqb_sso_test)."""
import time
import unittest

from dbcase import DbTestCase, _password_hash, sign_in


def _row(html, user_id):
    marker = f'id="user-{user_id}"'
    return html.split(marker, 1)[1].split('</tr>', 1)[0]


class SummaryFormatTests(unittest.TestCase):
    def test_compact_shapes(self):
        from app.users_directory import summarize
        subjects = ['ICT', 'MAT1', 'MAT2', 'MATC', 'S1', 'S2', 'S3', 'S4', 'S5']
        levels = {
            'ICT': 'admin', 'MAT1': 'user', 'MAT2': 'user', 'MATC': 'user',
            'S1': 'viewer', 'S2': 'viewer', 'S3': 'viewer', 'S4': 'viewer', 'S5': 'viewer',
        }
        self.assertEqual(
            summarize(levels, subjects, super_admin=False, active=True),
            'Admin: ICT | User: MAT1, MAT2, MATC | Viewer: 5 others')
        self.assertEqual(
            summarize({sid: 'viewer' for sid in ('ECON', 'ICT')}, ['ECON', 'ICT'],
                      super_admin=False, active=True),
            'Viewer: all subjects')
        self.assertEqual(summarize({}, [], super_admin=True, active=True), 'Super admin: all')
        self.assertEqual(summarize({}, [], super_admin=True, active=False), 'No access')
        self.assertEqual(summarize({}, ['ICT'], super_admin=False, active=True), 'No access')


class UsersTableTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.login('super')

    def _row(self, html, name):
        self.assertIn(f'id="user-{self.users[name]}"', html, name)
        return _row(html, self.users[name])

    def _get(self, url):
        self.fresh()
        return self.client.get(url)

    def fresh(self):
        from app import db
        db.session.rollback()

    def test_search_each_field_case_insensitive(self):
        from app import db
        self.user('viewer_ict').display_name = 'Qxdisplay'
        self.user('admin_matc').name_zh = '權限表專用'
        self.user('nogrants').email = 'qxmail@school.test'
        self.user('teacher_a').staff_code = 'QXSTAFF'
        db.session.commit()
        cases = (
            ('qxdisplay', 'viewer_ict'),
            ('QXDISPLAY', 'viewer_ict'),
            ('權限表專用', 'admin_matc'),
            ('QXMAIL@school.test', 'nogrants'),
            ('qxstaff', 'teacher_a'),
            ('viewer_ict', 'viewer_ict'),
        )
        for q, name in cases:
            html = self._get('/admin/users?q=' + q).get_data(as_text=True)
            self.assertIn(f'id="user-{self.users[name]}"', html, q)
            self.assertEqual(html.count('class="user-row"'), 1, q)
        empty = self._get('/admin/users?q=no-such-user-zz').get_data(as_text=True)
        self.assertIn('0-0 of 0', empty)
        self.assertIn('No users match these filters.', empty)
        self.assertEqual(empty.count('class="user-row"'), 0)

    def test_filters_status_source_super_and_subject(self):
        from datetime import datetime
        from app import db
        from app.models import UNUSABLE_PASSWORD_PREFIX, ExternalIdentity, User
        from app.sms_sso_service import METHOD_ADMIN, METHOD_JIT  # not a level
        self.user('viewer_ict').active = False
        jit = User(username='jituser', password_hash=UNUSABLE_PASSWORD_PREFIX + 'sms-no-password',
                   is_super_admin=False, is_admin=False)
        db.session.add(jit)
        db.session.flush()
        issuer = 'https://sms.users-table.test'
        db.session.add(ExternalIdentity(
            user_id=self.users['teacher_a'], issuer=issuer, sub='11111111-1111-4111-8111-111111111111',
            link_method=METHOD_ADMIN, linked_at=datetime.utcnow()))
        db.session.add(ExternalIdentity(
            user_id=jit.id, issuer=issuer, sub='22222222-2222-4222-8222-222222222222',
            link_method=METHOD_JIT, linked_at=datetime.utcnow()))
        db.session.commit()
        self.users['jituser'] = jit.id

        html = self._get('/admin/users').get_data(as_text=True)
        self.assertNotIn(f'id="user-{self.users["viewer_ict"]}"', html)
        self.assertIn('Show disabled', html)
        self.assertRegex(html, r'id="show-disabled"[^>]*href="[^"]*status=all')
        disabled = self._get('/admin/users?status=disabled').get_data(as_text=True)
        self.assertIn(f'id="user-{self.users["viewer_ict"]}"', disabled)
        self.assertNotIn(f'id="user-{self.users["teacher_a"]}"', disabled)
        both = self._get('/admin/users?status=all').get_data(as_text=True)
        self.assertIn(f'id="user-{self.users["viewer_ict"]}"', both)
        self.assertIn(f'id="user-{self.users["teacher_a"]}"', both)
        self.assertIn('Hide disabled', both)

        local = self._get('/admin/users?source=local&status=all').get_data(as_text=True)
        self.assertIn(f'id="user-{self.users["nogrants"]}"', local)
        self.assertNotIn(f'id="user-{self.users["teacher_a"]}"', local)
        self.assertNotIn('id="user-%s"' % jit.id, local)
        sms = self._get('/admin/users?source=sms&status=all').get_data(as_text=True)
        self.assertIn(f'id="user-{self.users["teacher_a"]}"', sms)
        self.assertIn('id="user-%s"' % jit.id, sms)
        self.assertNotIn(f'id="user-{self.users["nogrants"]}"', sms)
        jit_html = self._get('/admin/users?source=jit&status=all').get_data(as_text=True)
        self.assertEqual(jit_html.count('class="user-row"'), 1)
        self.assertIn('id="user-%s"' % jit.id, jit_html)

        supers = self._get('/admin/users?super=1').get_data(as_text=True)
        self.assertIn(f'id="user-{self.users["super"]}"', supers)
        self.assertIn(f'id="user-{self.users["breakglass"]}"', supers)
        self.assertNotIn(f'id="user-{self.users["teacher_a"]}"', supers)

        matc = self._get('/admin/users?subject=MATC').get_data(as_text=True)
        for name in ('teacher_a', 'admin_matc', 'super', 'breakglass'):
            self.assertIn(f'id="user-{self.users[name]}"', matc, name)
        for name in ('viewer_ict', 'nogrants'):
            self.assertNotIn(f'id="user-{self.users[name]}"', matc, name)
        self.user('teacher_a').active = False
        db.session.commit()
        hidden = self._get('/admin/users?subject=MATC&status=all').get_data(as_text=True)
        self.assertNotIn(f'id="user-{self.users["teacher_a"]}"', hidden)
        self.assertIn(f'id="user-{self.users["admin_matc"]}"', hidden)

    def test_paging_totals_and_clamp(self):
        from app import db
        from app.models import User
        for i in range(30):
            db.session.add(User(username=f'bulk{i:02d}', password_hash=_password_hash(),
                                is_super_admin=False, is_admin=False))
        db.session.commit()
        page1 = self._get('/admin/users').get_data(as_text=True)
        self.assertIn('1-25 of 36', page1)
        self.assertEqual(page1.count('class="user-row"'), 25)
        self.assertIn('>bulk00<', page1)
        self.assertNotIn('>bulk23<', page1)
        self.assertIn('page=2', page1)
        page2 = self._get('/admin/users?page=2').get_data(as_text=True)
        self.assertIn('26-36 of 36', page2)
        self.assertEqual(page2.count('class="user-row"'), 11)
        self.assertIn('>bulk23<', page2)
        self.assertNotIn('>bulk00<', page2)
        self.assertIn('q=bulk', self._get('/admin/users?q=bulk&page=2').get_data(as_text=True))
        clamped = self._get('/admin/users?page=99').get_data(as_text=True)
        self.assertIn('26-36 of 36', clamped)
        self.assertIn('>bulk23<', clamped)
        for raw in ('0', '-4', 'abc'):
            html = self._get('/admin/users?page=' + raw).get_data(as_text=True)
            self.assertIn('1-25 of 36', html, raw)

    def test_access_summary_local_and_super(self):
        from app import authz
        from app.models import Subject
        from app.users_directory import summarize
        ids = [row.id for row in Subject.query.order_by(Subject.id)]
        teacher = self.user('teacher_a')
        expected = summarize(authz.effective_levels(teacher), ids, super_admin=False, active=True)
        self.assertEqual(expected, 'User: MATC | Viewer: ICT')
        html = self._get('/admin/users').get_data(as_text=True)
        self.assertIn(expected, self._row(html, 'teacher_a'))
        self.assertIn('Super admin: all', self._row(html, 'super'))
        self.assertIn('No access', self._row(html, 'nogrants'))
        self.assertIn('Never', self._row(html, 'nogrants'))

    def test_access_summary_includes_sms_levels(self):
        from app import authz, db, sms_sso_service, sms_trust
        from app.models import SmsSubjectMap
        import smskit
        self.app.config.update(SMS_SSO_ENABLED=True, SMS_SCHEMA_READY=True)
        smskit.install_db_trust()
        for sid in ('MATC', 'MAT1', 'MAT2'):
            db.session.add(SmsSubjectMap(sms_uuid=smskit.subject_uuid('MATH'), oqb_subject_id=sid))
        sms_sso_service.replace_levels(
            self.users['nogrants'], smskit.PRIMARY, 'viewer',
            [(smskit.subject_uuid('MATH'), 'MATH', 'user')], 'handoff')
        db.session.commit()
        sms_trust.invalidate()
        self.fresh()
        levels = authz.effective_levels(self.user('nogrants'))
        self.assertEqual(levels, {
            'ECON': 'viewer', 'ICT': 'viewer', 'MAT1': 'user', 'MAT2': 'user', 'MATC': 'user',
        })
        html = self._get('/admin/users').get_data(as_text=True)
        self.assertIn('User: MAT1, MAT2, MATC | Viewer: ECON, ICT', self._row(html, 'nogrants'))

    def test_authz(self):
        anon = self.app.test_client()
        resp = anon.get('/admin/users')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login', resp.headers.get('Location', ''))
        panel = f'/admin/users/{self.users["nogrants"]}/permissions-panel'
        resp = anon.get(panel)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login', resp.headers.get('Location', ''))
        for name in ('admin_matc', 'teacher_a', 'viewer_ict'):
            client = self.app.test_client()
            sign_in(client, self.user(name))
            self.assertEqual(client.get('/admin/users').status_code, 403, name)
            self.assertEqual(client.get(panel).status_code, 403, name)
        self.assertEqual(self.client.get('/admin/users').status_code, 200)

    def test_permissions_panel_and_existing_save(self):
        uid = self.users['teacher_a']
        page = self._get('/admin/users').get_data(as_text=True)
        self.assertNotIn('permission-select', self._row(page, 'teacher_a'))
        panel = self.client.get(f'/admin/users/{uid}/permissions-panel')
        self.assertEqual(panel.status_code, 200)
        body = panel.get_data(as_text=True)
        self.assertIn('permission-select', body)
        self.assertIn('data-subject="MATC"', body)
        self.assertIn('data-subject="ICT"', body)
        self.assertIn('SMS level', body)
        self.assertIn('Effective', body)
        self.assertIn('updatePermission(', body)
        self.assertEqual(self.client.get('/admin/users/999999/permissions-panel').status_code, 404)
        self.assertEqual(self.client.get('/admin/users/999999/row').status_code, 404)

        resp = self.client.post(f'/admin/users/{self.users["nogrants"]}/permissions',
                                json={'permissions': {'ICT': 'admin'}})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json().get('success'))
        html = self._get('/admin/users').get_data(as_text=True)
        self.assertIn('Admin: ICT', self._row(html, 'nogrants'))
        saved = self.client.get(f'/admin/users/{self.users["nogrants"]}/permissions-panel').get_data(as_text=True)
        chunk = saved.split('data-subject="ICT"', 1)[1].split('</select>', 1)[0]
        self.assertIn('value="admin" selected', chunk)
        row = self.client.get(f'/admin/users/{self.users["nogrants"]}/row').get_data(as_text=True)
        self.assertIn('Admin: ICT', row)
        self.assertTrue(row.strip().startswith('<tr'))

    def test_old_urls_still_work(self):
        self.assertEqual(self._get('/admin/users?foo=1&status=nope').status_code, 200)
        resp = self.client.get(f'/admin/users/{self.users["teacher_a"]}/permissions/get')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()['permissions']['MATC'], 'user')
        edited = self.client.post(f'/admin/users/{self.users["nogrants"]}/edit',
                                  data={'username': 'nogrants', 'is_super_admin': '0'})
        self.assertTrue(edited.get_json().get('success'))
        preview = self.client.get(f'/admin/users/{self.users["nogrants"]}/delete-preview')
        self.assertEqual(preview.status_code, 200)
        partial = self.client.get('/admin/users', headers={'HX-Request': 'true'})
        text = partial.get_data(as_text=True)
        self.assertEqual(partial.status_code, 200)
        self.assertIn('id="users-browser"', text)
        self.assertNotIn('<html', text.lower())

    def test_toolbox_save_export_blocks_erase(self):
        from app import db
        from app.models import User
        from app.toolbox import pdf as toolbox_pdf
        uid = self.users['nogrants']
        job_id = 'users-table-save-job'
        toolbox_pdf._EXPORT_JOBS[job_id] = {
            'mode': 'save', 'owner': uid, 'ts': time.time(), 'finished': False,
        }
        self.addCleanup(toolbox_pdf._EXPORT_JOBS.pop, job_id, None)
        before = User.query.count()
        resp = self.client.post(f'/admin/users/{uid}/delete')
        self.fresh()
        self.assertEqual(resp.status_code, 409)
        body = resp.get_json()
        self.assertEqual(body.get('reason'), 'toolbox_export')
        self.assertIn('toolbox PDF save', body.get('error', ''))
        self.assertEqual(User.query.count(), before)
        self.assertIsNotNone(db.session.get(User, uid))
        blocked = self.client.get(f'/admin/users/{uid}/delete-preview').get_json()['blocked']
        self.assertIn('toolbox PDF save', blocked)
        toolbox_pdf._EXPORT_JOBS[job_id]['finished'] = True
        self.assertIsNone(self.client.get(f'/admin/users/{uid}/delete-preview').get_json()['blocked'])
        toolbox_pdf._EXPORT_JOBS[job_id]['finished'] = False
        toolbox_pdf._EXPORT_JOBS[job_id]['mode'] = 'download'
        self.assertIsNone(self.client.get(f'/admin/users/{uid}/delete-preview').get_json()['blocked'])
        toolbox_pdf._EXPORT_JOBS[job_id]['mode'] = 'save'
        toolbox_pdf._EXPORT_JOBS[job_id]['owner'] = self.users['teacher_a']
        self.assertIsNone(self.client.get(f'/admin/users/{uid}/delete-preview').get_json()['blocked'])

    def test_query_count_stays_flat_as_the_directory_grows(self):
        from sqlalchemy import event
        from app import db
        from app.models import User

        def count(url):
            self.fresh()
            box = {'n': 0}

            def before(*_a, **_k):
                box['n'] += 1

            event.listen(db.engine, 'before_cursor_execute', before)
            try:
                resp = self.client.get(url)
            finally:
                event.remove(db.engine, 'before_cursor_execute', before)
            return box['n'], resp

        small, resp = count('/admin/users')
        self.assertEqual(resp.status_code, 200)
        small_subject, _ = count('/admin/users?subject=MATC')
        for i in range(40):
            db.session.add(User(username=f'flat{i:02d}', password_hash=_password_hash(),
                                is_super_admin=False, is_admin=False))
        db.session.commit()
        big, resp = count('/admin/users')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_data(as_text=True).count('class="user-row"'), 25)
        big_subject, _ = count('/admin/users?subject=MATC')
        self.assertLessEqual(big, small + 3, (small, big))
        self.assertLessEqual(big_subject, small_subject + 3, (small_subject, big_subject))


if __name__ == '__main__':
    unittest.main()
