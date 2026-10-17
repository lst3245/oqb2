# SMS sign-in — deployment runbook (live OQB)

> For the person deploying branch `feature/sms-sso` to the live host (`https://oqb.mathedu.info`). Spec: [../modules/sms-sso.md](../modules/sms-sso.md). Contract: `OQB_CONTRACT_v1.2.md` (sections 2, 2a, 3, 13).

**State of the branch:** stages 1–5 are built (authorization, trust list, verifier, sign-in, linking, directory feed, the super-admin screens under **Admin → SMS sign-in** `/admin/sms/`). CSRF / session limits / cookie flags (stage 6) are **not**. Steps marked *(stage 6)* need that stage merged first. Do not switch `SMS_SSO_ENABLED` on in production before stage 6 is merged.

Screens used below (all super admin only): **Overview** `/admin/sms/`, **Subject mapping** `/admin/sms/mapping`, **Linking** `/admin/sms/linking`, **Snapshots** `/admin/sms/snapshots`, **Trust list** `/admin/sms/trust`, **Audit and alerts** `/admin/sms/audit`. The red badge on the Admin menu counts unacknowledged alerts.

## 0. Before the first live boot

1. **Back up the database** (`mysqldump` of `oqb`, plus the `Storage` tree), because the boot patches run as soon as the new code starts: they add the `users` columns and create 12 tables. They are additive; nothing existing is changed or dropped.
2. Install the new dependency into the **live interpreter** (the one that runs `run.py`): `python -m pip install "PyJWT>=2.8,<3"` (from `requirements.txt`).
3. Deploy by merging while the server is stopped, then start it once and check the log for `sso_schema` errors. If `SMS_SCHEMA_READY` is false (log line `sso_schema patches failed` / `creating … failed`), SMS sign-in stays off (503) and password login keeps working. Fix the cause before going further.
4. Everyone signs in again once (session ids carry the session version since stage 1).

## 1. Host prerequisites (contract section 13)

- HTTPS on `oqb.mathedu.info` with a publicly trusted certificate; HSTS on the proxy.
- The Windows clock synced by NTP (`w32time`), within ±5 seconds (`w32tm /query /status`). Tokens allow 30 s of skew.
- A local **break-glass** super admin with a password (`local_only`); it is never linked to SMS. The Overview screen warns when no active super admin can sign in with a password.

## 2. Settings and `.env`

| Key | Where | Value |
|---|---|---|
| `OQB_ENVIRONMENT` | `.env` | `production` (the default; never `test` / `staging` / `dev` on live — those trust the contract-test issuers and the bootstrap file) |
| `SMS_TRUST_BOOTSTRAP_FILE` | `.env` | unset (ignored in production, with an alert) |
| `PUBLIC_ORIGIN` | `.env` | `https://oqb.mathedu.info` |
| `PROXY_FIX_HOPS` | `.env` | the number of reverse proxies in front of Flask (normally `1`) |
| `SESSION_COOKIE_SECURE` | `.env` *(stage 6)* | on |
| `CSRF_MODE` | `.env` *(stage 6)* | `report` first; `enforce` after the logs show no `csrf_failed` for real pages |
| `SMS_SSO_ENABLED` | System Settings → SMS sign-in | on, only after the steps below |
| `SMS_FEED_ENABLED` | System Settings | on when the feed should start (before linking, to get the roster; works while `SMS_SSO_ENABLED` is still off) |
| `SMS_FEED_BRAKE_MIN` / `SMS_FEED_BRAKE_PCT` | System Settings | leave at 5 / 10 (hold a snapshot that would disable more than max(5, 10 % of linked) accounts) |
| `SMS_JIT_ENABLED` | System Settings | **off** until linking (phase A) is complete |

## 3. Pin the live key

The repo carries SMS's live public key: `deploy/sms/sms-live-202610.pub.pem` (kid `sms-live-202610`, RSA 3072). OQB never pins it by itself; the live trust entry `https://ms.tic.edu.hk` is seeded **enabled, primary, without a key**, so every SMS token is refused until this step.

