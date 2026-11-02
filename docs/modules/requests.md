# Requests (issue reports as chat threads)

> Teachers and admins report a problem (about selected questions, a subject, or nothing in particular) and then talk about it in a thread with admins and the AI agent: text, pasted screenshots and any attachment. The agent picks threads up on its schedule, answers, asks for more information, or asks a subject admin to approve a plan before it changes the library.

Decisions: [ADR-022](../decisions/ADR-022-requests-review-and-agent-writes.md) (separate from Review, soft approval gate as a thread state, module naming), [ADR-021](../decisions/ADR-021-agent-mcp-endpoint.md) (agent endpoint and token).

The code is named `support_requests*` (blueprint `support_requests`, URL `/requests`) so it cannot be confused with the `requests` HTTP library; the plan called it `app/requests.py`.

## Files

| File | Role |
|---|---|
| `app/support_requests_service.py` | Constants (`STATUSES`, `ACTIVE_STATUSES`, `AGENT_STATUSES`, `CATEGORIES`, `STATUS_LABELS`, `CATEGORY_LABELS`, `STATUS_BADGES`, limits). Pure rules: `needs_agent`, `allowed_statuses`, `can_set_status`, `can_reply`, `status_after_reply`, `visible`, `manages`, `approves`, `report_level_ok`, `render_body`, `safe_filename`, `stored_name`, `guess_mime`, `check_files`. Access: `Access`, `access_for`, `visible_filter`, `queue_filter`, `can_report`. Writes: `create_request`, `add_message`, `reply`, `add_system_note`, `set_status`, `request_approval`, `decide_approval`, `save_attachments`, `mark_read`, `mark_all_read`, `detach_subject`. Reads: `unread_count`, `unread_ids`, `thread`, `linked_questions`, `request_payload`, `message_payload`, `pending_approval` |
| `app/support_requests.py` | Blueprint `support_requests_bp` (`/requests`), `before_request` 404 gate on `REQUESTS_ENABLED`, context processor `requests_enabled()` / `requests_can_report()` / `requests_unread_count()`. The four write routes (`create`, `post_message`, `set_status`, `decide`) call `agent_wake.notify` after their commit |
| `app/agent_wake.py` | Optional agent wake webhook ([ADR-023](../decisions/ADR-023-agent-wake-webhook.md)): `should_wake`, `payload`, `headers`, `url_ok`, `claim` (dedupe), `send`, `notify` |
| `app/agent_mcp_tools.py` | Agent tools `list_requests`, `get_request`, `get_attachment`, `post_message`, `set_request_status`, `request_approval`, `link_review_batch` ([agent-mcp.md](agent-mcp.md)); every library write tool with a `request_id` adds a system line to the thread |
| `templates/requests_list.html` | List: tabs **My requests** / **My subjects** (admins) / **All requests** (super admins), status (not finished, waiting for the agent, each status, all), kind, search (title, QID, `#id`), unread dots, **Mark all read** |
| `templates/requests_new.html` | Report form: linked questions (from `?ids=`), kind, subject (when no question), title, composer |
| `templates/requests_thread.html` | Thread page: includes the body partial, the image preview modal (`#rqImageModal`), the **New messages** button, the live poll and the action JS (send / status / decide refresh in place, no page reload). Includes the Edit modal only when a linked question is editable |
| `templates/partials/request_thread_body.html` | The thread body, rendered by the page and alone by `/live`: message bubbles (people, agent, system lines), approval request / decision cards with Approve / Reject (note required to reject), status card, linked questions (preview toggle, Edit button for admins of that subject), linked review batches, composer. Live regions by id: `#rqHeader`, `#rqThread` (`data-last-id`, `data-status`), `#rqReplyArea` (`data-can-reply`) > `#rqReplyNotice`, `#rqStatusBox`, `#rqBatches` |
| `templates/partials/request_composer.html`, `templates/partials/request_composer_js.html` | The composer (textarea, file chips, paste / drop images and files, size and count checks) and `window.oqbRequestComposer(root)` / `window.oqbRequestPost(url, formData)` |
| `templates/base.html` | **Requests** navbar menu with the unread badge; `window.OQB_REPORT_URL` and `window.oqbReportIssue(ids)` |
| `templates/dashboard.html`, `templates/partials/edit_question_modal.html`, `templates/admin_questions/_action_bar.html` | Report entry points: dashboard **Report** (selection, or a general report when nothing is selected) and the Enlarge overlay flag; Edit modal header flag; Question Management **Report issue** |
| `tests/test_support_requests.py` | Pure tests: status rules, visibility, the agent queue, rendering and file names |
| `tests/test_agent_wake.py` | Pure tests of the wake webhook, plus the create / reply / approval routes on a bare Flask app (DB stubbed): schedules on a transition into the queue, skips with no URL, dedupes |
| `tests/test_requests_live.py` | Pure tests of `/live` (changed / unchanged, mark read only when visible, passive endpoint) and of the body partial (live region ids, closed thread, images open the preview and never a new tab) |

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
| GET | `/requests/<int:request_id>/live` | can see | Poll of the open thread page. `?after=<last message id>&status=<shown status>&read=0\|1` → `{success, changed, last_message_id, status, html?}`; `html` (the body partial) only when the newest message id or the status differs. `read=1` marks the thread read. Passive: does not count as session activity |
| POST | `/requests/<int:request_id>/messages` | can reply | Multipart `body`, `files` → `{success, message_id, status}` |
| POST | `/requests/<int:request_id>/status` | per role (below) | `{status, note?}` → `{success, status}` |
| POST | `/requests/<int:request_id>/approval` | approver | `{decision: approve\|reject, note}` (note required to reject) → `{success, status}` |
| GET | `/requests/attachment/<int:attachment_id>` | can see the thread | File. PNG / JPEG / GIF / WebP inline (unless `?download=1`), everything else as an `application/octet-stream` download; `nosniff`, `private, no-store` |

