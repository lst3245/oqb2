# 01 — Runtime and operations

> How to run, test, and operate OQB2 on the host it actually lives on. Read this before running any command. The safety NEVER/ALWAYS list is in [`.cursor/rules/safety-ops.mdc`](../../.cursor/rules/safety-ops.mdc).

## The environment you are in

| Fact | Value |
|---|---|
| OS | Windows (PowerShell shell) |
| Repo | `D:\oqb2` (git, branch `main`) |
| Interpreter | System Python 3.14 — `C:\Users\admin\AppData\Local\Programs\Python\Python314\python.exe`. **No virtualenv exists**; `venv/` and `.venv/` are absent. `python` on PATH is the right one. Do not conclude deps are missing because a venv is absent. |
| Dependencies | `requirements.txt` installed into system Python. `pytest` is **not** installed; tests use `unittest`. |
| Database | One MariaDB database (`DB_NAME` from `.env`) — **live production data**. There is no test database. |
| Data roots | `SOURCE_PATH=D:\oqb_data\Source` (question assets), `STORAGE_PATH=D:\oqb_data\Storage` (Shared / System / User). Both from `.env`. |
| Dev server | `python run.py` from `D:\oqb2` → Flask debug server on `0.0.0.0:5000`. It is normally **already running** in a user terminal (`dev-server.bat` restart loop) and serving LAN users. |
| External binaries | Microsoft Word (COM, required for DOC merge / PDF / DOC thumbnails), pandoc (MD → docx), Tesseract (optional OCR). |

## Live vs disposable

| Resource | Live? | Notes |
|---|---|---|
| MariaDB `DB_NAME` | **Live** | Only database. Every `create_app()` call connects and runs boot patches. |
| `D:\oqb_data\Source` | **Live** | Teachers' question library. Ingest is additive; `sync --no-dry-run` deletes DB rows for missing files. |
| `D:\oqb_data\Storage\User\<name>\generated` | **Live** | Users' generated documents; rows in `generated_files`. |
| `D:\oqb_data\Storage\System\` | Semi-disposable | Caches (DOC thumbnails), PDF Import + Toolbox staging. Safe to clear when no job is running; thumbnails regenerate lazily. |
| `output/` in repo | Legacy | Fallback for pre-migration generated files; gitignored. |
| `/tmp`-style scratch | Disposable | Use `$env:TEMP` for throwaway scripts; never write scratch files into the repo. |

## Running things

```powershell
cd D:\oqb2

# Dev server (usually already running - check the terminal first; do not start a second one on :5000)
python run.py

# Tests (unittest; fast; no network)
python -m unittest discover -s tests
python -m unittest tests.test_sorting -v          # one module
python -m unittest tests.test_hierarchy -v        # QNO grammar / sort / render plan / grouping / split-box (no live DB)

# CLI
python cli.py ingest                              # additive scan of SOURCE_PATH into the live DB
python cli.py ingest --source-path "D:\other"
python cli.py sync                                # DRY RUN: list orphaned DB rows (safe)
python cli.py sync --no-dry-run [--force]         # DELETES orphaned rows - only when the user asks
python cli.py migrate-storage --dry-run           # storage tree migration preview (idempotent)
```

Test caveats:
- `tests/test_llm_client.py` calls `create_app()` in `setUp`, which **connects to the live DB and runs boot patches**. Prefer not to run it casually; run targeted modules instead.
- `tests/test_hierarchy.py` is pure logic (no `create_app()`). Use it when changing QNO grammar, `resolve_render_plan`, dashboard grouping, or split-box labels.
- Tests import `app.*` directly; run from the repo root so `app` is importable.
- There is no coverage tooling or CI. Add a `tests/test_<module>.py` with the change when you touch pure logic (sorting, parsers, layout helpers, prompt builders).

## Boot side effects of `create_app()`

Anything that imports and calls `create_app()` (server, `init_db.py`, `cli.py`, `test_llm_client`) will, in order:

1. `load_dotenv(.env, override=True)` and build the DB URI.
2. `ensure_storage_tree()` — creates `Shared/ System/ User/` dirs if missing.
3. Mark stale `generated_files` rows (`pending`/`generating`) as `failed`.
4. Run idempotent schema patches (`CREATE TABLE ... checkfirst`, `INFORMATION_SCHEMA`-guarded `ALTER TABLE`) — see [03-data-model-and-migrations.md](03-data-model-and-migrations.md).
5. `ai_prompts.ensure_seeded()` — insert built-in prompt variant rows if missing.
6. `settings.load_all(app)` — overlay DB-backed tunables onto `app.config`.

All steps swallow exceptions so the app boots on a broken DB; you will see fallbacks, not crashes.

## Scripts at the repo root

| Script | Status | Run it? |
|---|---|---|
| `run.py` | current | Yes (if not already running) |
| `cli.py` | current | `ingest` / `sync` (dry) are safe; `sync --no-dry-run` and `migrate-storage --no-dry-run` only on request |
| `init_db.py` | current bootstrap | **Never on the live DB.** Creates tables (no-op if present) and, if `subjects` is empty, seeds subjects + `admin/admin123` + sample topics. |
| `test_db.py` | diagnostic | Read-only connectivity check using `.env` |
| `debug_env.py` | leftover | **Do not run** — prints `.env` values and contains a hardcoded password. Pending deletion (see STATUS). |
| `migrate_versions.py`, `migrate_starring.py`, `migrate_md_format.py` | historical one-offs | No — superseded by boot patches |
| `import_dse_p2.py`, `tag_topics.py` | historical one-off data scripts with hardcoded `Q:\Temp` paths | No |
| `dev-server.bat`, `quickstart.bat` | human helpers | Not from an agent session |

## Deployment shape

Single Flask process (`debug=True` today) behind nothing. Production guidance (in [`docs/manuals/ADMIN_GUIDE.md`](../manuals/ADMIN_GUIDE.md)): `FLASK_DEBUG=0`, strong `SECRET_KEY`, a WSGI server with **one worker** (hot-reload of System Settings, the SSE cancel registry, and the Word COM lock are all in-process), reverse proxy with `proxy_buffering off` for SSE, regular DB + `Storage` backups.

## Backups and recovery (do not touch prod to "fix" things)

- DB: `mysqldump` of `DB_NAME`; restore into a **new** database name and point a copy of `.env` at it if you need to experiment.
- Files: `SOURCE_PATH` and `STORAGE_PATH` are plain trees; copy them.
- If a boot patch or script misbehaved: stop, report, and let the user restore from backup. Do not attempt schema rollbacks by hand against the live DB.
- Word COM stuck: there is **no enforced per-job watchdog** (`WORD_COM_TIMEOUT` is registered but unused); `word_session` cleans up its own `WINWORD.EXE` on exit, and a wedged instance is ended by the user from Task Manager. Never kill the Flask server PID to "reset" Word.

## Scenario to keep in mind

The dev server in the user's terminal is the production instance for a school department on the LAN (request logs show other machines' IPs). An agent that "restarts the server to pick up changes" or runs `init_db.py` "to make sure tables exist" would be acting on live data with real users connected. Flask's debug reloader already picks up Python changes; templates reload on request. There is nothing to restart.
