"""Security headers and cookie flags through the real app on oqb_sso_test
(app/web_security.py; contract v1.2 section 14; ADR-018): the defaults, the
embed switch, Partitioned / SameSite=None cookies, and the frame-ancestors
list following the workshop entry."""
import unittest
from unittest import mock

from app.sms_trust import LIVE_ISSUER, WORKSHOP_ISSUER, TrustEntry, TrustList
from dbcase import PASSWORD, DbTestCase
from ssocase import SsoCase

DEFAULTS = {
    'Content-Security-Policy': "frame-ancestors 'none'",
    'X-Frame-Options': 'DENY',
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'strict-origin-when-cross-origin',
}


def _set_cookies(resp, name):
    return [h for h in resp.headers.getlist('Set-Cookie') if h.split('=', 1)[0] == name]


def _workshop(open_):
    from app import sms_clock
    now = sms_clock.now()
    return TrustList([TrustEntry(issuer=WORKSHOP_ISSUER, enabled=open_, enabled_until=now + 3600,
                                 allowed_subs=frozenset({'sub'}))])


class DefaultHeaderTests(DbTestCase):
    def test_defaults_on_pages_api_static_and_errors(self):
        self.login('teacher_a')
        for url in ('/login', '/user/sets', '/static/js/csrf.js', '/no/such/page', '/logout'):
            resp = self.client.get(url)
            with self.subTest(url=url, status=resp.status_code):
                for key, value in DEFAULTS.items():
                    self.assertEqual(resp.headers.get(key), value, key)

    def test_no_script_src_on_existing_pages(self):
        resp = self.client.get('/login')
        self.assertNotIn('script-src', resp.headers['Content-Security-Policy'])

    def test_cookie_flags(self):
        resp = self.client.get('/login')
        [cookie] = _set_cookies(resp, 'oqb_session')
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=Lax', cookie)
        self.assertNotIn('Partitioned', cookie)
        self.assertEqual('Secure' in cookie, self.app.config['SESSION_COOKIE_SECURE'])
        self.assertEqual(self.app.config['SESSION_COOKIE_SECURE'], self.app.config['REMEMBER_COOKIE_SECURE'])
        self.assertEqual(_set_cookies(resp, 'session'), [])


class EmbedTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.app.config.update(SMS_EMBED_ENABLED=True, PASSWORD_REMEMBER_DAYS=30)

    def test_embed_headers_live_only(self):
        with mock.patch('app.sms_trust.load_trust_list', return_value=_workshop(False)):
            resp = self.client.get('/login')
        self.assertEqual(resp.headers['Content-Security-Policy'], f'frame-ancestors {LIVE_ISSUER}')
        self.assertNotIn('X-Frame-Options', resp.headers)
        self.assertEqual(resp.headers['X-Content-Type-Options'], 'nosniff')

    def test_embed_headers_follow_the_workshop_window(self):
        with mock.patch('app.sms_trust.load_trust_list', return_value=_workshop(True)):
            resp = self.client.get('/login')
        self.assertEqual(resp.headers['Content-Security-Policy'],
                         f'frame-ancestors {LIVE_ISSUER} {WORKSHOP_ISSUER}')

    def test_trust_list_failure_keeps_only_the_live_sms(self):
        with mock.patch('app.sms_trust.load_trust_list', side_effect=RuntimeError('db down')):
            resp = self.client.get('/login')
        self.assertEqual(resp.headers['Content-Security-Policy'], f'frame-ancestors {LIVE_ISSUER}')

    def test_embed_cookies_are_partitioned(self):
        self.client.get('/login')
        with self.client.session_transaction() as sess:
            token = sess['_csrf']
        resp = self.client.post('/login', data={'username': 'nogrants', 'password': PASSWORD,
                                                'remember': '1', 'csrf_token': token})
        self.assertEqual(resp.status_code, 302)
        for name in ('oqb_session', 'oqb_remember'):
            [cookie] = _set_cookies(resp, name)
            with self.subTest(name):
                attrs = [a.strip() for a in cookie.split(';')[1:]]
                self.assertIn('Secure', attrs)
                self.assertIn('SameSite=None', attrs)
                self.assertIn('Partitioned', attrs)
                self.assertIn('HttpOnly', attrs)
                self.assertNotIn('SameSite=Lax', attrs)
                self.assertEqual(attrs.count('Secure'), 1)

    def test_embed_off_again(self):
        self.app.config['SMS_EMBED_ENABLED'] = False
        resp = self.client.get('/login')
        self.assertEqual(resp.headers['Content-Security-Policy'], "frame-ancestors 'none'")
        self.assertEqual(resp.headers['X-Frame-Options'], 'DENY')
        [cookie] = _set_cookies(resp, 'oqb_session')
        self.assertNotIn('Partitioned', cookie)


class SsoPageHeaderTests(SsoCase):
    def test_landing_keeps_its_csp_and_referrer_policy(self):
        resp = self.client.get('/sso/sms')
        csp = resp.headers['Content-Security-Policy']
        self.assertTrue(csp.startswith("default-src 'none'; script-src 'self' 'nonce-"), csp)
        self.assertTrue(csp.endswith("connect-src 'self'; form-action 'self'; frame-ancestors 'none'"), csp)
        self.assertEqual(resp.headers['Referrer-Policy'], 'no-referrer')
        self.assertEqual(resp.headers['Cache-Control'], 'no-store')
        self.assertEqual(resp.headers['X-Frame-Options'], 'DENY')

    def test_landing_frame_ancestors_follow_the_embed_switch(self):
        self.app.config['SMS_EMBED_ENABLED'] = True
        resp = self.client.get('/sso/sms')
        csp = resp.headers['Content-Security-Policy']
        self.assertTrue(csp.endswith(f"form-action 'self'; frame-ancestors {LIVE_ISSUER}"), csp)
        self.assertIn("script-src 'self' 'nonce-", csp)
        self.assertNotIn('X-Frame-Options', resp.headers)

    def test_feed_answer_has_headers_but_no_cookie(self):
        self.app.config['SMS_FEED_ENABLED'] = True
        resp = self.client.post('/api/sms/directory', data=b'x', content_type='application/jwt')
        self.assertEqual(resp.headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(resp.headers.getlist('Set-Cookie'), [])


if __name__ == '__main__':
    unittest.main()
