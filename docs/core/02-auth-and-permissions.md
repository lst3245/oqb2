# 02 — Authentication and permissions

> Every route declares its authorization at the edge with a decorator from `app/utils.py`; services and queries then scope by subject. All level decisions come from one place, `app/authz.py`. Read this before adding or changing any route.

## Authentication

- Flask-Login sessions (`login_manager` in `app/__init__.py`, `login_view = 'auth.login'`, `remember=True` on login). The stored id is `"<user id>:<session_version>"` (`User.get_id`). `load_user` rejects a plain id (cookies from before this change — everyone signs in once more), a version that no longer matches `users.session_version`, and a disabled account (`users.active = 0`). Bumping the version (`User.bump_session_version()`) therefore ends every session and remember cookie of that account; it happens on any password change. A super admin who changes their own password is signed in again on the current browser.
- Disabled accounts (`active = 0`) cannot sign in; the login page shows the same "Invalid username or password" message as a wrong password. A password hash that is empty or starts with `!` (`UNUSABLE_PASSWORD_PREFIX`, for accounts that will only sign in through SMS) never matches. A password sign-in records `last_login_at` and `last_login_method = 'password'`. The remember-me cookie lasts **90 days** (`REMEMBER_COOKIE_DURATION`); the session cookie and the remember cookie are `HttpOnly` and `SameSite=Lax`. `Secure` is off because the LAN still uses plain HTTP.
- Failed logins are throttled in memory (`app/login_guard.py`): **8** failures per username and **60** per client IP in a **15-minute** window. A hit returns the login page with **429** and "Too many login attempts". A successful login clears that username's count only. Counters reset when the process restarts.
- The client IP is the TCP peer, unless that peer is a trusted proxy, in which case `X-Real-IP` is used, else the rightmost `X-Forwarded-For` hop. `TRUSTED_PROXIES` unset means any private, loopback, or link-local peer (the LAN gateway). A comma list pins the proxy. An empty value trusts no header, so every internet client shares the proxy's address and one scanner can pause login for all of them.
- After login, `next` is followed only when it is a same-site path (`safe_next_url`). The login form keeps that path in its action so Flask-Login's `?next=` still returns the user to the page they asked for.
- **SMS sign-in** (branch `feature/sms-sso`, off unless `SMS_SSO_ENABLED` and `PUBLIC_ORIGIN` are set; spec [../modules/sms-sso.md](../modules/sms-sso.md)): the school SMS sends the browser to `/sso/sms#handoff=<signed token>`; OQB verifies it against pinned keys and signs a **linked** account in with `remember=False` (any remember cookie is deleted), `last_login_method = 'sms'`, `session['auth_method'] = 'sms'`. A different signed-in user is replaced; a failed token leaves the current session alone. Unlinked: the session is cleared and the user must sign in with their OQB password and confirm the link (or an admin links them); with `SMS_JIT_ENABLED` the live (primary) issuer creates a password-less account instead. Super admins and `local_only` accounts are never linked and are refused at SMS sign-in (`privileged_account`); promoting a linked user to super admin is refused. A disabled account is refused, except `disabled_reason = 'sms_deprovisioned'`, which a valid handoff re-enables. Unlinking bumps `session_version`. Every attempt is written to `auth_audit_log`; password logins (`password_login`) and password changes (`password_change`) are audited too.
- Routes: `GET|POST /login`, `GET /logout`, `GET|POST /register` (**super-admin only**; creates an account with no subject access and never sets `is_admin` — there is no public self-registration), `GET /` redirects to dashboard or login. Code: `app/auth.py`.
- `GET /i18n/<lang>.js` (the Chinese UI catalogue) is deliberately **unauthenticated**: the login page needs it, and it holds only UI strings. Do not put anything per-user or secret in `app/translations/`. The language cookies (`oqb_lang`, `oqb_tax_lang`) are plain JS-readable preferences, not auth. See [../modules/i18n.md](../modules/i18n.md).
- Passwords: `User.set_password` / `check_password` (Werkzeug hashes). No password reset flow exists; super admins set passwords from Admin → Users, including their own (**Change password**). That self-edit does not rename the account or clear `is_super_admin`.
- First account: `init_db.py` seeds `admin / admin123` only when the `subjects` table is empty. It sets the legacy `is_admin` flag but **not** `is_super_admin`, so on a fresh install you must promote that user in the DB (`UPDATE users SET is_super_admin=1 WHERE username='admin'`) before any admin page opens. Never run `init_db.py` on the live DB.

