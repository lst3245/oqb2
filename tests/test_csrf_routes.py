"""CSRF through the real app on oqb_sso_test: enforce vs report, header vs
form field, the two exemptions, the login form, token rotation, POST logout
and the share-target 303. The client does not add tokens here
(``auto_csrf = False``)."""
import json
import logging
import re
import unittest

from dbcase import PASSWORD, DbTestCase
from ssocase import SsoCase

XHR = {'X-Requested-With': 'XMLHttpRequest'}
HTML = {'Accept': 'text/html,application/xhtml+xml'}


class CsrfCase(DbTestCase):
    def setUp(self):
        super().setUp()
        self.app.config['CSRF_MODE'] = 'enforce'
        self.client.auto_csrf = False

    def token(self, client=None):
        with (client or self.client).session_transaction() as sess:
            return sess.get('_csrf')

    def session_user(self):
        with self.client.session_transaction() as sess:
            return sess.get('_user_id')

    def add_topic(self, **kw):
        """A POST that reaches the view only past the CSRF check; teacher_a is
        not an admin, so the view itself answers 403 and writes nothing."""
        return self.client.post('/admin/topics/add', data={'subject_id': 'ICT', 'name': 'X', **kw.pop('data', {})},
                                **kw)


class EnforceTests(CsrfCase):
    def test_missing_or_wrong_token_is_refused_with_json(self):
        self.login('teacher_a')
        token = self.client.csrf_token()
        for headers in (XHR, {**XHR, 'X-CSRF-Token': 'wrong'}, {**XHR, 'X-CSRF-Token': token + 'x'}):
            with self.subTest(headers=headers):
                resp = self.add_topic(headers=headers)
                self.assertEqual(resp.status_code, 400)
                self.assertEqual(resp.get_json(), {
                    'success': False, 'error': 'csrf_failed',
                    'message': 'Your session has expired. Reload the page and try again.'})

    def test_header_or_form_field_passes(self):
        self.login('teacher_a')
        token = self.client.csrf_token()
        self.assertEqual(self.add_topic(headers={**XHR, 'X-CSRF-Token': token}).status_code, 403)
        self.assertEqual(self.add_topic(headers=XHR, data={'csrf_token': token}).status_code, 403)
        resp = self.client.post('/admin/questions/batch-update', headers={**XHR, 'X-CSRF-Token': token},
                                data={'question_ids': json.dumps([self.questions['ECON']]), 'level': '2'})
        self.assertEqual(resp.status_code, 403)

    def test_json_body_needs_the_header(self):
        self.login('teacher_a')
        token = self.client.csrf_token()
        resp = self.client.post('/files/api/location', json={'csrf_token': token, 'scope': 'user'})
        self.assertEqual((resp.status_code, resp.get_json()['error']), (400, 'csrf_failed'))
        resp = self.client.post('/files/api/location', json={'scope': 'user'},
                                headers={'X-CSRF-Token': token})
        self.assertNotEqual((resp.get_json() or {}).get('error'), 'csrf_failed')

    def test_token_of_another_session_is_refused(self):
        other = self.app.test_client()
        other_token = other.csrf_token()
        self.login('teacher_a')
        self.client.csrf_token()
        self.assertEqual(self.add_topic(headers={**XHR, 'X-CSRF-Token': other_token}).status_code, 400)

    def test_html_form_post_gets_a_400_page_and_nothing_happens(self):
        self.login('teacher_a')
        self.client.csrf_token()
        resp = self.client.post('/logout', headers=HTML)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('Session expired', resp.get_data(as_text=True))
        self.assertIsNotNone(self.session_user())

    def test_delete_is_checked(self):
        self.login('teacher_a')
        url = f"/admin/topics/{self.topics['ICT']}/delete"
        self.assertEqual(self.client.delete(url, headers=XHR).status_code, 400)
        token = self.client.csrf_token()
        self.assertEqual(self.client.delete(url, headers={**XHR, 'X-CSRF-Token': token}).status_code, 403)

    def test_unknown_url_is_left_to_routing(self):
        self.assertEqual(self.client.post('/no/such/page').status_code, 404)


class ReportModeTests(CsrfCase):
    def test_report_logs_and_continues(self):
        self.app.config['CSRF_MODE'] = 'report'
        self.login('teacher_a')
        token = self.client.csrf_token()
        with self.assertLogs('app.csrf', logging.WARNING) as logs:
            resp = self.add_topic(headers=XHR)
        self.assertEqual(resp.status_code, 403)  # the view ran
        [line] = logs.output
        self.assertIn('csrf_failed mode=report endpoint=admin.add_topic path=/admin/topics/add', line)
        self.assertNotIn(token, line)

    def test_enforce_logs_too(self):
        self.login('teacher_a')
        with self.assertLogs('app.csrf', logging.WARNING) as logs:
            self.assertEqual(self.add_topic(headers=XHR).status_code, 400)
        self.assertIn('csrf_failed mode=enforce', logs.output[0])


