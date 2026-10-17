"""DB test base for SMS sign-in: the fixture trust list in the test DB, the
subject map, linked accounts, and helpers to submit tokens like the landing
page does. Runs only under tools/run_tests.py (see dbcase)."""
import contextlib

import smskit
from dbcase import DbTestCase, _password_hash

ORIGIN = 'https://oqb.test'

# SMS subject code -> OQB subjects (contract AT03: MATH -> MATC, MAT1, MAT2).
MAPPING = {'MATH': ('MATC', 'MAT1', 'MAT2'), 'PHY': ('ICT',), 'M2': ('MAT2',)}
# fixture person -> (OQB username, issuer). teacher_a has local grants.
LINKS = {
    'teacher': ('teacher_a', smskit.PRIMARY),
    'panel_admin': ('panel', smskit.PRIMARY),
    'school_admin': ('school', smskit.PRIMARY),
    'access_only': ('access', smskit.PRIMARY),
    'test_account': ('tester', smskit.SECONDARY),
}


class SsoCase(DbTestCase):
    def setUp(self):
        super().setUp()
        from app import db, sms_clock, sso
        from app.models import ExternalIdentity, SmsSubjectMap, User
        self._stack = contextlib.ExitStack()
        self._stack.enter_context(sms_clock.frozen(smskit.REFERENCE_NOW))
        self.addCleanup(self._stack.close)
        self.app.config.update(SMS_SSO_ENABLED=True, PUBLIC_ORIGIN=ORIGIN, SMS_JIT_ENABLED=False,
                               PROXY_FIX_HOPS=0)
        sso._limiter.reset()
        smskit.install_db_trust()
        for code, sids in MAPPING.items():
            for sid in sids:
                db.session.add(SmsSubjectMap(sms_uuid=smskit.subject_uuid(code), oqb_subject_id=sid))
        for person, (username, issuer) in LINKS.items():
            if username not in self.users:
                u = User(username=username, password_hash=_password_hash())
                db.session.add(u)
                db.session.flush()
                self.users[username] = u.id
            db.session.add(ExternalIdentity(user_id=self.users[username], issuer=issuer,
                                            sub=smskit.person(person)['sub'], link_method='admin'))
        db.session.commit()

    # ------------------------------------------------------------ helpers
    def token(self, name):
        return smskit.read(smskit.case(name)['file'])

    def at(self, name):
        """Freeze the clock to a fixture case's ``verify_at``."""
        from app import sms_clock
        return sms_clock.frozen(smskit.case(name)['verify_at'])

    def post_token(self, token, *, origin=ORIGIN, client=None, as_json=True, headers=None):
        client = client or self.client
        hdrs = {'Accept': 'application/json'} if as_json else {}
        if origin is not None:
            hdrs['Origin'] = origin
        hdrs.update(headers or {})
        if as_json:
            return client.post('/sso/sms', json={'token': token}, headers=hdrs)
        return client.post('/sso/sms', data={'token': token}, headers=hdrs)

    def submit(self, name, **kw):
        with self.at(name):
            return self.post_token(self.token(name), **kw)

    def assert_next(self, resp, path, status=200):
        self.assertEqual(resp.status_code, status, resp.get_data(as_text=True)[:300])
        self.assertEqual(resp.get_json(), {'next': path})

    def session_user(self, client=None):
        with (client or self.client).session_transaction() as sess:
            return sess.get('_user_id')

    def levels(self, username):
        from app import authz, db
        from app.models import User
        db.session.rollback()  # new snapshot: record_now commits elsewhere
        u = db.session.get(User, self.users[username])
        authz.invalidate(u)
        return dict(authz.effective_levels(u))

    def audit(self, event=None, reason=None):
        from app import db
        from app.models import AuthAuditLog
        db.session.rollback()  # new snapshot: record_now commits elsewhere
        q = AuthAuditLog.query
        if event:
            q = q.filter_by(event=event)
        if reason:
            q = q.filter_by(reason=reason)
        return q.order_by(AuthAuditLog.id).all()

    def user_id_of(self, username):
        from app import db
        from app.models import User
        db.session.rollback()  # new snapshot: record_now commits elsewhere
        u = User.query.filter_by(username=username).first()
        return u.id if u else None