## Roles and effective level

Each user has an **effective level** per subject, computed by `authz.effective_levels(user)` once per request (cached on `flask.g`, never in the session): the highest of the local grant (`user_subject_permissions.role`), the SMS `default_level` and the levels of SMS subjects mapped to that OQB subject, with the non-primary issuer cap (default ≤ viewer, subject ≤ user). SMS levels live in `user_sms_levels` (replaced on every handoff for that issuer, never merged into `user_subject_permissions`) and count only while `SMS_SSO_ENABLED` is on, the SSO schema is ready and the issuer is active on the trust list; any error reading them falls back to local grants only ([../modules/sms-sso.md](../modules/sms-sso.md)). SMS `admin` is per-subject admin, never super admin. The pure math is `authz.compute_levels`. A disabled account has no level anywhere. Super admin is `admin` on every subject.

Tiers, evaluated in this order:

| Tier | Where stored | Grants |
|---|---|---|
| **Super admin** | `users.is_super_admin` | Everything, every subject, all admin pages, System Settings, Users, Subjects, LLM Endpoints, AI Prompts, super-admin File Browser, Health. `get_subject_roles()` returns `admin` for every subject. |
| **Subject admin** | `user_subject_permissions.role = 'admin'` | For that subject: tag/edit/create/delete questions and assets, ingest, AI Tools, PDF Import, Topics/Chapters, Export/Import, PDF Tool, read-write `Shared/<SUBJECT>`. Also unlocks the Admin navbar (`admin_required`). |
| **User** | `role = 'user'` | Browse, filter, select, generate documents, My Files, profiles, sets, read-only `Shared/<SUBJECT>`, Markup. |
| **Viewer** | `role = 'viewer'` | Browse only. Cannot generate (`can_generate()` false), excluded from the File Browser and the Shared folders. |
| (no row) | — | No access to that subject at all. |

`users.is_admin` is a **legacy column** kept for compatibility; do not read it for authorization. Admin → Users keeps it equal to `is_super_admin`; `/register` no longer writes it.

## Decorators (`app/utils.py`)

| Decorator | Passes when | Use for |
|---|---|---|
| `@login_required` (Flask-Login) | authenticated | every non-auth route, applied first |
| `@admin_required` | admin level on any subject (`authz.any_level(user, 'admin')`; super admin passes) | admin panel pages that are not subject-specific |
| `@super_admin_required` | `authz.is_super_admin` | Users, Subjects, System Settings, LLM Endpoints, AI Prompts, `/admin/files`, Health, `/register` |
| `@subject_access_required` | viewer level on the request's subject | read routes on a specific subject |
| `@subject_admin_required` | admin level on the request's subject | mutating routes on a specific subject |
| `@subject_level_required(level, source=None)` | that level on the request's subject | anything in between (e.g. `'user'`) |

The subject decorators resolve `subject_id` with `request_subject_id`: URL kwargs → `request.args` → `request.form` → JSON body key `subject_id` (or the key named by `source=`). **They fail closed:** no subject id → 403 (a super admin passes). A route that identifies its target by `question_id` / `asset_id` / taxonomy id must load the object and check its subject itself, **before** any `subject_snapshot.capture*` and outside any broad `try/except Exception` (which would turn the 403 into a 500):

