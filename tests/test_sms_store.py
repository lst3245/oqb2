"""Trust list, level store and audit against oqb_sso_test (no routes)."""
import json
import unittest

import smskit
from dbcase import DbTestCase
from ssocase import SsoCase


class TrustStoreTests(DbTestCase):
    def setUp(self):
        super().setUp()
        from app import sms_clock
        self._freeze = sms_clock.frozen(smskit.REFERENCE_NOW)
        self._freeze.__enter__()
        self.addCleanup(self._freeze.__exit__, None, None, None)

    def test_seed_defaults_twice_live_keyless_workshop_disabled(self):
        from app import sms_trust
        from app.models import SmsTrustIssuer, SmsTrustKey
        self.assertEqual(sms_trust.seed_defaults(), 2)
        self.assertEqual(sms_trust.seed_defaults(), 0)
        live = SmsTrustIssuer.query.filter_by(issuer=sms_trust.LIVE_ISSUER).one()
        shop = SmsTrustIssuer.query.filter_by(issuer=sms_trust.WORKSHOP_ISSUER).one()
        self.assertTrue(live.enabled and live.is_primary and live.allow_jit and live.accepts_feed)
        self.assertFalse(shop.enabled or shop.is_primary or shop.allow_jit or shop.accepts_feed)
        self.assertTrue(shop.level_cap)
        self.assertEqual(SmsTrustKey.query.count(), 0)  # the live key is never auto-pinned
        tl = sms_trust.load_trust_list()
        self.assertEqual(tl.primary(smskit.REFERENCE_NOW).issuer, sms_trust.LIVE_ISSUER)
        self.assertFalse(tl.get(sms_trust.WORKSHOP_ISSUER).active(smskit.REFERENCE_NOW))

    def test_pin_requires_matching_fingerprint(self):
        from app import db, sms_trust
        from app.models import AuthAuditLog
        sms_trust.seed_defaults()
        spec = smskit.cases()['trust']['primary']
        pem = smskit.pem(spec['keys']['sms-contract-test'])
        with self.assertRaises(sms_trust.TrustEditError):
            sms_trust.pin_key(sms_trust.WORKSHOP_ISSUER, 'k1', pem, expected_fingerprint='00' * 32)
        sms_trust.pin_key(sms_trust.WORKSHOP_ISSUER, 'k1', pem,
                          expected_fingerprint=':'.join([spec['sha256']['sms-contract-test'].upper()]))
        with self.assertRaises(sms_trust.TrustEditError):
            sms_trust.pin_key(sms_trust.WORKSHOP_ISSUER, 'k1', pem,
                              expected_fingerprint=spec['sha256']['sms-contract-test'])
        with self.assertRaises(sms_trust.TrustEditError):
            sms_trust.pin_key(sms_trust.WORKSHOP_ISSUER, 'k2', 'not a pem', expected_fingerprint='x')
        db.session.commit()
        self.assertTrue(sms_trust.unpin_key(sms_trust.WORKSHOP_ISSUER, 'k1'))
        self.assertFalse(sms_trust.unpin_key(sms_trust.WORKSHOP_ISSUER, 'k1'))
        db.session.commit()
        events = [r.event for r in AuthAuditLog.query.order_by(AuthAuditLog.id)]
        self.assertEqual(events, ['trust_pin', 'trust_unpin'])

    def test_upsert_guards(self):
        from app import db, sms_trust
        sms_trust.seed_defaults()
        now = smskit.REFERENCE_NOW
        with self.assertRaises(sms_trust.TrustEditError):  # no enabled_until
            sms_trust.upsert_issuer(sms_trust.WORKSHOP_ISSUER, enabled=True)
        db.session.rollback()
        with self.assertRaises(sms_trust.TrustEditError):  # more than 7 days
            sms_trust.upsert_issuer(sms_trust.WORKSHOP_ISSUER, enabled=True, enabled_until=now + 7 * 86400 + 1)
        db.session.rollback()
        with self.assertRaises(sms_trust.TrustEditError):
            sms_trust.upsert_issuer(sms_trust.WORKSHOP_ISSUER, allowed_subs=['not-a-uuid'])
        db.session.rollback()
        with self.assertRaises(sms_trust.TrustEditError):  # a second primary
            sms_trust.upsert_issuer(sms_trust.WORKSHOP_ISSUER, is_primary=True)
        db.session.rollback()
        with self.assertRaises(sms_trust.TrustEditError):
            sms_trust.upsert_issuer('http://insecure.example')
        db.session.rollback()
        self.app.config['OQB_ENVIRONMENT'] = 'production'
        with self.assertRaises(sms_trust.TrustEditError):
            sms_trust.upsert_issuer(smskit.PRIMARY, enabled=True)
        db.session.rollback()

    def test_open_and_close_test_window(self):
        from app import db, sms_trust
        from app.models import AuthAuditLog, SmsTrustIssuer
        sms_trust.seed_defaults()
        spec = smskit.cases()['trust']['secondary']
        pem = smskit.pem(spec['keys']['sms-contract-test-workshop'])
        fp = spec['sha256']['sms-contract-test-workshop']
        sub = smskit.person('test_account')['sub']
        now = smskit.REFERENCE_NOW
        with self.assertRaises(sms_trust.TrustEditError):
            sms_trust.open_test_window(sms_trust.WORKSHOP_ISSUER, kid='sms-test-202610', pem=pem,
                                       expected_fingerprint='ab' * 32, allowed_subs=[sub],
                                       enabled_until=now + 86400)
        db.session.rollback()
        with self.assertRaises(sms_trust.TrustEditError):
            sms_trust.open_test_window(sms_trust.LIVE_ISSUER, kid='x', pem=pem, expected_fingerprint=fp,
                                       allowed_subs=[sub], enabled_until=now + 86400)
        db.session.rollback()
        with self.assertRaises(sms_trust.TrustEditError):
            sms_trust.open_test_window(sms_trust.WORKSHOP_ISSUER, kid='sms-test-202610', pem=pem,
                                       expected_fingerprint=fp, allowed_subs=[sub],
                                       enabled_until=now + 8 * 86400)
        db.session.rollback()
        sms_trust.open_test_window(sms_trust.WORKSHOP_ISSUER, kid='sms-test-202610', pem=pem,
                                   expected_fingerprint=fp,
                                   allowed_subs=[{'sub': sub, 'label': 'oqb-test-viewer'}],
                                   enabled_until=now + 6 * 86400, actor_id=1)
        db.session.commit()
        row = SmsTrustIssuer.query.filter_by(issuer=sms_trust.WORKSHOP_ISSUER).one()
        self.assertTrue(row.enabled and row.level_cap)
        self.assertFalse(row.is_primary or row.allow_jit or row.accepts_feed or row.jwks_auto)
        self.assertEqual(sms_trust.allowed_sub_items(row), [{'sub': sub, 'label': 'oqb-test-viewer'}])
        entry = sms_trust.load_trust_list().get(sms_trust.WORKSHOP_ISSUER)
        self.assertTrue(entry.active(now))
        self.assertEqual(set(entry.keys), {'sms-test-202610'})
        self.assertEqual(sms_trust.close_test_window(sms_trust.WORKSHOP_ISSUER, actor_id=1), ['sms-test-202610'])
        db.session.commit()
        entry = sms_trust.load_trust_list().get(sms_trust.WORKSHOP_ISSUER)
        self.assertFalse(entry.active(now))
        self.assertEqual(entry.keys, {})
        reasons = [r.reason for r in AuthAuditLog.query.filter_by(event='test_window').order_by(AuthAuditLog.id)]
        self.assertEqual(reasons, ['open', 'close'])

    def test_trust_list_cache_invalidated_on_edit(self):
        from app import db, sms_trust
        sms_trust.seed_defaults()
        first = sms_trust.load_trust_list()
        self.assertIs(sms_trust.load_trust_list(), first)
        sms_trust.upsert_issuer(sms_trust.LIVE_ISSUER, enabled=False)
        db.session.commit()
        self.assertIsNone(sms_trust.load_trust_list().primary(smskit.REFERENCE_NOW))


