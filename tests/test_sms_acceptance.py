"""Contract v1.2 section 15 acceptance tests AT01-AT16 and AT21 through the
real routes (``POST /sso/sms`` etc.) against oqb_sso_test, with the clock
frozen to each fixture's ``verify_at``. AT17-AT20 (feed) are in
test_sms_feed.py; AT22 (session limits) arrives with stage 6. AT03's mapping
removal and AT08's admin linking go through the service / DB until the
stage 5 admin screens exist.
"""
import html.parser
import json
import logging
import threading
import unittest

import smskit
from dbcase import PASSWORD
from ssocase import ORIGIN, SsoCase

XHR = {'X-Requested-With': 'XMLHttpRequest'}
ALL = ('MATC', 'MAT1', 'MAT2', 'ICT', 'ECON')
V, U, A = 'viewer', 'user', 'admin'


def _cookie_names(resp):
    return [h.split('=', 1)[0] for h in resp.headers.getlist('Set-Cookie')]


def _set_cookie(resp, name):
    """True when ``resp`` sets ``name`` to a non-empty value."""
    for h in resp.headers.getlist('Set-Cookie'):
        key, _, rest = h.partition('=')
        if key == name and rest.split(';', 1)[0].strip('"'):
            return True
    return False


class TestAT01LinkedTeacherLevels(SsoCase):
    def test_at01_linked_teacher_levels(self):
        resp = self.submit('teacher_viewer_user')
        self.assert_next(resp, '/')
        self.assertTrue(self.session_user().startswith(f"{self.users['teacher_a']}:"))
        self.assertFalse(_set_cookie(resp, self.app.config.get('REMEMBER_COOKIE_NAME', 'remember_token')))
        self.assertEqual(self.levels('teacher_a'), {'MATC': U, 'MAT1': U, 'MAT2': U, 'ICT': U, 'ECON': V})
        self.assertEqual(self.client.get('/').status_code, 302)
        [row] = self.audit('sso_login')
        self.assertEqual((row.outcome, row.user_id, row.sub), ('ok', self.users['teacher_a'],
                                                              smskit.person('teacher')['sub']))


class TestAT02EmptySubjectsViewer(SsoCase):
    def test_at02_empty_subjects_viewer_everywhere(self):
        self.assert_next(self.submit('teacher_empty_subjects'), '/')
        levels = self.levels('teacher_a')
        # Local grants survive (teacher_a: MATC user); SMS adds viewer everywhere.
        self.assertEqual(levels, {'MATC': U, 'MAT1': V, 'MAT2': V, 'ICT': V, 'ECON': V})

    def test_at02_viewer_cannot_edit_anywhere(self):
        self.assert_next(self.submit('teacher_empty_subjects', ), '/')
        for sid in ALL:
            resp = self.client.post('/admin/topics/add', data={'subject_id': sid, 'name': 'X'}, headers=XHR)
            self.assertEqual(resp.status_code, 403, sid)
        resp = self.client.post('/admin/questions/batch-update', headers=XHR, data={
            'question_ids': json.dumps([self.questions['ECON']]), 'level': '2'})
        self.assertEqual(resp.status_code, 403)

    def test_at02_default_none_local_grants_only(self):
        self.assert_next(self.submit('access_only_none'), '/')
        self.assertEqual(self.levels('access'), {})
        self.assertTrue(self.session_user().startswith(f"{self.users['access']}:"))


class TestAT03OneToManyMapping(SsoCase):
    def test_at03_one_to_many_mapping_and_removal(self):
        from app import db
        from app.models import SmsSubjectMap
        self.assert_next(self.submit('teacher_viewer_user'), '/')
        levels = self.levels('teacher_a')
        self.assertEqual([levels[s] for s in ('MATC', 'MAT1', 'MAT2')], [U, U, U])
        SmsSubjectMap.query.filter_by(sms_uuid=smskit.subject_uuid('MATH'), oqb_subject_id='MAT1').delete()
        db.session.commit()
        self.assertEqual(self.levels('teacher_a')['MAT1'], V)
        self.assertEqual(self.levels('teacher_a')['MAT2'], U)


