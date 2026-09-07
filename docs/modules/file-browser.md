# File Browser and File Selector

> One root-aware, permission-checked filesystem API (`files_bp`, `/files/api/*`) behind three UIs: the super-admin browser (`/admin/files`), the per-user browser (`/files/browser`), and the reusable `OQBFileSelector` modal used by Smart Import, PDF Import and the Toolbox.

The storage tree layout (`Source`, `Storage/{Shared,System,User}`), `safe_join`, path accessors and the `.env` path keys are documented in [../core/05-storage-and-paths.md](../core/05-storage-and-paths.md) and [../decisions/ADR-005-unified-storage-tree.md](../decisions/ADR-005-unified-storage-tree.md). This page covers only the browsing/selection layer built on top of them.

## Files

| File | Role |
|---|---|
| `app/files_service.py` | `Root`, `RootRegistry` (scopes), `FileServiceError`, `allowed_drive`, `path_on_allowed_drive`, `_load_extra_roots` / `_save_extra_roots`, `ensure_root_dir`, `_resolve`, `_require_write`, pure ops `list_dir`, `get_download_path`, `save_uploads`, `rename`, `delete`, `mkdir`, `copy`, `move`, `_unique_copy_name`. Constants `ROOT_SOURCE='source'`, `ROOT_STORAGE='storage'`, `ROOT_USER='user'`, prefixes `extra:` / `shared:`, `FILE_BROWSER_ROOTS_KEY='FILE_BROWSER_EXTRA_ROOTS'`. |
| `app/files.py` | Blueprint `files_bp` (`url_prefix='/files'`): page `/browser` + JSON API. Helpers `_request_scope`, `_request_root`, `_can_use_browser`, `_is_generated_path`, `_err`. |
| `app/storage.py` | `safe_join`, `source_path`, `storage_path`, `shared_subject_dir`, `user_home`, `safe_username`. See core/05. |
| `app/admin.py` | `GET /admin/files` renders the super-admin shell (section `File Browser (Super Admin Only)`). |
| `templates/admin_files.html` | Super-admin page: includes the shared partials with `fb_can_manage_roots=True`, `fb_scope='admin'`. |
| `templates/files_browser.html` | Per-user page (My Stuff -> File Browser): same partials with `fb_can_manage_roots=False`, `fb_scope='user'`. |
| `templates/partials/file_browser_css.html`, `file_browser_body.html`, `file_browser_js.html` | Shared browser UI. JS state: `ROOTS`, `ALLOWED_DRIVE`, `FB_CAN_MANAGE_ROOTS`, `FB_SCOPE`, `currentRoot`, `currentPath`, `selectedItems`, `clipboard[]`, `clipboardMode`. `URLS` map built from `url_for('files.api_*')`. |
| `templates/partials/file_selector.html` | `#oqbFsModal` + `window.OQBFileSelector = { open }`. Include once per page. |
| `templates/base.html` | Navbar link to `files.browser` for eligible users. |
| `app/toolbox/pdf.py`, `app/admin.py` (PDF Import) | Consumers that accept `{root, rel_path}` (see Backends below). |

## Tables

Schema reference: [../core/03-data-model-and-migrations.md](../core/03-data-model-and-migrations.md).

| Model | Use |
|---|---|
| `SystemSetting` | Row `key='FILE_BROWSER_EXTRA_ROOTS'`, `value` = JSON list of absolute paths, `updated_by` = current user. Read by `_load_extra_roots`, written by `_save_extra_roots`. Not part of `app/settings.py` `REGISTRY`. |
| `Subject` | Labels for `shared:<SID>` roots. |
| `User` / `UserSubjectPermission` | `user.get_subject_roles()` decides which `Shared/<subject>` roots appear and whether they are writable; `is_super_admin`, `is_all_view_only()` gate access. |
| `GeneratedFile` | Not touched here — that is why `User/<name>/generated/` is read-only in the browser. |

## Routes

### Pages

| Method | Path | Authz | Purpose |
|---|---|---|---|
| GET | `/admin/files` | `@super_admin_required` | `admin_files.html` with `roots=RootRegistry(user, scope='admin').list_dicts()`, `allowed_drive`, `fb_can_manage_roots=True`, `fb_scope='admin'`. |
| GET | `/files/browser` | `@login_required` + `_can_use_browser` (403 otherwise) | `files_browser.html`, **always user scope** even for super admins; ensures the first root dir exists. |

