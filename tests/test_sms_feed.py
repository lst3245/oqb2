"""Directory feed (contract v1.2 section 10): every official snapshot case
through the real ``POST /api/sms/directory`` handler on oqb_sso_test, applied
IN cases.json order with the clock frozen to each ``verify_at``, plus the
acceptance tests AT17-AT20 and the feed's own edge cases.

``test_case_<name>`` replays the snapshots up to ``<name>`` (later cases
depend on earlier ones), linking people the way each case's note says, then
checks that case's status, body and side effects.
"""
import json
import logging
import threading
import unittest

import smskit
from dbcase import _password_hash
from ssocase import SsoCase

JWT = 'application/jwt'
V, U = 'viewer', 'user'
BRAKE = [f'brake_{i:02d}' for i in range(1, 11)]
PRIMARY_LINKED = 4  # ssocase.LINKS on the primary issuer: teacher, panel, school, access


def _cookies(resp):
    return resp.headers.getlist('Set-Cookie')


class FeedCase(SsoCase):
    def setUp(self):
        super().setUp()
        from app import sso
        self.app.config.update(SMS_FEED_ENABLED=True, SMS_FEED_BRAKE_MIN=5, SMS_FEED_BRAKE_PCT=10)
        sso._feed_limiter.reset()

    # ------------------------------------------------------------ helpers
    def post_feed(self, body, *, content_type=JWT, client=None, headers=None):
        data = body.encode('ascii') if isinstance(body, str) else body
        return (client or self.client).post('/api/sms/directory', data=data,
                                            content_type=content_type, headers=headers or {})

    def push(self, name, **kw):
        with self.at(name):
            return self.post_feed(self.token(name), **kw)

    def link(self, person, username=None, issuer=smskit.PRIMARY):
        from app import db
        from app.models import ExternalIdentity, User
        username = username or person
        if username not in self.users:
            u = User(username=username, password_hash=_password_hash())
            db.session.add(u)
            db.session.flush()
            self.users[username] = u.id
        db.session.add(ExternalIdentity(user_id=self.users[username], issuer=issuer,
                                        sub=smskit.person(person)['sub'], link_method='admin'))
        db.session.commit()

    def replay_until(self, name):
        """Push the snapshot cases in order up to and including ``name``."""
        responses = {}
        for c in smskit.cases()['snapshots']:
            if c['name'] == 'snapshot_brake_setup':
                for person in BRAKE:
                    self.link(person)
            responses[c['name']] = self.push(c['name'])
            if c['name'] == name:
                return responses
        raise KeyError(name)

    def fresh(self, username):
        from app import db
        from app.models import User
        db.session.rollback()
        return db.session.get(User, self.users[username])

    def sms_rows(self, username):
        from app.models import UserSmsLevel
        return UserSmsLevel.query.filter_by(user_id=self.users[username]).count()

    def identity(self, person):
        from app import db
        from app.models import ExternalIdentity
        db.session.rollback()
        return ExternalIdentity.query.filter_by(issuer=smskit.PRIMARY,
                                                sub=smskit.person(person)['sub']).one()

    def snapshot_rows(self, status=None):
        from app import db
        from app.models import SmsSnapshot
        db.session.rollback()
        q = SmsSnapshot.query
        if status:
            q = q.filter_by(status=status)
        return q.order_by(SmsSnapshot.id).all()

    def mint_snapshot(self, users, *, iat, base='snapshot_baseline'):
        import uuid
        return smskit.mint(base, iat=iat, nbf=iat, exp=iat + 600, snapshot_id=str(uuid.uuid4()),
                           users=users)


# ---------------------------------------------------------------- fixture cases

