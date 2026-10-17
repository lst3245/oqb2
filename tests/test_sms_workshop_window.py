"""Contract v1.2 section 2a test window, end to end (V1_2_ADDENDUM_3).

The non-primary issuer is the fixture workshop stand-in
(``https://sms-contract-test-workshop.invalid``, kid
``sms-contract-test-workshop``) — never ``ms-test`` with a fixture key. The
window is opened with ``sms_trust.open_test_window`` (capped, no JIT / feed,
<= 7 days, ``allowed_subs`` = exactly the three workshop test subs), the
dedicated OQB test accounts are linked by typed ``(issuer, sub)``
(``sms_sso_service.admin_link``), and tokens minted with the stand-in key go
through the real ``POST /sso/sms`` handler and ``authz``.
"""
import json
import unittest

import smskit
from dbcase import _password_hash
from ssocase import SsoCase

XHR = {'X-Requested-With': 'XMLHttpRequest'}
ALL = ('MATC', 'MAT1', 'MAT2', 'ICT', 'ECON')
V, U = 'viewer', 'user'
KID = 'sms-contract-test-workshop'
WORKSHOP = smskit.SECONDARY

# The workshop window's test accounts (V1_2_ADDENDUM_3; runbook section 5).
TEST_SUBS = {
    'viewer': ('8a7b097b-52d2-4afe-9c13-47bd5d22dfc7', 'oqb-test-viewer@tic.edu.hk', 'sso_test_viewer'),
    'maths': ('0d2b22b8-4104-4d88-8c47-7ee2eee47679', 'oqb-test-maths@tic.edu.hk', 'sso_test_maths'),
    'admin': ('877f5123-e3a2-4ffd-a09e-7f016d264e04', 'oqb-test-admin@tic.edu.hk', 'sso_test_admin'),
}


def _math_subject(level):
    s = smskit.cases()['subjects']['MATH']
    return {'uuid': s['uuid'], 'code': s['code'], 'name_en': s['name_en'], 'name_zh': s['name_zh'],
            'level': level}


class WindowCase(SsoCase):
    def setUp(self):
        super().setUp()
        from app import db, sms_clock, sms_sso_service, sms_trust
        spec = smskit.cases()['trust']['secondary']
        sms_trust.close_test_window(WORKSHOP, actor_id=self.users['super'])
        sms_trust.open_test_window(
            WORKSHOP, kid=KID, pem=smskit.pem(spec['keys'][KID]), expected_fingerprint=spec['sha256'][KID],
            allowed_subs=[{'sub': sub, 'label': email} for sub, email, _u in TEST_SUBS.values()],
            enabled_until=sms_clock.now() + 6 * 86400, actor_id=self.users['super'])
        from app.models import User
        for key, (sub, email, username) in TEST_SUBS.items():
            u = User(username=username, password_hash=_password_hash())
            db.session.add(u)
            db.session.flush()
            self.users[username] = u.id
            sms_sso_service.admin_link(u, WORKSHOP, sub, actor_id=self.users['super'], label=email)
        db.session.commit()

    def window_token(self, key=None, *, sub=None, default_level='viewer', subjects=(), now=None):
        from app import sms_clock
        now = sms_clock.now() if now is None else now
        sub_, email, _u = TEST_SUBS[key] if key else (sub, 'someone@tic.edu.hk', None)
        return smskit.mint('secondary_test_account_admin', kid=KID, sub=sub or sub_, email=email,
                           name=email.split('@')[0], default_level=default_level,
                           subjects=list(subjects), iat=now, nbf=now, exp=now + 60)

    def sign_in(self, key, **kw):
        resp = self.post_token(self.window_token(key, **kw), client=self.app.test_client())
        return resp

    def assert_no_admin(self, client):
        self.assertEqual(client.get('/admin/', headers=XHR).status_code, 403)
        self.assertEqual(client.get('/register').status_code, 403)
        for sid in ALL:
            r = client.post('/admin/topics/add', data={'subject_id': sid, 'name': 'X'}, headers=XHR)
            self.assertEqual(r.status_code, 403, sid)