class TestAT04Admins(SsoCase):
    def test_at04_panel_admin_and_school_admin_not_super(self):
        from app import authz, db
        from app.models import User
        self.assert_next(self.submit('panel_admin'), '/')
        self.assertEqual(self.levels('panel'), {'MATC': A, 'MAT1': A, 'MAT2': A, 'ICT': V, 'ECON': V})
        self.assert_next(self.submit('school_admin'), '/')
        self.assertEqual(self.levels('school'), {s: A for s in ALL})
        school = db.session.get(User, self.users['school'])
        self.assertFalse(school.is_super_admin)
        self.assertFalse(authz.is_super_admin(school))
        self.assertEqual(self.client.get('/register').status_code, 403)


class TestAT05RemovedSubject(SsoCase):
    def test_at05_removed_subject_loses_level_local_grant_survives(self):
        self.assert_next(self.submit('teacher_viewer_user'), '/')
        self.assertEqual(self.levels('teacher_a')['ICT'], U)
        self.assert_next(self.submit('teacher_math_only'), '/')  # PHY gone
        levels = self.levels('teacher_a')
        self.assertEqual(levels['ICT'], V)   # local viewer grant survives
        self.assertEqual(levels['MATC'], U)  # local + SMS
        self.assert_next(self.submit('teacher_empty_subjects'), '/')
        self.assertEqual(self.levels('teacher_a')['MAT1'], V)
        self.assertEqual(self.levels('teacher_a')['MATC'], U)  # local grant only


class _LinkFlow(SsoCase):
    def start_unlinked(self):
        resp = self.submit('unlinked_teacher')
        self.assert_next(resp, '/sso/sms/link')
        self.assertIsNone(self.session_user())
        return resp

    def password_login(self, username, follow=False):
        return self.client.post('/login', data={'username': username, 'password': PASSWORD},
                                follow_redirects=follow)

    def form_token(self):
        with self.client.session_transaction() as sess:
            return sess.get('sso_link_form')

    def identity(self, sub_person='unlinked'):
        from app import db
        from app.models import ExternalIdentity
        db.session.expire_all()
        return ExternalIdentity.query.filter_by(issuer=smskit.PRIMARY,
                                                sub=smskit.person(sub_person)['sub']).first()


class TestAT06SelfLink(_LinkFlow):
    def test_at06_unlinked_jit_off_prompt_then_self_link_no_duplicate(self):
        from app.models import User
        users_before = User.query.count()
        self.start_unlinked()
        prompt = self.client.get('/sso/sms/link')
        self.assertEqual(prompt.status_code, 200)
        self.assertIn("isn&#39;t linked to an OQB account yet", prompt.get_data(as_text=True))
        login = self.password_login('nogrants')
        self.assertEqual(login.status_code, 302)
        self.assertTrue(login.headers['Location'].endswith('/sso/sms/link/confirm'))
        page = self.client.get('/sso/sms/link/confirm')
        self.assertEqual(page.status_code, 200)
        body = page.get_data(as_text=True)
        self.assertIn('nogrants', body)
        self.assertIn(smskit.person('unlinked')['name'], body)
        self.assertEqual(self.client.post('/sso/sms/link/confirm', data={'form_token': 'wrong'}).status_code, 400)
        self.assertIsNone(self.identity())
        resp = self.client.post('/sso/sms/link/confirm', data={'form_token': self.form_token()})
        self.assertEqual(resp.status_code, 302)
        link = self.identity()
        self.assertEqual((link.user_id, link.link_method), (self.users['nogrants'], 'self'))
        self.assertEqual(User.query.count(), users_before)
        self.assertEqual(self.levels('nogrants'), {s: V for s in ALL})
        # The next handoff signs into the same account.
        fresh = self.app.test_client()
        with self.at('unlinked_teacher'):
            token = smskit.mint('unlinked_teacher', iat=smskit.REFERENCE_NOW, nbf=smskit.REFERENCE_NOW,
                                exp=smskit.REFERENCE_NOW + 60)
            self.assert_next(self.post_token(token, client=fresh), '/')
        self.assertTrue(self.session_user(fresh).startswith(f"{self.users['nogrants']}:"))
        self.assertEqual([r.reason for r in self.audit('link')], ['self'])

    def test_at06_prompt_without_pending_goes_to_failed(self):
        resp = self.client.get('/sso/sms/link')
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.headers['Location'].endswith('/sso/sms/failed'))

    def test_at06_cancel_drops_the_pending_link(self):
        self.start_unlinked()
        self.client.get('/sso/sms/link')
        self.assertEqual(self.client.post('/sso/sms/link/cancel', data={'form_token': self.form_token()}).status_code, 302)
        self.password_login('nogrants')
        self.assertEqual(self.client.get('/sso/sms/link/confirm').status_code, 302)
        self.assertIsNone(self.identity())

    def test_at06_pending_link_expires_after_ten_minutes(self):
        from app import sms_clock
        self.start_unlinked()
        self.password_login('nogrants')
        with sms_clock.frozen(smskit.case('unlinked_teacher')['verify_at'] + 601):
            self.assertEqual(self.client.get('/sso/sms/link/confirm').status_code, 302)
        self.assertIsNone(self.identity())


