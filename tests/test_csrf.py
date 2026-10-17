"""app/csrf.py without the DB: token comparison, endpoint exemptions, mode
handling, the token helpers. The routes are in test_csrf_routes.py."""
import unittest

from flask import Flask, session

from app import csrf


class TokenTests(unittest.TestCase):
    def test_token_matches(self):
        m = csrf.token_matches
        self.assertTrue(m('abc123', 'abc123'))
        for expected, sent in (('abc123', 'abc124'), ('abc123', ''), ('', ''), (None, None),
                               ('abc123', None), (None, 'abc123'), ('abc', 'abc123'),
                               ('abc123', ['abc123']), ('令牌', '令')):
            with self.subTest(expected=expected, sent=sent):
                self.assertFalse(m(expected, sent))
        self.assertTrue(m('令牌', '令牌'))

    def test_get_token_is_lazy_and_stable_rotate_replaces_it(self):
        app = Flask(__name__)
        app.secret_key = 'tests-only'
        with app.test_request_context('/'):
            self.assertNotIn(csrf.SESSION_KEY, session)
            first = csrf.get_token()
            self.assertGreaterEqual(len(first), 40)
            self.assertEqual(csrf.get_token(), first)
            second = csrf.rotate()
            self.assertNotEqual(second, first)
            self.assertEqual(csrf.get_token(), second)

    def test_field_markup(self):
        app = Flask(__name__)
        app.secret_key = 'tests-only'
        with app.test_request_context('/'):
            session[csrf.SESSION_KEY] = 'a"b<c'
            self.assertEqual(str(csrf.field()),
                             '<input type="hidden" name="csrf_token" value="a&#34;b&lt;c">')


class CheckScopeTests(unittest.TestCase):
    def test_unsafe_methods_only(self):
        for method in ('POST', 'put', 'PATCH', 'delete'):
            self.assertTrue(csrf.needs_check(method, 'admin.add_topic'), method)
        for method in ('GET', 'HEAD', 'OPTIONS', None):
            self.assertFalse(csrf.needs_check(method, 'admin.add_topic'), method)

    def test_exemptions_by_endpoint_name(self):
        self.assertEqual(csrf.EXEMPT_ENDPOINTS, {'sso.token_post', 'sso.directory_feed'})
        for endpoint in csrf.EXEMPT_ENDPOINTS:
            self.assertFalse(csrf.needs_check('POST', endpoint))
        # not exempt: same blueprint, other views; the share target redirects instead
        for endpoint in ('sso.link_confirm', 'sso.link_cancel', 'auth.login', 'auth.logout',
                         'toolbox.markup_share_target'):
            self.assertTrue(csrf.needs_check('POST', endpoint), endpoint)

    def test_unrouted_requests_are_left_to_routing(self):
        self.assertFalse(csrf.needs_check('POST', None))


class ModeTests(unittest.TestCase):
    def test_mode(self):
        app = Flask(__name__)
        for raw, expected in (('report', 'report'), (' REPORT ', 'report'), ('enforce', 'enforce'),
                              ('bogus', 'enforce'), ('', 'enforce'), (None, 'enforce')):
            app.config['CSRF_MODE'] = raw
            with app.app_context():
                self.assertEqual(csrf.mode(), expected, raw)

    def test_mode_defaults_to_enforce(self):
        app = Flask(__name__)
        with app.app_context():
            self.assertEqual(csrf.mode(), 'enforce')


if __name__ == '__main__':
    unittest.main()
