"""Super-admin screens for SMS sign-in (stage 5) on oqb_sso_test: every page
and every POST of ``app/sms_admin.py`` and ``app/account_admin.py`` through
the real routes, AT03 (mapping removal) and AT08 (admin link) through the UI
routes, permissions, the users page and the nav alert badge."""
import json
import re
import unittest

import smskit
from dbcase import sign_in
from test_sms_feed import BRAKE, FeedCase
from test_sms_workshop_window import KID, TEST_SUBS, WORKSHOP, WindowCase

V, U = 'viewer', 'user'
ALL = ('MATC', 'MAT1', 'MAT2', 'ICT', 'ECON')
PAGES = ('/admin/sms/', '/admin/sms/mapping', '/admin/sms/linking', '/admin/sms/linking?show=all',
         '/admin/sms/snapshots', '/admin/sms/trust', '/admin/sms/audit', '/admin/sms/audit?alerts=1')
# Every POST endpoint of the two blueprints; RoutePermissionTests fails when a
# new one appears without being listed (and so without a test below).
POST_ENDPOINTS = {
    'sms_admin.mapping_add', 'sms_admin.mapping_remove', 'sms_admin.link', 'sms_admin.unlink',
    'sms_admin.snapshot_approve', 'sms_admin.snapshot_discard', 'sms_admin.trust_edit',
    'sms_admin.trust_pin', 'sms_admin.trust_unpin', 'sms_admin.trust_window_open',
    'sms_admin.trust_window_close', 'sms_admin.trust_disable_linked', 'sms_admin.audit_ack',
    'sms_admin.audit_ack_all', 'account_admin.revoke_sessions', 'account_admin.set_active',
    'account_admin.set_local_only',
}


class AdminMixin:
    def as_user(self, name):
        client = self.app.test_client()
        sign_in(client, self.user(name))
        return client

    @property
    def su(self):
        if not hasattr(self, '_su'):
            self._su = self.as_user('super')
        return self._su

    @staticmethod
    def _fresh_txn():
        """The test keeps one app context (and session) across requests; a
        real request starts a new transaction, so drop the old snapshot."""
        from app import db
        db.session.rollback()

    def post(self, url, data=None, client=None):
        self._fresh_txn()
        resp = (client or self.su).post(url, data=data or {})
        self.assertIn(resp.status_code, (200, 302), resp.get_data(as_text=True)[:300])
        return resp

    def post_follow(self, url, data=None):
        self._fresh_txn()
        return (self.su.post(url, data=data or {}, follow_redirects=True)).get_data(as_text=True)

    def page(self, url, client=None):
        self._fresh_txn()
        resp = (client or self.su).get(url)
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True)[:500])
        return resp.get_data(as_text=True)

    def issuer_id(self, issuer):
        from app import db
        from app.models import SmsTrustIssuer
        db.session.rollback()
        return SmsTrustIssuer.query.filter_by(issuer=issuer).one().id

    def maps(self, code='MATH'):
        from app import db
        from app.models import SmsSubjectMap
        db.session.rollback()
        return sorted(m.oqb_subject_id for m in SmsSubjectMap.query.filter_by(sms_uuid=smskit.subject_uuid(code)))


class AdminCase(AdminMixin, FeedCase):
    pass


# ---------------------------------------------------------------- permissions