class TestAT07PrivilegedRefused(_LinkFlow):
    def test_at07_self_link_privileged_refused(self):
        for username in ('super', 'breakglass'):
            with self.subTest(username):
                self.client = self.app.test_client()
                from app import sso
                sso._limiter.reset()
                with self.at('unlinked_teacher'):
                    token = smskit.mint('unlinked_teacher', iat=smskit.REFERENCE_NOW,
                                        nbf=smskit.REFERENCE_NOW, exp=smskit.REFERENCE_NOW + 60)
                    self.assert_next(self.post_token(token), '/sso/sms/link')
                self.password_login(username)
                page = self.client.get('/sso/sms/link/confirm')
                self.assertIn('cannot be linked to SMS', page.get_data(as_text=True))
                self.client.get('/sso/sms/link')
                self.client.post('/sso/sms/link/confirm', data={'form_token': self.form_token()})
                self.assertIsNone(self.identity())

    def test_at07_linked_privileged_account_refused_at_handoff(self):
        from app import db
        from app.models import ExternalIdentity, User
        ident = ExternalIdentity.query.filter_by(user_id=self.users['panel']).one()
        ident.user_id = self.users['super']
        db.session.commit()
        self.assert_next(self.submit('panel_admin'), '/sso/sms/failed', 401)
        self.assertIsNone(self.session_user())
        self.assertEqual(len(self.audit('sso_login', 'privileged_account')), 1)
        self.assertTrue(db.session.get(User, self.users['super']).is_super_admin)

    def test_at07_promoting_a_linked_user_to_super_admin_is_refused(self):
        self.login('super')
        resp = self.client.post(f"/admin/users/{self.users['panel']}/edit", headers=XHR,
                                data={'username': 'panel', 'is_super_admin': '1'})
        self.assertEqual(resp.status_code, 400, resp.get_data(as_text=True)[:200])
        from app import db
        from app.models import User
        db.session.expire_all()
        self.assertFalse(db.session.get(User, self.users['panel']).is_super_admin)


class TestAT08AdminLink(_LinkFlow):
    def test_at08_admin_link_from_roster_then_handoff(self):
        from app import db, sms_sso_service
        from app.models import SmsRoster, User
        sub = smskit.person('unlinked')['sub']
        chem = smskit.subject_uuid('CHEM')
        db.session.add(SmsRoster(issuer=smskit.PRIMARY, sub=sub, active=True, email='x@school.test',
                                 name='Roster Name', staff_code='RN', default_level='viewer',
                                 subjects_json=json.dumps([{'uuid': chem, 'code': 'CHEM', 'level': 'user'}])))
        db.session.commit()
        user = db.session.get(User, self.users['nogrants'])
        sms_sso_service.admin_link(user, smskit.PRIMARY, sub, actor_id=self.users['super'])
        db.session.commit()
        self.assertEqual(self.levels('nogrants'), {s: V for s in ALL})
        self.assertEqual(db.session.get(User, self.users['nogrants']).staff_code, 'RN')
        self.assert_next(self.submit('unlinked_teacher'), '/')
        self.assertTrue(self.session_user().startswith(f"{self.users['nogrants']}:"))
        [row] = self.audit('link')
        self.assertEqual((row.reason, row.actor_user_id), ('admin', self.users['super']))


