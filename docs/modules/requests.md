# Requests (issue reports as chat threads)

> Teachers and admins report a problem (about selected questions, a subject, or nothing in particular) and then talk about it in a thread with admins and the AI agent: text, pasted screenshots and any attachment. The agent picks threads up on its schedule, answers, asks for more information, or asks a subject admin to approve a plan before it changes the library.

Decisions: [ADR-022](../decisions/ADR-022-requests-review-and-agent-writes.md) (separate from Review, soft approval gate as a thread state, module naming), [ADR-021](../decisions/ADR-021-agent-mcp-endpoint.md) (agent endpoint and token).

The code is named `support_requests*` (blueprint `support_requests`, URL `/requests`) so it cannot be confused with the `requests` HTTP library; the plan called it `app/requests.py`.

## Files

| File | Role |
|---|---|
| `app/support_requests_service.py` | Constants (`STATUSES`, `ACTIVE_STATUSES`, `AGENT_STATUSES`, `CATEGORIES`, `STATUS_LABELS`, `CATEGORY_LABELS`, `STATUS_BADGES`, limits). Pure rules: `needs_agent`, `allowed_statuses`, `can_set_status`, `can_reply`, `status_after_reply`, `visible`, `manages`, `approves`, `report_level_ok`, `render_body`, `safe_filename`, `stored_name`, `guess_mime`, `check_files`. Access: `Access`, `access_for`, `visible_filter`, `queue_filter`, `can_report`. Writes: `create_request`, `add_message`, `reply`, `add_system_note`, `set_status`, `request_approval`, `decide_approval`, `save_attachments`, `mark_read`, `mark_all_read`, `detach_subject`. Reads: `unread_count`, `unread_ids`, `thread`, `linked_questions`, `request_payload`, `message_payload`, `pending_approval` |
| `app/support_requests.py` | Blueprint `support_requests_bp` (`/requests`), `before_request` 404 gate on `REQUESTS_ENABLED`, context processor `requests_enabled()` / `requests_can_report()` / `requests_unread_count()` |
| `app/agent_mcp_tools.py` | Agent tools `list_requests`, `get_request`, `get_attachment`, `post_message`, `set_request_status`, `request_approval`, `link_review_batch` ([agent-mcp.md](agent-mcp.md)); every library write tool with a `request_id` adds a system line to the thread |
| `templates/requests_list.html` | List: tabs **My requests** / **My subjects** (admins) / **All requests** (super admins), status (not finished, waiting for the agent, each status, all), kind, search (title, QID, `#id`), unread dots, **Mark all read** |
| `templates/requests_new.html` | Report form: linked questions (from `?ids=`), kind, subject (when no question), title, composer |
| `templates/requests_thread.html` | Thread: message bubbles (people, agent, system lines), approval request / decision cards with Approve / Reject (note required to reject), status card, linked questions (preview toggle, Edit button for admins of that subject), linked review batches, composer. Includes the Edit modal only when a linked question is editable |
| `templates/partials/request_composer.html`, `templates/partials/request_composer_js.html` | The composer (textarea, file chips, paste / drop images and files, size and count checks) and `window.oqbRequestComposer(root)` / `window.oqbRequestPost(url, formData)` |
| `templates/base.html` | **Requests** navbar menu with the unread badge; `window.OQB_REPORT_URL` and `window.oqbReportIssue(ids)` |
| `templates/dashboard.html`, `templates/partials/edit_question_modal.html`, `templates/admin_questions/_action_bar.html` | Report entry points: dashboard **Report** (selection, or a general report when nothing is selected) and the Enlarge overlay flag; Edit modal header flag; Question Management **Report issue** |
| `tests/test_support_requests.py` | Pure tests: status rules, visibility, the agent queue, rendering and file names |

## Tables

Schema detail: [../core/03-data-model-and-migrations.md](../core/03-data-model-and-migrations.md). No FKs to `users` or `questions` (ids are nulled by user erase; question links keep a copy of `qid` / `subject_id`).