class RoutePermissionTests(AdminCase):
    def _post_rules(self):
        return [r for r in self.app.url_map.iter_rules()
                if r.endpoint.split('.')[0] in ('sms_admin', 'account_admin') and 'POST' in r.methods]

    def _url(self, rule):
        return rule.rule.replace('<int:snapshot_id>', '1').replace('<int:issuer_id>', '1') \
            .replace('<int:row_id>', '1').replace('<int:user_id>', str(self.users['teacher_a']))

    def test_every_post_endpoint_is_listed(self):
        self.assertEqual({r.endpoint for r in self._post_rules()}, POST_ENDPOINTS)

    def test_pages_are_super_admin_only(self):
        anon = self.app.test_client()
        for name in ('admin_matc', 'teacher_a', 'nogrants'):
            client = self.as_user(name)
            for url in PAGES:
                self.assertEqual(client.get(url).status_code, 403, (name, url))
        for url in PAGES:
            self.assertIn('/login', anon.get(url).headers.get('Location', ''), url)
            self.page(url)

    def test_posts_are_super_admin_only(self):
        from app import db
        from app.models import AuthAuditLog
        before = AuthAuditLog.query.count()
        anon = self.app.test_client()
        for name in ('admin_matc', 'teacher_a'):
            client = self.as_user(name)
            for rule in self._post_rules():
                url = self._url(rule)
                self.assertEqual(client.post(url, data={'active': '0', 'sms_uuid': smskit.subject_uuid('MATH'),
                                                        'subject_id': 'MAT1'}).status_code, 403, (name, url))
                self.assertIn('/login', anon.post(url).headers.get('Location', ''), url)
        db.session.rollback()
        self.assertEqual(self.maps(), ['MAT1', 'MAT2', 'MATC'])
        self.assertTrue(self.user('teacher_a').active)
        self.assertEqual(AuthAuditLog.query.count(), before)

    def test_schema_not_ready(self):
        self.app.config['SMS_SCHEMA_READY'] = False
        self.assertIn('sms-schema-not-ready', self.page('/admin/sms/'))
        self.assertEqual(self.su.get('/admin/sms/mapping').status_code, 503)
        self.assertEqual(self.su.post('/admin/sms/mapping/add').status_code, 503)
        self.page('/admin/')  # the nav badge does not query
        self.page('/admin/users')


# ---------------------------------------------------------------- overview / nav

class OverviewTests(AdminCase):
    def test_overview_counts_and_break_glass_warning(self):
        self.push('snapshot_baseline')
        html = self.page('/admin/sms/')
        self.assertNotIn('sms-no-break-glass', html)
        self.assertIn(smskit.PRIMARY, html)
        from app import db
        for name in ('super', 'breakglass'):
            self.user(name).password_hash = '!unusable'
        db.session.commit()
        self.assertIn('sms-no-break-glass', self.page('/admin/sms/'))

    def test_alert_badge_for_super_admins_only(self):
        from app import auth_audit
        self.assertNotIn('nav-sms-alerts', self.page('/admin/'))
        auth_audit.record_now(auth_audit.NONPRIMARY_LOGIN, 'ok', issuer=smskit.SECONDARY, alert=True)
        self.assertIn('nav-sms-alerts', self.page('/admin/'))
        self.assertNotIn('nav-sms-alerts', self.page('/admin/', client=self.as_user('admin_matc')))
        self.assertIn('href="/admin/sms/"', self.page('/admin/'))


# ---------------------------------------------------------------- mapping

class MappingRouteTests(AdminCase):
    def test_add_and_remove_are_audited(self):
        chem = smskit.subject_uuid('CHEM')
        self.push('snapshot_baseline')
        html = self.page('/admin/sms/mapping')
        self.assertIn('sms-unmapped', html)
        self.assertIn(f'id="sms-subject-{chem}" class="table-warning"', html)
        self.post('/admin/sms/mapping/add', {'sms_uuid': chem, 'subject_id': 'ECON'})
        self.post('/admin/sms/mapping/add', {'sms_uuid': chem, 'subject_id': 'ECON'})  # no duplicate
        self.assertEqual(self.maps('CHEM'), ['ECON'])
        from app.models import SmsSubjectMap
        row = SmsSubjectMap.query.filter_by(sms_uuid=chem).one()
        self.assertEqual(row.confirmed_by_user_id, self.users['super'])
        self.assertNotIn('sms-unmapped', self.page('/admin/sms/mapping'))
        self.post('/admin/sms/mapping/remove', {'sms_uuid': chem, 'subject_id': 'ECON'})
        self.assertEqual(self.maps('CHEM'), [])
        rows = self.audit('sms_mapping')
        self.assertEqual([(r.reason, r.actor_user_id) for r in rows],
                         [('add', self.users['super']), ('remove', self.users['super'])])
        self.assertEqual(json.loads(rows[0].detail)['subject'], 'ECON')

    def test_bad_input_is_refused(self):
        html = self.post_follow('/admin/sms/mapping/add', {'sms_uuid': 'not-a-uuid', 'subject_id': 'MATC'})
        self.assertIn('Choose an SMS subject and an OQB subject.', html)
        self.post('/admin/sms/mapping/add', {'sms_uuid': smskit.subject_uuid('CHEM'), 'subject_id': 'NOPE'})
        self.assertEqual(self.maps('CHEM'), [])
        self.assertEqual(self.audit('sms_mapping'), [])