1. Confirm the fingerprint over a second channel with SMS (SMS shows it on **System → Connected apps**): `fed812e5e0ebcc47e44ec3942ccc74db8310ac9ea873cd56bfe628e9ca5ea1f2` (SHA-256 of the DER SubjectPublicKeyInfo; reproduce with `openssl pkey -pubin -in sms-live-202610.pub.pem -outform DER | sha256sum`).
2. Pin it on the live entry: **Trust list** → `https://ms.tic.edu.hk` → **Pin a key**: kid `sms-live-202610`, paste the PEM, type the confirmed fingerprint (a mismatch is refused, nothing is saved). Keep "Trust new keys from the JWKS automatically" off. The Overview then lists the kid next to the primary issuer.
3. Staging and test never trust this key (their trust lists hold only the contract-test issuers).

## 4. Feed, subject mapping and linking (phase A)

1. **Feed first.** Turn on `SMS_FEED_ENABLED` and ask SMS to point its push at `https://oqb.mathedu.info/api/sms/directory` (SMS pushes every 10 minutes and on "Sync now"). Check the first push: `auth_audit_log` event `feed_snapshot` with reason `applied`; `sms_subject_catalogue` and `sms_roster` are filled. Until `SMS_SSO_ENABLED` is on, SMS levels do not count, so nobody's access changes; a linked leaver is still disabled.
2. Map each SMS subject to OQB subjects on **Subject mapping** (pick the OQB subject, **Map**; one SMS subject may map to several). Nothing is matched automatically; an unmapped subject (highlighted) grants only the default level. Each push reports the uuids still unmapped (`unmapped_subjects`; Snapshots shows the count).
3. Link existing teachers on **Linking**: the roster lists every SMS staff member not yet linked, with up to five suggested OQB accounts (staff code, email, name); check the person, click the suggestion. Or teachers self-link once sign-in is on (open the Question Bank from SMS → sign in with the OQB password → confirm).
4. Turn on `SMS_SSO_ENABLED`. The login page then shows **Sign in with SMS** (`https://ms.tic.edu.hk/apps/oqb?origin=https://oqb.mathedu.info`).
5. **Watch the brake.** A push that would disable more than max(5, 10 %) of linked accounts answers 202 and is held (alert `feed_held`); nothing is disabled until a super admin approves it on **Snapshots** (**Approve: disable these accounts** or **Discard: keep them active**; only the latest snapshot can be decided). The first real snapshot is the most likely to trip it if SMS data is off. Linked subs missing from a snapshot are left alone and flagged (`feed_missing` alert, `external_identities.missing_since`).
6. JIT later: turn on `SMS_JIT_ENABLED` once linking is done; new staff then get a password-less account on first sign-in.

## 5. Workshop test window (contract section 2a)

The workshop issuer `https://ms-test.tic.edu.hk` exists on the trust list but is **disabled**. Use it against live OQB only inside a test window. The values below are the planned first window; they are **not** seeded or applied anywhere — enter them by hand when the window opens.

| Item | Value |
|---|---|
| Issuer | `https://ms-test.tic.edu.hk` |
| Key | kid `sms-test-202610`, SHA-256 (DER SPKI) fingerprint `450e8b5093650aa7c1feafd9458582c3c66c27687d19e79866d055ee8f239d2b` |
| JWKS | `https://tic-test.mathedu.info/api/v1/apps/jwks.json` (the same document is served at `https://ms-test.tic.edu.hk/api/v1/apps/jwks.json`, the URL seeded on the entry); `jwks_auto` stays off |

| SMS test account | `sub` (`allowed_subs`; label = email) | OQB test account | SMS sends | Live OQB gives (level cap) |
|---|---|---|---|---|
| `oqb-test-viewer@tic.edu.hk` | `8a7b097b-52d2-4afe-9c13-47bd5d22dfc7` | `sso_test_viewer` | `default_level=viewer`, no subjects | Viewer on every subject |
| `oqb-test-maths@tic.edu.hk` | `0d2b22b8-4104-4d88-8c47-7ee2eee47679` | `sso_test_maths` | `viewer` + `admin` on MATH | **User** on MATC / MAT1 / MAT2 (mapped from MATH), Viewer elsewhere; admin pages 403 |
| `oqb-test-admin@tic.edu.hk` | `877f5123-e3a2-4ffd-a09e-7f016d264e04` | `sso_test_admin` | `default_level=admin` | Viewer on every subject; never admin, never super admin |