class FixtureFeedCases(FeedCase):
    """Generated below: one method per snapshot case."""

    def check_snapshot_baseline(self, r):
        body = r.get_json()
        self.assertEqual(body['status'], 'applied')
        self.assertEqual(body['snapshot_id'], smskit.claims('snapshot_baseline')['snapshot_id'])
        self.assertEqual((body['linked'], body['deactivated'], body['reactivated']),
                         (PRIMARY_LINKED, 0, 0))
        self.assertEqual(body['unmapped_subjects'], [smskit.subject_uuid('CHEM')])
        self.assertEqual(self.levels('teacher_a'), {'MATC': U, 'MAT1': U, 'MAT2': U, 'ICT': U, 'ECON': V})
        from app.models import SmsRoster, SmsSubjectCatalogue
        self.assertEqual(SmsRoster.query.filter_by(issuer=smskit.PRIMARY).count(), 7)
        cat = {c.code: c for c in SmsSubjectCatalogue.query.all()}
        self.assertEqual(set(cat), {'MATH', 'M2', 'PHY', 'CHEM', 'LAT'})
        self.assertFalse(cat['LAT'].is_active)
        self.assertEqual(self.fresh('teacher_a').display_name, 'CHAN Tai Man')

    def check_snapshot_older_iat(self, r):
        self.assertEqual(r.get_json(), {'status': 'stale'})
        self.assertEqual(len(self.snapshot_rows()), 1)
        self.assertEqual(len(self.audit('feed_snapshot', 'stale_snapshot')), 1)

    def check_snapshot_deprovision_teacher(self, r):
        self.assertEqual(r.get_json()['deactivated'], 1)
        teacher = self.fresh('teacher_a')
        self.assertFalse(teacher.active)
        self.assertEqual(teacher.disabled_reason, 'sms_deprovisioned')
        self.assertEqual(teacher.session_version, 1)
        self.assertEqual(self.sms_rows('teacher_a'), 0)
        self.assertEqual(self.levels('teacher_a'), {})

    def check_snapshot_reactivate_teacher(self, r):
        self.assertEqual(r.get_json()['reactivated'], 1)
        teacher = self.fresh('teacher_a')
        self.assertTrue(teacher.active)
        self.assertIsNone(teacher.disabled_reason)
        self.assertEqual(self.levels('teacher_a')['MAT1'], U)

    def check_snapshot_missing_panel_admin(self, r):
        self.assertEqual(r.get_json()['missing'], 1)
        self.assertIsNotNone(self.identity('panel_admin').missing_since)
        self.assertEqual(self.levels('panel'), {'MATC': 'admin', 'MAT1': 'admin', 'MAT2': 'admin',
                                                'ICT': V, 'ECON': V})
        self.assertTrue(self.fresh('panel').active)
        [row] = self.audit('feed_missing')
        self.assertTrue(row.is_alert)

    def check_snapshot_brake_setup(self, r):
        self.assertEqual(r.get_json()['linked'], PRIMARY_LINKED + len(BRAKE))
        self.assertEqual(self.levels('brake_01'), {s: V for s in ('MATC', 'MAT1', 'MAT2', 'ICT', 'ECON')})

    def check_snapshot_brake_trip(self, r):
        body = r.get_json()
        self.assertEqual((body['status'], body['deactivated'], body['held']), ('held', 0, 10))
        self.assertTrue(all(self.fresh(p).active for p in BRAKE))
        [held] = self.snapshot_rows('held')
        self.assertEqual(len(json.loads(held.held_subs_json)), 10)
        [alert] = self.audit('feed_held')
        self.assertTrue(alert.is_alert)

    def check_snapshot_secondary_issuer(self, r):
        self.assertEqual(r.get_json(), {'status': 'rejected', 'reason': 'issuer_disabled'})


def _feed_case(c):
    def test(self):
        r = self.replay_until(c['name'])[c['name']]
        status = int(c['expect'].split()[0])
        self.assertEqual(r.status_code, status, r.get_data(as_text=True)[:300])
        self.assertFalse(_cookies(r))
        getattr(self, f"check_{c['name']}")(r)
    test.__doc__ = f"{c['name']} via POST /api/sms/directory: {c['expect']} (AT {c['acceptance_tests']})."
    return test


for _c in smskit.cases()['snapshots']:
    setattr(FixtureFeedCases, f"test_case_{_c['name']}", _feed_case(_c))


class FixtureFeedMeta(unittest.TestCase):
    def test_every_snapshot_case_has_a_route_test(self):
        names = [n for n in dir(FixtureFeedCases) if n.startswith('test_case_')]
        self.assertEqual(len(names), len(smskit.cases()['snapshots']))


# ---------------------------------------------------------------- acceptance tests