class TestWorkshopWindow(WindowCase):
    def test_window_entry_is_capped_without_jit_or_feed(self):
        from app.models import SmsTrustIssuer
        row = SmsTrustIssuer.query.filter_by(issuer=WORKSHOP).one()
        self.assertEqual((row.enabled, row.is_primary, row.allow_jit, row.accepts_feed, row.level_cap,
                          row.jwks_auto), (True, False, False, False, True, False))
        self.assertEqual(sorted(i['sub'] for i in json.loads(row.allowed_subs_json)),
                         sorted(s for s, _e, _u in TEST_SUBS.values()))

    def test_1_viewer_sub_viewer_everywhere(self):
        client = self.app.test_client()
        self.assert_next(self.post_token(self.window_token('viewer'), client=client), '/')
        self.assertEqual(self.levels('sso_test_viewer'), {s: V for s in ALL})
        self.assert_no_admin(client)

    def test_2_maths_admin_capped_to_user_on_mapped_subjects(self):
        from app import db
        from app.models import SmsSubjectMap
        math = smskit.subject_uuid('MATH')
        SmsSubjectMap.query.filter_by(sms_uuid=math).delete()
        db.session.commit()
        token = lambda: self.window_token('maths', subjects=[_math_subject('admin')])  # noqa: E731
        self.assert_next(self.post_token(token(), client=self.app.test_client()), '/')
        self.assertEqual(self.levels('sso_test_maths'), {s: V for s in ALL})  # before the mapping
        for sid in ('MATC', 'MAT1', 'MAT2'):
            db.session.add(SmsSubjectMap(sms_uuid=math, oqb_subject_id=sid,
                                         confirmed_by_user_id=self.users['super']))
        db.session.commit()
        client = self.app.test_client()
        self.assert_next(self.post_token(token(), client=client), '/')
        self.assertEqual(self.levels('sso_test_maths'),
                         {'MATC': U, 'MAT1': U, 'MAT2': U, 'ICT': V, 'ECON': V})
        self.assert_no_admin(client)

    def test_3_school_admin_is_viewer_never_super(self):
        client = self.app.test_client()
        self.assert_next(self.post_token(self.window_token('admin', default_level='admin'), client=client), '/')
        self.assertEqual(self.levels('sso_test_admin'), {s: V for s in ALL})
        from app import authz
        user = self.user('sso_test_admin')
        self.assertFalse(user.is_super_admin or authz.is_super_admin(user))
        self.assert_no_admin(client)

    def test_4_real_teacher_sub_not_allowed(self):
        resp = self.post_token(self.window_token(sub=smskit.person('teacher')['sub']),
                               client=self.app.test_client())
        self.assert_next(resp, '/sso/sms/failed', 401)
        self.assertEqual(len(self.audit('sso_login', 'sub_not_allowed')), 1)

    def test_5_no_jit_for_the_non_primary_issuer(self):
        from app import db, sms_sso_service
        from app.models import User
        self.app.config['SMS_JIT_ENABLED'] = True
        sms_sso_service.unlink(self.user('sso_test_viewer'), WORKSHOP, actor_id=self.users['super'])
        db.session.commit()
        before = User.query.count()
        self.assert_next(self.sign_in('viewer'), '/sso/sms/failed', 401)
        self.assertEqual(User.query.count(), before)
        self.assertEqual(len(self.audit('sso_login', 'not_linked')), 1)

    def test_6_window_closed_disabled_expired_unpinned(self):
        from app import db, sms_clock, sms_trust
        # Expired: past enabled_until (no edit needed).
        later = sms_clock.now() + 6 * 86400 + 1
        with sms_clock.frozen(later):
            resp = self.post_token(self.window_token('viewer', now=later), client=self.app.test_client())
        self.assert_next(resp, '/sso/sms/failed', 401)
        # Kid unpinned while the entry is still enabled.
        sms_trust.unpin_key(WORKSHOP, KID, actor_id=self.users['super'])
        db.session.commit()
        self.assert_next(self.sign_in('viewer'), '/sso/sms/failed', 401)
        # Disabled.
        sms_trust.upsert_issuer(WORKSHOP, enabled=False, actor_id=self.users['super'])
        db.session.commit()
        self.assert_next(self.sign_in('viewer'), '/sso/sms/failed', 401)
        reasons = [r.reason for r in self.audit('sso_login')]
        self.assertEqual(reasons, ['issuer_disabled', 'unknown_kid', 'issuer_disabled'])

    def test_6_close_test_window_then_tokens_refused(self):
        from app import db, sms_sso_service, sms_trust
        self.assert_next(self.sign_in('viewer'), '/')
        self.assertEqual(sms_trust.close_test_window(WORKSHOP, actor_id=self.users['super']), [KID])
        linked = sms_sso_service.linked_users(WORKSHOP)
        self.assertEqual([u.username for _i, u in linked if u.username.startswith('sso_test_')],
                         ['sso_test_admin', 'sso_test_maths', 'sso_test_viewer'])
        for _identity, user in linked:
            sms_sso_service.unlink(user, WORKSHOP, actor_id=self.users['super'])
            user.active, user.disabled_reason = False, 'local'
        db.session.commit()
        self.assert_next(self.sign_in('viewer'), '/sso/sms/failed', 401)
        self.assertEqual(self.audit('sso_login')[-1].reason, 'issuer_disabled')
        self.assertEqual([r.reason for r in self.audit('test_window')], ['close', 'open', 'close'])

    def test_6_more_than_seven_days_is_refused(self):
        # Service level; test_sms_admin.TrustRouteTests checks the same refusal through the route.
        from app import sms_clock, sms_trust
        spec = smskit.cases()['trust']['secondary']
        with self.assertRaises(sms_trust.TrustEditError):
            sms_trust.upsert_issuer(WORKSHOP, enabled=True, enabled_until=sms_clock.now() + 8 * 86400)
        from app import db
        db.session.rollback()
        with self.assertRaises(sms_trust.TrustEditError):
            sms_trust.open_test_window(
                WORKSHOP, kid=KID, pem=smskit.pem(spec['keys'][KID]), expected_fingerprint=spec['sha256'][KID],
                allowed_subs=[TEST_SUBS['viewer'][0]], enabled_until=sms_clock.now() + 8 * 86400)
        db.session.rollback()

    def test_7_every_non_primary_sign_in_alerts(self):
        self.assert_next(self.sign_in('viewer'), '/')
        self.assert_next(self.sign_in('maths', subjects=[_math_subject('admin')]), '/')
        self.assert_next(self.sign_in('admin', default_level='admin'), '/')
        rows = self.audit('nonprimary_login')
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(r.is_alert and r.issuer == WORKSHOP for r in rows))
        self.assertEqual({r.user_id for r in rows},
                         {self.users[u] for _s, _e, u in TEST_SUBS.values()})

    def test_admin_link_refuses_a_sub_outside_the_window(self):
        from app import sms_sso_service
        with self.assertRaises(sms_sso_service.LinkError) as cm:
            sms_sso_service.admin_link(self.user('nogrants'), WORKSHOP, smskit.person('teacher')['sub'],
                                       actor_id=self.users['super'])
        self.assertEqual(cm.exception.reason, 'sub_not_allowed')


if __name__ == '__main__':
    unittest.main()