class TestAT09Jit(SsoCase):
    def test_at09_jit_primary_creates_passwordless_nonprimary_never(self):
        from app import db
        from app.models import ExternalIdentity, User
        self.app.config['SMS_JIT_ENABLED'] = True
        self.assert_next(self.submit('unlinked_teacher'), '/')
        ident = ExternalIdentity.query.filter_by(sub=smskit.person('unlinked')['sub']).one()
        user = db.session.get(User, ident.user_id)
        self.assertEqual(ident.link_method, 'jit')
        self.assertEqual(user.username, smskit.person('unlinked')['email'].split('@')[0].lower())
        self.assertFalse(user.check_password(''))
        self.assertFalse(user.check_password(PASSWORD))
        self.assertFalse(user.is_super_admin)
        self.assertTrue(self.session_user().startswith(f'{user.id}:'))
        # Non-primary: the allowed test sub, unlinked, with JIT on -> no user.
        ExternalIdentity.query.filter_by(issuer=smskit.SECONDARY).delete()
        db.session.commit()
        before = User.query.count()
        self.client = self.app.test_client()
        self.assert_next(self.submit('secondary_test_account_admin'), '/sso/sms/failed', 401)
        self.assertEqual(User.query.count(), before)
        self.assertEqual(len(self.audit('sso_login', 'not_linked')), 1)

    def test_at09_jit_respects_the_trust_entry(self):
        from app import sms_trust, db
        from app.models import User
        self.app.config['SMS_JIT_ENABLED'] = True
        sms_trust.upsert_issuer(smskit.PRIMARY, allow_jit=False)
        db.session.commit()
        before = User.query.count()
        self.assert_next(self.submit('unlinked_teacher'), '/sso/sms/link')
        self.assertEqual(User.query.count(), before)

    def test_at09_jit_username_collision(self):
        from app import db
        from app.models import ExternalIdentity, User
        local = smskit.person('unlinked')['email'].split('@')[0].lower()
        db.session.add(User(username=local.upper(), password_hash='x'))
        db.session.commit()
        self.app.config['SMS_JIT_ENABLED'] = True
        self.assert_next(self.submit('unlinked_teacher'), '/')
        ident = ExternalIdentity.query.filter_by(sub=smskit.person('unlinked')['sub']).one()
        self.assertEqual(db.session.get(User, ident.user_id).username, f'{local}-2')
        self.assertEqual(User.query.filter_by(username=local.upper()).count(), 1)


class TestAT10Replay(SsoCase):
    def test_at10_replay(self):
        self.assert_next(self.submit('teacher_viewer_user'), '/')
        fresh = self.app.test_client()
        resp = self.submit('teacher_viewer_user', client=fresh)
        self.assert_next(resp, '/sso/sms/failed', 401)
        self.assertIsNone(self.session_user(fresh))
        self.assertEqual(len(self.audit('sso_login', 'replay')), 1)

    def test_at10_replay_and_concurrent_double_submit(self):
        from app import db
        token = smskit.mint(iat=smskit.REFERENCE_NOW, nbf=smskit.REFERENCE_NOW, exp=smskit.REFERENCE_NOW + 60)
        db.session.remove()
        barrier = threading.Barrier(2)
        results = [None, None]
        cookie = self.app.config['SESSION_COOKIE_NAME']

        def worker(i):
            try:
                with self.app.app_context():
                    client = self.app.test_client()
                    barrier.wait(timeout=10)
                    resp = client.post('/sso/sms', json={'token': token},
                                       headers={'Origin': ORIGIN, 'Accept': 'application/json'})
                    results[i] = (resp.status_code, _set_cookie(resp, cookie), resp.get_json())
                    db.session.remove()
            except Exception as exc:  # surfaced below
                results[i] = exc

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertTrue(all(isinstance(r, tuple) for r in results), results)
        self.assertEqual(sorted(r[0] for r in results), [200, 401])
        ok = next(r for r in results if r[0] == 200)
        refused = next(r for r in results if r[0] == 401)
        self.assertTrue(ok[1])
        self.assertFalse(refused[1])
        self.assertEqual(refused[2], {'next': '/sso/sms/failed'})


