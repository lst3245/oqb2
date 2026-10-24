# ADR-020 — User groups raise access; they are not stored back onto personal grants or SMS

**Status:** accepted, 2026-10-20.

## Context

Subject access was set one person at a time (`user_subject_permissions`) and, when SMS sign-in is on, combined with SMS levels. [ADR-019](ADR-019-effective-level-authz.md) already says the effective level is one pure function, and that a new source is another input to that function rather than a copy written into `user_subject_permissions`.

Departments want to define a role once (for example every English teacher is a user of ENG) and attach people to it. A person can belong to more than one such set.

## Decision

- A **user group** has a name and, per subject, a role of `viewer`, `user` or `admin` (`user_groups`, `user_group_permissions`). Membership is `user_group_members`. A person may be in several groups.
- Effective level on a subject is the **highest** of the personal grant, every group the person belongs to, and the SMS level (default and mapped subjects, with the existing issuer cap). A group never lowers a personal grant or an SMS level.
- Group rows are never copied into `user_subject_permissions` or `user_sms_levels`, and SMS rows are never copied into a group. `authz.compute_levels` takes the folded group map as `groups`. Both `authz._load_levels` and `users_directory._ranks` pass it, so the users table and the live checks agree.
- A group cannot grant super admin. That remains `users.is_super_admin`.
- A disabled account still has no effective level. Membership and grants stay stored.
- Changing a group does not bump `session_version`. Levels are not in the session; the next request reads them again.
- Only a super admin manages groups (Admin → User Groups) and membership (that page, or the user's permissions panel).

## Consequences

- Deleting a group drops only the inherited part. Personal grants and SMS levels stay. Erasing a user drops that user's membership rows; the group stays.
- Deleting a subject deletes `user_group_permissions` for that subject in the same cleanup as `user_subject_permissions` (no cascade from `subjects`).
- One extra membership query per request per user, inside the existing per-request cache.