## Business rules / invariants

- **Statuses:** `open` → `in_progress` → `needs_info` / `awaiting_approval` → `approved` → `resolved` → `closed`. Active = the first five. Every change is a `status_change` message.
- **Who may set what** (`allowed_statuses`): a **manager** (admin of one of the thread's subjects, or super admin) any status; the **agent** `open`, `in_progress`, `needs_info`, `resolved`, `closed` (never `approved`); the **reporter** may reopen a `resolved` thread and close their own. A person's reply (the reporter's or an admin's) to a `needs_info` or `resolved` thread sets it back to `open`, which puts it in the agent's queue; a reply to a `closed` thread (admins only) keeps it closed and wakes nobody. (Until 2026-11-01 only the reporter role reopened, so a reporter who was also an admin, role `manager`, had to reopen by hand.)
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

`REQUESTS_ENABLED`, `REQUESTS_MIN_REPORT_LEVEL`, `REQUESTS_MAX_ATTACHMENT_MB`, `REQUESTS_MAX_ATTACHMENTS_PER_MESSAGE` (System Settings, group "Requests and Review"); `REQUESTS_PATH`, `AGENT_WAKE_WEBHOOK_URL`, `AGENT_WAKE_WEBHOOK_SECRET` (`.env` only; restart to change). See [../core/06-system-settings.md](../core/06-system-settings.md).

## Permissions

See the rules above and [../core/02-auth-and-permissions.md](../core/02-auth-and-permissions.md). The report level is checked per linked question's subject, or per chosen subject; a general report needs it on at least one subject. Super admins always may report. The agent has super-admin power through the token.

## Background work / SSE / threads

The nav badge is computed when a page renders (`requests_unread_count()`, one query); the list page and the badge do not update live.

**Live thread page** (polling, no SSE: a held-open stream would pin a dev-server thread per open tab). The page polls `/live` every 4 s while the tab is visible and every 30 s in the background, and at once when the tab becomes visible again or after the user's own send / status change / decision. An unchanged answer is three small queries and no HTML. On a change the page swaps the live regions in place (`#rqHeader`, `#rqThread`, `#rqStatusBox`, `#rqBatches`, and `#rqReplyNotice` while the composer stays), keeps the value, focus and caret of any field in them (decision note, status note), re-formats times, and scrolls to the new message when the end of the thread was on screen; otherwise it shows a **New messages** button. A background tab gets `● ` in its title. When the composer must appear or go (`data-can-reply` flips: closed or reopened) the page reloads, unless the composer holds a draft. Eight failed polls in a row (signed out, thread gone, server down) stop it until the tab is shown again.

**Image preview:** a plain click on a message image (or a linked question's preview image) opens `#rqImageModal` with Previous / Next (arrow keys) over the images of the same gallery (`[data-rq-gallery]`: the thread, or one question preview) and a **Download** button for attachments (`?download=1`). Ctrl / Shift / middle click keep the browser's own behaviour.

**Agent wake webhook** (`app/agent_wake.py`, optional, [ADR-023](../decisions/ADR-023-agent-wake-webhook.md)). The URL is a Grok Bot routine's webhook; after the wake the agent still works only through `POST /mcp` ([agent-mcp.md](agent-mcp.md)).

- **When:** `create`, `post_message`, `set_status` and `decide` read `needs_agent(status, last_actor_kind)` before the change and call `agent_wake.notify(req, was_needed)` **after the commit**. It fires only when the thread was not waiting for the agent and now is: a new request, a person's reply to a `needs_info` / `resolved` thread, a person's reply after the agent spoke last, an approval or rejection, or an admin setting `open` / `in_progress` / `approved`. Agent messages, agent status changes and system notes (agent tool notes, linked batches) never fire it. A failed action (refused, rolled back) does not fire.
- **Request:** `POST AGENT_WAKE_WEBHOOK_URL`, `Content-Type: application/json`, plus `Authorization: Bearer <secret>` and `X-Webhook-Secret: <secret>` when `AGENT_WAKE_WEBHOOK_SECRET` is set. Body:

  ```json
  {"event": "request.needs_agent", "request_id": 12, "status": "open", "subject_id": "MATC",
   "title": "Wrong answer in Q3", "last_message_at": "2026-11-01T02:00:00Z",
   "questions": [{"qid": "MATC_2020_P1_Q3", "question_id": 901, "subject_id": "MATC"}]}
  ```

  No message text, attachments, user names or tokens. `subject_id` is null for a general request.
- **Delivery:** a daemon thread, 3 s timeout (`TIMEOUT_SECONDS`), redirects not followed, any HTTP status accepted; a failure is a WARNING log line with the host only. The person's reply never waits for or fails with it. One notice per request per 5 s (`DEDUPE_SECONDS`, in-process). HTTPS only, or plain HTTP to `localhost` / `127.0.0.1` / `::1`; another URL is skipped with a warning.

## Gotchas

- **Never serve an attachment by path or from a static route.** Only `/requests/attachment/<id>`, after the thread visibility check. Non-images are always downloads (an uploaded HTML or SVG must never render in OQB's origin).
- **`approves` is stricter than `manages`.** An admin of one of two subjects can reply and change the status but cannot approve the agent's plan.
- **Do not set `approved` from the agent side.** `AGENT_STATUSES` leaves it out on purpose; only `decide_approval` sets it.
- The list filter `needs_agent` filters in Python after the query (it depends on `last_actor_kind`), so it is applied after the 300-row list limit (`LIST_LIMIT`).
- Links in a message open in a new tab with `rel="noopener noreferrer"`; do not add other markup to `render_body` without escaping first.
- `CSRF`: the composer posts `FormData` through `fetch`; `static/js/csrf.js` adds the header. Plain forms use `{{ csrf_field() }}`.
- **A new route that lets a person change a thread must call `agent_wake.notify(req, was_needed)` after its commit**, with `was_needed` read before the change, or the agent sleeps until its next scheduled run. Never call it before the commit (the agent could read the thread before the change exists) and never from agent tools.
- **`/live` must stay in `web_security.PASSIVE_ENDPOINTS`.** Otherwise an open thread tab moves the session's last-seen time every minute and the 2 h idle limit never ends it. A new background poll elsewhere needs the same entry.
- **Keep the live region ids in `partials/request_thread_body.html`** (and anything that must survive a poll, such as the composer and the question previews, outside them). A field inside a region needs an `id` to keep its value across a swap. Do not render the thread body anywhere but this partial: the page and `/live` must produce the same markup.
- Thread images are `a.rq-zoom` handled by one delegated click listener; do not add `target="_blank"` or a per-image handler (swapped regions would lose it).
- **Never add message text or attachment data to the wake payload**: the webhook leaves OQB for a third-party platform. Never log the URL path or the secret (the path often is the credential).

## Related

- [review.md](review.md) (a request can link review batches), [agent-mcp.md](agent-mcp.md), [../agents/PLAYBOOK.md](../agents/PLAYBOOK.md), [dashboard.md](dashboard.md), [admin-questions.md](admin-questions.md)
- [ADR-022](../decisions/ADR-022-requests-review-and-agent-writes.md), [ADR-021](../decisions/ADR-021-agent-mcp-endpoint.md), [ADR-023](../decisions/ADR-023-agent-wake-webhook.md)
