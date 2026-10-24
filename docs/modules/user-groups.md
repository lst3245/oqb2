# User groups

> Super admins define a named set of per-subject roles and put people in one or more groups. Members inherit the highest role. Effective access is still the highest of the personal grant, those groups, and SMS.

## Files

| File | Role |
|---|---|
| `app/models.py` | `UserGroup`, `UserGroupPermission`, `UserGroupMember` |
| `app/user_groups.py` | Create, rename, delete, replace grants, replace membership. Never writes personal grants or SMS levels. |
| `app/groups_admin.py` | Blueprint `groups_admin_bp`, prefix `/admin/user-groups`. Thin routes. |
| `app/authz.py` | `highest_grant`, `compute_levels(..., groups=)`, `_group_grants` inside `_load_levels` |
| `app/users_directory.py` | Same group grants for the users table, the subject filter, and the permissions panel. `group=<id>` filter. |
| `app/admin.py` | `POST /admin/users/<id>/groups`; subject delete also removes group grants for that subject |
| `app/user_erase.py` | Erase deletes `user_group_members` rows for that user (`LINKS`) |
| `templates/admin_user_groups.html` | Group list, add form, offcanvas |
| `templates/partials/admin_user_group_panel.html` | Name, per-subject role, member checklist |
| `templates/partials/admin_user_permissions.html` | Personal / From groups / SMS / Effective, plus group checkboxes |

## Tables

Schema: [../core/03-data-model-and-migrations.md](../core/03-data-model-and-migrations.md).

| Model | Table | Notes |
|---|---|---|
| `UserGroup` | `user_groups` | `name` VARCHAR(80) unique, `description` VARCHAR(200) NULL, `created_at` |
| `UserGroupPermission` | `user_group_permissions` | `role` ∈ `viewer` / `user` / `admin`; unique `(group_id, subject_id)`. FK to `user_groups` CASCADE. FK to `subjects` has no cascade — subject delete removes the rows first. |
| `UserGroupMember` | `user_group_members` | Primary key `(user_id, group_id)`. FK to `user_groups` CASCADE. FK to `users` has no cascade — user erase removes the rows first. |

## Routes

Every route is `@login_required` + `@super_admin_required`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/admin/user-groups/` | List: name, description, member count, access summary (`users_directory.summarize`). |
| POST | `/admin/user-groups/` | Form `name`, `description`. Blank name, name over 80 characters, description over 200, or a duplicate name (case-insensitive) flashes an error and redirects. |
| POST | `/admin/user-groups/<int:group_id>` | Form `name`, `description`. Rename and replace the description (blank clears it). |
| POST | `/admin/user-groups/<int:group_id>/delete` | Deletes the group, its grants and its memberships. Personal grants and SMS levels stay. |
| GET | `/admin/user-groups/<int:group_id>/panel` | HTML partial for the offcanvas. 404 unknown id. |
| POST | `/admin/user-groups/<int:group_id>/permissions` | JSON `{permissions: {subject_id: role\|''\|null}}`. Merges per key, same shape as a user's personal grants. Role must be `viewer`, `user` or `admin`. Unknown subject → 400. |
| POST | `/admin/user-groups/<int:group_id>/members` | JSON `{user_ids: [...]}` replaces that group's members. Empty list clears them. Unknown id → 400. |
| POST | `/admin/users/<int:user_id>/groups` | JSON `{group_ids: [...]}` replaces that user's memberships. On `admin_bp`. |

Create, rename and delete redirect back to the list with a flash. Permission and member saves return JSON `{success: true}` (plus `summary` or `member_count`).

## Business rules

- Effective level = highest of personal grant, every group, and SMS. A group never lowers the others. Unknown role strings are ignored.
- Several groups: `authz.highest_grant` keeps the higher role per subject.
- Super admin is not a group role. An active super admin is admin on every subject whether or not they are in a group. Membership is still stored, so it applies if they are later demoted.
- A disabled account has no effective level. The panel still shows the stored personal grant and the group contribution.
- SMS levels stay in `user_sms_levels`. Nothing in this module copies them into a group or into `user_subject_permissions`.
- Do not bump `session_version` when a group changes. The per-request cache in `authz` is enough; the next request sees the new level.
- Both `authz._load_levels` and `users_directory._ranks` must pass `groups`. The SMS-only column passes an empty group map on purpose.
- If the group tables cannot be read, authz logs and continues with personal grants and SMS. The users page does the same for its summary.

## Permissions

Super admin only. See [../core/02-auth-and-permissions.md](../core/02-auth-and-permissions.md).

## Gotchas

- `users_directory.sms_only_for` calls `_ranks` without groups. Passing the user's groups there would paint SMS and groups in the same column.
- Subject delete must delete `user_group_permissions` for that subject before the subject row (the FK does not cascade).
- User erase must delete `user_group_members` for that user. Deleting the group is a different action.
- A bulk `DELETE` of members uses `synchronize_session=False`, then new rows are added. Do not load those membership objects earlier in the same request.

## Related

[ADR-020](../decisions/ADR-020-user-groups.md), [ADR-019](../decisions/ADR-019-effective-level-authz.md), [admin-panel.md](admin-panel.md), [sms-sso.md](sms-sso.md).
