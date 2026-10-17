"""Shared fixture for tests that need the database.

Runs ONLY against the throwaway database ``oqb_sso_test`` (launched through
``tools/run_tests.py``, which sets ``OQB_TEST_DB_GUARD``). Before anything is
dropped or created it checks both the guard variable and ``SELECT DATABASE()``
on a raw engine, and checks again before every reset. Without the guard the
DB tests skip; they never touch another database.
"""
import os
import unittest

from flask import g, has_app_context
from flask.testing import FlaskClient
from sqlalchemy import create_engine, text

GUARD = 'oqb_sso_test'

SUBJECTS = ('MATC', 'MAT1', 'MAT2', 'ICT', 'ECON')
# username -> (is_super_admin, {subject: role})
USERS = {
    'super': (True, {}),
    'breakglass': (True, {}),
    'teacher_a': (False, {'MATC': 'user', 'ICT': 'viewer'}),
    'admin_matc': (False, {'MATC': 'admin'}),
    'viewer_ict': (False, {'ICT': 'viewer'}),
    'nogrants': (False, {}),
}
PASSWORD = 'pw-for-tests-only'

_APP = None
_HASH = None


def _password_hash() -> str:
    """One scrypt hash for every seeded user (hashing per test is slow)."""
    global _HASH
    if _HASH is None:
        from werkzeug.security import generate_password_hash
        _HASH = generate_password_hash(PASSWORD)
    return _HASH


def guard_set() -> bool:
    return (os.environ.get('OQB_TEST_DB_GUARD') == GUARD
            and os.environ.get('DB_NAME') == GUARD)


def assert_test_db(engine) -> None:
    if os.environ.get('OQB_TEST_DB_GUARD') != GUARD:
        raise RuntimeError('refusing: OQB_TEST_DB_GUARD is not oqb_sso_test')
    with engine.connect() as conn:
        name = conn.execute(text('SELECT DATABASE()')).scalar()
    if name != GUARD:
        raise RuntimeError(f'refusing: connected to {name!r}, not {GUARD!r}')


def raw_engine():
    from app.config import build_database_uri
    return create_engine(build_database_uri())


def rebuild_schema() -> None:
    """Drop and recreate every model table in the test DB."""
    from app import db
    import app.models  # noqa: F401  (registers the tables)
    engine = raw_engine()
    try:
        assert_test_db(engine)
        db.metadata.drop_all(engine)
        assert_test_db(engine)
        db.metadata.create_all(engine)
    finally:
        engine.dispose()


class FreshGClient(FlaskClient):
    """The test keeps an app context pushed, and Flask reuses it for each
    request, so ``g`` (Flask-Login's user, the authz level cache) would leak
    between requests. Clear it first, as a real server would."""

    def open(self, *args, **kwargs):
        if has_app_context():
            for key in list(vars(g)):
                delattr(g, key)
        return super().open(*args, **kwargs)


class FreshGClient(FlaskClient):
    """The test keeps an app context pushed, and Flask reuses it for each
    request, so ``g`` (Flask-Login's user, the authz level cache) would leak
    between requests. Clear it first, as a real server would."""

    def open(self, *args, **kwargs):
        if has_app_context():
            for key in list(vars(g)):
                delattr(g, key)
        return super().open(*args, **kwargs)


def get_app():
    global _APP
    if _APP is not None:
        return _APP
    rebuild_schema()
    from app import create_app, db
    app = create_app()
    app.config.update(TESTING=True)
    app.test_client_class = FreshGClient
    app.test_client_class = FreshGClient
    with app.app_context():
        assert_test_db(db.engine)
    _APP = app
    return app


class DbTestCase(unittest.TestCase):
    """Fresh seed per test: subjects, taxonomy, questions, users."""

    @classmethod
    def setUpClass(cls):
        if not guard_set():
            raise unittest.SkipTest('DB tests need tools/run_tests.py (OQB_TEST_DB_GUARD)')
        cls.app = get_app()

    def setUp(self):
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.reset()
        self.client = self.app.test_client()

    def tearDown(self):
        from app import db
        db.session.remove()
        self.ctx.pop()

    # ------------------------------------------------------------ seed
    def reset(self, attempts=3):
        from sqlalchemy.exc import OperationalError
        from app import db
        for attempt in range(attempts):
            try:
                return self._reset_once()
            except OperationalError as exc:
                db.session.rollback()
                if attempt == attempts - 1 or 'Deadlock' not in str(exc):
                    raise

    def _reset_once(self):
        from app import db
        from app.models import (Chapter, Question, Subchapter, Subject, Subtopic,
                                Topic, User, UserSubjectPermission)
        assert_test_db(db.engine)
        db.session.remove()
        with db.engine.begin() as conn:
            conn.execute(text('SET FOREIGN_KEY_CHECKS = 0'))
            for table in reversed(db.metadata.sorted_tables):
                conn.execute(table.delete())
            conn.execute(text('SET FOREIGN_KEY_CHECKS = 1'))

        for sid in SUBJECTS:
            db.session.add(Subject(id=sid, name=f'{sid} subject'))
        db.session.flush()

        self.users = {}
        for name, (is_super, roles) in USERS.items():
            u = User(username=name, is_super_admin=is_super, is_admin=is_super,
                     password_hash=_password_hash())
            if name == 'breakglass':
                u.local_only = True
            db.session.add(u)
            db.session.flush()
            for sid, role in roles.items():
                db.session.add(UserSubjectPermission(user_id=u.id, subject_id=sid, role=role))
            self.users[name] = u.id

        self.topics, self.subtopics, self.chapters, self.subchapters = {}, {}, {}, {}
        for sid in ('MATC', 'ICT', 'ECON'):
            t = Topic(subject_id=sid, name=f'{sid} topic')
            c = Chapter(subject_id=sid, name=f'{sid} chapter')
            db.session.add_all([t, c])
            db.session.flush()
            st = Subtopic(topic_id=t.id, name=f'{sid} subtopic')
            sc = Subchapter(chapter_id=c.id, name=f'{sid} subchapter')
            db.session.add_all([st, sc])
            db.session.flush()
            self.topics[sid], self.chapters[sid] = t.id, c.id
            self.subtopics[sid], self.subchapters[sid] = st.id, sc.id

        self.questions = {}
        for sid in ('MATC', 'ICT', 'ECON'):
            q = Question(qid=f'{sid}_DSE_2024_P1_Q1', subject=sid, source='DSE',
                         year=2024, paper='P1', qno=1)
            db.session.add(q)
            db.session.flush()
            self.questions[sid] = q.id
        db.session.commit()

    # ------------------------------------------------------------ helpers
    def user(self, name):
        from app import db
        from app.models import User
        return db.session.get(User, self.users[name])

    def login(self, name):
        """Sign ``name`` in on ``self.client`` the way Flask-Login stores it."""
        u = self.user(name)
        with self.client.session_transaction() as sess:
            sess['_user_id'] = u.get_id()
            sess['_fresh'] = True
        return u

    def api_post(self, url, json=None, **kw):
        return self.client.post(url, json=json if json is not None else {}, **kw)

    def assert_denied_json(self, resp):
        self.assertEqual(resp.status_code, 403, resp.get_data(as_text=True)[:300])
        body = resp.get_json()
        self.assertIsNotNone(body, 'expected the JSON deny envelope')
        self.assertIs(body.get('success'), False)
        self.assertTrue(body.get('error'))