class TestAT11Times(SsoCase):
    def test_at11_time_windows(self):
        for name, path in (('expired_31s', '/sso/sms/failed'), ('expired_20s', '/'),
                           ('nbf_60s_ahead', '/sso/sms/failed'), ('lifetime_300s', '/sso/sms/failed')):
            with self.subTest(name):
                self.assert_next(self.submit(name, client=self.app.test_client()), path,
                                 200 if path == '/' else 401)
        self.assertEqual({r.reason for r in self.audit('sso_login') if r.outcome == 'fail'},
                         {'expired', 'not_yet_valid', 'bad_lifetime'})


class TestAT12Algorithms(SsoCase):
    def test_at12_alg_none_hs256_tamper_unknown_kid(self):
        expected = {'alg_none': 'bad_alg', 'hs256_with_public_key': 'bad_alg',
                    'tampered_payload': 'bad_signature', 'unknown_kid': 'unknown_kid'}
        for name, reason in expected.items():
            with self.subTest(name):
                self.assert_next(self.submit(name), '/sso/sms/failed', 401)
                self.assertEqual(len(self.audit('sso_login', reason)), 1 + (name == 'hs256_with_public_key'))
                self.assertIsNone(self.session_user())

    def test_at12_jwks_kid_is_alerted_not_trusted(self):
        from app import db, sms_trust
        sms_trust.unpin_key(smskit.PRIMARY, 'sms-contract-test-2')
        sms_trust.upsert_issuer(smskit.PRIMARY, jwks_url='https://jwks.invalid/jwks.json', jwks_auto=False)
        db.session.commit()
        with open(smskit.path('jwks', 'primary.json'), 'r', encoding='utf-8') as fh:
            doc = json.load(fh)
        saved = sms_trust.JWKS.fetcher
        sms_trust.JWKS.fetcher = lambda url, timeout: doc
        self.addCleanup(setattr, sms_trust.JWKS, 'fetcher', saved)
        self.assert_next(self.submit('teacher_rotated_kid'), '/sso/sms/failed', 401)
        self.assertEqual(len(self.audit('sso_login', 'unknown_kid')), 1)
        [alert] = self.audit('jwks_new_kid')
        self.assertTrue(alert.is_alert)
        self.assertIn('sms-contract-test-2', alert.detail)


