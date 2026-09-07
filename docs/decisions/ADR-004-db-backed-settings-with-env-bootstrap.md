# ADR-004 — DB-backed System Settings with `.env` as bootstrap default

**Status:** accepted, in force.

## Context

Tunables (thumbnail width, Word timeouts, page size, LLM defaults, PDF import thresholds) kept accumulating as `.env` keys that only the machine operator could change, requiring a restart. Admins needed to adjust them from the UI without touching the server.

## Decision

- A REGISTRY in `app/settings.py` declares every tunable (type, default, group, label, help, validator, optional dynamic choices).
- `app/config.py` `Config` provides the bootstrap default from `.env`.
- A `system_settings` key/value table stores admin overrides; `settings.load_all(app)` overlays them onto `app.config` at boot, and the super-admin Settings page saves/resets rows and mirrors to `app.config` immediately (hot reload, single process).
- **Secrets and filesystem paths stay `.env`-only** and never appear in the UI.
- Consumers read `current_app.config[KEY]` — never `os.getenv` at call time.

## Consequences

- One place to add a tunable (registry + Config default + `.env.example` comment) and the UI renders it automatically.
- Precedence is `literal < Config < .env < DB row`; a value in `.env` may be silently overridden by a DB row — check the Settings page when a config "does not take effect".
- Hot reload is per process; a multi-worker deployment would need a fan-out mechanism (not built).
- Details and the current key table: [../core/06-system-settings.md](../core/06-system-settings.md).