### API (`files_bp`, all `@login_required`)

Every call carries `root` (root id) and `scope` (`admin`|`user`) as query, form or JSON field. `_request_scope()` returns `admin` only if the client asked for it; `RootRegistry` then downgrades non-super-admins to `user`. `_request_root()` resolves the root via the registry (blank id = first root); unknown root -> 400 `Invalid root`. Non-eligible users (pure viewers) get 403 on everything. Errors are `{error}` with the `FileServiceError.status`.

| Method | Path | Body / query | Response |
|---|---|---|---|
| GET | `/files/api/roots` | `scope` | `{roots:[{id,label,can_write,removable,missing,path?}], allowed_drive}`; `path` only for admin-scope roots (`expose_path`). |
| GET | `/files/api/list` | `root, path` | `{current_path, items:[{name,is_dir,size(null for dirs),modified(epoch)}]}`; dirs first, case-insensitive name order. Creates the root dir on demand. |
| GET | `/files/api/download` | `root, path` | `send_file(..., as_attachment=True)`; 400 without path, 404 if not a file. Also used as a preview URL by Smart Import. |
| POST | `/files/api/upload` | multipart `files[]`, `paths[]` (parallel relative paths), form `path` (target dir), `root`, `scope` | `{success, uploaded[], errors[], message}`. `paths[]` entries create intermediate folders (every component `secure_filename`-sanitised, `.`/`..` dropped) so folder uploads keep their tree. |
| POST | `/files/api/rename` | JSON `{path, new_name}` | `{success, new_name}`; 400 on separators in `new_name`, 409 if the target exists. |
| POST | `/files/api/delete` | JSON `{paths[]}` | `{success, deleted[], errors[], message}`; refuses the root itself; `rmtree` for folders. |
| POST | `/files/api/mkdir` | JSON `{path, name}` | `{success, name, rel_path}`; 409 if exists. |
| POST | `/files/api/copy` | JSON `{sources[], dest_dir}` | `{success, copied:[{original,new_name}], errors[], message}`; same root only; name clashes get `_copy`, `_copy2`, ... via `_unique_copy_name`; cannot copy a folder into itself/subtree. |
| POST | `/files/api/move` | JSON `{sources[], dest_dir}` | `{success, moved:[{original,new_name}], errors[]}`; `shutil.move` within one root; rejects moving a folder into its own subtree; `already in this folder` error for no-ops; collisions auto-uniquified. |
| POST | `/files/api/roots/add` | JSON `{path}` — **super admin only** (403 otherwise) | Validates `os.path.isdir`, `path_on_allowed_drive` (400 `Root must be on the <drive> drive.`), not already registered (409). Appends to `FILE_BROWSER_EXTRA_ROOTS`. Returns `{success, roots}` (admin scope). |
| POST | `/files/api/roots/remove` | JSON `{id}` — super admin only | Only ids starting with `extra:` (400 otherwise); 404 if not found. Returns `{success, roots}`. |

There are no `/admin/files/roots/*` routes; root management lives only in `files_bp`.

## Business rules / invariants

### Scopes (`RootRegistry(user, scope=None)`)

- **admin scope** — honoured only when `scope='admin'` **and** `user.is_super_admin`; anyone else is silently downgraded (no escalation). Roots: `source` (`Source`, `SOURCE_PATH`), `storage` (`Storage`, `STORAGE_PATH` — covers Shared/System/User), plus one `extra:<normcase(abs path)>` root per entry in `FILE_BROWSER_EXTRA_ROOTS` (deduplicated against built-ins). All rw, `expose_path=True`. Used **only** by `/admin/files`.
- **user scope** (default, including for super admins) — `user` root labelled `My Files` at `storage.user_home(user)` (rw, created on demand); then for each `(subject, role)` in `user.get_subject_roles()` sorted by id: `shared:<SID>` labelled `Shared · <name>` at `storage.shared_subject_dir(SID)`, rw for `admin`, ro for `user`, omitted for `viewer`/unknown. Paths are never exposed. A super admin gets every subject as `admin`. Used by `/files/browser`, the selector, PDF Import and Toolbox resolvers.
- `resolve(root_id)`: blank -> first root; exact match; then case-insensitive match (extra roots encode a path).
- `Root.to_dict()` includes `missing` (dir absent) so the UI can flag it.

