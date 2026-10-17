# SMS sign-in — deployment runbook (live OQB)

> For the person deploying branch `feature/sms-sso` to the live host (`https://oqb.mathedu.info`). Spec: [../modules/sms-sso.md](../modules/sms-sso.md). Contract: `OQB_CONTRACT_v1.2.md` (sections 2, 2a, 3, 13).

**State of the branch:** stages 1–3 are built (authorization, trust list, verifier, sign-in, linking services). The feed (stage 4), the super-admin screens — trust list, mapping, linking, audit (stage 5) — and CSRF / session limits / cookie flags (stage 6) are **not**. Steps marked *(stage N)* need that stage merged first. Do not switch `SMS_SSO_ENABLED` on in production before stages 5 and 6 are merged.

## 0. Before the first live boot

1. **Back up the database** (`mysqldump` of `oqb`, plus the `Storage` tree), because the boot patches run as soon as the new code starts: they add the `users` columns and create 12 tables. They are additive; nothing existing is changed or dropped.
2. Install the new dependency into the **live interpreter** (the one that runs `run.py`): `python -m pip install "PyJWT>=2.8,<3"` (from `requirements.txt`).
3. Deploy by merging while the server is stopped, then start it once and check the log for `sso_schema` errors. If `SMS_SCHEMA_READY` is false (log line `sso_schema patches failed` / `creating … failed`), SMS sign-in stays off (503) and password login keeps working. Fix the cause before going further.
4. Everyone signs in again once (session ids carry the session version since stage 1).

## 1. Host prerequisites (contract section 13)

- HTTPS on `oqb.mathedu.info` with a publicly trusted certificate; HSTS on the proxy.
- The Windows clock synced by NTP (`w32time`), within ±5 seconds (`w32tm /query /status`). Tokens allow 30 s of skew.
- A local **break-glass** super admin with a password (`local_only`); it is never linked to SMS.

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
| `SMS_FEED_ENABLED` | System Settings *(stage 4)* | on when the feed should start (before linking, to get the roster) |
| `SMS_JIT_ENABLED` | System Settings | **off** until linking (phase A) is complete |

## 3. Pin the live key

The repo carries SMS's live public key: `deploy/sms/sms-live-202610.pub.pem` (kid `sms-live-202610`, RSA 3072). OQB never pins it by itself; the live trust entry `https://ms.tic.edu.hk` is seeded **enabled, primary, without a key**, so every SMS token is refused until this step.

1. Confirm the fingerprint over a second channel with SMS (SMS shows it on **System → Connected apps**): `fed812e5e0ebcc47e44ec3942ccc74db8310ac9ea873cd56bfe628e9ca5ea1f2` (SHA-256 of the DER SubjectPublicKeyInfo; reproduce with `openssl pkey -pubin -in sms-live-202610.pub.pem -outform DER | sha256sum`).
2. Pin it on the live entry from the trust screen *(stage 5)*: paste the PEM, type the confirmed fingerprint (a mismatch is refused), kid `sms-live-202610`. Keep `jwks_auto` off.
3. Staging and test never trust this key (their trust lists hold only the contract-test issuers).

## 4. Subject mapping and linking (phase A)

1. Map each SMS subject uuid to OQB subjects *(stage 5; the catalogue fills from the feed or from sign-ins)*. Nothing is matched automatically; an unmapped subject grants only the default level.
2. Link existing teachers: admin linking from the roster *(stage 5 screen; `sms_sso_service.admin_link`)*, or teachers self-link (open the Question Bank from SMS → sign in with the OQB password → confirm).
3. Turn on `SMS_SSO_ENABLED`. The login page then shows **Sign in with SMS** (`https://ms.tic.edu.hk/apps/oqb?origin=https://oqb.mathedu.info`).
4. Feed *(stage 4)*: SMS pushes every 10 minutes to `POST /api/sms/directory`; leavers are deprovisioned, with a safety brake for mass deactivations.
5. JIT later: turn on `SMS_JIT_ENABLED` once linking is done; new staff then get a password-less account on first sign-in.

## 5. Workshop test window (contract section 2a)

The workshop issuer `https://ms-test.tic.edu.hk` exists on the trust list but is **disabled**. Use it against live OQB only inside a test window:

**Open** (trust screen *(stage 5)*; service `sms_trust.open_test_window`):
1. SMS sends the new workshop public key (`sms-test-YYYYMM`), its fingerprint over a second channel, the test accounts' `sub`s with account names, and the end date.
2. On the `https://ms-test.tic.edu.hk` entry: pin the key after checking the fingerprint; `jwks_auto` off; enabled with `enabled_until` **at most 7 days ahead** (more is refused); primary / JIT / feed off; level cap on; `allowed_subs` = exactly the test subs (each with its account name as label). OQB refuses any other combination.
3. Create one dedicated, non-privileged OQB test account per SMS test account (Admin → Users; never a real teacher's account) and link each `(https://ms-test.tic.edu.hk, sub)` to it by **typing the issuer and sub** in admin linking (the test accounts are never in the roster). A sub that is not on `allowed_subs` is refused.
4. Every non-primary sign-in writes an `is_alert` audit row (`nonprimary_login`) for super admins.

**During**: each test account opens the Question Bank from the workshop SMS. Expected: `oqb-test-viewer` → Viewer everywhere; `oqb-test-maths` → Viewer plus **User** on the mapped maths subjects (admin is capped); `oqb-test-admin` → Viewer everywhere; a real teacher's workshop account is refused (`sub_not_allowed`).

**Close** (on the end date or earlier): "Close test window" disables the entry and unpins every kid in one action (`sms_trust.close_test_window`, audited); then unlink or disable each test account (`sms_sso_service.linked_users(issuer)` lists them); review the `nonprimary_login` audit rows. A workshop token is then refused with `issuer_disabled` / `unknown_kid`.

## 6. Rollback

Redeploy `main` (stop the server, check out `main`, start). The new columns and tables are additive and ignored by the old code; leave them in place. To switch SMS sign-in off without a redeploy: System Settings → `SMS_SSO_ENABLED` off (SMS routes 404, SMS levels stop counting, local grants and passwords keep working). Key compromise: unpin the kid at once; password login keeps working.