- `authz.require_subject(subject_id, level)` / `authz.has_level(user, subject_id, level)`;
- `authz.require_taxonomy(Model, ids, level)` for Topic / Subtopic / Chapter / Subchapter ids (every owning subject must pass; ids owned by nothing are refused);
- `authz.owns(owner_id)` for owner-bound staging and jobs (owner or super admin; `None` owner = super admin only).

**Refusals:** `authz.deny()` aborts 403. Fetch / XHR / JSON-body / `/api/` / HTMX requests (and `Accept: application/json`) get `{"success": false, "error": msg, "message": msg}`; page loads get the HTML 403 page. The app-wide 403 handler applies the same rule to a plain `abort(403)`.

## `User` helpers (`app/models.py`)

All of these delegate to `app/authz.py`, so they reflect the effective level (not just the local grant):

| Method | Meaning |
|---|---|
| `has_subject_access(subject_id)` | viewer level or above on that subject |
| `can_generate_in(subject_id)` | user level or above on that subject |
| `is_subject_admin(subject_id)` | admin level on that subject |
| `has_any_admin_access()` | admin level on at least one subject |
| `can_generate()` | user level on at least one subject |
| `is_all_view_only()` | has levels, all of them viewer (super admin → False) |
| `get_subject_roles()` | `{subject_id: level}` copy; super admin → `admin` for all subjects |
| `is_view_only(subject_id)` | level on that subject is `viewer` |
| `get_accessible_subjects()` / `get_generate_subjects()` / `get_admin_subjects()` | subject ids at viewer / user / admin |
| `is_active` | `users.active` is not false (Flask-Login also uses it) |
| `bump_session_version()` | end every session of the account (caller commits) |

Query helpers: `get_user_accessible_subjects()` and `get_user_admin_subjects()` return `Subject` lists for `current_user` — use them to build subject dropdowns and to scope list queries.

## Scoping inside services