class TestAT17FeedApplied(FeedCase):
    def test_at17_feed_applied(self):
        r = self.push('snapshot_baseline')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.levels('panel'), {'MATC': 'admin', 'MAT1': 'admin', 'MAT2': 'admin',
                                                'ICT': V, 'ECON': V})
        self.assertEqual(self.levels('school'), {s: 'admin' for s in ('MATC', 'MAT1', 'MAT2', 'ICT', 'ECON')})
        self.assertFalse(self.fresh('school').is_super_admin)
        self.assertEqual(self.fresh('panel').staff_code, 'LSY')
        self.assertEqual(r.get_json()['unmapped_subjects'], [smskit.subject_uuid('CHEM')])
        [row] = self.audit('feed_snapshot')
        self.assertEqual((row.outcome, row.reason), ('ok', 'applied'))

    def test_at17_feed_works_before_sign_in_is_enabled(self):
        self.app.config['SMS_SSO_ENABLED'] = False
        self.assertEqual(self.push('snapshot_baseline').status_code, 200)
        self.assertEqual(self.client.get('/sso/sms').status_code, 404)
        self.assertEqual(self.levels('teacher_a'), {'MATC': U, 'ICT': V})  # SMS levels not counted

    def test_at05_snapshot_removes_subject_local_grant_survives(self):
        self.push('snapshot_baseline')
        self.assertEqual(self.levels('teacher_a')['ICT'], U)
        users = smskit.claims('snapshot_baseline')['users']
        teacher = dict(users[0], subjects=[s for s in users[0]['subjects'] if s['code'] == 'MATH'])
        token = self.mint_snapshot([teacher] + users[1:], iat=smskit.REFERENCE_NOW + 300)
        from app import sms_clock
        with sms_clock.frozen(smskit.REFERENCE_NOW + 305):
            self.assertEqual(self.post_feed(token).status_code, 200)
        levels = self.levels('teacher_a')
        self.assertEqual(levels['ICT'], V)    # local viewer grant survives
        self.assertEqual(levels['MAT1'], U)


class TestAT08FromFeedRoster(FeedCase):
    def test_at08_admin_link_from_the_fed_roster_then_handoff(self):
        from app import db, sms_sso_service
        self.push('snapshot_baseline')  # lists the unlinked teacher (HPL)
        sms_sso_service.admin_link(self.user('nogrants'), smskit.PRIMARY, smskit.person('unlinked')['sub'],
                                   actor_id=self.users['super'])
        db.session.commit()
        self.assertEqual(self.fresh('nogrants').staff_code, 'HPL')
        self.assertEqual(self.levels('nogrants'), {s: V for s in ('MATC', 'MAT1', 'MAT2', 'ICT', 'ECON')})
        self.assert_next(self.submit('unlinked_teacher'), '/')
        self.assertTrue(self.session_user().startswith(f"{self.users['nogrants']}:"))


class TestAT18Deprovision(FeedCase):
    def test_at18_feed_deprovision_ends_session_reactivate_only_sms(self):
        self.assertEqual(self.push('snapshot_baseline').status_code, 200)
        self.assert_next(self.submit('teacher_viewer_user'), '/')
        self.assertIn('/dashboard', self.client.get('/').headers['Location'])
        self.assertEqual(self.push('snapshot_deprovision_teacher').status_code, 200)
        resp = self.client.get('/')
        self.assertIn('/login', resp.headers['Location'])  # session ended at the next request
        self.assertEqual(self.push('snapshot_reactivate_teacher').status_code, 200)
        self.assertTrue(self.fresh('teacher_a').active)

    def test_at18_locally_disabled_account_is_never_reenabled(self):
        from app import db
        self.push('snapshot_baseline')
        teacher = self.fresh('teacher_a')
        teacher.active, teacher.disabled_reason = False, 'local'
        db.session.commit()
        r = self.push('snapshot_deprovision_teacher')
        self.assertEqual(r.get_json()['deactivated'], 0)
        self.assertEqual(self.sms_rows('teacher_a'), 0)
        r = self.push('snapshot_reactivate_teacher')
        self.assertEqual(r.get_json()['reactivated'], 0)
        teacher = self.fresh('teacher_a')
        self.assertEqual((teacher.active, teacher.disabled_reason), (False, 'local'))

    def test_at18_handoff_reactivates_only_sms_deprovisioned(self):
        self.push('snapshot_baseline')
        self.push('snapshot_deprovision_teacher')
        self.assert_next(self.submit('teacher_viewer_user'), '/')
        self.assertTrue(self.fresh('teacher_a').active)
        self.assertEqual(len(self.audit('reactivate')), 1)


