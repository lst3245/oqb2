"""Session limits, remember-me and cookie helpers (app/web_security.py;
contract v1.2 section 11; ADR-018). The policy and helper tests need no DB;
SessionLimitTests drive the real login / pages against oqb_sso_test with the
clock frozen. AT22 (SMS sessions) is in test_sms_acceptance.py."""
import unittest
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from flask import Flask

from app import web_security
from app.sms_trust import LIVE_ISSUER, WORKSHOP_ISSUER, TrustEntry, TrustList
from dbcase import PASSWORD, DbTestCase

T = 1_800_000_000
HOUR, DAY = 3600, 86400


def _auth(method, *, seen=0, **extra):
    return {'m': method, 'at': T, 'seen': T + seen, **extra}


class CheckSessionTests(unittest.TestCase):
    def check(self, auth, dt, *, linked=False, limits_on=True, days=30):
        return web_security.check_session(auth, T + dt, linked=linked, limits_on=limits_on,
                                          remember_days=days)

    def test_sms_absolute_and_idle(self):
        self.assertIsNone(self.check(_auth('sms'), 2 * HOUR - 1))
        self.assertEqual(self.check(_auth('sms'), 2 * HOUR), 'idle')
        self.assertIsNone(self.check(_auth('sms', seen=11 * HOUR), 12 * HOUR - 1))
        self.assertEqual(self.check(_auth('sms', seen=11 * HOUR), 12 * HOUR), 'absolute')

    def test_sms_limits_ignore_the_password_switch(self):
        self.assertEqual(self.check(_auth('sms'), 2 * HOUR, limits_on=False), 'idle')

    def test_sms_never_remembered(self):
        self.assertEqual(self.check(_auth('sms', r=True), 0), 'remember_sms')

    def test_password_limits_on(self):
        self.assertIsNone(self.check(_auth('password'), 2 * HOUR - 1))
        self.assertEqual(self.check(_auth('password'), 2 * HOUR), 'idle')
        self.assertEqual(self.check(_auth('password', seen=12 * HOUR - 5), 12 * HOUR), 'absolute')

    def test_password_limits_off(self):
        self.assertIsNone(self.check(_auth('password'), 40 * DAY, limits_on=False))

    def test_remember_absolute_only(self):
        self.assertIsNone(self.check(_auth('password', r=True), 30 * DAY - 1))
        self.assertEqual(self.check(_auth('password', r=True), 30 * DAY), 'absolute')
        self.assertEqual(self.check(_auth('password', r=True), 7 * DAY, days=7), 'absolute')

    def test_remember_refused_for_linked_or_switched_off(self):
        self.assertEqual(self.check(_auth('password', r=True), 0, linked=True), 'remember_linked')
        self.assertEqual(self.check(_auth('password', r=True), 0, days=0), 'remember_off')
        # a linked account without remember-me is fine
        self.assertIsNone(self.check(_auth('password'), 0, linked=True))

    def test_missing_or_bad_marker(self):
        for bad in (None, {}, 'sms', {'m': 'other', 'at': T, 'seen': T},
                    {'m': 'sms', 'at': str(T), 'seen': T}, {'m': 'sms', 'at': True, 'seen': T},
                    {'m': 'password', 'at': T}):
            with self.subTest(bad=bad):
                self.assertEqual(self.check(bad, 0), 'no_marker')


