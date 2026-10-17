# SMS sign-in and centralised authorization

> Teachers sign in with the school SMS; OQB keeps local accounts, decides every permission through one effective-level function, and never trusts the browser for a subject. Built in six stages on branch `feature/sms-sso`; **all six stages (authorization, trust list + verifier + audit, sign-in endpoints + linking, directory feed, super-admin screens, cookies + session limits + CSRF + security headers) are done. Not deployed.**

Contract: `OQB_CONTRACT_v1.2.md` (refreshed, with section 2a "Test window"), `V1_2_ADDENDUM.md`, `V1_2_ADDENDUM_2.md`, `V1_2_ADDENDUM_3.md` and the build plan live outside the repo (`D:\oqb_work\SMS_integration\`). This doc describes what exists in the repo. Deployment: [../manuals/SMS_SSO_DEPLOYMENT.md](../manuals/SMS_SSO_DEPLOYMENT.md).

## Stages

| Stage | Scope | State |
|---|---|---|
| 1 | `app/authz.py` (effective level, fail-closed decorators, deny envelope), the 34 scope-gap fixes, `users` account-state columns, `session_version` / `active` | done |
| 2 | 12 SSO tables, trust list + bootstrap file + production guards, pure token verifier, auth audit, SMS levels fed into `authz`, level module, mint script, the official fixtures in `tests/fixtures/sms` | done |
| 3 | Sign-in endpoints (landing, token POST, failure page, link prompt / confirm / cancel), JIT accounts, session replacement, `PUBLIC_ORIGIN` / `PROXY_FIX_HOPS` / rate limit, login button; service-level admin linking by typed issuer + sub and the section 2a test-window open / close | done |
| 4 | Directory feed (`POST /api/sms/directory`): catalogue, roster, profile + level refresh, deprovision / reactivate, missing-sub flags, safety brake with held snapshots (approve / discard services), the workshop test-window end-to-end test | done |
| 5 | Super-admin screens under `/admin/sms/`: overview, subject mapping, linking (roster + suggestions + typed issuer/sub), snapshots (approve / discard held), trust list (pin after fingerprint check, edit, open / close test window, bulk unlink + disable), audit / alerts (acknowledge, nav badge); account actions (end sessions, disable / enable, local-only); SMS badge / levels / state on the users page | done |
| 6 | Cookie names `oqb_session` / `oqb_remember` + `Secure` (`SESSION_COOKIE_SECURE`), session limits (SMS 12 h / 2 h; password the same with `PASSWORD_SESSION_LIMITS`), opt-in remember-me for unlinked accounts (`PASSWORD_REMEMBER_DAYS`), POST logout + confirmation page, CSRF on every POST / PUT / PATCH / DELETE (`CSRF_MODE`, `static/js/csrf.js`), security headers + the embed switch (`SMS_EMBED_ENABLED`), [ADR-018](../decisions/ADR-018-sessions-csrf-and-headers.md), AT22 | done |

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
| `app/sms_feed_service.py` | `apply_snapshot` (contract section 10 "Applying the snapshot" in one transaction), `brake_threshold`, `held_snapshots`, `approve_held`, `discard_held`, `purge_snapshots`, `FeedResult`, `FeedError`. |
| `app/sso.py` | Blueprint `sso`: the routes below (including the feed endpoint `sso.directory_feed`), `signin_url()` for the login button, the fixed `next` allowlist, response headers. |
| `app/web_security.py` | ProxyFix (`init_proxy`), `public_origin`, `normalise_origin`, `origin_ok`, `client_ip`; cookies (`cookie_secure`, `SessionInterface` + `embed_cookie` for `Partitioned`); the session marker (`start_auth`, `auth_method`), the pure limit policy `check_session`, `password_login` / `relogin` (remember-me policy), `enforce_session` (`before_request`), the remember-cookie hook; `security_headers` (`after_request`) with `frame_ancestors`. `init_app` wires all of it. |
| `app/csrf.py` | `get_token` / `rotate`, Jinja globals `csrf_token()` / `csrf_field()`, `protect` (`before_request`), `EXEMPT_ENDPOINTS`, `REDIRECT_ON_FAILURE` (share target), `CSRF_MODE` handling. |
| `static/js/csrf.js` | Adds the token to `fetch`, `XMLHttpRequest`, htmx and form posts; `OQB.csrfToken()` / `OQB.csrfInput()`. |
| `templates/logout_confirm.html`, `templates/csrf_failed.html` | `GET /logout` confirmation (POST form); the 400 page for a refused HTML form post. |
| `app/rate_limit.py` | `RateLimiter(limit, window)` per key on the login guard's sliding window. |
| `app/sso_schema.py` | Boot patches: `patch_users` (users columns) + `create_sso_tables` (the 12 tables, `checkfirst`, one try per table); `apply_sso_patches` → `SMS_SCHEMA_READY`. |
| `app/auth.py` | Password login (`web_security.password_login`: marker `m=password`, opt-in remember-me) records `password_login`, rotates the CSRF token, sends a pending SMS link to its confirmation; login page gets `sms_signin_url` and `remember_days`; `GET /logout` asks, `POST /logout` signs out; `render_login()` for a CSRF failure on the login form. |
| `app/admin.py` | Password changes record `password_change`; an own password change re-signs in with `web_security.relogin` (keeps the remember choice); promoting an SMS-linked user to super admin is refused (unlink first); `users()` passes each user's identities and SMS levels to the users page. |
| `app/sms_admin.py` | Blueprint `sms_admin` (`/admin/sms`, super admin only): the screens below; form parsing (`parse_allowed_subs`, window length in days) and rendering only, the work is in the services. Helpers `open_alerts`, `break_glass_ok`, `identities_by_user`, `sms_levels_by_user`; app-wide template globals `sms_alert_count()` (nav badge) and `sms_level_label(level)`. |
| `app/account_admin.py` | Blueprint `account_admin` (`/admin`, super admin only, JSON): end sessions, disable / enable, local-only. |
| `templates/admin_sms/*.html` | `_nav.html` (tabs), `index`, `mapping`, `linking`, `snapshots`, `trust`, `audit`. Server-rendered POST forms, redirect + flash. |
| `templates/admin_users.html` | SMS badge (per link; warning icon when missing from the last snapshot), disabled / disabled-by-SMS and local-only badges, an Account menu (end sessions, disable / enable, local-only, unlink), read-only SMS levels. |
| `templates/sso/landing.html`, `templates/sso/failed.html` | Standalone pages (no `base.html`, no CDN). |
| `templates/sso/link_prompt.html`, `templates/sso/link_confirm.html` | Extend `base.html`. |
| `templates/login.html` | "Sign in with SMS" button, pending-link hint, "Remember me for N days" box (hidden when `PASSWORD_REMEMBER_DAYS` is 0), `csrf_field()`. |
| `scripts/sms_contract_mint.py` | CLI: mint a handoff / snapshot with a key file; no app import, no DB; refuses non-`.invalid` issuers without `--allow-issuer`. |
| `deploy/sms/sms-live-202610.pub.pem` | The live SMS public key (RSA 3072, kid `sms-live-202610`, fingerprint `fed812e5e0ebcc47e44ec3942ccc74db8310ac9ea873cd56bfe628e9ca5ea1f2`). **Never auto-pinned**; a super admin pins it at deployment after checking the fingerprint ([runbook](../manuals/SMS_SSO_DEPLOYMENT.md)). |
| `docs/reference/sms-trust-bootstrap.example.json` | Bootstrap file format (contract-test keys). |
| `tests/fixtures/sms/` | Byte copy of SMS's `oqb-fixtures` (cases.json, tokens, snapshots, keys incl. private test keys, reference `verify.py` / `sign.py`). `.gitattributes` marks them `-text`. |
| `tests/smskit.py`, `tests/ssocase.py` | Fixture helpers (trust list, minting edge cases) and the SSO DB test base (fixture trust list in the DB, mapping, linked accounts). |
| Tests | Pure: `test_sms_fixtures` (31 `test_case_<name>` + variants), `test_sms_verify`, `test_sms_trust`, `test_sms_levels`, `test_sms_mint`, `test_sms_linking`, `test_web_security`, `test_csrf`, `test_csrf_templates` (every POST form has `csrf_field()`, pages load `csrf.js` first, landing page has nothing external), `test_session_policy` (policy + helpers; its `SessionLimitTests` use the DB). DB: `test_sms_acceptance` (AT01–AT16, AT21, AT22), `test_csrf_routes`, `test_security_headers`, `test_sms_routes_fixtures` (23 token cases through `POST /sso/sms`), `test_sms_feed` (the 8 snapshot cases through `POST /api/sms/directory` in order, AT17–AT20, AT05 / AT08 via the feed, endpoint edge cases, concurrency), `test_sms_workshop_window` (contract 2a with the fixture workshop stand-in and the three window subs), `test_sms_admin` (every super-admin page and POST, permissions, AT03 / AT08 through the UI routes, the > 7 day refusal through the trust route, window open / close, held approve / discard, alerts, account actions, users page), `test_sms_sso_routes`, `test_sms_store`, `test_schema_patches`. |

## Tables

All new tables are InnoDB, `utf8mb4` / `utf8mb4_unicode_ci` (the live schema mixes collations and engines). Details in [../core/03-data-model-and-migrations.md](../core/03-data-model-and-migrations.md) and [../reference/schema-history.md](../reference/schema-history.md).

| Table | Holds |
|---|---|
| `users` (columns) | Account state (stage 1). |
| `external_identities` | `(issuer, sub)` → one user; unique `(issuer, sub)` and `(issuer, user_id)`; `link_method` self / admin / jit. |
| `user_sms_levels` | SMS-sourced levels (OQB level names), replaced per user + issuer; `sms_subject_uuid = ''` is the default-level row. |
| `sms_subject_map` | Admin-confirmed SMS subject uuid → OQB subject (one-to-many). |
| `sms_subject_catalogue` | SMS subjects: the full catalogue from snapshots (inactive ones too), or subjects seen in primary-issuer tokens. Never deleted. |
| `sms_roster` | The latest snapshot's users per issuer (replaced on every applied snapshot); feeds admin linking. Never authorises. |
| `sms_feed_state` | Last applied `iat` / `snapshot_id` per issuer; the row is locked `FOR UPDATE` to serialise pushes. |
| `sms_snapshots` | One row per accepted snapshot: `applied` / `held` (+ `held_subs_json`) / `approved` / `discarded` / `superseded`, counts, unmapped uuids. Decided rows purged after 90 days. |
| `sso_used_jti` | Shared single-use store, unique `(issuer, jti)`; purged after 24 h. |
| `sso_pending_links` | Verified claims of an unlinked handoff, 10 min, keyed by sha256 of a session-bound nonce. |
| `sms_trust_issuers`, `sms_trust_keys` | The trust list and pinned keys. |
| `auth_audit_log` | Every sign-in / link / trust / feed event; `is_alert` rows for super admins. |

## Routes

All `/sso/*` routes answer **404** unless `SMS_SSO_ENABLED` is on and `PUBLIC_ORIGIN` is set, and **503** when `SMS_SCHEMA_READY` is false. Every response carries `Referrer-Policy: no-referrer` and `Cache-Control: no-store`. CSRF: `sso.token_post` (checked by Origin) and `sso.directory_feed` (signed, no cookies) are the **only** exemptions; the link confirm / cancel forms carry `csrf_field()`.

| Method | Path | Endpoint | Authz | Purpose |
|---|---|---|---|---|
| GET | `/sso/sms` | `sso.landing` | public | Standalone landing page. First child of `<head>` is a nonce'd script: reads `#handoff=<JWT>`, `history.replaceState(null, "", "/sso/sms")`, then `fetch` POSTs `{"token"}` and `location.replace`s the returned `next` if it is in the fixed allowlist. CSP exactly `default-src 'none'; script-src 'self' 'nonce-…'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'`. |
| POST | `/sso/sms` | `sso.token_post` | public; `Origin` must equal `PUBLIC_ORIGIN` | Rate limit (20 / 5 min / IP) → Origin → body ≤ 16 KB → verify → handoff transaction. JSON callers get `{"next": "/" \| "/sso/sms/link" \| "/sso/sms/failed"}` (200 / 200 / 401, 429 when rate-limited); form callers get 303 or the failure page with 401. |
| GET | `/sso/sms/failed` | `sso.failed` | public | Standalone generic failure page (contract section 12 text). |
| GET | `/sso/sms/link` | `sso.link_prompt` | pending link in the session | The "not linked yet" prompt; sign in with the OQB password or cancel. |
| GET/POST | `/sso/sms/link/confirm` | `sso.link_confirm` | password-signed-in user (marker `m=password`) + pending link; POST: CSRF token | "Link OQB account X to SMS account <name (staff code)>?"; POST links (`self_link`). |
| POST | `/sso/sms/link/cancel` | `sso.link_cancel` | CSRF token | Drops the pending link. |
| GET/POST | `/login` | `auth.login` | public; POST: CSRF token | Shows "Sign in with SMS" (`{primary issuer}/apps/oqb?origin={PUBLIC_ORIGIN}`) when SSO is on, the schema is ready and the primary has a pinned key (or `jwks_auto`); after a password login with a pending link, redirects to `/sso/sms/link/confirm`. `remember=1` asks for remember-me (granted only to unlinked accounts while `PASSWORD_REMEMBER_DAYS` > 0). A missing / wrong CSRF token re-renders the page with 400 and a flash. |
| GET | `/logout` | `auth.logout_confirm` | signed in (else redirect to login) | Confirmation page with a POST form; signs nobody out. |
| POST | `/logout` | `auth.logout` | CSRF token | Signs out of OQB only (session cleared, remember cookie deleted, CSRF token rotated), 303 to `/login`. Never redirects to SMS. |

| POST | `/api/sms/directory` | `sso.directory_feed` | the snapshot's signature (primary issuer with `accepts_feed`); no cookies / session | **404** unless `SMS_FEED_ENABLED` (independent of `SMS_SSO_ENABLED`); 503 `{"status":"unavailable"}` without the schema. Rate limit (30 / 10 min / IP) → `Content-Type: application/jwt` and `Content-Length` ≤ 5 MB (else 400 `malformed`) → `verify_snapshot` → `apply_snapshot`. Bodies: 200 `{"status":"applied","snapshot_id","linked","deactivated","reactivated","unmapped_subjects":[uuid…],"missing","invalid_entries"}`; 202 the same with `"status":"held","held":n`; 409 `{"status":"stale"}`; 400 / 401 / 403 / 429 `{"status":"rejected","reason":code}`; 500 `{"status":"error"}`, 503 `{"status":"busy"}` (SMS retries 5xx). `Cache-Control: no-store`. |

**Super-admin screens** (`sms_admin`, stage 5). Every route is `@login_required` + `@super_admin_required` (others get 403, anonymous users the login redirect). Every page except the overview answers **503** when `SMS_SCHEMA_READY` is false; the overview then shows a notice. POSTs are HTML forms that redirect back with a flash; refusals from the services (`TrustEditError`, `LinkError`, `FeedError`) are shown as a danger / warning flash and change nothing.

| Method | Path | Endpoint | Purpose |
|---|---|---|---|
| GET | `/admin/sms/` | `sms_admin.index` | Overview: the three switches, `PUBLIC_ORIGIN`, `OQB_ENVIRONMENT`, primary issuer + pinned kids; counts (linked, unmapped, held, open alerts, last snapshot); warnings: no break-glass super admin with a password (plan point 18), bootstrap file in use, a test issuer enabled, open alerts. |
| GET | `/admin/sms/mapping` | `sms_admin.mapping` | Every SMS subject (catalogue + uuids someone holds a level for) with its mappings; unmapped ones highlighted. |
| POST | `/admin/sms/mapping/add` | `sms_admin.mapping_add` | `sms_uuid`, `subject_id` → `sms_subject_map` row with `confirmed_by_user_id`; `sms_mapping` audit `add`. A duplicate is a no-op. |
| POST | `/admin/sms/mapping/remove` | `sms_admin.mapping_remove` | Deletes one mapping; `sms_mapping` audit `remove`. Effective at the next request (AT03). |
| GET | `/admin/sms/linking` | `sms_admin.linking` | `?show=unlinked` (default) / `all`. Typed link form (issuer from the trust list, sub, OQB account, optional note; datalist of the test issuers' `allowed_subs`); the primary issuer's roster with up to 5 `suggest_users` buttons per unlinked active person; every linked identity with Unlink. |
| POST | `/admin/sms/link` | `sms_admin.link` | `issuer`, `sub`, `user_id`, `label` → `admin_link` + commit. A `LinkError` is flashed and audited `link` / `fail` with its reason. |
| POST | `/admin/sms/unlink` | `sms_admin.unlink` | `issuer`, `user_id` → `unlink` (levels dropped, sessions end); `next=users` returns to Admin → Users. |
| GET | `/admin/sms/snapshots` | `sms_admin.snapshots` | Held snapshots with the people they would disable; the last 50 snapshots with their counts. |
| POST | `/admin/sms/snapshots/<id>/approve` | `sms_admin.snapshot_approve` | `approve_held`. |
| POST | `/admin/sms/snapshots/<id>/discard` | `sms_admin.snapshot_discard` | `discard_held`. |
| GET | `/admin/sms/trust` | `sms_admin.trust` | Each entry: state badge, pinned keys, JWKS kids seen, settings form; test issuers also get the window forms and their linked test accounts. Shows the recorded live / workshop fingerprints. |
| POST | `/admin/sms/trust/<id>/edit` | `sms_admin.trust_edit` | `enabled`, `enabled_days` (from now; blank keeps), `clear_until`, `jwks_url`; primary also `accepts_feed`, `allow_jit`, `level_cap`, `jwks_auto`; test issuer also `allowed_subs` (one `<sub> [label]` per line, `#` comments) → `upsert_issuer`. A test issuer enabled for more than 7 days is refused (addendum 3 item 6). `is_primary` is not editable here. |
| POST | `/admin/sms/trust/<id>/pin` | `sms_admin.trust_pin` | `kid`, `pem`, `fingerprint` (typed) → `pin_key`; a mismatch is refused. |
| POST | `/admin/sms/trust/<id>/unpin` | `sms_admin.trust_unpin` | `kid` → `unpin_key`. |
| POST | `/admin/sms/trust/<id>/window/open` | `sms_admin.trust_window_open` | `kid`, `fingerprint`, `pem` (not needed if that kid is pinned with the same fingerprint), `allowed_subs`, `enabled_days` (≤ 7) → `open_test_window`. |
| POST | `/admin/sms/trust/<id>/window/close` | `sms_admin.trust_window_close` | `close_test_window`; with `disable_accounts` (checked by default) also unlinks and disables (`local`) every account linked to that issuer. |
| POST | `/admin/sms/trust/<id>/linked/disable` | `sms_admin.trust_disable_linked` | Unlink + disable every account linked to a test issuer (`account_state` audit `disabled`, detail `test_window_close`). Refused for the primary. |
| GET | `/admin/sms/audit` | `sms_admin.audit` | Open alerts (acknowledge one / all), then the audit log filtered by `event` and `alerts=1`, 100 rows a page. |
| POST | `/admin/sms/audit/<id>/ack` | `sms_admin.audit_ack` | Sets `acknowledged_at` / `acknowledged_by` on an open alert (other rows unchanged). |
| POST | `/admin/sms/audit/ack-all` | `sms_admin.audit_ack_all` | Acknowledges every open alert. |

**Account actions** (`account_admin`, stage 5): super admin only, JSON `{"success": true, ...}` or `{"error"}` with 400 / 404; never on the caller's own account (400).

| Method | Path | Endpoint | Purpose |
|---|---|---|---|
| POST | `/admin/users/<id>/revoke-sessions` | `account_admin.revoke_sessions` | Bumps `session_version` (every session ends at its next request); `sessions_revoked` audit. |
| POST | `/admin/users/<id>/active` | `account_admin.set_active` | `active=0`: disabled `local`, `disabled_at`, sessions end; `active=1`: re-enabled whatever the reason (also `sms_deprovisioned`). `account_state` audit `disabled` / `enabled` with the previous reason. |
| POST | `/admin/users/<id>/local-only` | `account_admin.set_local_only` | `local_only=1/0`; refused (400) while the account is SMS-linked. `account_state` audit `local_only` / `not_local_only`. |

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

Session (route): a different signed-in user is logged out and the session cleared; the same user keeps the session; `login_user(remember=False)`, any remember cookie is deleted (`_remember = 'clear'`), `session['oqb_auth'] = {'m': 'sms', 'iss', 'at', 'seen'}`, CSRF token rotated, level cache dropped. A "not linked" outcome signs out whoever is signed in, clears the session and stores only the nonce.

## Sessions, CSRF and headers (contract sections 11, 14; stage 6)

- **Cookies**: `oqb_session` (Flask's signed session) and `oqb_remember` (Flask-Login), both `HttpOnly`, `SameSite=Lax`, `Secure` per `SESSION_COOKIE_SECURE` (`auto` = `PUBLIC_ORIGIN` is `https://`). With `SMS_EMBED_ENABLED` both become `SameSite=None; Secure; Partitioned` (`web_security.SessionInterface` rewrites the `Set-Cookie` headers after Flask-Login has added its own; Flask 3.0 cannot set `Partitioned`). The session cookie is a browser-session cookie (not `permanent`), so closing the browser ends it.
- **Marker**: every sign-in stores `session['oqb_auth'] = {'m': 'sms' | 'password', 'at', 'seen'[, 'iss'][, 'r': True]}` (unix seconds; `r` = remember-me). `enforce_session` (`before_request`, skipped for `static` and the feed and for requests with neither a signed-in session nor a remember cookie) runs `check_session` and ends a session that breaks it (`logout_user`, remember cookie deleted, CSRF token rotated, flash "Your session has ended…"); otherwise it moves `seen` forward at most once a minute.
- **Limits** (`check_session`, pure): SMS — 12 h absolute, 2 h idle, always, never remembered. Password without remember-me — the same 12 h / 2 h while `PASSWORD_SESSION_LIMITS` is on (default), else until the browser closes. Password with remember-me — absolute `PASSWORD_REMEMBER_DAYS` (≤ 30) from the login, no idle limit; refused (`remember_linked`) once the account is SMS-linked and (`remember_off`) when the setting is 0. A session without a valid marker ends (`no_marker`).
- **Remember-me** is opt-in (login checkbox), never for SMS sessions, never for SMS-linked accounts (`password_login` refuses it; a cookie restore of a since-linked account is refused). A cookie restore stamps a fresh `r` marker (`user_loaded_from_cookie`). An own password change keeps the choice (`relogin`).
- **CSRF** (`app/csrf.py`, [../core/04-backend-conventions.md](../core/04-backend-conventions.md)): `session['_csrf']` (32 random bytes, lazy), rotated at password login, SMS sign-in, logout and session end. POST / PUT / PATCH / DELETE need `X-CSRF-Token` or the `csrf_token` form field (`hmac.compare_digest`); endpoints exempt by name. Failure with `CSRF_MODE=enforce`: JSON-ish requests 400 `{"success": false, "error": "csrf_failed", "message"}`, HTML posts a 400 page, the login form re-renders with a flash, the Web Share Target fallback (`toolbox.markup_share_target`) 303 to the Markup tool. `report` logs `csrf_failed mode=… endpoint=… path=…` (never the token) and lets it through; `enforce` logs the same line.
- **Headers** (`security_headers`, every response): `Content-Security-Policy: frame-ancestors 'none'` (when the route set no CSP of its own), `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin` (the `/sso/*` `no-referrer` wins). No `script-src` on existing pages. With `SMS_EMBED_ENABLED`: `frame-ancestors https://ms.tic.edu.hk`, plus `https://ms-test.tic.edu.hk` only while the workshop trust entry is active (`frame_ancestors`; a trust-list error leaves only the live SMS), in the default CSP and in the `/sso/*` CSPs; no `X-Frame-Options`.

## Directory feed (contract section 10; `sms_feed_service.apply_snapshot`)

Verification is `verify_snapshot` (section 6 steps 1–7, `purpose=directory_snapshot`, lifetime ≤ 600 s, `nbf` required, primary issuer with `accepts_feed` else 403 `issuer_disabled`). Then one transaction:

1. Lock the issuer's `sms_feed_state` row (created first in its own short transaction, so two first pushes never deadlock on a gap lock). `iat` ≤ last applied → 409 `stale`. Insert the jti into `sso_used_jti` (duplicate → 409).
2. Upsert the whole catalogue; replace the issuer's roster with the snapshot's users.
3. For every **linked** identity of the issuer:
   - absent from the snapshot → unchanged; `external_identities.missing_since` set (first time: `feed_missing` audit row with `is_alert`); cleared when the sub reappears;
   - listed but its entry was invalid → unchanged (counted in `invalid_entries`, not flagged missing);
   - privileged account → skipped;
   - otherwise refresh the profile, then `active=false` → drop **all** its SMS levels; an active account is disabled `sms_deprovisioned`, `disabled_at` set, `session_version` bumped (every session ends at its next request), `deprovision` audit; an account disabled for another reason keeps that reason;
   - `active=true` → a `sms_deprovisioned` account is re-enabled (`reactivate` audit; any other disabled reason stays disabled), then the issuer's levels are replaced (`source='snapshot'`).
4. **Safety brake**: if the active linked accounts the snapshot would deactivate exceed `max(SMS_FEED_BRAKE_MIN, SMS_FEED_BRAKE_PCT % of linked)` (default `max(5, 10 %)`), everything else is applied, those accounts stay active, the snapshot is stored `held` with their subs, a `feed_held` alert is written and the answer is 202. Any older `held` row of the issuer becomes `superseded`.
5. Record the snapshot row, the new `last_iat` / `last_snapshot_id`, a `feed_snapshot` audit row; commit.

`unmapped_subjects` = uuids that are granted to someone in the snapshot or active in its catalogue and have no `sms_subject_map` row.

**Held snapshots** (Admin → SMS sign-in → Snapshots): `approve_held(id, actor_id)` disables the held subs the latest roster still lists as inactive (`deprovision` audit reason `held_approved`) and marks the row `approved`; `discard_held` marks it `discarded` (the accounts stay active; the next snapshot that still drops them is held again). Only the latest snapshot of an issuer can be decided (`FeedError` otherwise). Both audited `feed_decision`.

## Linking (contract section 9)

- **Self-link**: prompt → password login (marker `m = password`) → confirm page → `self_link` re-checks the pending link (10 min), that its issuer is still the primary, privileged / already-linked refusals, then links (`self`) and applies the token's levels. Only the primary issuer has self-links.
- **Admin link** (`admin_link`; Admin → SMS sign-in → Linking, from the roster or typed): typed `(issuer, sub)` with `sub` a UUID; the issuer must be on the trust list; a non-primary sub must be on that entry's `allowed_subs`; privileged and already-linked accounts refused; levels from the roster row if the feed has listed the sub (capped for non-primary). Audited with `actor_user_id`.
- **Unlink**: drops the link and that issuer's levels, bumps `session_version`, audited. From Linking, the trust list's test-account list, or the users page.
- **Test window close** (contract 2a closing step 1): "Close test window" disables the entry and unpins its keys, and by default unlinks and disables every account linked to it, in one request; "Unlink and disable all" does the account half alone. The live key is never pinned automatically (seeded without a key; pinned only from the trust screen with a typed fingerprint).
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

See [../core/06-system-settings.md](../core/06-system-settings.md). Env-only: `OQB_ENVIRONMENT`, `SMS_TRUST_BOOTSTRAP_FILE`, `PUBLIC_ORIGIN`, `PROXY_FIX_HOPS`, `SESSION_COOKIE_SECURE` (auto / true / false; config key `SESSION_COOKIE_SECURE_MODE`, resolved at boot into Flask's `SESSION_COOKIE_SECURE` / `REMEMBER_COOKIE_SECURE`). Registry, group "SMS sign-in": `SMS_SSO_ENABLED`, `SMS_JIT_ENABLED`, `SMS_FEED_ENABLED`, `SMS_FEED_BRAKE_MIN`, `SMS_FEED_BRAKE_PCT`, `SMS_EMBED_ENABLED`; group "Sign-in and sessions": `CSRF_MODE`, `PASSWORD_SESSION_LIMITS`, `PASSWORD_REMEMBER_DAYS`. Runtime flag `SMS_SCHEMA_READY`. Constants (contract MUSTs): skew 30 s, handoff lifetime 120 s, snapshot 600 s, token 8 KB, snapshot 5 MB, pending link 10 min, jti kept 24 h, sign-in rate limit 20 / 5 min, feed rate limit 30 / 10 min, decided snapshot rows kept 90 days, non-primary window 7 days, SMS session 12 h absolute / 2 h idle, remember-me at most 30 days.

## Permissions

See [../core/02-auth-and-permissions.md](../core/02-auth-and-permissions.md). The `/sso/*` routes and `/api/sms/directory` are public by design (the signed token, the snapshot signature or the pending link is the credential). Everything under `/admin/sms/` and the three `/admin/users/<id>/…` account actions are super admin only (`@login_required` + `@super_admin_required`); subject admins get 403. Super admins see the open-alert count as a red badge on the Admin menu, the SMS sign-in entry and the hub card (`sms_alert_count()`, one count query per page for super admins only, 0 without the schema).

## Background work / SSE / threads

None. `maybe_purge` runs at most every 10 minutes inside a token or feed POST. The JWKS cache and the rate limiters are per process; the feed is serialised in the DB (`sms_feed_state` `FOR UPDATE`).

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
- Feed: never `SELECT … FOR UPDATE` a missing `sms_feed_state` row and then insert it from another connection — the gap lock blocks the insert. `_lock_state` creates the row first, then locks.
- Feed: SMS levels from snapshots count only once `SMS_SSO_ENABLED` is on, so the feed can run before go-live (roster for linking) without changing anyone's access. Deprovisioning does act while sign-in is off.
- The feed is independent of `PUBLIC_ORIGIN` and sends no cookies; do not add `current_user` / `session` access to `directory_feed` (it would start setting cookies).
- Admin screens: the service raises, the route rolls back and flashes. Keep `db.session.rollback()` + `sms_trust.invalidate()` in `_trust_change` — a refused edit has already flushed (and cached) the change.
- Admin screens: test-window lengths are typed in days from now (`enabled_days`), so no browser time zone is involved; stored and shown as UTC.
- Admin screens: dict keys and form values must not be bare level names (`'user'` etc.) — the level-literal scan covers `sms_admin.py` and `account_admin.py` too.
- Route tests keep one app context and session across requests: `test_sms_admin` rolls the session back before each request (a real request starts a new transaction), otherwise a page reads the test's stale snapshot.
- Every `<form method="post">` needs `{{ csrf_field() }}` (`test_csrf_templates` fails otherwise); a page that does not extend `base.html` needs the `csrf-token` meta tag and `js/csrf.js` before its other scripts (`viewer.html`, `markup.html` do). A form built in JS appends `OQB.csrfInput()`; a hand-made `XMLHttpRequest` sets `X-CSRF-Token: OQB.csrfToken()`.
- A new CSRF exemption is a security decision: the only two are `sso.token_post` and `sso.directory_feed`. A request that cannot carry a token gets a redirect in `REDIRECT_ON_FAILURE`, not an exemption.
- `enforce_session` must not load the user for anonymous requests: Flask-Login's session protection marks the session not-fresh and the cookie is re-sent (AT10 asserts the refused handoff sets no cookie).
- Do not make the session `permanent`: `SESSION_REFRESH_EACH_REQUEST` would then re-send the cookie on every response, including the feed's.
- `web_security.init_app` registers request hooks; tests that only need ProxyFix on an existing app call `init_proxy`.
- Test clients: `tests/dbcase.FreshGClient` sends the session's CSRF token on unsafe requests unless the test passes its own (`auto_csrf = False` in `test_csrf_routes`); `dbcase.sign_in` writes `_user_id` **and** the `oqb_auth` marker (a session without a marker ends at the next request).
- Every session ended once at deploy: the cookie names changed (`session` → `oqb_session`, `remember_token` → `oqb_remember`) and ids are versioned. Announce it.

## Open issues

- **SSE GETs that write** (plan point 12, not implemented by decision): `GET /admin/health/sync?mode=delete`, PDF-import `GET /admin/pdf-import/commit`, `GET /admin/import/apply`, `GET /admin/ingestion/start`, `GET /admin/health/doc-thumbnails/backfill`. A top-level GET navigation carries `SameSite=Lax` cookies, so these can be triggered cross-site by a signed-in admin's browser. Proposal: require a `csrf` query parameter (`OQB.csrfUrl(url)`) on these endpoints behind `CSRF_MODE`; needs the owner's approval.
- Test accounts are created in Admin → Users like any account (no special screen); the runbook lists the names.
- The "Sign in with SMS" landing, `CSRF_MODE=report` log review, `Partitioned` cookies inside the SMS frame and the HTTPS cookie flags must be checked in a real browser on staging (plan points 5, 15).
- A remember-me cookie restore stamps the session marker at restore time; the absolute limit is still bounded by the cookie's own expiry (set at the password login).
- Smart Import plan tokens (`/admin/import/apply?token=`) and IMG Split staging tokens are not owner-bound.

## Related

[ADR-018](../decisions/ADR-018-sessions-csrf-and-headers.md), [ADR-019](../decisions/ADR-019-effective-level-authz.md), [../core/02-auth-and-permissions.md](../core/02-auth-and-permissions.md), [../core/06-system-settings.md](../core/06-system-settings.md), [../manuals/SMS_SSO_DEPLOYMENT.md](../manuals/SMS_SSO_DEPLOYMENT.md), [my-files.md](my-files.md), [generator.md](generator.md), [subject-snapshots.md](subject-snapshots.md).