**Open** (**Trust list** → `https://ms-test.tic.edu.hk` → **Open test window**):
1. SMS sends the workshop public key, its fingerprint over a second channel, the test accounts' `sub`s with account names, and the end date. Check the fingerprint against the table (or the new one for a later window); the Trust list shows the recorded one.
2. Fill the form: kid `sms-test-202610`, the typed fingerprint, the PEM, the test accounts one per line as `<sub> <email>` (exactly the three subs above), and **Open for (days)** — at most 7 (more is refused, nothing is saved). OQB then enables the entry capped (`primary=false`, `allow_jit=false`, `accepts_feed=false`, `level_cap` on, `jwks_auto` off) in one audited action. A wrong fingerprint is refused.
3. Create the three OQB test accounts `sso_test_viewer`, `sso_test_maths`, `sso_test_admin` (Admin → Users → **Add User**, a long random password): not super admin, not local-only, **no local subject grants**, no real teacher's data. Never reuse a real teacher's account.
4. Link each to its sub on **Linking** → **Link by SMS account ID**: issuer `https://ms-test.tic.edu.hk`, the sub (the field suggests the window's subs), the test account, the email as note. The test accounts are never in the roster. A sub outside the window's list is refused.
5. Confirm the mapping MATH → MATC, MAT1, MAT2 exists (without it `sso_test_maths` gets Viewer only).
6. Alerts: every non-primary sign-in writes an `is_alert` audit row (`nonprimary_login`); super admins see it under **Audit and alerts → Open alerts** and as the badge on the Admin menu. The Overview also warns while a test issuer is enabled.

**During**: each test account opens the Question Bank from the workshop SMS. Expect the last column of the table. A real teacher's workshop account is refused (`sub_not_allowed`); nothing changes for real accounts. SMS must not refresh the workshop database during the window (that recreates the test accounts with new `sub`s).

**Close** (on the end date or earlier; do not rely on `enabled_until` alone):
1. **Trust list** → `https://ms-test.tic.edu.hk` → **Close test window** with "Also unlink and disable the linked accounts" ticked (the default): `enabled=false`, every workshop kid unpinned, and `sso_test_viewer`, `sso_test_maths`, `sso_test_admin` (every account linked to that issuer) unlinked and disabled, in one request. If the box was not ticked, **Unlink and disable all** on the same card does the accounts afterwards; single accounts can be unlinked there or on Linking, and disabled in Admin → Users → Account.
2. Check the card: no linked test accounts left, no key pinned, state Disabled.
3. Review the `nonprimary_login` alerts on **Audit and alerts** (filter by event) and acknowledge them.
4. Confirm a workshop token is now refused with `issuer_disabled` (or `unknown_kid`).

This procedure is rehearsed end to end by `tests/test_sms_workshop_window.py` with the fixture workshop stand-in issuer `https://sms-contract-test-workshop.invalid` (never with `ms-test` and a fixture key).

## 6. Rollback

Redeploy `main` (stop the server, check out `main`, start). The new columns and tables are additive and ignored by the old code; leave them in place. To switch SMS sign-in off without a redeploy: System Settings → `SMS_SSO_ENABLED` off (SMS routes 404, SMS levels stop counting, local grants and passwords keep working). To stop the feed: `SMS_FEED_ENABLED` off (`/api/sms/directory` answers 404; SMS keeps retrying on its schedule). Accounts the feed disabled (`sms_deprovisioned`) are re-enabled by their next valid sign-in or snapshot, or at once by a super admin (Admin → Users → Account → **Enable account**). To lock someone out at once: **Disable account** (SMS never re-enables it) or **End all sessions**. Key compromise: **Trust list → Unpin** the kid at once; password login keeps working.
