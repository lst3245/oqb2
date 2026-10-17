# SMS sign-in and centralised authorization

> Teachers sign in with the school SMS; OQB keeps local accounts, decides every permission through one effective-level function, and never trusts the browser for a subject. Built in six stages on branch `feature/sms-sso`; **stages 1–3 (authorization, trust list + verifier + audit, sign-in endpoints + linking) are done; stages 4–6 are not built. Not deployed.**

Contract: `OQB_CONTRACT_v1.2.md` (refreshed, with section 2a "Test window"), `V1_2_ADDENDUM.md`, `V1_2_ADDENDUM_2.md` and the build plan live outside the repo (`D:\oqb_work\SMS_integration\`). This doc describes what exists in the repo. Deployment: [../manuals/SMS_SSO_DEPLOYMENT.md](../manuals/SMS_SSO_DEPLOYMENT.md).

## Stages

| Stage | Scope | State |
|---|---|---|
| 1 | `app/authz.py` (effective level, fail-closed decorators, deny envelope), the 34 scope-gap fixes, `users` account-state columns, `session_version` / `active` | done |
| 2 | 12 SSO tables, trust list + bootstrap file + production guards, pure token verifier, auth audit, SMS levels fed into `authz`, level module, mint script, the official fixtures in `tests/fixtures/sms` | done |
| 3 | Sign-in endpoints (landing, token POST, failure page, link prompt / confirm / cancel), JIT accounts, session replacement, `PUBLIC_ORIGIN` / `PROXY_FIX_HOPS` / rate limit, login button; service-level admin linking by typed issuer + sub and the section 2a test-window open / close | done |
| 4 | Directory feed (`POST /api/sms/directory`): catalogue, roster, deprovision / reactivate, brake | pending (verifier side done in stage 2) |
| 5 | Admin screens: subject mapping, linking (roster + typed issuer/sub), snapshots, trust list (pin after fingerprint check, test window), audit / alerts, account admin | pending (services exist) |
| 6 | Cookie names / `Secure`, session limits + opt-in remember-me, POST logout, CSRF, security headers, ADR-018, manuals | pending |

## Files

| File | Role |
|---|---|
| `app/authz.py` | Level math (`compute_levels`, pure), per-request cache (`effective_levels`), lookups, route-edge helpers, owner checks, `scope_query`. `_sms_inputs(user)` reads `user_sms_levels`, `sms_subject_map` and the trust list's `issuer_caps`, only when `sms_levels_count()` (schema ready **and** `SMS_SSO_ENABLED`); any error → local grants only. |
| `app/sms_levels.py` | **The only module that spells the SMS level names** (`none` / `viewer` / `user` / `admin`), enums, ranks, the non-primary cap, `to_oqb_level`, display names Viewer / User / Admin (`display`, translated with `t()`). |
| `app/sms_clock.py` | `now()` / `utcnow()` used by every SMS check; `frozen(ts)` freezes it process-wide (tests, fixtures' `verify_at`). |
| `app/sms_trust.py` | `TrustEntry` / `TrustList`, production guards (`normalise`, `.invalid` issuers dropped in production), DB / bootstrap loading with a 60 s cache (`load_trust_list`, `invalidate`), PEM parsing + SHA-256 DER-SPKI fingerprints (RSA ≥ 2048), edits (`upsert_issuer`, `pin_key` with a required `expected_fingerprint`, `unpin_key`, `open_test_window`, `close_test_window`), `seed_defaults`, JWKS cache (`JwksCache`, `key_resolver`), recorded issuer constants (live + workshop, kids, fingerprints). |
| `app/sms_verify.py` | Pure verifier, no Flask / DB: `verify_handoff`, `verify_snapshot`, `check_fresh`, `feed_status`, `canonical_uuid`, `VerifyError(reason)`, `Verified*` dataclasses. |
| `app/auth_audit.py` | `record` (caller's transaction) / `record_now` (own connection, never raises); event constants; `scrub` replaces anything JWS-shaped with `[token]`. |
| `app/sms_linking.py` | Pure: `derive_username`, `suggest_users` (rapidfuzz), `link_refusal`, `is_privileged`. |
| `app/sms_sso_service.py` | `complete_handoff` (contract section 6 step 9 in one transaction), `replace_levels`, pending links (`pending_link`, `cancel_pending`), `self_refusal` / `self_link`, `admin_link` (typed or roster issuer + sub), `unlink`, `linked_users`, `purge` / `maybe_purge`. |
| `app/sso.py` | Blueprint `sso`: the routes below, `signin_url()` for the login button, the fixed `next` allowlist, response headers. |
| `app/web_security.py` | ProxyFix wiring (`init_app`), `public_origin`, `normalise_origin`, `origin_ok`, `client_ip`. Stage 6 adds cookies / headers / session limits. |
| `app/rate_limit.py` | `RateLimiter(limit, window)` per key on the login guard's sliding window. |
| `app/sso_schema.py` | Boot patches: `patch_users` (users columns) + `create_sso_tables` (the 12 tables, `checkfirst`, one try per table); `apply_sso_patches` → `SMS_SCHEMA_READY`. |
| `app/auth.py` | Password login records `password_login`, sets `session['auth_method'] = 'password'`, sends a pending SMS link to its confirmation; login page gets `sms_signin_url`. |
| `app/admin.py` | Password changes record `password_change`; promoting an SMS-linked user to super admin is refused (unlink first). |
| `templates/sso/landing.html`, `templates/sso/failed.html` | Standalone pages (no `base.html`, no CDN). |
| `templates/sso/link_prompt.html`, `templates/sso/link_confirm.html` | Extend `base.html`. |
| `templates/login.html` | "Sign in with SMS" button, pending-link hint. |
| `scripts/sms_contract_mint.py` | CLI: mint a handoff / snapshot with a key file; no app import, no DB; refuses non-`.invalid` issuers without `--allow-issuer`. |
| `deploy/sms/sms-live-202610.pub.pem` | The live SMS public key (RSA 3072, kid `sms-live-202610`, fingerprint `fed812e5e0ebcc47e44ec3942ccc74db8310ac9ea873cd56bfe628e9ca5ea1f2`). **Never auto-pinned**; a super admin pins it at deployment after checking the fingerprint ([runbook](../manuals/SMS_SSO_DEPLOYMENT.md)). |
| `docs/reference/sms-trust-bootstrap.example.json` | Bootstrap file format (contract-test keys). |
| `tests/fixtures/sms/` | Byte copy of SMS's `oqb-fixtures` (cases.json, tokens, snapshots, keys incl. private test keys, reference `verify.py` / `sign.py`). `.gitattributes` marks them `-text`. |
| `tests/smskit.py`, `tests/ssocase.py` | Fixture helpers (trust list, minting edge cases) and the SSO DB test base (fixture trust list in the DB, mapping, linked accounts). |
| Tests | Pure: `test_sms_fixtures` (31 `test_case_<name>` + variants), `test_sms_verify`, `test_sms_trust`, `test_sms_levels`, `test_sms_mint`, `test_sms_linking`, `test_web_security`. DB: `test_sms_acceptance` (AT01–AT16, AT21), `test_sms_routes_fixtures` (23 token cases through `POST /sso/sms`), `test_sms_sso_routes`, `test_sms_store`, `test_schema_patches`. |

## Tables

All new tables are InnoDB, `utf8mb4` / `utf8mb4_unicode_ci` (the live schema mixes collations and engines). Details in [../core/03-data-model-and-migrations.md](../core/03-data-model-and-migrations.md) and [../reference/schema-history.md](../reference/schema-history.md).

| Table | Holds |
|---|---|
| `users` (columns) | Account state (stage 1). |
| `external_identities` | `(issuer, sub)` → one user; unique `(issuer, sub)` and `(issuer, user_id)`; `link_method` self / admin / jit. |
| `user_sms_levels` | SMS-sourced levels (OQB level names), replaced per user + issuer; `sms_subject_uuid = ''` is the default-level row. |
| `sms_subject_map` | Admin-confirmed SMS subject uuid → OQB subject (one-to-many). |
| `sms_subject_catalogue` | SMS subjects seen in snapshots (stage 4) or primary-issuer tokens. |
| `sms_roster`, `sms_feed_state`, `sms_snapshots` | Feed state (stage 4). |
| `sso_used_jti` | Shared single-use store, unique `(issuer, jti)`; purged after 24 h. |
| `sso_pending_links` | Verified claims of an unlinked handoff, 10 min, keyed by sha256 of a session-bound nonce. |
| `sms_trust_issuers`, `sms_trust_keys` | The trust list and pinned keys. |
| `auth_audit_log` | Every sign-in / link / trust / feed event; `is_alert` rows for super admins. |

## Routes

All `/sso/*` routes answer **404** unless `SMS_SSO_ENABLED` is on and `PUBLIC_ORIGIN` is set, and **503** when `SMS_SCHEMA_READY` is false. Every response carries `Referrer-Policy: no-referrer` and `Cache-Control: no-store`.

| Method | Path | Endpoint | Authz | Purpose |
|---|---|---|---|---|
| GET | `/sso/sms` | `sso.landing` | public | Standalone landing page. First child of `<head>` is a nonce'd script: reads `#handoff=<JWT>`, `history.replaceState(null, "", "/sso/sms")`, then `fetch` POSTs `{"token"}` and `location.replace`s the returned `next` if it is in the fixed allowlist. CSP exactly `default-src 'none'; script-src 'self' 'nonce-…'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'`. |
| POST | `/sso/sms` | `sso.token_post` | public; `Origin` must equal `PUBLIC_ORIGIN` | Rate limit (20 / 5 min / IP) → Origin → body ≤ 16 KB → verify → handoff transaction. JSON callers get `{"next": "/" \| "/sso/sms/link" \| "/sso/sms/failed"}` (200 / 200 / 401, 429 when rate-limited); form callers get 303 or the failure page with 401. |
| GET | `/sso/sms/failed` | `sso.failed` | public | Standalone generic failure page (contract section 12 text). |
| GET | `/sso/sms/link` | `sso.link_prompt` | pending link in the session | The "not linked yet" prompt; sign in with the OQB password or cancel. |
| GET/POST | `/sso/sms/link/confirm` | `sso.link_confirm` | password-signed-in user + pending link + per-link form token | "Link OQB account X to SMS account <name (staff code)>?"; POST links (`self_link`). |
| POST | `/sso/sms/link/cancel` | `sso.link_cancel` | per-link form token | Drops the pending link. |
| GET/POST | `/login` | `auth.login` | public | Shows "Sign in with SMS" (`{primary issuer}/apps/oqb?origin={PUBLIC_ORIGIN}`) when SSO is on, the schema is ready and the primary has a pinned key (or `jwks_auto`); after a password login with a pending link, redirects to `/sso/sms/link/confirm`. |

`POST /api/sms/directory` does not exist yet (404; stage 4).

**Stage 1 authorization changes to existing routes** (details in [../core/02-auth-and-permissions.md](../core/02-auth-and-permissions.md)):

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
| POST | `/admin/users/<id>/edit` | a password change bumps `session_version` and is audited; making an SMS-linked user super admin is refused (400) |

## Verification (contract section 6; `sms_verify`)

Stop at the first failure; the order follows the reference `verify.py`:

1. **shape**: ≤ 8 KB (snapshot 5 MB), three base64url segments (an empty signature reaches the alg check), JSON header / payload → `malformed`;
2. **issuer** from the unverified payload: entry exists (`unknown_issuer`; `.invalid` issuers in production too), active (`issuer_disabled`), a primary must be *the* primary;
3. **alg / kid**: `RS256` only (`bad_alg`), kid pinned or JWKS-resolved (`unknown_kid`);
4. **signature** with that key only (`bad_signature`; also when the signed `iss` differs);
5. `aud` contains `oqb` (`bad_aud`), `purpose`, `ver` int 1 (`bad_purpose_or_ver`);
6. **times** with 30 s skew: `not_yet_valid` → `expired` → `bad_lifetime` (`exp - iat` > 120, snapshots > 600; `nbf` required for both);
7. **types / enums** (`malformed`): UUID `sub` / `jti` (stored lower-case), string caps, `default_level` ∈ none / viewer / admin, subject levels ∈ viewer / user / admin and above the default, `admin` default with subjects, duplicate subject uuids;
8. **non-primary**: `sub` in `allowed_subs` (`sub_not_allowed`), then the cap (default ≤ viewer, subject ≤ user; entries no longer above the default are dropped).

Snapshots: a non-primary or non-feed issuer → `issuer_disabled` (403); a broken user entry is skipped and listed in `invalid_entries`; `check_fresh` → `stale_snapshot` (409); `feed_status(reason)` maps reasons to 400 / 401 / 403 / 409.

Reason codes: the contract section 12 list plus local `server_error`, `bad_origin`, `rate_limited` (audit only; the user always sees the generic page).

## Handoff transaction (`sms_sso_service.complete_handoff`)

One transaction, before any cookie: insert the jti (duplicate → roll back, `replay`); resolve the identity — linked: privileged → `privileged_account`, disabled for any reason but `sms_deprovisioned` → `disabled`, `sms_deprovisioned` → re-enabled (`reactivate` audit); unlinked primary with `SMS_JIT_ENABLED` and the entry's `allow_jit` → new password-less user (`!sms-no-password`, username from `derive_username`), link `jit`; unlinked primary otherwise → commit **only** the jti, a pending link and a `not_linked` audit row; unlinked non-primary → `not_linked` failure. Then replace that issuer's levels, upsert the catalogue (primary tokens only), refresh `display_name` / `name_zh` / `email` / `staff_code`, record `last_login_*`, audit `sso_login` (+ an `is_alert` `nonprimary_login` row for a non-primary issuer), commit. Any exception → roll back, `server_error`.

Session (route): a different signed-in user is logged out and the session cleared; the same user keeps the session; `login_user(remember=False)`, any remember cookie is deleted (`_remember = 'clear'`), `session['auth_method'] = 'sms'`. A "not linked" outcome signs out whoever is signed in, clears the session and stores only the nonce.

## Linking (contract section 9)

- **Self-link**: prompt → password login (`auth_method = password`) → confirm page → `self_link` re-checks the pending link (10 min), that its issuer is still the primary, privileged / already-linked refusals, then links (`self`) and applies the token's levels. Only the primary issuer has self-links.
- **Admin link** (`admin_link`, UI in stage 5): typed `(issuer, sub)` with `sub` a UUID; the issuer must be on the trust list; a non-primary sub must be on that entry's `allowed_subs`; privileged and already-linked accounts refused; levels from the roster row if the feed has listed the sub (capped for non-primary). Audited with `actor_user_id`.
- **Unlink**: drops the link and that issuer's levels, bumps `session_version`, audited.
- Privileged = `is_super_admin` or `local_only`: never linked (refused at link time and at sign-in). Promoting a linked user to super admin is refused in Admin → Users.

## Trust list

- **Source**: the DB, cached 60 s. `SMS_TRUST_BOOTSTRAP_FILE` replaces it only when `OQB_ENVIRONMENT` ∈ staging / test / dev; in production it is ignored and alerts once (`trust_bootstrap_ignored`). Format: [../reference/sms-trust-bootstrap.example.json](../reference/sms-trust-bootstrap.example.json) (`issuers[]` with `issuer`, `enabled`, `enabled_until` (ISO or unix), `primary`, `allow_jit`, `accepts_feed`, `level_cap`, `allowed_subs`, `jwks_url`, `jwks_auto`, `keys[]` with `kid` and `pem` or `pem_file` relative to the file).
- **Guards**: `.invalid` issuers dropped in production (and refused by `upsert_issuer`); non-primary entries never JIT / feed, always capped, active only with a future `enabled_until` (≤ 7 days ahead when enabled) and a non-empty `allowed_subs`; SSO needs exactly one active primary (`TrustList.primary`, else everything fails closed); keys are RSA ≥ 2048.
- **Seed** (`seed_defaults`, boot, insert-if-absent, never updates): `https://ms.tic.edu.hk` enabled + primary + JIT + feed allowed, JWKS `https://ms.tic.edu.hk/api/v1/apps/jwks.json`, **no key** (sign-in fails closed until a super admin pins `sms-live-202610`); `https://ms-test.tic.edu.hk` disabled, capped, JWKS `https://ms-test.tic.edu.hk/api/v1/apps/jwks.json` (also served by `tic-test.mathedu.info`), no key. Recorded workshop key: kid `sms-test-202610`, fingerprint `450e8b5093650aa7c1feafd9458582c3c66c27687d19e79866d055ee8f239d2b` (constants only).
- **Pinning** requires the fingerprint the admin confirmed over a second channel (`pin_key(..., expected_fingerprint=)`); colons / case are ignored.
- **JWKS**: `jwks_auto=false` (default) fetches only to alert on a new kid (`jwks_new_kid`, once per kid per process) and never trusts it; `true` trusts JWKS keys cached ≤ 1 h, refetching an unknown kid at most every 5 min. Timeout 5 s.
- **Test window (contract 2a)**: `open_test_window(issuer, kid, pem, expected_fingerprint, allowed_subs (+labels), enabled_until)` pins after the fingerprint check and sets enabled, ≤ 7 days, non-primary, no JIT / feed / `jwks_auto`, capped; `close_test_window` disables and unpins every kid in one call; both audited `test_window`. `linked_users(issuer)` lists the test accounts to unlink / disable.

## Business rules / invariants

- **Effective level** on subject S = max(local grant, SMS `default_level`, levels of SMS subjects mapped to S), non-primary issuers capped, rows from issuers not active on the trust list ignored, SMS rows ignored unless `SMS_SSO_ENABLED` and `SMS_SCHEMA_READY`. Per request, never stored in the session ([ADR-019](../decisions/ADR-019-effective-level-authz.md)).
- **Replace, never merge**: each handoff replaces that user's levels for that issuer; local grants are never touched.
- **Redirects** are fixed paths only; the token never chooses one.
- **The token is never logged or stored**: not in audit rows (`scrub`), not in exceptions (`VerifyError` carries the reason only), not in the session.
- Super admin stays local; SMS `admin` is per-subject admin.
- Fail closed: no schema → 503; no primary / no key → every token refused; any error in the level lookup → local grants only.

## Settings & config keys

See [../core/06-system-settings.md](../core/06-system-settings.md). Env-only: `OQB_ENVIRONMENT`, `SMS_TRUST_BOOTSTRAP_FILE`, `PUBLIC_ORIGIN`, `PROXY_FIX_HOPS`. Registry (group "SMS sign-in"): `SMS_SSO_ENABLED`, `SMS_JIT_ENABLED`, `SMS_FEED_ENABLED`, `SMS_FEED_BRAKE_MIN`, `SMS_FEED_BRAKE_PCT` (the feed keys are used from stage 4). Runtime flag `SMS_SCHEMA_READY`. Constants (contract MUSTs): skew 30 s, handoff lifetime 120 s, snapshot 600 s, token 8 KB, snapshot 5 MB, pending link 10 min, jti kept 24 h, rate limit 20 / 5 min, non-primary window 7 days.

## Permissions

See [../core/02-auth-and-permissions.md](../core/02-auth-and-permissions.md). The `/sso/*` routes are public by design (the token or the pending link is the credential). Trust / linking edits are super-admin services (screens in stage 5).

## Background work / SSE / threads

None. `maybe_purge` runs at most every 10 minutes inside a token POST. The JWKS cache and the rate limiter are per process.

## Gotchas

- The level names exist only in `app/sms_levels.py`; `tests/test_sms_levels.py` scans the SSO modules for quoted level literals.
- `record_now` writes on its own connection; a caller inside REPEATABLE READ will not see the row until its own transaction ends (tests call `db.session.rollback()` before reading audit rows).
- `pin_key` without `expected_fingerprint` is a TypeError on purpose.
- Behind the proxy, set `PROXY_FIX_HOPS`; otherwise the Origin check still uses `PUBLIC_ORIGIN` (never `request.host_url`, which is `http://` behind the proxy) but the audit / rate-limit IP comes from the login guard's `TRUSTED_PROXIES` rules.
- The landing page must stay free of external resources and inline styles (CSP `default-src 'none'`); it uses `fetch` (not a form) so the browser sends a real `Origin` under `no-referrer`.
- Do not put an authz check inside a `try/except Exception` block: the 403 becomes a 500.
- `authz.require_subjects([])` denies; callers with an optional empty list short-circuit first.
- A refused fetch gets JSON only when the request looks like an API call; a bare test-client GET gets the HTML 403.
- Tests keep an app context pushed; `tests/dbcase.FreshGClient` clears `g` between requests, and `DbTestCase` restores `app.config` and the trust cache after each test.

## Open issues

- **SSE GETs that write** (plan point 12, not implemented by decision): `GET /admin/health/sync?mode=delete`, PDF-import `GET /admin/pdf-import/commit`, `GET /admin/import/apply`, `GET /admin/ingestion/start`, `GET /admin/health/doc-thumbnails/backfill`. A top-level GET navigation carries `SameSite=Lax` cookies, so these can be triggered cross-site by a signed-in admin's browser. Proposed for stage 6: require a `csrf` query parameter (`OQB.csrfUrl(url)`) on these endpoints behind `CSRF_MODE`; needs the owner's approval.
- The link confirm / cancel forms use an interim per-link form token until stage 6 adds CSRF everywhere.
- SMS sessions get no remember-me now; the 12 h / 2 h limits arrive with stage 6.
- The "Sign in with SMS" landing must be checked in a real browser on staging (Origin under `no-referrer`, plan point 5).
- Smart Import plan tokens (`/admin/import/apply?token=`) and IMG Split staging tokens are not owner-bound.

## Related

[ADR-019](../decisions/ADR-019-effective-level-authz.md), [../core/02-auth-and-permissions.md](../core/02-auth-and-permissions.md), [../core/06-system-settings.md](../core/06-system-settings.md), [../manuals/SMS_SSO_DEPLOYMENT.md](../manuals/SMS_SSO_DEPLOYMENT.md), [my-files.md](my-files.md), [generator.md](generator.md), [subject-snapshots.md](subject-snapshots.md).
