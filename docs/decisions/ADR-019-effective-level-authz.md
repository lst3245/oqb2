# ADR-019 — One effective-level function decides every subject permission

**Status:** accepted, in force since 2026-10-18 (stage 1 of the SMS sign-in work, [../modules/sms-sso.md](../modules/sms-sso.md)). Since stage 2 `authz._sms_inputs` feeds SMS levels (`user_sms_levels` + `sms_subject_map` + the trust list's issuer caps) into `compute_levels`, only while `SMS_SSO_ENABLED` is on and the SSO schema is ready; any error falls back to local grants.

## Context

Permission checks were spread over `User` helpers, `get_subject_roles()` reads, raw `is_super_admin` checks and per-route queries. The subject decorators let a request through when no `subject_id` could be found, so any route keyed by a question, taxonomy or file id was guarded only by `@login_required` unless the author remembered an explicit check. A review found 34 such gaps: subject admins could add, delete or reorder topics and chapters of other subjects, Bulk Edit could write another subject's questions or attach another subject's topics, dashboard taxonomy APIs and searches leaked other subjects, viewers could generate and save, share recipients could download files whose subjects they cannot read, and staging tokens / cancel endpoints were not bound to their owner.

SMS sign-in adds two more level sources (an SMS `default_level` and mapped SMS subject levels, with an issuer cap). Adding them to every helper and route separately would multiply the drift.

Options considered:

1. **Keep the helpers, add SMS checks to each.** Every new source repeats the same work in many places; gaps stay possible.
2. **Store the merged level in `user_subject_permissions` at sign-in.** Mixes SMS-owned and locally granted rows, needs cleanup on every feed change, and a stale row outlives a revoked SMS grant.
3. **One pure function plus a per-request cache, used by everything.** Chosen.

## Decision

- `app/authz.py` owns the level math. `compute_levels` is pure: level on S = max(local grant, SMS `default_level`, every SMS subject mapped to S), with non-primary issuers capped (default ≤ viewer, subject ≤ user) and rows from untrusted issuers ignored. Super admin is `admin` everywhere; a disabled account has no level.
- `effective_levels(user)` loads the inputs once per request and caches them on `flask.g`; levels are never stored in the session or written back to the grant table.
- `User` helpers, the decorators, dashboard scoping, generation, file downloads and the taxonomy / bulk routes all call `authz`. The subject decorators **fail closed** (no subject id → 403).
- Object-keyed routes check the object's subject with `require_subject` / `require_taxonomy` before any restore-point capture; owner-bound staging uses `owns`.
- Refusals go through `deny()` with one JSON envelope for API callers.

## Consequences

- A new level source (stage 2) changes `authz._sms_inputs` and nothing else.
- Routes that relied on the pass-through now need an explicit check; all routes that carry the decorators pass `subject_id` in the URL, so none changed behaviour.
- Generation requires `user` level on each question's subject; a viewer grant on one subject plus a user grant on another no longer lets a teacher generate from the viewer subject.
- One extra grant query per request per user (cached), plus one `Subject` query for super admins.
- The session marker added in stage 6 ([ADR-018](ADR-018-sessions-csrf-and-headers.md), `session['oqb_auth']`) records only how and when the user signed in; it never carries levels. An SMS sign-in drops the per-request cache (`authz.invalidate()`) so the new levels apply at once.