class LevelStoreTests(SsoCase):
    def put_levels(self, username, issuer, default, subjects):
        from app import db, sms_sso_service
        sms_sso_service.replace_levels(self.users[username], issuer, default,
                                       [(smskit.subject_uuid(c), c, lv) for c, lv in subjects], 'handoff')
        db.session.commit()

    def test_levels_counted_only_when_sso_on_and_schema_ready(self):
        self.put_levels('panel', smskit.PRIMARY, 'viewer', [('MATH', 'admin')])
        self.assertEqual(self.levels('panel')['MAT2'], 'admin')
        self.app.config['SMS_SSO_ENABLED'] = False
        self.assertEqual(self.levels('panel'), {})
        self.app.config['SMS_SSO_ENABLED'] = True
        self.app.config['SMS_SCHEMA_READY'] = False
        self.assertEqual(self.levels('panel'), {})

    def test_replace_not_merge(self):
        self.put_levels('panel', smskit.PRIMARY, 'viewer', [('MATH', 'admin')])
        self.put_levels('panel', smskit.PRIMARY, None, [('PHY', 'user')])
        self.assertEqual(self.levels('panel'), {'ICT': 'user'})

    def test_stored_non_primary_levels_are_capped(self):
        self.put_levels('tester', smskit.SECONDARY, 'admin', [('MATH', 'admin')])
        levels = self.levels('tester')
        self.assertEqual(levels['ECON'], 'viewer')
        self.assertEqual(levels['MATC'], 'user')

    def test_many_to_one_highest_wins(self):
        self.put_levels('panel', smskit.PRIMARY, None, [('MATH', 'user'), ('M2', 'admin')])
        levels = self.levels('panel')
        self.assertEqual((levels['MAT1'], levels['MAT2']), ('user', 'admin'))

    def test_authz_failure_falls_back_to_local(self):
        from unittest import mock
        from app import sms_trust
        self.put_levels('teacher_a', smskit.PRIMARY, 'admin', [])
        with mock.patch.object(sms_trust, 'load_trust_list', side_effect=RuntimeError('x')):
            self.assertEqual(self.levels('teacher_a'), {'MATC': 'user', 'ICT': 'viewer'})

    def test_audit_record_rolls_back_record_now_survives(self):
        from app import auth_audit, db
        auth_audit.record(auth_audit.LINK, 'ok', issuer=smskit.PRIMARY, detail={'t': 'a.' * 3})
        db.session.rollback()
        auth_audit.record_now(auth_audit.JWKS_NEW_KID, 'info', alert=True,
                              detail={'seen': 'eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJl'})
        self.assertEqual(self.audit('link'), [])
        [row] = self.audit('jwks_new_kid')
        self.assertTrue(row.is_alert)
        self.assertIn('[token]', row.detail)
        self.assertNotIn('c2lnbmF0dXJl', row.detail)

    def test_password_change_is_audited(self):
        self.login('super')
        resp = self.client.post(f"/admin/users/{self.users['nogrants']}/edit",
                                headers={'X-Requested-With': 'XMLHttpRequest'},
                                data={'username': 'nogrants', 'password': 'new-password-1'})
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True)[:200])
        [row] = self.audit('password_change')
        self.assertEqual((row.user_id, row.actor_user_id), (self.users['nogrants'], self.users['super']))
        self.assertEqual(json.loads(row.detail), {'self': False})


