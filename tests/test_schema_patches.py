"""Boot patch for the users account-state columns (app/sso_schema.py),
run against a scratch table in oqb_sso_test."""
import logging
import unittest

from sqlalchemy import text

from dbcase import assert_test_db, guard_set, raw_engine

PROBE = 'sso_probe_users'
LOG = logging.getLogger('test_schema_patches')


class SchemaPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not guard_set():
            raise unittest.SkipTest('DB tests need tools/run_tests.py (OQB_TEST_DB_GUARD)')
        cls.engine = raw_engine()
        assert_test_db(cls.engine)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        assert_test_db(self.engine)
        with self.engine.begin() as conn:
            conn.execute(text(f'DROP TABLE IF EXISTS {PROBE}'))
            conn.execute(text(
                f'CREATE TABLE {PROBE} (id INT PRIMARY KEY, username VARCHAR(80) NOT NULL)'))
            conn.execute(text(f"INSERT INTO {PROBE} VALUES (1, 'legacy')"))

    def tearDown(self):
        assert_test_db(self.engine)
        with self.engine.begin() as conn:
            conn.execute(text(f'DROP TABLE IF EXISTS {PROBE}'))

    def _columns(self):
        from app.sso_schema import _table_columns
        with self.engine.connect() as conn:
            return _table_columns(conn, PROBE)

    def test_legacy_table_is_upgraded_twice_idempotently(self):
        from app.sso_schema import USER_COLUMNS, patch_users
        self.assertTrue(patch_users(self.engine, LOG, table=PROBE))
        cols = self._columns()
        for name, _ddl in USER_COLUMNS:
            self.assertIn(name, cols)
        with self.engine.connect() as conn:
            row = conn.execute(text(
                f'SELECT active, session_version, local_only, display_name FROM {PROBE} WHERE id = 1')).one()
        self.assertEqual(tuple(row), (1, 0, 0, None))
        self.assertTrue(patch_users(self.engine, LOG, table=PROBE))
        self.assertEqual(self._columns(), cols)

    def test_partly_patched_table_gets_the_rest(self):
        from app.sso_schema import USER_COLUMNS, patch_users
        with self.engine.begin() as conn:
            conn.execute(text(f'ALTER TABLE {PROBE} ADD COLUMN active TINYINT(1) NOT NULL DEFAULT 1'))
        self.assertTrue(patch_users(self.engine, LOG, table=PROBE))
        self.assertTrue({n for n, _ in USER_COLUMNS} <= self._columns())

    def test_missing_table_reports_not_ready(self):
        from app.sso_schema import patch_users
        self.assertFalse(patch_users(self.engine, LOG, table='sso_probe_absent'))

    def test_database_error_reports_not_ready(self):
        from app.sso_schema import apply_sso_patches

        class BrokenEngine:
            def begin(self):
                raise RuntimeError('db down')

            connect = begin

        self.assertFalse(apply_sso_patches(BrokenEngine(), LOG))


if __name__ == '__main__':
    unittest.main()