class TestAT03MappingRemovalViaRoute(AdminCase):
    def test_at03_remove_mapping_through_the_admin_route(self):
        self.assert_next(self.submit('teacher_viewer_user'), '/')
        levels = self.levels('teacher_a')
        self.assertEqual([levels[s] for s in ('MATC', 'MAT1', 'MAT2')], [U, U, U])
        self.post('/admin/sms/mapping/remove', {'sms_uuid': smskit.subject_uuid('MATH'), 'subject_id': 'MAT1'})
        self.assertEqual(self.maps(), ['MAT2', 'MATC'])
        levels = self.levels('teacher_a')
        self.assertEqual((levels['MAT1'], levels['MAT2']), (V, U))


# ---------------------------------------------------------------- linking

class TestAT08LinkViaRoute(AdminCase):
    def test_at08_link_from_the_roster_page_then_handoff(self):
        from app import db
        self.push('snapshot_baseline')
        sub = smskit.person('unlinked')['sub']
        nogrants = self.user('nogrants')
        nogrants.staff_code = 'HPL'
        db.session.commit()
        html = self.page('/admin/sms/linking')
        row = html.split(f'id="roster-{sub}"', 1)[1].split('</tr>', 1)[0]
        self.assertIn(f'name="user_id" value="{self.users["nogrants"]}"', row)  # suggested by staff code
        self.post('/admin/sms/link', {'issuer': smskit.PRIMARY, 'sub': sub, 'user_id': self.users['nogrants']})
        self.assertEqual(self.levels('nogrants'), {s: V for s in ALL})
        [row] = self.audit('link', 'admin')
        self.assertEqual((row.user_id, row.actor_user_id), (self.users['nogrants'], self.users['super']))
        self.assertNotIn(f'id="roster-{sub}"', self.page('/admin/sms/linking'))  # linked: hidden
        self.assertIn(f'id="roster-{sub}"', self.page('/admin/sms/linking?show=all'))
        self.assert_next(self.submit('unlinked_teacher'), '/')
        self.assertTrue(self.session_user().startswith(f"{self.users['nogrants']}:"))


class LinkRouteTests(AdminCase):
    def test_refusals_are_shown_and_audited(self):
        from app.models import ExternalIdentity
        sub = smskit.person('unlinked')['sub']
        cases = [
            ({'issuer': smskit.PRIMARY, 'sub': sub, 'user_id': self.users['breakglass']},
             'Super admin and local-only accounts cannot be linked to SMS.', 'privileged_account'),
            ({'issuer': smskit.PRIMARY, 'sub': 'nope', 'user_id': self.users['nogrants']},
             'The SMS sub must be a UUID.', 'bad_sub'),
            ({'issuer': smskit.PRIMARY, 'sub': smskit.person('teacher')['sub'], 'user_id': self.users['nogrants']},
             'That SMS account is already linked to an OQB account.', 'already_linked'),
            ({'issuer': smskit.PRIMARY, 'sub': sub, 'user_id': self.users['teacher_a']},
             'This OQB account is already linked for that issuer.', 'already_linked'),
            ({'issuer': 'https://unknown.invalid', 'sub': sub, 'user_id': self.users['nogrants']},
             'That issuer is not on the trust list.', 'unknown_issuer'),
            ({'issuer': smskit.PRIMARY, 'sub': sub, 'user_id': ''}, 'Choose an OQB account.', 'no_user'),
        ]
        for data, message, reason in cases:
            html = self.post_follow('/admin/sms/link', data)
            self.assertIn(message.replace("'", '&#39;'), html, reason)
        self.assertIsNone(ExternalIdentity.query.filter_by(sub=sub).first())
        self.assertEqual([r.reason for r in self.audit('link', None) if r.outcome == 'fail'],
                         [c[2] for c in cases])

    def test_unlink_ends_sessions_and_drops_levels(self):
        from app.models import ExternalIdentity, UserSmsLevel
        teacher = self.app.test_client()
        self.assert_next(self.submit('teacher_viewer_user', client=teacher), '/')
        version = self.user('teacher_a').session_version
        resp = self.post('/admin/sms/unlink', {'issuer': smskit.PRIMARY, 'user_id': self.users['teacher_a'],
                                               'next': 'users'})
        self.assertTrue(resp.headers['Location'].endswith('/admin/users'))
        self.assertEqual(self.user('teacher_a').session_version, version + 1)
        self.assertIsNone(ExternalIdentity.query.filter_by(user_id=self.users['teacher_a']).first())
        self.assertEqual(UserSmsLevel.query.filter_by(user_id=self.users['teacher_a']).count(), 0)
        self.assertIn('/login', teacher.get('/').headers['Location'])
        self.assertEqual(len(self.audit('unlink')), 1)
        html = self.post_follow('/admin/sms/unlink', {'issuer': smskit.PRIMARY, 'user_id': self.users['teacher_a']})
        self.assertIn('That link no longer exists.', html)


