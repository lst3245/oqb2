# Review (proposed tag and field changes)

> Subject admins work through lists of proposed question changes ("review batches") one question at a time: the question image beside an old → new diff, then Apply, Reject (with a note), Skip or Edit. Batches come from the AI agent over `/mcp` or from JSON pasted on the Import page. Nothing changes in the library until an item is applied.

Decisions: [ADR-022](../decisions/ADR-022-requests-review-and-agent-writes.md) (review items are stored field diffs, applied through the Edit-modal service), [ADR-021](../decisions/ADR-021-agent-mcp-endpoint.md) (the agent endpoint).

## Files

| File | Role |
|---|---|
| `app/review_service.py` | Pure: `parse_confidence`, `parse_payload`, `item_kind`, `validate_items(raw, subject, find=, taxonomy=, current=, stem=)`, `change_rows`, `next_batch_status`. DB: `prepare` (validate against the live subject), `create_batch`, `add_items`, `item_counts`, `refresh_batch_status`, `batch_payload`, `item_payload`, `apply_item`, `reject_item`, `skip_item`, `reopen_item`, `apply_all`, `log_corrections`, `admin_subject_ids`, `scoped_batches`, `pending_count`, `delete_for_subject` |
| `app/question_edit_service.py` | The field model shared with the Edit modal and the agent: `FIELDS` (name, kind, label, snapshot flag), `Taxonomy`, `normalize_changes`, `apply_fields` (restore point first), `StaleError`, `label`, `correction_labels` |
| `app/review.py` | Blueprint `review_bp` (`/admin/review`): pages, JSON for the review page, decisions. Context processor `review_pending_count()` (Admin nav badge) |
| `app/agent_mcp_tools.py` | Agent tools `create_review_batch`, `add_review_items`, `list_review_batches`, `get_review_batch` ([agent-mcp.md](agent-mcp.md)) |
| `templates/admin_review.html` | Batch list: status tabs (open / done / archived / all), subject filter, progress counts, **Import JSON** |
| `templates/admin_review_import.html` | Paste or upload JSON, choose subject / title, **Preview** (per-item errors and warnings), **Save batch** |
| `templates/admin_review_batch.html` | The one-by-one page: item list (to do / all), question image (version picker, parts and stem shown), change table with per-field tick boxes and override pickers, stale warning, Apply / Reject / Skip / Reopen / Edit, Apply all with a confidence threshold, keyboard shortcuts. Includes the Edit modal partials |
| `templates/admin_index.html`, `templates/base.html` | Admin hub card **Review** and Admin dropdown entry, both with the pending-item badge |
| `tests/test_review_service.py` | Pure tests: payload parsing, confidence, item validation with fake questions, change rows, batch status |

## Tables

Schema detail: [../core/03-data-model-and-migrations.md](../core/03-data-model-and-migrations.md).

| Table / model | Notes |
|---|---|
| `review_batches` (`ReviewBatch`) | `subject_id` (no FK), `title`, `summary`, `source` `agent` / `import`, `agent_name`, `created_by` (users.id, no FK; nulled by user erase), `linked_request_id` (no FK), `status` `open` / `done` / `archived`, `created_at`, `updated_at` |
| `review_items` (`ReviewItem`) | `batch_id` FK CASCADE, `question_id` (no FK) + `qid` copy, `position`, `kind` `fields` / `freeform`, `changes` JSON `[{field, old, new}]` (taxonomy as ids), `title`, `rationale`, `confidence` 0–1, `status` `pending` / `skipped` / `applied` / `done` / `rejected`, `applied_changes` JSON (what was actually written), `admin_note`, `decided_by`, `decided_at` |

## Routes

All `@login_required` + `@admin_required`; every batch / item route then calls `authz.require_subject(batch.subject_id, 'admin')`. Prefix `/admin/review`. JSON errors are `{success: false, error}`.

