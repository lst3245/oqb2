"""SMS sign-in routes: gates, rate limit, sessions, account states, login page."""
import unittest

import smskit
from dbcase import PASSWORD
from ssocase import ORIGIN, SsoCase

ROUTES = (('get', '/sso/sms'), ('post', '/sso/sms'), ('get', '/sso/sms/failed'), ('get', '/sso/sms/link'),
          ('get', '/sso/sms/link/confirm'), ('post', '/sso/sms/link/confirm'), ('post', '/sso/sms/link/cancel'))


class GateTests(SsoCase):
    def test_off_means_404(self):
        for key, value in (('SMS_SSO_ENABLED', False), ('PUBLIC_ORIGIN', '')):
            self.app.config.update(SMS_SSO_ENABLED=True, PUBLIC_ORIGIN=ORIGIN)
            self.app.config[key] = value
            for method, path in ROUTES:
                with self.subTest(key=key, path=path, method=method):
                    self.assertEqual(getattr(self.client, method)(path).status_code, 404)

    def test_schema_not_ready_means_503(self):
        self.app.config['SMS_SCHEMA_READY'] = False
        for method, path in ROUTES:
            with self.subTest(path=path, method=method):
                self.assertEqual(getattr(self.client, method)(path).status_code, 503)

    def test_feed_path_stays_404(self):
        self.assertEqual(self.client.post('/api/sms/directory', data='x',
                                          content_type='application/jwt').status_code, 404)

    def test_rate_limit(self):
        for _ in range(20):
            self.post_token('x.y.z')
        resp = self.post_token('x.y.z')
        self.assert_next(resp, '/sso/sms/failed', 429)
        self.assertEqual(len(self.audit('sso_login', 'rate_limited')), 1)

    def test_oversize_and_missing_body(self):
        self.assert_next(self.post_token('a' * 20000), '/sso/sms/failed', 401)
        resp = self.client.post('/sso/sms', headers={'Origin': ORIGIN, 'Accept': 'application/json'},
                                json={'nope': 1})
        self.assert_next(resp, '/sso/sms/failed', 401)
        self.assertEqual(len(self.audit('sso_login', 'malformed')), 2)


class SessionTests(SsoCase):
    def test_different_user_replaces_session(self):
        self.login('viewer_ict')
        self.assert_next(self.submit('teacher_viewer_user'), '/')
        self.assertTrue(self.session_user().startswith(f"{self.users['teacher_a']}:"))

    def test_same_user_keeps_session_and_refreshes_levels(self):
        self.assert_next(self.submit('teacher_viewer_user'), '/')
        with self.client.session_transaction() as sess:
            sess['marker'] = 1
        self.assert_next(self.submit('teacher_math_only'), '/')
        with self.client.session_transaction() as sess:
            self.assertEqual(sess.get('marker'), 1)
        self.assertEqual(self.levels('teacher_a')['ICT'], 'viewer')

    def test_unlinked_handoff_signs_out_current_user(self):
        self.login('super')
        self.assert_next(self.submit('unlinked_teacher'), '/sso/sms/link')
        self.assertIsNone(self.session_user())
        self.assertEqual(self.client.get('/sso/sms/link/confirm').status_code, 302)

    def test_remember_cookie_cleared_on_sms_login(self):
        resp = self.client.post('/login', data={'username': 'teacher_a', 'password': PASSWORD})
        name = self.app.config.get('REMEMBER_COOKIE_NAME', 'remember_token')
        self.assertTrue(any(h.startswith(name + '=') for h in resp.headers.getlist('Set-Cookie')))
        resp = self.submit('teacher_viewer_user')
        cleared = [h for h in resp.headers.getlist('Set-Cookie') if h.startswith(name + '=')]
        self.assertTrue(cleared and ('Expires=Thu, 01 Jan 1970' in cleared[0] or 'Max-Age=0' in cleared[0]),
                        cleared)

    def test_failed_handoff_keeps_existing_session(self):
        self.login('viewer_ict')
        self.assert_next(self.submit('expired_31s'), '/sso/sms/failed', 401)
        self.assertTrue(self.session_user().startswith(f"{self.users['viewer_ict']}:"))


class AccountStateTests(SsoCase):
    def set_state(self, username, active, reason):
        from app import db
        from app.models import User
        u = db.session.get(User, self.users[username])
        u.active, u.disabled_reason = active, reason
        db.session.commit()

    def test_locally_disabled_is_refused(self):
        self.set_state('teacher_a', False, 'local')
        self.assert_next(self.submit('teacher_viewer_user'), '/sso/sms/failed', 401)
        self.assertEqual(len(self.audit('sso_login', 'disabled')), 1)

    def test_deprovisioned_is_reactivated_by_handoff(self):
        from app import db
        from app.models import User
        self.set_state('teacher_a', False, 'sms_deprovisioned')
        self.assert_next(self.submit('teacher_viewer_user'), '/')
        db.session.expire_all()
        u = db.session.get(User, self.users['teacher_a'])
        self.assertTrue(u.active)
        self.assertIsNone(u.disabled_reason)
        self.assertEqual(len(self.audit('reactivate')), 1)

    def test_profile_refreshed_and_catalogue_seen(self):
        from app import db
        from app.models import SmsSubjectCatalogue, User
        self.assert_next(self.submit('teacher_viewer_user'), '/')
        db.session.expire_all()
        u = db.session.get(User, self.users['teacher_a'])
        teacher = smskit.person('teacher')
        self.assertEqual((u.display_name, u.email, u.staff_code, u.last_login_method),
                         (teacher['name'], teacher['email'], teacher['staff_code'], 'sms'))
        seen = {r.code: r.seen_in for r in SmsSubjectCatalogue.query}
        self.assertEqual(seen, {'MATH': 'token', 'PHY': 'token'})

    def test_capped_handoff_does_not_touch_catalogue(self):
        from app.models import SmsSubjectCatalogue
        self.assert_next(self.submit('secondary_test_account_admin'), '/')
        self.assertEqual(SmsSubjectCatalogue.query.count(), 0)


class LoginPageTests(SsoCase):
    def test_sms_button(self):
        body = self.client.get('/login').get_data(as_text=True)
        self.assertIn(f'href="{smskit.PRIMARY}/apps/oqb?origin={ORIGIN}"', body)
        self.app.config['SMS_SSO_ENABLED'] = False
        self.assertNotIn('/apps/oqb?origin=', self.client.get('/login').get_data(as_text=True))

    def test_no_button_without_a_pinned_key(self):
        from app import db, sms_trust
        for kid in ('sms-contract-test', 'sms-contract-test-2'):
            sms_trust.unpin_key(smskit.PRIMARY, kid)
        db.session.commit()
        self.assertNotIn('/apps/oqb?origin=', self.client.get('/login').get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()
