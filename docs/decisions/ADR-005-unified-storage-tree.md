# ADR-005 — One `STORAGE_PATH` tree beside a read-mostly `SOURCE_PATH`

**Status:** accepted, in force.

## Context

App-written files had spread across `OUTPUT_PATH` (generated docs in one flat folder for all users), a `Source_PDF` folder for scanned papers, a hidden `.doc_thumbnails` cache, and per-tool staging dirs. Permissions were ad hoc and the file browser had to special-case each location.

## Decision

Keep `SOURCE_PATH` as the canonical, read-mostly question library (its layout is dictated by the QID grammar). Put **everything the app writes** under one `STORAGE_PATH` with three roots:

- `Shared/<SUBJECT_ID>/` — role-gated per subject (admin rw, user ro, viewer hidden), plus `_archive/` for the migrated `Source_PDF`;
- `System/` — caches and staging (`doc_thumbnails/`, `.pdf_import/`, `.toolbox/`);
- `User/<username>/` — personal home with a read-only `generated/` folder owned by My Files.

All path resolution goes through `app/storage.py` (`safe_join` and root accessors); all browsing through one root-aware API (`files_bp`) driven by `files_service.RootRegistry`. A one-time, idempotent `cli.py migrate-storage` relocates legacy files.

## Consequences

- Permissions follow the tree: the subject role decides Shared access, ownership decides User access, super admins get admin scope with extra roots.
- Usernames double as folder names, so the username policy is a filesystem policy and renames move folders ([../core/02-auth-and-permissions.md](../core/02-auth-and-permissions.md)).
- `OUTPUT_PATH` survives only as a fallback for un-migrated files.
- Storage and Source must share a drive (drive gate in the browser).
- Layout and rules: [../core/05-storage-and-paths.md](../core/05-storage-and-paths.md); API and UI: [../modules/file-browser.md](../modules/file-browser.md).
