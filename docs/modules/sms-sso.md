# SMS sign-in and centralised authorization

> Teachers sign in with the school SMS; OQB keeps local accounts, decides every permission through one effective-level function, and never trusts the browser for a subject. Built in six stages on branch `feature/sms-sso`; **stage 1 (authorization + account state) is done, stages 2–6 are not built.**

Contract: `OQB_CONTRACT_v1.1.md` (sections 2–15, v1.2 enums) and the build plan live outside the repo (`D:\oqb_work\SMS_integration\`). This doc describes what exists in the repo.

## Stages

| Stage | Scope | State |
|---|---|---|
| 1 | `app/authz.py` (effective level, fail-closed decorators, deny envelope), the 34 scope-gap fixes, `users` account-state columns, `session_version` / `active` | done |
| 2 | Trust list + bootstrap file + production guards, token verifier, auth audit, SMS levels fed into `authz`, PyJWT | pending |
| 3 | Sign-in endpoints (landing, token POST, failure page, link prompt / confirm), JIT accounts, `PUBLIC_ORIGIN`, rate limit, login button | pending |
| 4 | Directory feed (`POST /api/sms/directory`): catalogue, roster, deprovision / reactivate, brake | pending |
| 5 | Admin screens: subject mapping, linking, snapshots, trust pins, audit, account admin (revoke / disable / local-only) | pending |
| 6 | Cookie names / `Secure`, session limits + opt-in remember-me, POST logout, CSRF, security headers, ADR-018, manuals | pending |

## Files

| File | Role |
|---|---|
| `app/authz.py` | Level math (`compute_levels`, pure), per-request cache (`effective_levels`), lookups (`has_level`, `subjects_with_level`, `any_level`, `is_super_admin`), route-edge helpers (`require_subject(s)`, `require_taxonomy`, `deny`, `wants_json`, `deny_payload`), owner checks (`owns`, `current_user_id`), legacy PDF source scope (`pdf_source_allowed`, pure `path_in_subjects`), `scope_query`. `_sms_inputs` is the stage-2 hook (returns `{}` today). |
| `app/files_policy.py` | Generated-file `can_view` / `can_download`, pure `strip_out_of_scope`, DB-backed `scrub` for saved JSON blobs. |
| `app/sso_schema.py` | Additive boot patch for the `users` columns (`USER_COLUMNS`, `missing_columns`, `alter_users_sql`, `patch_users`, `apply_sso_patches`). |
| `app/utils.py` | Decorators on top of `authz` (`subject_level_required`, `request_subject_id`). |
| `app/models.py` | `User` columns, `get_id` = `"<id>:<session_version>"`, `is_active`, `bump_session_version`, unusable-password sentinel `!`, helpers delegating to `authz`. |
| `app/__init__.py` | `load_user` (version + active check), app-wide 403 JSON handler, calls `apply_sso_patches` first and sets `SMS_SCHEMA_READY`. |
| `tests/dbcase.py` | Guarded DB fixture (only `oqb_sso_test`). |
| `tests/test_authz_levels.py`, `tests/test_scope_helpers.py` | Pure tests. |
| `tests/test_scope_gaps.py`, `tests/test_schema_patches.py` | DB tests through the routes / against a scratch table. |

## Tables

`users` gains `active`, `disabled_reason`, `disabled_at`, `display_name`, `name_zh`, `email`, `staff_code`, `session_version`, `local_only`, `last_login_at`, `last_login_method` — see [../core/03-data-model-and-migrations.md](../core/03-data-model-and-migrations.md) and [../reference/schema-history.md](../reference/schema-history.md). Stage 2+ adds the SMS tables (identities, levels, trust list, audit); none exist yet.

## Routes

Stage 1 adds no routes. It changes the authorization of existing ones (details in [../core/02-auth-and-permissions.md](../core/02-auth-and-permissions.md)):

| Method | Path | Authz after stage 1 |
|---|---|---|
| GET/POST | `/register` | super admin (was any admin); never sets `is_admin` |
| POST | `/admin/topics/add`, `/admin/chapters/add`, `/admin/subtopics/add`, `/admin/subchapters/add` | admin on the target / parent's subject, before capture |
| POST/DELETE | `/admin/topics/<id>/delete`, `/admin/subtopics/<id>/delete`, `/admin/chapters/<id>/delete`, `/admin/subchapters/<id>/delete` | admin on the row's subject, before capture |
| POST | `/admin/topics/reorder`, `/admin/subtopics/reorder`, `/admin/chapters/reorder`, `/admin/subchapters/reorder` | admin on every listed row's subject |
| POST | `/admin/questions/batch-update`, `/admin/questions/batch-update/preview` | admin on every question's subject (403); topics / chapters must belong to the questions' subject (400) |
| GET | `/dashboard/api/subtopics`, `/dashboard/api/subchapters` | viewer on every parent's subject |
| GET | `/dashboard/api/doc_thumbnail/<id>.png` | orphan asset → super admin only (404) |
| GET/POST | `/generate/`, POST `/generate/create` | questions without `user` level dropped |
| GET | `/generate/download/<id>`, `/generate/pdf/<id>`; POST `/user/files/bulk-download` | `files_policy.can_download` |
| GET | `/user/profiles/<id>/data` | shared: viewer on the profile's subject; ids scrubbed |
| GET | `/user/gen-profiles/<id>/data`, `/user/files/<id>/filter`, `/user/files/<id>/generation_options` | ids scrubbed |
| GET | `/user/files/list` | `can_generate()` |
| POST | `/user/profiles/save`, `/user/gen-profiles/save` | `can_generate()`; `/user/sets/save` needs `user` on the set's subject |
| POST | `/admin/pdf-import/cancel`, `/admin/questions/ai/cancel`, `/admin/toolbox/pdf/llm-detect-cancel` | job owner or super admin |
| * | `/admin/toolbox/pdf/*` token routes, `/pdf/sessions`, `/pdf/sessions/delete`, `/pdf/sessions/clear-all`, `/pdf/export-stream`, `/pdf/export-download` | session / job owner or super admin |
| * | `/admin/pdf-import/*` token routes (via `_pdf_load_token_meta`) | token owner or super admin; exam mode also admin on the subject |
| GET | `/admin/pdf-import/source-list` (+ legacy server-PDF picks without `root_id`) | non-super admins: only `<subject>/…` of administered subjects |
| POST/GET | `/admin/import/upload` consumers, `/admin/import/uploaded-file` | upload owner or super admin |
| POST | `/admin/users/<id>/edit` | a password change bumps `session_version` |

## Business rules / invariants

- **Effective level** on subject S = max(local grant, SMS `default_level`, levels of SMS subjects mapped to S), non-primary issuers capped (default ≤ viewer, subject ≤ user), rows from issuers not in the trust list ignored. Super admin = admin everywhere; `active = 0` = nothing. Computed once per request, never stored ([ADR-019](../decisions/ADR-019-effective-level-authz.md)).
- **Fail closed:** subject decorators without a subject id → 403; object routes check the object's subject before any restore-point capture.
- **Sessions:** the session id carries `session_version`; a mismatch, a plain legacy id or an inactive account logs out. Every user signs in once after deploy.
- **Owner-bound staging:** items created before stage 1 have no owner and are super admin only.
- Super admin stays local (`users.is_super_admin`); SMS never grants it (stage 3+).

## Settings & config keys

`SMS_SCHEMA_READY` (runtime flag set by `create_app()`, not a setting): `False` when the `users` patch failed; stage 3 keeps SMS sign-in off when it is false. SMS settings arrive in stage 2 ([../core/06-system-settings.md](../core/06-system-settings.md)).

## Permissions

See [../core/02-auth-and-permissions.md](../core/02-auth-and-permissions.md).

## Background work / SSE / threads

None in stage 1. The AI / PDF cancel registries record the owner (`new_job` defaults to the request's user; jobs created outside a request have no owner and are super-admin-cancellable only).

## Gotchas

- Do not put an authz check inside a `try/except Exception` block: the 403 becomes a 500. The reorder routes read and check the ids before their `try`.
- `authz.require_subjects([])` denies; callers with an optional empty list (reorder) short-circuit to success first.
- A refused fetch gets JSON only when the request looks like an API call (JSON body, `/api/`, `X-Requested-With`, `HX-Request`, `Sec-Fetch-Mode` ≠ navigate, or `Accept: application/json`); a bare test-client GET gets the HTML 403.
- In tests that keep an app context pushed, Flask reuses it for every request, so `g` (Flask-Login's user, the level cache) leaks between requests; `tests/dbcase.FreshGClient` clears it.
- Saving a set used to need any access to the subject; it now needs `user` level.

## Open issues

- **SSE GETs that write** (plan point 12, not implemented by decision): `GET /admin/health/sync?mode=delete`, PDF-import `GET /admin/pdf-import/commit`, `GET /admin/import/apply`, `GET /admin/ingestion/start`, `GET /admin/health/doc-thumbnails/backfill`. A top-level GET navigation carries `SameSite=Lax` cookies, so these can be triggered cross-site by a signed-in admin's browser. Proposed for stage 6: require a `csrf` query parameter (`OQB.csrfUrl(url)`) on these endpoints behind `CSRF_MODE`; needs the owner's approval.
- Smart Import plan tokens (`/admin/import/apply?token=`) are not owner-bound; apply re-checks subject access per job.
- IMG Split staging tokens (`question_split`) are not owner-bound (question-level checks only).

## Related

[ADR-019](../decisions/ADR-019-effective-level-authz.md), [../core/02-auth-and-permissions.md](../core/02-auth-and-permissions.md), [my-files.md](my-files.md), [generator.md](generator.md), [subject-snapshots.md](subject-snapshots.md), [toolbox-pdf.md](toolbox-pdf.md), [pdf-import.md](pdf-import.md).