class HelperTests(unittest.TestCase):
    def test_cookie_secure(self):
        cs = web_security.cookie_secure
        self.assertTrue(cs('auto', 'https://oqb.example'))
        self.assertTrue(cs(None, 'https://oqb.example'))
        self.assertFalse(cs('auto', 'http://oqb.example:5000'))
        self.assertFalse(cs('auto', ''))
        self.assertTrue(cs('true', ''))
        self.assertFalse(cs('false', 'https://oqb.example'))

    def test_embed_cookie(self):
        self.assertEqual(
            web_security.embed_cookie('oqb_session=abc; HttpOnly; Path=/; SameSite=Lax'),
            'oqb_session=abc; HttpOnly; Path=/; Secure; SameSite=None; Partitioned')
        self.assertEqual(
            web_security.embed_cookie('oqb_remember=x; Expires=Thu, 01 Jan 2026 00:00:00 GMT; Secure; '
                                      'HttpOnly; Path=/; SameSite=Lax; Partitioned'),
            'oqb_remember=x; Expires=Thu, 01 Jan 2026 00:00:00 GMT; HttpOnly; Path=/; Secure; '
            'SameSite=None; Partitioned')

    def test_frame_ancestors_follow_the_workshop_entry(self):
        fa = web_security.frame_ancestors
        open_ws = TrustEntry(issuer=WORKSHOP_ISSUER, enabled=True, enabled_until=T + DAY,
                             allowed_subs=frozenset({'sub'}))
        self.assertEqual(fa(TrustList([open_ws]), T), [LIVE_ISSUER, WORKSHOP_ISSUER])
        self.assertEqual(fa(TrustList([open_ws]), T + DAY), [LIVE_ISSUER])
        closed = TrustEntry(issuer=WORKSHOP_ISSUER, enabled=False, enabled_until=T + DAY,
                            allowed_subs=frozenset({'sub'}))
        self.assertEqual(fa(TrustList([closed]), T), [LIVE_ISSUER])
        self.assertEqual(fa(TrustList([]), T), [LIVE_ISSUER])
        self.assertEqual(fa(None, T), [LIVE_ISSUER])

    def test_remember_days_clamped(self):
        app = Flask(__name__)
        for raw, days in ((30, 30), (45, 30), (0, 0), (-3, 0), ('7', 7), ('x', 0), (None, 0)):
            app.config['PASSWORD_REMEMBER_DAYS'] = raw
            with app.app_context():
                self.assertEqual(web_security.remember_days(), days, raw)


def _cookie(resp, name):
    """The ``Set-Cookie`` header for ``name`` with a non-empty value, or None."""
    for h in resp.headers.getlist('Set-Cookie'):
        key, _, rest = h.partition('=')
        if key == name and rest.split(';', 1)[0].strip('"'):
            return h
    return None