### Path safety and write gating

- Every join goes through `storage.safe_join` (`_resolve`); escapes raise `FileServiceError(400, 'Invalid path')`. Never use `startswith` checks.
- Mutating ops call `_require_write(can_write)` -> 403 `This location is read-only.` for `shared:<SID>` roots where the user is only `user`.
- `User/<name>/generated/` is read-only in the browser: `_is_generated_path` blocks upload/rename/delete/mkdir/copy-into and **move into or out of** it (403 `The generated/ folder is managed in My Files...`). This prevents orphaning `GeneratedFile` rows.
- Extra roots must be on the same drive as `SOURCE_PATH` (`allowed_drive()` = drive of `storage.source_path()`, normcased with trailing separator).
- Copy/move never cross roots; `sources` and `dest_dir` are both relative to the one `root` in the request.

### Frontend (`file_browser_js.html`)

- `withRoot(url)` appends `&root=<currentRoot>&scope=<FB_SCOPE>` to GETs; POST bodies include `root` and `scope`.
- Clipboard: `clipboard[]` + `clipboardMode` (`copy` | `cut`). Per-row and selection Copy/Cut buttons switch mode (switching clears the clipboard). Paste calls `/api/copy` or `/api/move` and relabels the button (`Paste here` / `Move here`); a successful cut-paste clears the clipboard. **Duplicate** (files only) copies into the item's own parent.
- Upload modal accepts files and whole folders (`Add folder` uses `webkitdirectory`; drag-and-drop recurses dropped directories through `webkitGetAsEntry`); each file is appended with its `paths` entry (`relPath`).
- Deleting a folder (single or batch) requires typing `DELETE` (`#btnSingleDelete`, `#btnBatchDelete` stay disabled otherwise).
- Root management UI (add/remove, shows `ALLOWED_DRIVE`) renders only when `fb_can_manage_roots` is true; `refreshRoots()` re-fetches `/api/roots` after add/remove.

### File selector (`partials/file_selector.html`)

```js
OQBFileSelector.open({
  mode: 'file' | 'folder',
  multiple: false,            // folder mode only: checkbox multi-pick
  extensions: ['.pdf'],       // file mode filter, lowercase
  title: 'Choose a PDF',
  preferRootId: 'shared:MATC',
  locationKey: 'pdf-import',  // remembered dir key (default: mode|extensions)
  onPick(sel) { /* {root_id, root_label, rel_path, name, token} or array when multiple */ }
});
```

- Always **user scope** (`/files/api/roots?scope=user`), regardless of who is logged in.
- `token` is `root_id|rel_path`; pasting it into the path box of another selector jumps there.
- Remembers the last `{root, path}` per `locationKey` in JS memory (`state.rememberedLocations`) until page reload — no `localStorage`. `preferRootId` wins over the remembered location when it exists in the root list.
- Sorting by clicking the Name / Size / Modified headers (`state.sortField`, `state.sortDir`); no sort dropdown. Filter box narrows the current listing. New-folder button uses `/api/mkdir` when the root is writable.
- In multi-folder mode, `Add current folder` adds `currentPath`, and the Choose button reads `Use selected folders`.
- Consumers: PDF Import (single file, `.pdf`), Toolbox PDF Tool, Smart Import (`mode:'folder', multiple:true, locationKey:'smart-import'`).

### Backends accepting `{root, rel_path}`

| Consumer | Resolver | Fields |
|---|---|---|
| PDF Import (`app/admin.py`) | `_resolve_server_pdf(rel_path, root_id=None)` — root-aware via user-scope registry; blank root falls back to legacy `PDF_SOURCE_PATH`. | `stage`: `que_server_root` / `sol_server_root`; `guess-paper`: `server_root`. |
| Toolbox PDF (`app/toolbox/pdf.py`) | `_resolve_root_base(root_id, require_write=False)` | upload `server_root`; `export-save-start` and `mkdir` use `dest_root` with `require_write=True`. |
| Smart Import (`app/admin.py`) | `_smart_import_resolve_root(root_id)` -> `RootRegistry(current_user).resolve` | `sources:[{root_id, rel_path}]`. |

## Settings & config keys

