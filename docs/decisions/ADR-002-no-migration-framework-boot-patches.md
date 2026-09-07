# ADR-002 — No migration framework; idempotent boot patches

**Status:** accepted, in force.

## Context

There is exactly one MariaDB database (production) and one deployment, operated by the same people who run the app. Early schema additions were nullable columns that `create_all()` could not add to existing tables, so root-level `migrate_*.py` scripts appeared and had to be run by hand after each upgrade — and were forgotten.

## Decision

Schema changes ship as **idempotent patches inside `create_app()`** (`app/__init__.py`):

- new tables: `Model.__table__.create(db.engine, checkfirst=True)`;
- new columns / enum widening / indexes: check `INFORMATION_SCHEMA.COLUMNS` for `TABLE_SCHEMA = DATABASE()` and `ALTER TABLE` only when missing;
- one-time back-fills guarded by the same "just added" flag;
- each block wrapped in `try/except Exception: pass` so a broken or pre-init DB still boots.

No Alembic. The three historical `migrate_*.py` scripts are superseded and must not be extended.

## Consequences

- Upgrading is "pull and let the server reload"; there is no migration step for operators to forget.
- Every process that calls `create_app()` (server, CLI, some tests) mutates the live schema if it is behind — agents must treat `create_app()` as a write operation ([../core/01-runtime-and-ops.md](../core/01-runtime-and-ops.md)).
- Patches must be MariaDB-syntax correct on first try; there is no staging database to rehearse against.
- Downgrades are not supported; recovery is restore-from-backup.
- The record of what changed lives in [../reference/schema-history.md](../reference/schema-history.md) and must be appended for every new patch ([../core/03-data-model-and-migrations.md](../core/03-data-model-and-migrations.md)).