class ExemptionTests(CsrfCase):
    def test_exempt_endpoints_reach_their_view(self):
        # both features are off: their views answer 404, the CSRF check would say 400
        self.app.config.update(SMS_SSO_ENABLED=False, SMS_FEED_ENABLED=False)
        self.client.csrf_token()
        self.assertEqual(self.client.post('/sso/sms', data={'token': 'x'}).status_code, 404)
        self.assertEqual(self.client.post('/api/sms/directory', data=b'x',
                                          content_type='application/jwt').status_code, 404)

    def test_link_forms_are_not_exempt(self):
        self.app.config.update(SMS_SSO_ENABLED=True, PUBLIC_ORIGIN='https://oqb.test')
        self.client.csrf_token()
        self.assertEqual(self.client.post('/sso/sms/link/cancel', headers=HTML).status_code, 400)

    def test_listed_endpoints_exist(self):
        from app import csrf
        names = set(self.app.view_functions)
        self.assertTrue(csrf.EXEMPT_ENDPOINTS <= names)
        self.assertTrue(set(csrf.REDIRECT_ON_FAILURE) <= names)
        self.assertTrue(set(csrf.REDIRECT_ON_FAILURE.values()) <= names)

    def test_share_target_without_token_redirects_303(self):
        self.login('teacher_a')
        resp = self.client.post('/admin/toolbox/markup/share-target', data={'title': 'x'},
                                content_type='multipart/form-data')
        self.assertEqual(resp.status_code, 303)
        self.assertTrue(resp.headers['Location'].endswith('/admin/toolbox/markup'), resp.headers['Location'])


class SsoExemptionTests(SsoCase):
    def test_token_post_needs_no_csrf_token(self):
        self.client.auto_csrf = False
        self.assert_next(self.submit('teacher_viewer_user'), '/')


class LoginLogoutTests(CsrfCase):
    def login_post(self, token, **extra):
        data = {'username': 'teacher_a', 'password': PASSWORD, **extra}
        if token is not None:
            data['csrf_token'] = token
        return self.client.post('/login', data=data, headers=HTML)

    def test_login_form_is_protected(self):
        page = self.client.get('/login').get_data(as_text=True)
        token = self.token()
        self.assertIn(f'name="csrf_token" value="{token}"', page)
        resp = self.login_post(None)
        self.assertEqual(resp.status_code, 400)
        body = resp.get_data(as_text=True)
        self.assertIn('Your session has expired. Reload the page and try again.', body)
        self.assertIn('name="password"', body)
        self.assertIsNone(self.session_user())
        self.assertEqual(self.login_post('wrong').status_code, 400)
        self.assertIsNone(self.session_user())
        self.assertEqual(self.login_post(self.token()).status_code, 302)
        self.assertIsNotNone(self.session_user())

    def test_token_rotates_on_login_and_logout(self):
        self.client.get('/login')
        before = self.token()
        self.assertEqual(self.login_post(before).status_code, 302)
        after_login = self.token()
        self.assertNotEqual(after_login, before)
        resp = self.client.post('/logout', data={'csrf_token': after_login}, headers=HTML)
        self.assertEqual(resp.status_code, 303)
        self.assertTrue(resp.headers['Location'].endswith('/login'))
        self.assertIsNone(self.session_user())
        after_logout = self.token()
        self.assertTrue(after_logout and after_logout != after_login)

    def test_logout_get_only_asks(self):
        self.login('teacher_a')
        resp = self.client.get('/logout')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertRegex(body, r'<form method="post" action="/logout">\s*<input type="hidden" name="csrf_token"')
        self.assertIsNotNone(self.session_user())
        anon = self.app.test_client()
        resp = anon.get('/logout')
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.headers['Location'].endswith('/login'))

    def test_pages_carry_the_session_token(self):
        self.login('teacher_a')
        body = self.client.get('/user/sets').get_data(as_text=True)
        [meta] = re.findall(r'<meta name="csrf-token" content="([^"]+)">', body)
        self.assertEqual(meta, self.token())
        self.assertIn('js/csrf.js', body)
        self.assertRegex(body, r'action="/logout"')

    def test_logout_ends_the_session_and_remember_cookie(self):
        self.client.get('/login')
        self.assertEqual(self.login_post(self.token(), remember='1').status_code, 302)
        resp = self.client.post('/logout', data={'csrf_token': self.token()}, headers=HTML)
        cleared = [h for h in resp.headers.getlist('Set-Cookie') if h.startswith('oqb_remember=')]
        self.assertTrue(cleared and cleared[0].startswith('oqb_remember=;'), cleared)
        self.assertEqual(self.client.get('/user/sets').status_code, 302)


if __name__ == '__main__':
    unittest.main()