Reference: [../core/06-system-settings.md](../core/06-system-settings.md) and [../core/05-storage-and-paths.md](../core/05-storage-and-paths.md).

- `SOURCE_PATH`, `STORAGE_PATH` (and derived `SHARED_PATH`, `SYSTEM_PATH`, `USER_PATH`) from `.env` via `app/storage.py`.
- `FILE_BROWSER_EXTRA_ROOTS` — `system_settings` row, JSON list of absolute paths. Deliberately **not** in the settings `REGISTRY` (it is a list, not a scalar); `settings.load_all` logs and ignores it as an unknown key. Managed exclusively by `/files/api/roots/add|remove`.
- `PDF_SOURCE_PATH` — legacy fallback used by `_resolve_server_pdf` when no root id is supplied.

## Permissions

See [../core/02-auth-and-permissions.md](../core/02-auth-and-permissions.md).

- Browser + API eligibility (`_can_use_browser`): super admin, or any subject role other than `viewer` (`not user.is_all_view_only()`). Pure viewers get 403 on `/files/browser` and every `/files/api/*` call.
- Root visibility and `can_write` come from `RootRegistry` (see Scopes). The server never trusts client-side `can_write`; every mutating op re-checks `root.can_write`.
- `/admin/files` and the admin root set: super admin only. `/files/api/roots/add|remove`: super admin only (checked inline, not via decorator).
- Super admins in user scope see all `Shared/<subject>` roots as rw plus their own `My Files`; they do not see `Source`/`Storage` there.

## Background work / SSE / threads

None. All operations are synchronous request/response; large folder deletes/copies (`shutil.rmtree` / `copytree`) block the request.

## Gotchas

- Sending `scope=admin` from a non-super-admin does not error; you silently get user-scope roots, so an unknown `root` id then yields 400 `Invalid root`.
- `/files/browser` is user scope for everyone. The admin root set (Source, Storage, extras) is only on `/admin/files`; do not expect `source`/`storage` ids to resolve on the user page.
- Blank `root` resolves to the **first** root (My Files in user scope, Source in admin scope). Always pass an explicit root when acting on shared folders.
- `generated/` under My Files is read-only here even though the root is rw; manage those files in My Files (`/user/files`). Moves both into and out of it are blocked.
- `copy`/`move` are same-root only. Moving between `Shared/MATC` and `My Files` requires download + upload.
- Upload sanitises every path component with `secure_filename`; non-ASCII folder or file names are transformed or dropped (an all-empty result is reported in `errors`).
- Extra roots are stored normcased in their id (`extra:q:\other`) but the JSON keeps the original absolute path; removal matches by normcase.
- Extra roots must be on the `SOURCE_PATH` drive. Network shares on another letter cannot be added.
- `list_dir` swallows `PermissionError` and returns an empty listing rather than an error.
- `_unique_copy_name` inserts `_copy`/`_copyN` before the extension for both copy and move collisions; a move never overwrites.
- The selector keeps remembered locations in memory only; reloading the page forgets them. `preferRootId` takes precedence when present.
- Root ids are opaque tokens, not paths, for user-scope roots. Store `root_id` + `rel_path` (or the `token`) in consumer forms rather than absolute paths.
- Include `file_selector.html` once per page; the module is idempotent (`if (window.OQBFileSelector) return;`) but the modal markup is not.

## Related

- [../core/05-storage-and-paths.md](../core/05-storage-and-paths.md) — tree layout, `safe_join`, path config, `migrate-storage`.
- [../decisions/ADR-005-unified-storage-tree.md](../decisions/ADR-005-unified-storage-tree.md).
- [admin-panel.md](admin-panel.md) — `/admin/files` shell, System Settings.
- [ingestion.md](ingestion.md) — Smart Import's multi-folder selector usage.
- [pdf-import.md](pdf-import.md), [toolbox-pdf.md](toolbox-pdf.md) — other `{root, rel_path}` consumers.
- [my-files.md](my-files.md) — My Files, which owns `generated/`.
- [../core/02-auth-and-permissions.md](../core/02-auth-and-permissions.md), [../core/03-data-model-and-migrations.md](../core/03-data-model-and-migrations.md), [../core/04-backend-conventions.md](../core/04-backend-conventions.md), [../core/06-system-settings.md](../core/06-system-settings.md).