# ---------------------------------------------------------------- snapshots

class SnapshotRouteTests(AdminCase):
    def _held(self):
        self.replay_until('snapshot_brake_trip')
        [held] = self.snapshot_rows('held')
        html = self.page('/admin/sms/snapshots')
        self.assertIn(f'id="held-{held.id}"', html)
        return held

    def test_approve_held_snapshot_via_route(self):
        held = self._held()
        html = self.post_follow(f'/admin/sms/snapshots/{held.id}/approve')
        self.assertIn('Approved: 10 account(s) disabled.', html)
        for person in BRAKE:
            u = self.fresh(person)
            self.assertEqual((u.active, u.disabled_reason), (False, 'sms_deprovisioned'))
        self.assertEqual(self.snapshot_rows('approved')[0].decided_by_user_id, self.users['super'])
        html = self.post_follow(f'/admin/sms/snapshots/{held.id}/approve')
        self.assertIn('That snapshot is no longer held.', html)
        self.assertNotIn(f'id="held-{held.id}"', self.page('/admin/sms/snapshots'))

    def test_discard_held_snapshot_via_route(self):
        held = self._held()
        self.post(f'/admin/sms/snapshots/{held.id}/discard')
        self.assertTrue(all(self.fresh(p).active for p in BRAKE))
        self.assertEqual(self.snapshot_rows('discarded')[0].id, held.id)
        self.assertEqual(self.audit('feed_decision')[-1].reason, 'discard')


# ---------------------------------------------------------------- trust list