| Table / model | Notes |
|---|---|
| `support_requests` (`SupportRequest`) | `title`, `category` `question` / `general` / `app_bug` / `feature`, `subject_id` (the first linked question's subject, or the chosen subject; NULL = general), `reporter_id`, `status`, `last_actor_kind` `user` / `admin` / `agent`, `last_message_at`, `created_at`, `closed_by`, `closed_at` |
| `support_request_questions` (`SupportRequestQuestion`) | `request_id` FK CASCADE, `question_id`, `qid` and `subject_id` copied at report time (≤ 200 per request) |
| `support_request_messages` (`SupportRequestMessage`) | `author_kind` `user` / `agent` / `system`, `author_user_id`, `agent_name`, `kind` `message` / `approval_request` / `approval` / `rejection` / `status_change`, `body` (MEDIUMTEXT, ≤ 20 000 chars), `meta` JSON |
| `support_request_attachments` (`SupportRequestAttachment`) | `message_id` FK CASCADE, `request_id`, `original_name`, `stored_name`, `mime`, `size` |
| `support_request_reads` (`SupportRequestRead`) | PK (`user_id`, `request_id`), `last_read_message_id` — the unread badge |

Files: `STORAGE_PATH\Requests\<request_id>\<message_id>_<random>.<ext>` (`storage.request_dir`, `REQUESTS_PATH`; [../core/05-storage-and-paths.md](../core/05-storage-and-paths.md)).

## Routes

All `@login_required`; 404 while `REQUESTS_ENABLED` is off. Thread routes load the request and refuse (403) when the user cannot see it. JSON answers (fetch / `Accept: application/json`) are `{success, ...}` / `{success: false, error}`; plain form posts redirect.

| Method | Path | Authz | Purpose / shapes |
|---|---|---|---|
| GET | `/requests/` | signed in | List. `?tab=mine\|queue\|all`, `?status=active\|needs_agent\|<status>\|all`, `?category=`, `?q=` |
| POST | `/requests/mark-all-read` | signed in | Marks every visible thread read |
| GET | `/requests/new` | report level | Form. `?ids=1,2,3` links questions (those below the report level are left out and counted); `?subject=`, `?category=` |
| POST | `/requests/new` | report level on every linked question's subject (403 otherwise) | Multipart: `title`, `category`, `subject_id` (only without questions), `question_ids` (repeated or comma list), `body`, `files` → `{success, id, url}` |
| GET | `/requests/<int:request_id>` | can see | Thread page; marks the thread read |
| POST | `/requests/<int:request_id>/messages` | can reply | Multipart `body`, `files` → `{success, message_id, status}` |
| POST | `/requests/<int:request_id>/status` | per role (below) | `{status, note?}` → `{success, status}` |
| POST | `/requests/<int:request_id>/approval` | approver | `{decision: approve\|reject, note}` (note required to reject) → `{success, status}` |
| GET | `/requests/attachment/<int:attachment_id>` | can see the thread | File. PNG / JPEG / GIF / WebP inline (unless `?download=1`), everything else as an `application/octet-stream` download; `nosniff`, `private, no-store` |

## Business rules / invariants

- **Statuses:** `open` → `in_progress` → `needs_info` / `awaiting_approval` → `approved` → `resolved` → `closed`. Active = the first five. Every change is a `status_change` message.
- **Who may set what** (`allowed_statuses`): a **manager** (admin of one of the thread's subjects, or super admin) any status; the **agent** `open`, `in_progress`, `needs_info`, `resolved`, `closed` (never `approved`); the **reporter** may reopen a `resolved` thread and close their own. A reporter's reply to a `needs_info` or `resolved` thread sets it back to `open`.
- **The agent's queue** (`needs_agent`): every `approved` thread, plus `open` / `in_progress` threads where a person (`user` or `admin`) acted last (`last_actor_kind`). A person's message, status change or approval decision counts, so an admin reopening a thread puts it back in the queue; system lines (`add_system_note`, agent tool notes) do not.
- **Soft approval gate:** the agent posts `request_approval(plan)` → `approval_request` message, status `awaiting_approval`. An approver clicks Approve (→ `approval`, status `approved`) or Reject with a note (→ `rejection`, status `open`). The agent acts on its next run. OQB does not block the write tools; the playbook does ([../agents/PLAYBOOK.md](../agents/PLAYBOOK.md)).
- **Approver** = admin of **every** subject of the thread; a general thread needs a super admin.
- **Visibility:** the reporter, admins of any of the thread's subjects (its `subject_id` and its linked questions' subjects), super admins, and the agent. General threads: reporter and super admins.
- **Message text is plain text.** `render_body` escapes everything, then shows ```` ``` ```` blocks, `` `code` ``, `**bold**`, http(s) links and line breaks. Agent text goes through the same function.
- **Attachments:** at most `REQUESTS_MAX_ATTACHMENTS_PER_MESSAGE` files of `REQUESTS_MAX_ATTACHMENT_MB` each; empty files refused. Stored names are `<message_id>_<random>.<ext>`; the original name is kept only in the row. Written only by `save_attachments` (via `safe_join`), which deletes what it wrote if one file fails.
- **Unread:** a thread is unread when it has a message newer than the user's `last_read_message_id` that someone else wrote. Opening a thread, posting and changing status mark it read.
- **Subject delete** turns its threads into general ones (`detach_subject`): the thread's `subject_id` and the `subject_id` copies on its question links are cleared, so only the reporter and super admins still see them, and a later subject with the same id does not inherit them. Threads are never deleted by the app.
- **User erase** nulls `reporter_id`, `closed_by` and `author_user_id` and deletes the user's read markers; the threads stay for the admins (`app/user_erase.py`).

## Settings & config keys

`REQUESTS_ENABLED`, `REQUESTS_MIN_REPORT_LEVEL`, `REQUESTS_MAX_ATTACHMENT_MB`, `REQUESTS_MAX_ATTACHMENTS_PER_MESSAGE` (System Settings, group "Requests and Review"); `REQUESTS_PATH` (`.env`). See [../core/06-system-settings.md](../core/06-system-settings.md).

## Permissions

See the rules above and [../core/02-auth-and-permissions.md](../core/02-auth-and-permissions.md). The report level is checked per linked question's subject, or per chosen subject; a general report needs it on at least one subject. Super admins always may report. The agent has super-admin power through the token.

## Background work / SSE / threads

None. The nav badge is computed when a page renders (`requests_unread_count()`, one query).

## Gotchas

- **Never serve an attachment by path or from a static route.** Only `/requests/attachment/<id>`, after the thread visibility check. Non-images are always downloads (an uploaded HTML or SVG must never render in OQB's origin).
- **`approves` is stricter than `manages`.** An admin of one of two subjects can reply and change the status but cannot approve the agent's plan.
- **Do not set `approved` from the agent side.** `AGENT_STATUSES` leaves it out on purpose; only `decide_approval` sets it.
- The list filter `needs_agent` filters in Python after the query (it depends on `last_actor_kind`), so it is applied after the 300-row list limit (`LIST_LIMIT`).
- Links in a message open in a new tab with `rel="noopener noreferrer"`; do not add other markup to `render_body` without escaping first.
- `CSRF`: the composer posts `FormData` through `fetch`; `static/js/csrf.js` adds the header. Plain forms use `{{ csrf_field() }}`.

## Related

- [review.md](review.md) (a request can link review batches), [agent-mcp.md](agent-mcp.md), [../agents/PLAYBOOK.md](../agents/PLAYBOOK.md), [dashboard.md](dashboard.md), [admin-questions.md](admin-questions.md)
- [ADR-022](../decisions/ADR-022-requests-review-and-agent-writes.md), [ADR-021](../decisions/ADR-021-agent-mcp-endpoint.md)