class TestAT13Issuers(SsoCase):
    def test_at13_claim_and_issuer_rejections(self):
        expected = {'wrong_aud_jupas': 'bad_aud', 'missing_purpose': 'bad_purpose_or_ver',
                    'ver_2': 'bad_purpose_or_ver', 'unknown_issuer': 'unknown_issuer',
                    'admin_with_subjects': 'malformed'}
        for name, reason in expected.items():
            with self.subTest(name):
                self.assert_next(self.submit(name), '/sso/sms/failed', 401)
        self.assertEqual(sorted(r.reason for r in self.audit('sso_login')),
                         sorted(expected.values()))

    def test_at13_issuer_checks_workshop_allowlist_cap(self):
        from app import db, sms_trust
        self.assert_next(self.submit('secondary_teacher_not_allowed'), '/sso/sms/failed', 401)
        self.assertEqual(len(self.audit('sso_login', 'sub_not_allowed')), 1)
        self.assert_next(self.submit('secondary_test_account_admin'), '/')
        self.assertEqual(self.levels('tester'), {s: V for s in ALL})
        [alert] = self.audit('nonprimary_login')
        self.assertTrue(alert.is_alert)
        sms_trust.upsert_issuer(smskit.SECONDARY, enabled=False)
        db.session.commit()
        self.assertEqual(self.levels('tester'), {})  # a disabled issuer's levels stop counting
        self.client = self.app.test_client()
        with self.at('secondary_test_account_admin'):
            token = smskit.mint('secondary_test_account_admin', kid='sms-contract-test-workshop',
                                iat=smskit.REFERENCE_NOW, nbf=smskit.REFERENCE_NOW, exp=smskit.REFERENCE_NOW + 60)
            self.assert_next(self.post_token(token), '/sso/sms/failed', 401)
        self.assertEqual(len(self.audit('sso_login', 'issuer_disabled')), 1)

    def test_at13_test_window_close_rejects_with_issuer_disabled(self):
        from app import db, sms_sso_service, sms_trust
        kids = sms_trust.close_test_window(smskit.SECONDARY, actor_id=self.users['super'])
        db.session.commit()
        self.assertEqual(kids, ['sms-contract-test-workshop'])
        self.assert_next(self.submit('secondary_test_account_admin'), '/sso/sms/failed', 401)
        self.assertEqual(len(self.audit('sso_login', 'issuer_disabled')), 1)
        self.assertEqual([u.username for _i, u in sms_sso_service.linked_users(smskit.SECONDARY)], ['tester'])
        self.assertEqual([r.reason for r in self.audit('test_window')], ['close'])


class TestAT14Rotation(SsoCase):
    def test_at14_key_rotation(self):
        from app import db, sms_trust
        self.assert_next(self.submit('teacher_viewer_user'), '/')
        self.assert_next(self.submit('teacher_rotated_kid', client=self.app.test_client()), '/')
        sms_trust.unpin_key(smskit.PRIMARY, 'sms-contract-test')
        db.session.commit()
        with self.at('teacher_viewer_user'):
            old = smskit.mint(iat=smskit.REFERENCE_NOW, nbf=smskit.REFERENCE_NOW, exp=smskit.REFERENCE_NOW + 60)
            self.assert_next(self.post_token(old, client=self.app.test_client()), '/sso/sms/failed', 401)
        self.assertEqual(len(self.audit('sso_login', 'unknown_kid')), 1)


