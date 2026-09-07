# 05 — Storage tree and path safety

> Two filesystem roots exist: a read-mostly question library (`SOURCE_PATH`) and an app-managed tree (`STORAGE_PATH`). `app/storage.py` is the single source of truth for locating anything in either. Decision record: [ADR-005](../decisions/ADR-005-unified-storage-tree.md). Browser/API surface: [../modules/file-browser.md](../modules/file-browser.md).

## Layout

```
<SOURCE_PATH>                    question assets; layout <SUBJ>/PP/<SOURCE>/<YEAR>/<PAPER>/ and <SUBJ>/QB/<DETAIL>/
<STORAGE_PATH>                   parent of the three managed roots (same drive as SOURCE_PATH)
  Shared\                        SHARED_PATH  - per-subject shared files, role-gated
    <SUBJECT_ID>\                admin role = read/write, user role = read-only, viewer = hidden
    _archive\                    migrated legacy Source_PDF (super-admin only)
  System\                        SYSTEM_PATH  - caches and staging (safe to clear when idle)
    doc_thumbnails\              DOC_THUMBNAIL_PATH default: <asset_id>.png
    .pdf_import\<token>\         PDF Batch Import staging (purged after 6 h)
    .toolbox\<token>\            PDF Tool sessions (purged after TOOLBOX_SESSION_RETENTION_HOURS, default 48)
    .smart_import\ .smart_import_uploads\ ImportBackups\<ts>\   Smart Import plans, uploads, backups
  User\                          USER_PATH
    <username>\                  personal home (owner has full CRUD in the browser)
      generated\                 generated documents (+ lazy PDF siblings); READ-ONLY in the browser
```

On the current host: `SOURCE_PATH=D:\oqb_data\Source`, `STORAGE_PATH=D:\oqb_data\Storage` (from `.env`; comments in older code mention `Q:\Source` / `Q:\Storage` — treat those as examples, not facts).

## Config keys (`.env` only, never runtime-tunable)

| Key | Default | Meaning |
|---|---|---|
| `SOURCE_PATH` | `<repo>/Source` | question library |
| `STORAGE_PATH` | sibling `Storage` of `SOURCE_PATH` | parent of Shared/System/User |
| `SHARED_PATH` / `SYSTEM_PATH` / `USER_PATH` | `<STORAGE_PATH>/Shared|System|User` | per-root override |
| `DOC_THUMBNAIL_PATH` | `<SYSTEM_PATH>/doc_thumbnails` | DOC thumbnail cache |
| `PDF_SOURCE_PATH` | `SHARED_PATH` | legacy server-side PDF library for import/toolbox pickers |
| `OUTPUT_PATH` | `<repo>/output` | legacy fallback for pre-migration generated files |
| `TOOLBOX_SAVE_SUBDIR` | `Saved` | subfolder used by PDF Tool "Save to server" |

Storage must be on the same drive as `SOURCE_PATH`; the browser's drive gate enforces this.

## `app/storage.py`

| Function | Use |
|---|---|
| `safe_join(base, *rel)` | Hardened join using `os.path.commonpath` + `normcase`; returns `None` when the result escapes `base` (also across drives). Fixes the sibling-prefix trap (`Q:\Source` vs `Q:\SourceBackup`). **Use this everywhere; never `startswith`.** |
| `source_path()`, `storage_path()`, `shared_path()`, `system_path()`, `user_path()`, `output_path()` | root accessors reading `current_app.config` |
| `safe_username(user)` | filesystem-safe folder name; verbatim for names passing `validate_username`, sanitised (never empty) for legacy names |
| `user_home(user)`, `user_generated_dir(user)`, `ensure_user_generated_dir(user)` | per-user paths |
| `shared_subject_dir(subject_id)`, `shared_archive_dir()` | Shared roots |
| `ensure_storage_tree()` | called at startup; best-effort `makedirs`, swallows `OSError` |

## Rules

- All reads and writes under a root go through `safe_join`; a `None` result is a 403/404, never a fallback.
- Serve files only via authenticated routes; no `send_from_directory` on a root without a registry check.
- `User/<name>/generated/` is managed by My Files (`generated_files` rows). The File Browser treats it as read-only and refuses to move files into or out of it so rows never orphan.
- Renaming a user moves `User/<old>` → `User/<new>` (Admin → Users). Usernames are validated to be safe folder names ([02-auth-and-permissions.md](02-auth-and-permissions.md)).
- Extra browser roots for super admins are stored as a JSON list in `system_settings.FILE_BROWSER_EXTRA_ROOTS` (not a REGISTRY scalar) and appear only in admin scope.
- New app-written artefacts belong under `System/` (caches, staging) or the owning user's home — never in the repo, never in `SOURCE_PATH` unless they are question assets created through the asset routes.
- Uploads: every path component passes through `secure_filename`; folder uploads preserve structure via `paths[]` but stay inside the target root.

## Migration

`python cli.py migrate-storage [--dry-run|--no-dry-run] [--old-pdf-source P] [--old-thumbnails P]` builds the tree, creates per-subject `Shared/` folders, moves thumbnails to `System/`, relocates generated docs (+ PDF siblings) into each owner's `generated/`, and archives `Source_PDF` into `Shared/_archive`. Idempotent; skips existing targets. Dry-run is the default.
