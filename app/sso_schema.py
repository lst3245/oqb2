"""Additive boot patches for account state and SMS sign-in
(docs/modules/sms-sso.md, docs/reference/schema-history.md).

Runs from ``create_app()`` before anything reads ``users``. Every step is
guarded by INFORMATION_SCHEMA and idempotent; a failure is logged and
reported as ``False`` so SMS sign-in stays disabled (fail closed). Nothing
here drops, retypes or rewrites existing rows.
"""
from __future__ import annotations

import logging

from sqlalchemy import text

# Column name -> MariaDB definition. Defaults fill existing rows.
USER_COLUMNS = (
    ('active', 'TINYINT(1) NOT NULL DEFAULT 1'),
    ('disabled_reason', 'VARCHAR(32) NULL'),
    ('disabled_at', 'DATETIME NULL'),
    ('display_name', 'VARCHAR(200) NULL'),
    ('name_zh', 'VARCHAR(200) NULL'),
    ('email', 'VARCHAR(254) NULL'),
    ('staff_code', 'VARCHAR(32) NULL'),
    ('session_version', 'INT NOT NULL DEFAULT 0'),
    ('local_only', 'TINYINT(1) NOT NULL DEFAULT 0'),
    ('last_login_at', 'DATETIME NULL'),
    ('last_login_method', 'VARCHAR(16) NULL'),
)


def missing_columns(existing, wanted=USER_COLUMNS):
    have = {c.lower() for c in existing}
    return [(name, ddl) for name, ddl in wanted if name.lower() not in have]


def alter_users_sql(missing, table: str = 'users') -> str:
    """One statement, so the columns arrive together or not at all."""
    clauses = ', '.join(f'ADD COLUMN {name} {ddl}' for name, ddl in missing)
    return f'ALTER TABLE {table} {clauses}'


def _table_columns(conn, table):
    return {row[0] for row in conn.execute(text(
        "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t"
    ), {'t': table})}


def patch_users(engine, logger, table: str = 'users') -> bool:
    """``table`` is fixed in code (tests point it at a scratch table)."""
    try:
        with engine.begin() as conn:
            cols = _table_columns(conn, table)
            if not cols:
                logger.warning('sso_schema: %s table missing; run init_db.py first', table)
                return False
            missing = missing_columns(cols)
            if missing:
                conn.execute(text(alter_users_sql(missing, table)))
                logger.info('sso_schema: added %s columns %s', table, ', '.join(n for n, _ in missing))
        with engine.connect() as conn:
            still = missing_columns(_table_columns(conn, table))
        if still:
            logger.error('sso_schema: users columns still missing: %s', ', '.join(n for n, _ in still))
            return False
        return True
    except Exception:
        logger.exception('sso_schema: users patch failed')
        return False


def apply_sso_patches(engine, logger=None) -> bool:
    """True when every SSO-related patch is in place."""
    logger = logger or logging.getLogger(__name__)
    return patch_users(engine, logger)
