# Question Sets

> Per-subject, named, server-persisted lists of question DB IDs (`SavedQuestionSet`) with a manage page, a `/user/sets` JSON API, and the dashboard Set Operations builder that consumes them.

## Files

| File | Role |
|---|---|
| `app/user.py` | `SavedQuestionSet` routes under `user_bp` (`/user/sets...`): `sets`, `sets_list`, `sets_save`, `sets_data`, `sets_delete`, `sets_bulk_delete`, `sets_star`, `sets_share`, `sets_rename`; helpers `_serialize_question_set`, `_can_view_set`, `_can_manage_set`. |
| `app/models.py` | `SavedQuestionSet` model. |
| `templates/saved_question_sets.html` | Manage page: subject filter, list with Starred / My sets / Shared grouping, rename / star / share / delete / bulk delete, "Apply on dashboard" link (`/dashboard/?question_set_id=<id>`). |
| `templates/dashboard.html` | **Set** button, `#setOpsModal` (sources chips, expression bar, Evaluate, Apply actions, Save-as-set), scratch-set helpers, `?question_set_id=` URL handler. |
| `templates/partials/question_list.html` | Renders the list header where the Set button lives (gated on `can_generate`). |

## Tables

`SavedQuestionSet` (`saved_question_sets`): `id`, `user_id` FK, `name` (200), `subject` (String(10) FK `subjects.id`), `question_ids` (Text, JSON list of int `Question.id` materialised at save time — not a formula), `is_starred` (indexed), `is_shared` (indexed), `created_at`, `updated_at`. Logical upsert key `(user_id, subject, name)` (enforced in `sets_save`, not by a DB constraint). Parallel to `SavedFilter` / `SavedGenerationProfile` (same star/share machinery, see [my-files.md](my-files.md)).

Schema: [../core/03-data-model-and-migrations.md](../core/03-data-model-and-migrations.md).

## Routes

All `@login_required`. Owner/super-admin checks are inline (`_can_manage_set`, `_can_view_set`, `is_super_admin`).

| Method | Path | Authz | Purpose |
|---|---|---|---|
| GET | `/user/sets` | login | Manage page. Passes `subjects=[{id,name}]` — all subjects for super admin, else those in `current_user.subject_permissions`. |
| GET | `/user/sets/list?subject=&show_all=0` | login | JSON array of sets: own + `is_shared` sets the user has subject access to, ordered `is_starred DESC, subject, name`. `show_all=1` (super admin only) returns every set. Optional `subject` filter. Row shape: `{id, name, subject, is_starred, is_shared, is_own, username (owner, null when own), created_at, updated_at, question_count}` (no `question_ids`). |
| POST | `/user/sets/save` | login; must have access to `subject` (403); unknown subject 400 | Body `{name, subject, question_ids:[int]}`. Upsert by `(current_user, subject, name)`. IDs are int-coerced, de-duplicated preserving order, and filtered to `Question.id` rows whose `subject` matches. Returns `{success, id, updated, question_count}`. |
| GET | `/user/sets/<int:id>/data` | `_can_view_set` (403) | Row shape above plus `question_ids:[int]`. Used by the Set Ops modal and `?question_set_id=`. |
| DELETE | `/user/sets/<int:id>` | `_can_manage_set` (owner / super admin) | Delete one. |
| POST | `/user/sets/bulk-delete` | per-row `_can_manage_set` | Body `{ids:[]}`; non-manageable IDs are skipped. |
| POST | `/user/sets/<int:id>/star` | `_can_manage_set` | Body `{is_starred?: bool}`; toggles when omitted. |
| POST | `/user/sets/<int:id>/share` | **super admin only** (403 "Only super admins can share sets") | Body `{is_shared?: bool}`; toggles when omitted. |
| POST | `/user/sets/<int:id>/rename` | `_can_manage_set` | Body `{name}`; 409 if another set of the same owner+subject already has that name. |

## Business rules / invariants

- **Subject-scoped**: a set belongs to exactly one subject; `sets_save` drops IDs that do not belong to that subject. The dashboard modal lists only sets for the active subject (`setOpsCurrentSubject`), grouped Starred / My sets / Shared by admins.
- **Materialised, not live**: `question_ids` is a snapshot. To change contents, save again under the same name (upsert) or a new name.
- **ID typing**: server stores ints; the dashboard's `selectedQuestions` and every chip set use **strings** (checkbox `value`). Coerce to string on load, int on save.
- **Sharing is super-admin only** (`is_shared`), and shared sets are visible only to users who also have subject access (`_can_view_set`, `sets_list` filter).
- **Set button** sits in the question-list header between Manage and Generate, gated on `current_user.can_generate()` (viewers do not see it).

### Set Operations modal (`#setOpsModal` in `dashboard.html`)