class TrustRouteTests(AdminMixin, WindowCase):
    def spec(self):
        return smskit.cases()['trust']['secondary']

    def window_form(self, days='3', fingerprint=None, subs=None):
        spec = self.spec()
        return {'kid': KID, 'pem': smskit.pem(spec['keys'][KID]),
                'fingerprint': fingerprint or spec['sha256'][KID], 'enabled_days': days,
                'allowed_subs': subs if subs is not None else '\n'.join(f'{s} {e}' for s, e, _u in TEST_SUBS.values())}

    def row(self, issuer=WORKSHOP):
        from app import db
        from app.models import SmsTrustIssuer
        db.session.rollback()
        return SmsTrustIssuer.query.filter_by(issuer=issuer).one()

    def test_trust_page_lists_entries_keys_and_test_accounts(self):
        html = self.page('/admin/sms/trust')
        for text in (smskit.PRIMARY, WORKSHOP, KID, 'sso_test_viewer', 'oqb-test-viewer@tic.edu.hk'):
            self.assertIn(text, html)

    def test_edit_more_than_seven_days_is_refused_by_the_route(self):
        """V1_2_ADDENDUM_3 item 6, through the trust admin route."""
        wid = self.issuer_id(WORKSHOP)
        before = self.row().enabled_until
        subs = '\n'.join(f'{s} {e}' for s, e, _u in TEST_SUBS.values())
        html = self.post_follow(f'/admin/sms/trust/{wid}/edit',
                                {'enabled': '1', 'enabled_days': '8', 'allowed_subs': subs})
        self.assertIn('Not saved: a non-primary issuer needs enabled_until within 7 days', html)
        self.assertEqual(self.row().enabled_until, before)
        self.assertIn('Not saved', self.post_follow(f'/admin/sms/trust/{wid}/window/open',
                                                    self.window_form(days='8')))
        self.assertEqual(self.row().enabled_until, before)
        self.post(f'/admin/sms/trust/{wid}/edit', {'enabled': '1', 'enabled_days': '2', 'allowed_subs': subs})
        row = self.row()
        from app import sms_clock
        self.assertEqual(sms_clock.to_ts(row.enabled_until), sms_clock.now() + 2 * 86400)
        self.assertEqual(self.audit('trust_edit')[-1].actor_user_id, self.users['super'])

    def test_edit_allowed_subs_with_labels(self):
        wid = self.issuer_id(WORKSHOP)
        sub = TEST_SUBS['viewer'][0]
        html = self.post_follow(f'/admin/sms/trust/{wid}/edit',
                                {'enabled': '1', 'allowed_subs': 'not-a-uuid label'})
        self.assertIn('Not saved: not a UUID: not-a-uuid', html)
        self.post(f'/admin/sms/trust/{wid}/edit',
                  {'enabled': '1', 'allowed_subs': f'# viewer only\n{sub.upper()} viewer account\n'})
        from app import sms_trust
        self.assertEqual(sms_trust.allowed_sub_items(self.row()), [{'sub': sub, 'label': 'viewer account'}])

    def test_primary_edit_keeps_it_primary(self):
        pid = self.issuer_id(smskit.PRIMARY)
        self.post(f'/admin/sms/trust/{pid}/edit', {'enabled': '1', 'accepts_feed': '1'})
        row = self.row(smskit.PRIMARY)
        self.assertEqual((row.is_primary, row.enabled, row.accepts_feed, row.allow_jit, row.jwks_auto),
                         (True, True, True, False, False))

    def test_pin_requires_the_typed_fingerprint_then_unpin(self):
        wid = self.issuer_id(WORKSHOP)
        self.post(f'/admin/sms/trust/{wid}/unpin', {'kid': KID})
        self.assertEqual([k.kid for k in self.row().keys], [])
        spec = self.spec()
        form = {'kid': KID, 'pem': smskit.pem(spec['keys'][KID]), 'fingerprint': '00' * 32}
        html = self.post_follow(f'/admin/sms/trust/{wid}/pin', form)
        self.assertIn('Not saved: the fingerprint does not match this key', html)
        self.assertEqual([k.kid for k in self.row().keys], [])
        form['fingerprint'] = spec['sha256'][KID].upper()
        self.post(f'/admin/sms/trust/{wid}/pin', form)
        self.assertEqual([k.kid for k in self.row().keys], [KID])
        self.assertIn('Not saved: that kid is not pinned',
                      self.post_follow(f'/admin/sms/trust/{wid}/unpin', {'kid': 'nope'}))
        last = self.audit('trust_pin')[-1]
        self.assertEqual((last.actor_user_id, json.loads(last.detail)['fingerprint']),
                         (self.users['super'], spec['sha256'][KID].lower()))

    def test_open_window_via_route(self):
        wid = self.issuer_id(WORKSHOP)
        self.post(f'/admin/sms/trust/{wid}/window/close')
        self.assertIn('Not saved: the fingerprint does not match this key',
                      self.post_follow(f'/admin/sms/trust/{wid}/window/open', self.window_form(fingerprint='ab' * 32)))
        self.assertFalse(self.row().enabled)
        self.post(f'/admin/sms/trust/{wid}/window/open', self.window_form())
        row = self.row()
        self.assertEqual((row.enabled, row.is_primary, row.allow_jit, row.accepts_feed, row.level_cap,
                          row.jwks_auto), (True, False, False, False, True, False))
        self.assertEqual([k.kid for k in row.keys], [KID])
        self.assert_next(self.sign_in('viewer'), '/')
        self.assertEqual(self.audit('test_window')[-1].actor_user_id, self.users['super'])

    def test_close_window_unlinks_and_disables_test_accounts(self):
        self.assert_next(self.sign_in('viewer'), '/')
        wid = self.issuer_id(WORKSHOP)
        html = self.post_follow(f'/admin/sms/trust/{wid}/window/close', {'disable_accounts': '1'})
        # The three sso_test_* accounts plus ssocase's 'tester'.
        self.assertIn('Test window closed: 1 key(s) unpinned, 4 account(s) unlinked and disabled.', html)
        row = self.row()
        self.assertEqual((row.enabled, list(row.keys)), (False, []))
        from app import sms_sso_service
        self.assertEqual(sms_sso_service.linked_users(WORKSHOP), [])
        for username in [u for _s, _e, u in TEST_SUBS.values()] + ['tester']:
            u = self.user(username)
            self.assertEqual((u.active, u.disabled_reason), (False, 'local'))
        self.assertTrue(self.user('teacher_a').active)
        self.assert_next(self.sign_in('viewer'), '/sso/sms/failed', 401)
        self.assertEqual(len(self.audit('account_state', 'disabled')), 4)

    def test_close_window_without_disabling_then_bulk_disable(self):
        wid = self.issuer_id(WORKSHOP)
        self.post(f'/admin/sms/trust/{wid}/window/close')
        from app import sms_sso_service
        self.assertEqual(len(sms_sso_service.linked_users(WORKSHOP)), 4)
        self.post(f'/admin/sms/trust/{wid}/linked/disable')
        self.assertEqual(sms_sso_service.linked_users(WORKSHOP), [])
        self.assertFalse(self.user('sso_test_maths').active)

    def test_bulk_disable_refused_for_the_primary(self):
        pid = self.issuer_id(smskit.PRIMARY)
        html = self.post_follow(f'/admin/sms/trust/{pid}/linked/disable')
        self.assertIn('Not saved: accounts of the primary issuer are never disabled in bulk', html)
        from app import sms_sso_service
        self.assertEqual(len(sms_sso_service.linked_users(smskit.PRIMARY)), 4)
        self.assertTrue(self.user('teacher_a').active)
        self.assertIn('Not saved: test windows are for a non-primary issuer only',
                      self.post_follow(f'/admin/sms/trust/{pid}/window/close'))
        self.assertTrue(self.row(smskit.PRIMARY).enabled)

    def test_unknown_issuer_is_404(self):
        self.assertEqual(self.su.post('/admin/sms/trust/999999/edit').status_code, 404)

    def test_nonprimary_sign_in_alert_is_shown_and_acknowledged(self):
        self.assert_next(self.sign_in('viewer'), '/')
        html = self.page('/admin/sms/audit')
        alerts = html.split('id="sms-open-alerts"', 1)[1].split('</table>', 1)[0]
        self.assertIn('nonprimary_login', alerts)
        self.assertIn(WORKSHOP, alerts)
        self.assertIn('nav-sms-alerts', self.page('/admin/'))
        from app import sms_admin
        before = sms_admin.open_alerts()
        [row] = self.audit('nonprimary_login')
        self.post(f'/admin/sms/audit/{row.id}/ack')
        [row] = self.audit('nonprimary_login')
        self.assertEqual(row.acknowledged_by, self.users['super'])
        self.assertIsNotNone(row.acknowledged_at)
        self.assertEqual(sms_admin.open_alerts(), before - 1)