class SessionLimitTests(DbTestCase):
    PAGE = '/user/sets'

    def setUp(self):
        super().setUp()
        self.app.config.update(PASSWORD_SESSION_LIMITS=True, PASSWORD_REMEMBER_DAYS=30)

    def frozen(self, dt):
        from app import sms_clock
        return sms_clock.frozen(T + dt)

    def login_pw(self, name='teacher_a', remember=False, client=None):
        data = {'username': name, 'password': PASSWORD}
        if remember:
            data['remember'] = '1'
        with self.frozen(0):
            resp = (client or self.client).post('/login', data=data)
        self.assertEqual(resp.status_code, 302, resp.get_data(as_text=True)[:300])
        return resp

    def get(self, dt, client=None):
        from app import db
        db.session.rollback()
        with self.frozen(dt):
            return (client or self.client).get(self.PAGE)

    def marker(self, client=None):
        with (client or self.client).session_transaction() as sess:
            return sess.get('oqb_auth')

    def test_password_session_idle_limit(self):
        self.login_pw()
        self.assertEqual(self.marker(), {'m': 'password', 'at': T, 'seen': T})
        self.assertEqual(self.get(2 * HOUR - 1).status_code, 200)
        self.assertEqual(self.get(4 * HOUR - 2).status_code, 200)
        resp = self.get(6 * HOUR)
        self.assertEqual(resp.status_code, 302)
        self.assertIsNone(self.marker())
        page = self.client.get('/login').get_data(as_text=True)
        self.assertIn('Your session has ended. Please sign in again.', page)

    def test_password_session_absolute_limit(self):
        self.login_pw()
        for dt in range(HOUR, 12 * HOUR, HOUR):
            self.assertEqual(self.get(dt).status_code, 200, dt)
        self.assertEqual(self.get(12 * HOUR).status_code, 302)

    def test_limits_switched_off(self):
        self.app.config['PASSWORD_SESSION_LIMITS'] = False
        self.login_pw()
        self.assertEqual(self.get(3 * DAY).status_code, 200)

    def test_remember_me_is_opt_in(self):
        resp = self.login_pw()
        self.assertIsNone(_cookie(resp, 'oqb_remember'))
        self.client = self.app.test_client()
        resp = self.login_pw(remember=True)
        cookie = _cookie(resp, 'oqb_remember')
        self.assertIsNotNone(cookie)
        self.assertIn('HttpOnly', cookie)
        expires = parsedate_to_datetime(cookie.split('Expires=', 1)[1].split(';', 1)[0])
        left = (expires - datetime.now(timezone.utc)).total_seconds()
        self.assertTrue(29 * DAY < left <= 30 * DAY + 60, left)
        self.assertEqual(self.marker()['r'], True)
        # no idle limit, absolute limit = PASSWORD_REMEMBER_DAYS
        self.assertEqual(self.get(10 * DAY).status_code, 200)
        self.assertEqual(self.get(30 * DAY).status_code, 302)

    def test_remember_days_setting(self):
        self.app.config['PASSWORD_REMEMBER_DAYS'] = 7
        self.assertIn('Remember me for 7 days', self.client.get('/login').get_data(as_text=True))
        cookie = _cookie(self.login_pw(remember=True), 'oqb_remember')
        expires = parsedate_to_datetime(cookie.split('Expires=', 1)[1].split(';', 1)[0])
        self.assertLess((expires - datetime.now(timezone.utc)).total_seconds(), 7 * DAY + 60)

    def test_remember_days_zero_hides_the_box(self):
        self.app.config['PASSWORD_REMEMBER_DAYS'] = 0
        self.assertNotIn('name="remember"', self.client.get('/login').get_data(as_text=True))
        self.assertIsNone(_cookie(self.login_pw(remember=True), 'oqb_remember'))
        self.assertNotIn('r', self.marker())

    def _link(self, name):
        from app import db
        from app.models import ExternalIdentity
        db.session.add(ExternalIdentity(user_id=self.users[name], issuer='https://sms-contract-test.invalid',
                                        sub='00000000-0000-4000-8000-0000000000aa', link_method='admin'))
        db.session.commit()

    def test_linked_account_gets_no_remember_cookie(self):
        self._link('teacher_a')
        self.assertIsNone(_cookie(self.login_pw(remember=True), 'oqb_remember'))
        self.assertNotIn('r', self.marker())

    def test_remember_cookie_restores_an_unlinked_session(self):
        self.login_pw(remember=True)
        self.client.delete_cookie('oqb_session')
        self.assertEqual(self.get(3 * DAY).status_code, 200)
        marker = self.marker()
        self.assertEqual((marker['m'], marker['r']), ('password', True))

    def test_remember_cookie_refused_once_linked(self):
        self.login_pw(remember=True)
        self._link('teacher_a')
        self.client.delete_cookie('oqb_session')
        resp = self.get(HOUR)
        self.assertEqual(resp.status_code, 302)
        cleared = [h for h in resp.headers.getlist('Set-Cookie') if h.startswith('oqb_remember=')]
        self.assertTrue(cleared and _cookie(resp, 'oqb_remember') is None, cleared)
        self.assertIsNone(self.marker())
        # the live session of a remembered login is refused the same way
        self.client = self.app.test_client()
        from app import db
        from app.models import ExternalIdentity
        ExternalIdentity.query.delete()
        db.session.commit()
        self.login_pw(remember=True)
        self._link('teacher_a')
        self.assertEqual(self.get(60).status_code, 302)

    def test_own_password_change_keeps_this_session(self):
        self.login_pw('super', remember=True)
        with self.frozen(60):
            resp = self.client.post(f"/admin/users/{self.users['super']}/edit",
                                    data={'password': 'new-pw-for-tests'},
                                    headers={'X-Requested-With': 'XMLHttpRequest'})
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True)[:300])
        self.assertIsNotNone(_cookie(resp, 'oqb_remember'))
        self.assertEqual(self.get(120).status_code, 200)
        self.assertEqual(self.marker()['at'], T)


if __name__ == '__main__':
    unittest.main()