class _Head(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_head, self.first_in_head, self.script, self.urls, self._in_script = False, None, '', [], False
        self.script_nonce = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'head':
            self.in_head = True
        elif self.in_head and self.first_in_head is None:
            self.first_in_head = tag
            if tag == 'script':
                self.script_nonce = attrs.get('nonce')
                self._in_script = True
        for key in ('src', 'href', 'action'):
            if attrs.get(key):
                self.urls.append(attrs[key])

    def handle_endtag(self, tag):
        if tag == 'script':
            self._in_script = False
        if tag == 'head':
            self.in_head = False

    def handle_data(self, data):
        if self._in_script:
            self.script += data


class TestAT15LandingPage(SsoCase):
    def test_at15_landing_page_headers_and_html(self):
        resp = self.client.get('/sso/sms')
        self.assertEqual(resp.status_code, 200)
        parser = _Head()
        parser.feed(resp.get_data(as_text=True))
        self.assertEqual(parser.first_in_head, 'script')
        self.assertTrue(parser.script_nonce)
        self.assertEqual(resp.headers['Content-Security-Policy'],
                         f"default-src 'none'; script-src 'self' 'nonce-{parser.script_nonce}'; "
                         f"connect-src 'self'; form-action 'self'; frame-ancestors 'none'")
        self.assertEqual(resp.headers['Referrer-Policy'], 'no-referrer')
        self.assertEqual(resp.headers['Cache-Control'], 'no-store')
        self.assertLess(parser.script.index('history.replaceState'), parser.script.index('fetch('))
        self.assertLess(parser.script.index('location.hash'), parser.script.index('fetch('))
        for url in parser.urls:
            self.assertTrue(url.startswith('/') and not url.startswith('//'), url)
        self.assertNotEqual(self.client.get('/sso/sms').headers['Content-Security-Policy'],
                            resp.headers['Content-Security-Policy'])  # fresh nonce per response

    def test_at15_failed_page(self):
        resp = self.client.get('/sso/sms/failed')
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Sign-in from SMS didn&#39;t work. Please open the Question Bank from SMS again, "
                      "or sign in with your OQB password.", resp.get_data(as_text=True))
        self.assertEqual(resp.headers['Referrer-Policy'], 'no-referrer')
        self.assertEqual(resp.headers['Cache-Control'], 'no-store')

    def test_at15_no_token_in_logs_or_audit(self):
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
            tokens = [self.token('teacher_viewer_user'), self.token('teacher_viewer_user'),
                      self.token('tampered_payload'), self.token('expired_31s')]
            with self.at('teacher_viewer_user'):
                for tok in tokens:
                    self.post_token(tok, client=self.app.test_client())
                self.post_token(tokens[0] + '.x', client=self.app.test_client())
                self.post_token(tokens[0], origin=None, client=self.app.test_client())
        finally:
            for lg, level in saved:
                lg.removeHandler(handler)
                lg.setLevel(level)
        rows = self.audit()
        self.assertTrue(rows)
        haystack = '\n'.join(records) + '\n'.join(
            f'{r.event} {r.reason} {r.detail} {r.sub} {r.issuer}' for r in rows)
        for tok in tokens:
            self.assertNotIn(tok, haystack)
            self.assertNotIn(tok.split('.')[2], haystack)
            self.assertNotIn(tok.split('.')[1], haystack)


class TestAT16Origin(SsoCase):
    def test_at16_origin_check_direct(self):
        for origin in (None, 'null', 'http://oqb.test', 'https://evil.test', 'http://localhost',
                       'https://oqb.test.evil.test', 'https://oqb.test:8443'):
            with self.subTest(origin):
                resp = self.submit('teacher_viewer_user', origin=origin, client=self.app.test_client())
                self.assert_next(resp, '/sso/sms/failed', 401)
        self.assertEqual(len(self.audit('sso_login', 'bad_origin')), 7)
        self.assert_next(self.submit('teacher_viewer_user', origin='https://OQB.test/'), '/')

    def test_at16_origin_check_behind_proxy(self):
        from app import web_security
        original = self.app.wsgi_app
        self.app.config['PROXY_FIX_HOPS'] = 1
        web_security.init_app(self.app)
        self.addCleanup(setattr, self.app, 'wsgi_app', original)
        proxied = {'X-Forwarded-Proto': 'https', 'X-Forwarded-Host': 'oqb.test',
                   'X-Forwarded-For': '203.0.113.9'}
        internal = self.client.get('/sso/sms/failed', headers=proxied)
        self.assertEqual(internal.status_code, 200)
        for origin in (None, 'http://oqb.test', 'http://localhost'):
            with self.subTest(origin):
                self.assert_next(self.submit('teacher_viewer_user', origin=origin, headers=proxied,
                                             client=self.app.test_client()), '/sso/sms/failed', 401)
        self.assert_next(self.submit('teacher_viewer_user', headers=proxied), '/')
        [ok] = [r for r in self.audit('sso_login') if r.outcome == 'ok']
        self.assertEqual(ok.ip, '203.0.113.9')


class TestAT21PasswordLogin(SsoCase):
    def test_at21_password_login_unlinked_and_linked_legacy(self):
        for username in ('nogrants', 'teacher_a'):
            with self.subTest(username):
                client = self.app.test_client()
                resp = client.post('/login', data={'username': username, 'password': PASSWORD})
                self.assertEqual(resp.status_code, 302)
                self.assertTrue(self.session_user(client).startswith(f'{self.users[username]}:'))
        self.assertEqual(len(self.audit('password_login')), 2)


if __name__ == '__main__':
    unittest.main()
