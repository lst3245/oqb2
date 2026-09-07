# 02 — Authentication and permissions

> Every route declares its authorization at the edge with a decorator from `app/utils.py`; services and queries then scope by subject using the `User` helpers. Read this before adding or changing any route.

## Authentication

- Flask-Login sessions (`login_manager` in `app/__init__.py`, `login_view = 'auth.login'`, `remember=True` on login).
- Routes: `GET|POST /login`, `GET /logout`, `GET|POST /register` (**admin-only** — there is no public self-registration), `GET /` redirects to dashboard or login. Code: `app/auth.py`.
- Passwords: `User.set_password` / `check_password` (Werkzeug hashes). No password reset flow exists; super admins reset passwords from Admin → Users.
- First account: `init_db.py` seeds `admin / admin123` only when the `subjects` table is empty. It sets the legacy `is_admin` flag but **not** `is_super_admin`, so on a fresh install you must promote that user in the DB (`UPDATE users SET is_super_admin=1 WHERE username='admin'`) before any admin page opens. Never run `init_db.py` on the live DB.

## Roles

Three tiers, evaluated in this order:

| Tier | Where stored | Grants |
|---|---|---|
| **Super admin** | `users.is_super_admin` | Everything, every subject, all admin pages, System Settings, Users, Subjects, LLM Endpoints, AI Prompts, super-admin File Browser, Health. `get_subject_roles()` returns `admin` for every subject. |
| **Subject admin** | `user_subject_permissions.role = 'admin'` | For that subject: tag/edit/create/delete questions and assets, ingest, AI Tools, PDF Import, Topics/Chapters, Export/Import, PDF Tool, read-write `Shared/<SUBJECT>`. Also unlocks the Admin navbar (`admin_required`). |
| **User** | `role = 'user'` | Browse, filter, select, generate documents, My Files, profiles, sets, read-only `Shared/<SUBJECT>`, Markup. |
| **Viewer** | `role = 'viewer'` | Browse only. Cannot generate (`can_generate()` false), excluded from the File Browser and the Shared folders. |
| (no row) | — | No access to that subject at all. |

`users.is_admin` is a **legacy column** kept for compatibility; do not read it for authorization. The `register` form still writes it; it means nothing.

## Decorators (`app/utils.py`)

| Decorator | Passes when | Use for |
|---|---|---|
| `@login_required` (Flask-Login) | authenticated | every non-auth route, applied first |
| `@admin_required` | super admin OR `has_any_admin_access()` (admin role on any subject) | admin panel pages that are not subject-specific |
| `@super_admin_required` | `is_super_admin` | Users, Subjects, System Settings, LLM Endpoints, AI Prompts, `/admin/files`, Health |
| `@subject_access_required` | super admin OR `has_subject_access(subject_id)` | read routes on a specific subject |
| `@subject_admin_required` | super admin OR `is_subject_admin(subject_id)` | mutating routes on a specific subject |

`subject_*_required` resolve `subject_id` from, in order: URL kwargs → `request.args` → `request.form` → JSON body key `subject_id`.

**Footgun:** if no `subject_id` can be found, both subject decorators **let the request through** (`if subject_id and not ...`). A route that identifies its target by `question_id` only is therefore protected only by `@login_required` unless it checks `question.subject_id` itself. Existing question routes in `app/admin.py` do this with helpers like `_require_md_admin(question)`; follow that pattern — load the object, then check `current_user.is_subject_admin(obj.subject_id)` (or `has_subject_access`) and `abort(403)`.

## `User` helpers (`app/models.py`)

| Method | Meaning |
|---|---|
| `has_subject_access(subject_id)` | any role on that subject (viewer / user / admin) or super admin |
| `is_subject_admin(subject_id)` | admin role or super admin |
| `has_any_admin_access()` | admin role on at least one subject or super admin |
| `can_generate()` | user or admin role on at least one subject, or super admin |
| `is_all_view_only()` | every permission row is `viewer` (super admin → False) |
| `get_subject_roles()` | `{subject_id: role}`; super admin → `admin` for all subjects |
| `is_view_only(subject_id)` | role on that subject is `viewer` |
| `get_accessible_subjects()` / `get_admin_subjects()` | lists of subject ids (super admin → all) |

Query helpers: `get_user_accessible_subjects()` and `get_user_admin_subjects()` return `Subject` lists for `current_user` — use them to build subject dropdowns and to scope list queries.

## Scoping inside services

- Dashboard and generation queries filter by the subjects returned from `get_user_accessible_subjects()`; never trust a `subject_id` from the client without one of the checks above.
- File access is scoped by `files_service.RootRegistry(user, scope)`: **user scope** exposes `User/<name>` (rw) plus `shared:<SID>` per subject (rw for admin role, ro for user role, viewers excluded); **admin scope** (`Source`, `Storage`, extra roots) is honoured only for super admins and a non-super request for it is silently downgraded. Details: [../modules/file-browser.md](../modules/file-browser.md).
- Generated documents, saved filters, presets, and sets are owned by `user_id`; super admins may view/share anything (`_user_can_view_file`, `_user_owns_file` in `app/user.py`).
- AI Tools, PDF Import, Smart Import, and batch ops are subject-admin scoped; the AI feature as a whole is additionally gated by the `AI_TOOLS_ENABLED` setting (templates read `window.OQB_AI_TOOLS_ENABLED`).

## Username policy (login name = filesystem folder)

`validate_username(username)` in `app/utils.py` is used by `/register` and Admin → Users:

- Regex `^[A-Za-z0-9._-]{1,80}$`; no leading or trailing dot.
- Reserved (case-insensitive): `generated`, `con`, `prn`, `aux`, `nul`, `com1-4`, `lpt1-3`.
- The personal home is `STORAGE_PATH/User/<username>`; for names passing the validator the folder equals the username verbatim (`storage.safe_username` sanitises legacy names). Renaming a user in Admin → Users **moves** `User/<old>` to `User/<new>`.

## Template-side flags

`base.html` emits `window.OQB_IS_ADMIN` (any admin role or super admin) and `window.OQB_AI_TOOLS_ENABLED`; use these only to hide UI. The server decorators are the real gate.

## Checklist for a new route

1. `@login_required` first, then the narrowest decorator above.
2. If the route targets a question / asset / file rather than a subject, load the object and check its subject or owner explicitly.
3. Read-only vs mutating: viewers can read, users can generate, admins can mutate library data.
4. Add the route and its authz to the module doc's Routes table.