# ---------------------------------------------------------------- audit

class AuditRouteTests(AdminCase):
    def test_filter_and_ack_all(self):
        from app import auth_audit
        for _ in range(2):
            auth_audit.record_now(auth_audit.JWKS_NEW_KID, 'info', issuer=smskit.PRIMARY, alert=True)
        auth_audit.record_now(auth_audit.PASSWORD_LOGIN, 'ok', user_id=self.users['teacher_a'])
        html = self.page('/admin/sms/audit?event=password_login')
        self.assertIn('password_login', html)
        self.assertNotIn('<code>jwks_new_kid</code>', html.split('id="sms-audit"', 1)[1])
        from app import sms_admin
        n = sms_admin.open_alerts()
        self.assertGreaterEqual(n, 2)
        self.assertIn(f'{n} alert(s) acknowledged.', self.post_follow('/admin/sms/audit/ack-all'))
        self.assertTrue(all(r.acknowledged_by == self.users['super'] for r in self.audit('jwks_new_kid')))
        self.assertNotIn('sms-open-alerts', self.page('/admin/sms/audit'))
        row = self.audit('password_login')[0]
        self.post(f'/admin/sms/audit/{row.id}/ack')  # not an alert: unchanged
        self.assertIsNone(self.audit('password_login')[0].acknowledged_at)


# ---------------------------------------------------------------- account actions