- Dashboard queries are always clamped to readable subjects (`authz.scope_query` in `dashboard._build_filtered_query`, so filter, sort groups and selection all agree); `/dashboard/api/subtopics|subchapters` refuse parents in unreadable subjects; an orphan DOC thumbnail is super admin only. Generation needs `user` level on every question's subject (viewer-only questions are dropped). Never trust a `subject_id` from the client without one of the checks above.
- Generated files: seeing a row (`files_policy.can_view`: owner, super admin, direct or section share) is not downloading it. `files_policy.can_download` additionally requires a share recipient to read every subject of the file's `question_ids`; it guards `/generate/download/<id>`, `/generate/pdf/<id>` and bulk download. Saved JSON blobs returned to a reader (file filter data, generation options, shared profiles / presets) pass through `files_policy.scrub`, which drops question ids outside the reader's subjects. A shared filter profile needs viewer on its subject. Viewers cannot save profiles, presets or sets, and cannot list My Files.
- Owner-bound staging: PDF Tool sessions (`session.json` `owner_user_id`) and their export jobs, PDF-import tokens (`meta.json` `owner_user_id`), Smart Import uploaded folders (sibling `<token>.owner.json`) and the AI / PDF cancel registries record the user who created them; other users get 403 (or 404 for files), a super admin may act on all, and items created before this change are super admin only. The legacy `PDF_SOURCE_PATH` picker (no `root_id`) shows a non-super admin only `<subject>/…` folders of subjects they administer (`authz.pdf_source_allowed`).
- Taxonomy writes (`/admin/topics|subtopics|chapters|subchapters/*`) check the owning subject of every id, including reorder lists; Bulk Edit refuses questions outside the user's admin subjects (403) and topics / chapters from another subject than the questions (400) before capturing a restore point.
- File access is scoped by `files_service.RootRegistry(user, scope)`: **user scope** exposes `User/<name>` (rw) plus `shared:<SID>` per subject (rw for admin role, ro for user role, viewers excluded); **admin scope** (`Source`, `Storage`, extra roots) is honoured only for super admins and a non-super request for it is silently downgraded. Details: [../modules/file-browser.md](../modules/file-browser.md).
- Generated documents, saved filters, presets, and sets are owned by `user_id`; super admins may view/share anything (`_user_can_view_file`, `_user_owns_file` in `app/user.py`).
- AI Tools, PDF Import, Smart Import, and batch ops are subject-admin scoped; the AI feature as a whole is additionally gated by the `AI_TOOLS_ENABLED` setting (templates read `window.OQB_AI_TOOLS_ENABLED`).
- **Question Management is one subject at a time** ([ADR-016](../decisions/ADR-016-single-subject-question-management.md)). `/admin/questions/api/tree|facets|ids|list` require `subject` (400 `subject_required`; 403 `subject_denied` when it is not in `get_user_admin_subjects()`). Every bulk route on that page (delete, delete assets, combine, verified, check state, Generate IMG, MCQ ANS, copy/move, AI streams) also calls `_bulk_subject_guard(ids)`: with `subject_id` every id must be in that subject, without it the ids must not span subjects; otherwise 400 `outside_subject` / `mixed_subjects`. The guard is not a role check, so it applies to super admins too. Per-question routes there (`update`, `assets`, `assets/<aid>/delete`, `assets/reorder`, `rename` — current and new subject) check the question's subject with `_require_md_admin`.
- **Subject AI tuning** (`/admin/subjects/<subject_id>/ai/*`) is `@subject_admin_required` with the subject id in the URL — a deliberately lower tier than the super-admin AI Prompts page. Subject admins may change the Auto Tag prompt *body* and taxonomy hints for their own subject only; the output-format contract is never editable from that tier. `/admin/questions/<id>/ai/tag-corrections` checks the question's subject explicitly. See [../modules/subject-ai.md](../modules/subject-ai.md).
- **Restore points** are subject-admin scoped: the page lists `get_user_admin_subjects()`, manual capture is `@subject_admin_required` with the id in the URL (`/admin/subjects/<sid>/restore-points/capture`), and `/admin/restore-points/<point_id>/preview|restore` check `is_subject_admin(point.subject_id)` explicitly. A restore rewrites only that subject. See [../modules/subject-snapshots.md](../modules/subject-snapshots.md).
- **Marks spacing rules** (`/admin/subjects/<subject_id>/marks-spacing`): `GET` is readable by anyone with `has_subject_access`; `POST` (save the subject default) is `@subject_admin_required` plus an explicit `_require_subject_admin`. Any user who can generate may edit rules for one paper: `POST /generate/api/marks-rules/check` (`can_generate`, validation only) and form field `marks_rules_override` on `/generate/create`, limited to subjects in the selection and never written to the subject. See [../modules/marks.md](../modules/marks.md).

## Username policy (login name = filesystem folder)

`validate_username(username)` in `app/utils.py` is used by `/register` and Admin → Users:

- Regex `^[A-Za-z0-9._-]{1,80}$`; no leading or trailing dot.
- Reserved (case-insensitive): `generated`, `con`, `prn`, `aux`, `nul`, `com1-4`, `lpt1-3`.
- The personal home is `STORAGE_PATH/User/<username>`; for names passing the validator the folder equals the username verbatim (`storage.safe_username` sanitises legacy names). Renaming a user in Admin → Users **moves** `User/<old>` to `User/<new>`.

## Template-side flags

`base.html` emits `window.OQB_IS_ADMIN` (any admin role or super admin) and `window.OQB_AI_TOOLS_ENABLED`; use these only to hide UI. The server decorators are the real gate.

## Checklist for a new route

1. `@login_required` first, then the narrowest decorator above.
2. If the route targets a question / asset / file / taxonomy id rather than a subject, load the object and check its subject (`authz.require_subject` / `require_taxonomy`) or owner (`authz.owns`) explicitly, before any restore-point capture.
3. Read-only vs mutating: viewers can read, users can generate, admins can mutate library data.
4. Add the route and its authz to the module doc's Routes table.