- Sources panel chips: `Selection` (live `selectedQuestions`), `Filter Result` (all IDs of the current sidebar filter from `#allQuestionIds`), `Result` (last Evaluate output), **Scratch sets** (browser-only snapshots in `localStorage['oqb_scratchSets']`, subject-scoped, with Duplicate / Rename / Delete / Clear-all), and **Saved sets** from `GET /user/sets/list?subject=<active>` (chip `src:'saved'` carries `id`, `name` and the cached `ids` fetched from `/user/sets/<id>/data`). Full chip taxonomy and scratch helper list: [dashboard.md](dashboard.md).
- Expression bar: tapping a source chip appends it; operator buttons (union, intersect, difference) and parentheses append their own chips; Backspace / Clear edit the chip array.
- Evaluate: recursive-descent parse of the chip array (single precedence, left-associative, parens for grouping) → AST → client-side evaluation over `Set<string>` via `setUnion`, `setIntersect`, `setDifference` (left minus right, binary). No complement/`Not` (universe is ambiguous). Empty expression → empty result.
- Apply actions: **Replace Selection** (order-preserving), **Append to Selection**, **Save Result as set…** (`POST /user/sets/save` with the active subject), plus **Save current Selection as set…** which bypasses the expression. Apply actions are selection-only (category 1): they never touch the sidebar filter; if Show Selected Only is on, the page re-syncs to the new selection via `syncShowSelectedOnlyToServer()`.
- `Selection ∩ Filter Result → Replace` is the idiom for trimming the selection to the current filter.

### Apply via URL (`/dashboard/?question_set_id=<id>`)

Handled in the dashboard `DOMContentLoaded` hook beside `?profile_id=` and `?filter_data_id=`. Fetches `/user/sets/<id>/data` then:

- **Same subject** as the persisted dashboard subject → replace `selectedQuestions` only; sidebar filter untouched; no auto-submit; **Show Selected Only auto-enabled** so the set is visible immediately (`questionSetSameSubject` flag).
- **Different subject** → switch `#subjectSelect` with `suppressSelectionClearOnSubjectChange = true` (subject change normally clears selection), reset the sidebar filter to subject-only defaults (old topic IDs would be stale), seed the selection from the set, auto-submit, auto-enable Show Selected Only.
- The param is removed from the URL afterwards.

## Settings & config keys

None. (No `current_app.config` reads in the sets routes.) See [../core/06-system-settings.md](../core/06-system-settings.md) for the global registry.

## Permissions

| Operation | Owner | Super admin | Other user with subject access | Other user without subject access |
|---|---|---|---|---|
| List (`/sets/list`) | own rows | all (`show_all=1`) or own + shared | only `is_shared` rows for accessible subjects | nothing |
| Read `/data` | yes | yes | yes if `is_shared` | no |
| Create / Save | yes (needs subject access) | yes | yes (creates their own set) | 403 |
| Delete / Rename / Star | yes | yes | no | no |
| Toggle Share | no | yes | no | no |
| Manage page | yes | yes | yes | yes (empty subject list) |

Details: [../core/02-auth-and-permissions.md](../core/02-auth-and-permissions.md).

## Background work / SSE / threads

None. All operations are synchronous JSON; evaluation of set algebra happens entirely in the browser.

## Gotchas

1. **Chip IDs are strings, DB IDs are ints.** A saved set loaded without `String()` coercion will silently fail to intersect with the selection.
2. **Apply actions must stay selection-only.** Never submit `#filterForm` from the Set Ops modal; the only allowed side-effect is `syncShowSelectedOnlyToServer()` when the toggle is already on ([ADR-008](../decisions/ADR-008-selection-independent-of-filter.md)).
3. **Different-subject apply must set `suppressSelectionClearOnSubjectChange`** before changing the subject, or the seeded selection is wiped by the subject-change handler.
4. **`sets_save` silently drops foreign-subject and unknown IDs**; `question_count` in the response is the post-filter count — surface it to the user.
5. **Rename collision is 409** on `(owner, subject, name)`; save-with-same-name is an upsert (200, `updated: true`). Do not conflate the two.
6. **Scratch sets are not server data.** They live only in `localStorage['oqb_scratchSets']`; deleting one also removes its chips from the expression. Promote to a saved set for persistence/sharing.
7. **Shared sets need subject access** on the reader's side; `is_shared` alone is not enough (unlike search profiles / gen presets, which any logged-in user can read when shared).
8. **While Show Selected Only is on, the Filter Result chip equals the Selection chip** (`#allQuestionIds` is the selection) — harmless but confusing.

## Related

- [dashboard.md](dashboard.md) — selection vs filter policy, Set Ops chip taxonomy, scratch helpers, URL handler matrix.
- [my-files.md](my-files.md) — sibling `SavedFilter` / `SavedGenerationProfile` star/share conventions.
- [../decisions/ADR-008-selection-independent-of-filter.md](../decisions/ADR-008-selection-independent-of-filter.md)
- [../core/02-auth-and-permissions.md](../core/02-auth-and-permissions.md), [../core/04-backend-conventions.md](../core/04-backend-conventions.md)