class AccountAdminTests(AdminCase):
    def test_revoke_sessions(self):
        teacher = self.app.test_client()
        self.assert_next(self.submit('teacher_viewer_user', client=teacher), '/')
        resp = self.su.post(f'/admin/users/{self.users["teacher_a"]}/revoke-sessions')
        self.assertEqual(resp.get_json()['success'], True)
        self.assertIn('/login', teacher.get('/').headers['Location'])
        [row] = self.audit('sessions_revoked')
        self.assertEqual((row.user_id, row.actor_user_id), (self.users['teacher_a'], self.users['super']))

    def test_not_on_your_own_account(self):
        me = self.users['super']
        for url, data in ((f'/admin/users/{me}/revoke-sessions', {}), (f'/admin/users/{me}/active', {'active': '0'}),
                          (f'/admin/users/{me}/local-only', {'local_only': '1'})):
            self.assertEqual(self.su.post(url, data=data).status_code, 400, url)
        self.assertTrue(self.user('super').active)
        self.assertEqual(self.su.post('/admin/users/999999/active', data={'active': '0'}).status_code, 404)

    def test_disable_and_enable(self):
        teacher = self.app.test_client()
        self.assert_next(self.submit('teacher_viewer_user', client=teacher), '/')
        url = f'/admin/users/{self.users["teacher_a"]}/active'
        self.assertEqual(self.su.post(url, data={'active': '0'}).get_json(), {'success': True, 'active': False})
        u = self.fresh('teacher_a')
        self.assertEqual((u.active, u.disabled_reason), (False, 'local'))
        self.assertIsNotNone(u.disabled_at)
        self.assertIn('/login', teacher.get('/').headers['Location'])
        self.assert_next(self.submit('teacher_viewer_user'), '/sso/sms/failed', 401)  # SMS never re-enables
        card = self.page('/admin/users?status=all').split(f'id="user-{self.users["teacher_a"]}"', 1)[1]
        self.assertIn('disabled-badge', card.split('</tr>', 1)[0])
        self.su.post(url, data={'active': '1'})
        u = self.fresh('teacher_a')
        self.assertEqual((u.active, u.disabled_reason, u.disabled_at), (True, None, None))
        self.assertEqual([r.reason for r in self.audit('account_state')], ['disabled', 'enabled'])

    def test_enable_an_sms_deprovisioned_account(self):
        self.push('snapshot_baseline')
        self.push('snapshot_deprovision_teacher')
        self.assertIn('Disabled by SMS', self.page('/admin/users?status=all'))
        self.su.post(f'/admin/users/{self.users["teacher_a"]}/active', data={'active': '1'})
        self.assertTrue(self.fresh('teacher_a').active)
        self.assertEqual(json.loads(self.audit('account_state')[-1].detail),
                         {'previous_reason': 'sms_deprovisioned'})

    def test_local_only(self):
        resp = self.su.post(f'/admin/users/{self.users["teacher_a"]}/local-only', data={'local_only': '1'})
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(self.fresh('teacher_a').local_only)
        url = f'/admin/users/{self.users["nogrants"]}/local-only'
        self.assertEqual(self.su.post(url, data={'local_only': '1'}).get_json()['local_only'], True)
        self.assertTrue(self.fresh('nogrants').local_only)
        html = self.post_follow('/admin/sms/link', {'issuer': smskit.PRIMARY, 'sub': smskit.person('unlinked')['sub'],
                                                    'user_id': self.users['nogrants']})
        self.assertIn('Super admin and local-only accounts cannot be linked to SMS.', html)
        self.su.post(url, data={'local_only': '0'})
        self.assertFalse(self.fresh('nogrants').local_only)
        self.assertEqual([r.reason for r in self.audit('account_state')], ['local_only', 'not_local_only'])


class UsersPageTests(AdminCase):
    def test_sms_badge_levels_and_states(self):
        self.push('snapshot_baseline')
        html = self.page('/admin/users')
        card = html.split(f'id="user-{self.users["teacher_a"]}"', 1)[1].split('</tr>', 1)[0]
        self.assertIn('sms-badge', card)
        self.assertNotIn('permission-select', card)
        self.assertIn('/revoke-sessions', card)
        panel = self.su.get(f'/admin/users/{self.users["teacher_a"]}/permissions-panel')
        self.assertEqual(panel.status_code, 200)
        panel_html = panel.get_data(as_text=True)
        self.assertIn('SMS levels (read-only)', panel_html)
        self.assertRegex(panel_html, r'MATH: User')
        glass = html.split(f'id="user-{self.users["breakglass"]}"', 1)[1].split('</tr>', 1)[0]
        self.assertIn('local-only-badge', glass)
        self.assertNotIn('sms-badge', glass)
        mine = html.split(f'id="user-{self.users["super"]}"', 1)[1].split('</tr>', 1)[0]
        self.assertNotIn('/revoke-sessions', mine)
        # ssocase.LINKS: four primary links and 'tester' on the secondary issuer.
        self.assertEqual(len(re.findall(r'class="badge bg-info text-dark ms-1 sms-badge"', html)), 5)


if __name__ == '__main__':
    unittest.main()
