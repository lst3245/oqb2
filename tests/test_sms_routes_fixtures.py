"""Every official token case (tests/fixtures/sms/cases.json) through the real
``POST /sso/sms`` route and handler, with the clock frozen to its
``verify_at``: accepted cases sign in, ``not_linked`` shows the link prompt,
every refusal answers 401 with the failure page and audits its reason code.
Method names are stable: ``test_case_<name>``. Snapshot cases go through
``POST /api/sms/directory`` in test_sms_feed.py."""
import unittest

import smskit
from ssocase import LINKS, SsoCase

SIGNED_IN = {person: username for person, (username, _iss) in LINKS.items()}


class FixtureRouteCases(SsoCase):
    """Generated below."""


def _route_test(c):
    def test(self):
        resp = self.submit(c['name'])
        claims = smskit.claims(c['name'])
        if c['expect'] in ('accept', 'accept_capped'):
            self.assert_next(resp, '/')
            person = next(p for p, v in smskit.cases()['people'].items() if v['sub'] == claims['sub'])
            self.assertTrue(self.session_user().startswith(f"{self.users[SIGNED_IN[person]]}:"))
            [row] = self.audit('sso_login')
            self.assertEqual(row.outcome, 'ok')
            self.assertEqual(bool(self.audit('nonprimary_login')), c['expect'] == 'accept_capped')
        elif c['expect'] == 'not_linked':
            self.assert_next(resp, '/sso/sms/link')
            self.assertIsNone(self.session_user())
            [row] = self.audit('not_linked')
            self.assertEqual(row.sub, claims['sub'])
        else:
            self.assert_next(resp, '/sso/sms/failed', 401)
            self.assertIsNone(self.session_user())
            [row] = self.audit('sso_login')
            self.assertEqual((row.outcome, row.reason), ('fail', c['expect']))
    test.__doc__ = f"{c['name']} via POST /sso/sms: {c['expect']} (AT {c['acceptance_tests']})."
    return test


for _c in smskit.cases()['tokens']:
    setattr(FixtureRouteCases, f"test_case_{_c['name']}", _route_test(_c))


class FixtureRouteVariants(SsoCase):
    def test_every_token_case_has_a_route_test(self):
        names = [n for n in dir(FixtureRouteCases) if n.startswith('test_case_')]
        self.assertEqual(len(names), len(smskit.cases()['tokens']))

    def test_secondary_disabled_variant(self):
        from app import db, sms_trust
        sms_trust.upsert_issuer(smskit.SECONDARY, enabled=False)
        db.session.commit()
        for name in ('secondary_test_account_admin', 'secondary_teacher_not_allowed'):
            self.assert_next(self.submit(name, client=self.app.test_client()), '/sso/sms/failed', 401)
        self.assertEqual(len(self.audit('sso_login', 'issuer_disabled')), 2)

    def test_unlinked_with_jit_on_signs_in(self):
        self.app.config['SMS_JIT_ENABLED'] = True
        self.assert_next(self.submit('unlinked_teacher'), '/')

    def test_form_post_without_json(self):
        resp = self.submit('teacher_viewer_user', as_json=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers['Location'], '/')
        resp = self.submit('teacher_viewer_user', as_json=False, client=self.app.test_client())
        self.assertEqual(resp.status_code, 401)
        self.assertIn('Sign-in from SMS didn', resp.get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()