class TestAT19StaleWorkshopBrake(FeedCase):
    def test_at19_feed_stale_workshop_and_brake(self):
        self.assertEqual(self.push('snapshot_baseline').status_code, 200)
        before = self.levels('teacher_a')
        again = self.app.test_client()
        self.assertEqual(self.push('snapshot_baseline', client=again).status_code, 409)
        self.assertEqual(self.push('snapshot_older_iat').status_code, 409)
        self.assertEqual(self.levels('teacher_a'), before)
        r = self.push('snapshot_secondary_issuer')
        self.assertEqual((r.status_code, r.get_json()['reason']), (403, 'issuer_disabled'))

    def test_at19_brake_holds_then_approve(self):
        from app import sms_feed_service
        responses = self.replay_until('snapshot_brake_trip')
        self.assertEqual(responses['snapshot_brake_trip'].status_code, 202)
        [held] = self.snapshot_rows('held')
        done = sms_feed_service.approve_held(held.id, actor_id=self.users['super'])
        self.assertEqual(done, 10)
        for person in BRAKE:
            u = self.fresh(person)
            self.assertEqual((u.active, u.disabled_reason), (False, 'sms_deprovisioned'))
        self.assertEqual(self.snapshot_rows('approved')[0].decided_by_user_id, self.users['super'])
        self.assertEqual(len(self.audit('deprovision', 'held_approved')), 10)

    def test_at19_brake_discard_and_superseded(self):
        from app import sms_feed_service
        self.replay_until('snapshot_brake_trip')
        [held] = self.snapshot_rows('held')
        sms_feed_service.discard_held(held.id, actor_id=self.users['super'])
        self.assertTrue(all(self.fresh(p).active for p in BRAKE))
        with self.assertRaises(sms_feed_service.FeedError):
            sms_feed_service.approve_held(held.id)
        # A newer push that still drops them is held again; a decided snapshot stays decided.
        users = smskit.claims('snapshot_brake_trip')['users']
        from app import sms_clock
        later = smskit.case('snapshot_brake_trip')['verify_at'] + 600
        with sms_clock.frozen(later):
            r = self.post_feed(self.mint_snapshot(users, iat=later - 5, base='snapshot_brake_trip'))
        self.assertEqual(r.status_code, 202)
        first, second = sorted(self.snapshot_rows('held') + self.snapshot_rows('discarded'), key=lambda s: s.id)
        self.assertEqual((first.status, second.status), ('discarded', 'held'))
        with sms_clock.frozen(later + 600):
            self.post_feed(self.mint_snapshot(users, iat=later + 595, base='snapshot_brake_trip'))
        self.assertEqual([s.status for s in self.snapshot_rows()][-2:], ['superseded', 'held'])
        with self.assertRaises(sms_feed_service.FeedError):
            sms_feed_service.approve_held(second.id)

    def test_brake_threshold(self):
        from app.sms_feed_service import brake_threshold
        self.assertEqual(brake_threshold(14, 5, 10), 5)
        self.assertEqual(brake_threshold(200, 5, 10), 20)


class TestAT20MissingSub(FeedCase):
    def test_at20_missing_linked_sub_flagged_unchanged(self):
        self.push('snapshot_baseline')
        before = self.levels('panel')
        r = self.push('snapshot_missing_panel_admin')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.levels('panel'), before)
        self.assertIsNotNone(self.identity('panel_admin').missing_since)
        self.assertEqual(len(self.audit('feed_missing')), 1)
        self.push('snapshot_brake_setup')  # panel_admin listed again
        self.assertIsNone(self.identity('panel_admin').missing_since)


# ---------------------------------------------------------------- the endpoint itself