class AdminLinkTests(SsoCase):
    def user(self, name):
        from app import db
        from app.models import User
        return db.session.get(User, self.users[name])

    def test_typed_link_rules(self):
        from app import db, sms_sso_service as svc
        sub = smskit.person('unlinked')['sub']
        cases = [
            ((self.user('nogrants'), smskit.PRIMARY, 'nope'), 'bad_sub'),
            ((self.user('nogrants'), 'https://other.example', sub), 'unknown_issuer'),
            ((self.user('nogrants'), smskit.SECONDARY, sub), 'sub_not_allowed'),
            ((self.user('super'), smskit.PRIMARY, sub), 'privileged_account'),
            ((self.user('breakglass'), smskit.PRIMARY, sub), 'privileged_account'),
            ((self.user('panel'), smskit.PRIMARY, sub), 'already_linked'),
            ((self.user('nogrants'), smskit.PRIMARY, smskit.person('panel_admin')['sub']), 'already_linked'),
        ]
        for args, reason in cases:
            with self.subTest(reason=reason, user=args[0].username):
                with self.assertRaises(svc.LinkError) as cm:
                    svc.admin_link(*args, actor_id=self.users['super'])
                self.assertEqual(cm.exception.reason, reason)
                db.session.rollback()
        svc.admin_link(self.user('nogrants'), smskit.PRIMARY, sub.upper(), actor_id=self.users['super'])
        db.session.commit()
        self.assertEqual([u.username for _i, u in svc.linked_users(smskit.PRIMARY)],
                         ['access', 'nogrants', 'panel', 'school', 'teacher_a'])

    def test_typed_link_for_allowed_test_sub(self):
        from app import db, sms_sso_service as svc
        from app.models import ExternalIdentity
        ExternalIdentity.query.filter_by(issuer=smskit.SECONDARY).delete()
        db.session.commit()
        svc.admin_link(self.user('nogrants'), smskit.SECONDARY, smskit.person('test_account')['sub'],
                       actor_id=self.users['super'], label='oqb-test-admin')
        db.session.commit()
        self.assert_next(self.submit('secondary_test_account_admin'), '/')
        self.assertTrue(self.session_user().startswith(f"{self.users['nogrants']}:"))

    def test_unlink_ends_sessions_and_drops_levels(self):
        from app import db, sms_sso_service as svc
        self.assert_next(self.submit('panel_admin'), '/')
        self.assertEqual(self.client.get('/dashboard/').status_code, 200)
        self.assertTrue(svc.unlink(self.user('panel'), smskit.PRIMARY, actor_id=self.users['super']))
        db.session.commit()
        self.assertEqual(self.levels('panel'), {})
        self.assertEqual(self.client.get('/dashboard/').status_code, 302)
        self.assertEqual(len(self.audit('unlink')), 1)
        self.assertFalse(svc.unlink(self.user('panel'), smskit.PRIMARY))

    def test_purge(self):
        from app import db, sms_clock, sms_sso_service as svc
        from app.models import SsoUsedJti
        self.assert_next(self.submit('teacher_viewer_user'), '/')
        self.assertEqual(svc.purge(smskit.REFERENCE_NOW + 3600), 0)
        self.assertEqual(svc.purge(smskit.REFERENCE_NOW + 86400 + 60), 1)
        db.session.expire_all()
        self.assertEqual(SsoUsedJti.query.count(), 0)


if __name__ == '__main__':
    unittest.main()