| Method | Path | Purpose / shapes |
|---|---|---|
| GET | `/admin/review/` | Batch list. `?status=open\|done\|archived\|all`, `?subject=` |
| GET, POST | `/admin/review/import` | Import page. POST form: `payload` (text) or `file` (≤ 5 MB), `subject_id`, `title`, `action=save` to save (otherwise preview). Save → redirect to the batch |
| GET | `/admin/review/<int:batch_id>` | The review page (`?item=<id>` opens that item) |
| GET | `/admin/review/<int:batch_id>/items` | `{success, batch, taxonomy{topics\|subtopics\|chapters\|subchapters: [{id, label, parent}]}, items[]}`; item = `item_payload` + `ancestors[{id, qid}]` |
| GET | `/admin/review/item/<int:item_id>` | `{success, item, batch}` |
| POST | `/admin/review/item/<int:item_id>/apply` | JSON `{fields?: [names], overrides?: {field: new}, confirm_stale?, note?}` → `{success, item, batch}`. 409 `{stale: [fields]}` when a field no longer holds its `old` value (nothing written); 400 on a bad value |
| POST | `/admin/review/item/<int:item_id>/reject` | `{note?}` → `{success, item, batch}` |
| POST | `/admin/review/item/<int:item_id>/skip` | pending → skipped |
| POST | `/admin/review/item/<int:item_id>/reopen` | skipped / rejected / done → pending (an applied item cannot be reopened) |
| POST | `/admin/review/<int:batch_id>/apply-all` | `{min_confidence?}` (0–1 or %) → `{success, summary{applied, stale[qid], errors[{qid, message}], below_threshold}, batch}` |
| POST | `/admin/review/<int:batch_id>/status` | `{status: open\|done\|archived}` (JSON or form) |

## Business rules / invariants

- **Items are field diffs, not free edits.** Fields and value rules are `question_edit_service.FIELDS`, the same as the Edit modal and the agent's `update_question_fields`: `major_topic`, `major_subtopic`, `minor_topics`, `subtopics`, `chapter`, `subchapter`, `level`, `q_type`, `section`, `marks`, `correct_percentage`, `answer`, `description`, `comment`. Taxonomy values are stored as ids (names in either language are resolved at import). `freeform` items carry only a title / rationale; applying one just marks it `done`.
- **Validation at import** (`validate_items`): one subject per batch; an item for a question in another subject, an unknown question, an unknown field or a change that would do nothing is an **error** (item dropped); a stale `old`, a field already at its new value (left out), a bad confidence (set to none), a duplicate question or an unknown question on a freeform item are **warnings** (item kept). Max 2000 items per batch.
- **Stale = the question no longer holds the item's `old` value.** The page shows it; Apply returns 409 until the admin confirms (`confirm_stale`). Apply all skips stale items (they stay pending).
- **Restore point before writing** (ADR-013): `apply_item` goes through `qes.apply_fields(action='review-apply', note='Review #<id>: <qid>')`, which captures only when a snapshotted field changes. Apply all takes **one** `review-apply` point for the batch and applies items with `capture=False`.
- **Partial apply:** `fields` limits the item to the ticked fields; `overrides` replaces a proposed value (the admin's own value wins). `applied_changes` records what was actually written; fields already at the new value are left out.
- **Batch status follows the items:** `open` while anything is pending or skipped, else `done`; `archived` is only set by hand and stays.
- **Feedback to Auto Tag:** after an apply or reject commits, `log_corrections` writes tag fields (`CORRECTION_FIELDS`) to `tag_corrections` (suggested = the proposal, saved = the question's value now; model `agent:<name>` for agent batches, else `review-<source>`). It commits on its own and never fails the request ([subject-ai.md](subject-ai.md)).
- **Subject delete** removes the subject's batches and items (`review_service.delete_for_subject` in `admin.delete_subject`).

## Settings & config keys

None of its own. The agent side is gated by `AGENT_MCP_ENABLED` ([agent-mcp.md](agent-mcp.md)); Review pages are always on for admins. See [../core/06-system-settings.md](../core/06-system-settings.md).

## Permissions

Subject admins of the batch's subject, and super admins. The list and the nav badge count only administered subjects (`scoped_batches`, `pending_count`). Import refuses a subject the user does not administer. The agent creates batches with the shared token (super-admin power). See [../core/02-auth-and-permissions.md](../core/02-auth-and-permissions.md).

## Background work / SSE / threads

None. Apply all runs inside the request (one transaction).

## Gotchas

- **Never write question fields from Review code directly.** Go through `apply_item` → `qes.apply_fields`; that is where the restore point and the stem rules (stems cannot hold tags, level, type or marks) live.
- **`old` values are what make stale detection work.** An item stored without `old` takes the question's value at import time as `old`.
- `log_corrections` runs **after** the commit; calling it before would log against values that might roll back.
- The page and the Edit modal can both change a question: after the modal saves, the page reloads the item, so its stale flags reflect the edit.
- An applied item is undone from **Restore Points**, not by reopening it.

## Related

- [requests.md](requests.md) (a batch can be linked to a request thread), [agent-mcp.md](agent-mcp.md), [subject-snapshots.md](subject-snapshots.md), [subject-ai.md](subject-ai.md), [admin-questions.md](admin-questions.md) (Edit modal)
- [ADR-022](../decisions/ADR-022-requests-review-and-agent-writes.md), [ADR-013](../decisions/ADR-013-subject-restore-points.md)