class FeedEndpointTests(FeedCase):
    def test_off_unless_feed_enabled(self):
        self.app.config['SMS_FEED_ENABLED'] = False
        self.assertEqual(self.push('snapshot_baseline').status_code, 404)

    def test_schema_not_ready_is_503(self):
        self.app.config['SMS_SCHEMA_READY'] = False
        self.assertEqual(self.push('snapshot_baseline').status_code, 503)

    def test_content_type_and_size(self):
        r = self.push('snapshot_baseline', content_type='application/json')
        self.assertEqual((r.status_code, r.get_json()['reason']), (400, 'malformed'))
        from app.sms_verify import MAX_SNAPSHOT_BYTES
        r = self.post_feed(b'a' * (MAX_SNAPSHOT_BYTES + 1))
        self.assertEqual((r.status_code, r.get_json()['reason']), (400, 'malformed'))
        self.assertEqual(self.client.get('/api/sms/directory').status_code, 405)

    def test_rejections_use_the_contract_bodies(self):
        r = self.post_feed(self.token('teacher_viewer_user'))  # a handoff, not a snapshot
        self.assertEqual((r.status_code, r.get_json()), (400, {'status': 'rejected',
                                                               'reason': 'bad_purpose_or_ver'}))
        with self.at('snapshot_baseline'):
            r = self.post_feed(self.token('snapshot_baseline')[:-4] + 'AAAA')
        self.assertEqual((r.status_code, r.get_json()['reason']), (401, 'bad_signature'))

    def test_issuer_without_accepts_feed_is_refused(self):
        from app import db, sms_trust
        sms_trust.upsert_issuer(smskit.PRIMARY, accepts_feed=False)
        db.session.commit()
        r = self.push('snapshot_baseline')
        self.assertEqual((r.status_code, r.get_json()['reason']), (403, 'issuer_disabled'))

    def test_rate_limited(self):
        from app import sso
        for _ in range(sso.FEED_RATE_LIMIT):
            self.post_feed('x', content_type='text/plain')
        r = self.post_feed('x', content_type='text/plain')
        self.assertEqual((r.status_code, r.get_json()['reason']), (429, 'rate_limited'))

    def test_no_cookies_even_with_a_browser_session(self):
        self.login('teacher_a')
        r = self.push('snapshot_baseline')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(_cookies(r), [])
        self.assertEqual(r.headers['Cache-Control'], 'no-store')

    def test_invalid_entries_skipped_and_counted(self):
        users = smskit.claims('snapshot_baseline')['users']
        panel = dict(users[1], default_level='boss')  # linked, invalid: unchanged, not "missing"
        token = self.mint_snapshot([users[0], panel, {'sub': 'not-a-uuid', 'active': True}] + users[2:],
                                   iat=smskit.REFERENCE_NOW)
        with self.at('snapshot_baseline'):
            r = self.post_feed(token)
        body = r.get_json()
        self.assertEqual((r.status_code, body['invalid_entries'], body['missing']), (200, 2, 0))
        self.assertEqual(self.sms_rows('panel'), 0)
        self.assertIsNone(self.identity('panel_admin').missing_since)
        self.assertEqual(self.levels('teacher_a')['MAT1'], U)

    def test_body_never_logged_or_audited(self):
        records = []

        class Grab(logging.Handler):
            def emit(self, record):
                records.append(self.format(record))

        handler = Grab(level=logging.DEBUG)
        loggers = [logging.getLogger(), logging.getLogger('werkzeug'), self.app.logger]
        saved = [(lg, lg.level) for lg in loggers]
        for lg in loggers:
            lg.addHandler(handler)
            lg.setLevel(logging.DEBUG)
        try:
            body = self.token('snapshot_baseline')
            self.push('snapshot_baseline')
            self.push('snapshot_baseline')                       # stale
            with self.at('snapshot_baseline'):
                self.post_feed(body[:-4] + 'AAAA')               # bad signature
            self.post_feed(body, content_type='text/plain')      # malformed
        finally:
            for lg, level in saved:
                lg.removeHandler(handler)
                lg.setLevel(level)
        signature = body.rsplit('.', 1)[1]
        text = '\n'.join(records) + '\n'.join(str(a.detail) for a in self.audit())
        self.assertNotIn(signature, text)
        self.assertNotIn(body.split('.')[1][:40], text)

    def test_concurrent_pushes_apply_once(self):
        from app import db
        body = self.token('snapshot_baseline')
        db.session.remove()
        barrier = threading.Barrier(2)
        results = [None, None]

        def worker(i):
            try:
                with self.app.app_context():
                    client = self.app.test_client()
                    barrier.wait(timeout=10)
                    resp = client.post('/api/sms/directory', data=body, content_type=JWT)
                    results[i] = resp.status_code
                    db.session.remove()
            except Exception as exc:  # surfaced below
                results[i] = exc

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        self.assertEqual(sorted(results), [200, 409], results)
        self.assertEqual(len(self.snapshot_rows()), 1)

    def test_purge_keeps_held_snapshots(self):
        from app import db, sms_feed_service
        self.replay_until('snapshot_brake_trip')
        later = smskit.REFERENCE_NOW + (sms_feed_service.SNAPSHOT_KEEP_DAYS + 1) * 86400
        n = sms_feed_service.purge_snapshots(later)
        db.session.commit()
        self.assertEqual(n, 5)  # baseline, deprovision, reactivate, missing, brake_setup
        self.assertEqual([s.status for s in self.snapshot_rows()], ['held'])


if __name__ == '__main__':
    unittest.main()
