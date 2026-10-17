# ADR-018 — Server-side session limits, a session CSRF token and default-deny framing

**Status:** accepted, in force since 2026-10-18 (stage 6 of the SMS sign-in work, [../modules/sms-sso.md](../modules/sms-sso.md)).

## Context

Before SMS sign-in, OQB set a 90-day remember-me cookie on every password login, named its cookies `session` / `remember_token`, never set `Secure` (the LAN uses plain HTTP), had no CSRF protection beyond `SameSite=Lax`, logged out with a GET link, and sent no framing or sniffing headers. The SMS contract (v1.2 sections 11 and 14) and the build brief require: SMS sessions of at most 12 hours absolute and 2 hours idle with no remember-me; the same limits for password logins by default; remember-me only for unlinked accounts and for at most 30 days; a POST logout protected by CSRF; `frame-ancestors 'none'` + `X-Frame-Options: DENY` unless embedding inside the SMS is switched on; and a cookie named `oqb_session`.

Options considered for each part:

1. **Session limits.** (a) Cookie lifetimes only: an idle limit needs a server-side "last seen" anyway, and a stolen cookie would keep working until it expires. (b) A server-side session store: a new table and cleanup for a single-host app. (c) **Timestamps in the signed session cookie, checked on every request.** Chosen: no new storage, the cookie cannot be edited (signed with `SECRET_KEY`), and `session_version` (stage 1) already revokes cookies server-side.
2. **CSRF.** (a) Flask-WTF: a new dependency, and its JSON / htmx handling still needs a client script. (b) Origin / Referer checks only: the LAN deployment and `Referrer-Policy` make them unreliable. (c) **A per-session random token in the signed session, sent as `X-CSRF-Token` or a `csrf_token` field, added to every fetch / XHR / htmx / form by one script loaded before all others.** Chosen: no dependency, and about 130 existing fetch call sites need no edit.
3. **Framing.** A strict `script-src` CSP would break the inline scripts and CDN libraries on every page; only `frame-ancestors` is set, plus the classic headers.

## Decision

- `app/web_security.py` owns cookies, session limits and headers. Every sign-in stores `session['oqb_auth'] = {'m': 'sms' | 'password', 'at', 'seen'[, 'iss'][, 'r']}`. A pure `check_session` decides; a `before_request` hook ends a session that breaks the limits (the remember cookie is deleted too). SMS: 12 h / 2 h, never remembered. Password: 12 h / 2 h while `PASSWORD_SESSION_LIMITS` is on; with the opt-in remember box, `PASSWORD_REMEMBER_DAYS` (≤ 30, 0 = off) absolute and no idle limit; never for SMS-linked accounts (refused at login and when a cookie restores a since-linked account).
- Cookies are `oqb_session` / `oqb_remember`, `HttpOnly`, `SameSite=Lax`, `Secure` per `SESSION_COOKIE_SECURE` (`auto` = `PUBLIC_ORIGIN` is https). With `SMS_EMBED_ENABLED`: `SameSite=None; Secure; Partitioned`, written by a session-interface subclass because Flask 3.0 cannot set `Partitioned`. The session cookie stays a browser-session cookie.
- `app/csrf.py` checks every POST / PUT / PATCH / DELETE. Exemptions are by endpoint name and limited to the two SSO endpoints that carry their own credential (Origin check; signed snapshot). A request that structurally cannot carry a token (the Web Share Target fallback) gets a redirect, not an exemption. The token rotates on every login, logout and session end. `CSRF_MODE=report` exists only for rollout.
- Every response gets `frame-ancestors 'none'` (unless the route set its own CSP), `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`. Embedding allows the live SMS, and the workshop SMS only while its trust entry is active.
- Logout is `POST /logout`; `GET /logout` only shows a confirmation.

## Consequences

- Every existing session ends once at deploy (cookie rename + versioned ids). Teachers on password accounts must tick "Remember me" to stay signed in past 2 idle hours.
- Once `PUBLIC_ORIGIN` is `https://`, `Secure` cookies are not stored over plain-HTTP LAN access; LAN users move to the HTTPS name first, or `SESSION_COOKIE_SECURE=false` is set temporarily.
- A page that does not extend `base.html` must include the meta tag and `js/csrf.js`; every `<form method="post">` needs `csrf_field()` (enforced by `tests/test_csrf_templates.py`). A third-party script that posts with its own transport would fail; deploy with `CSRF_MODE=report` first and read the `csrf_failed` log lines.
- Writing `seen` re-sends the session cookie at most once a minute per active session.
- GET endpoints that write (SSE jobs, plan point 12) remain outside the scheme; listed as an open issue in the module doc.
