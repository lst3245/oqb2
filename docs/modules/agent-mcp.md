# Agent endpoint (MCP)

> The only way an external AI agent (a scheduled Grok bot today) works with OQB: one Model Context Protocol endpoint, `POST /mcp`, inside the Flask app. The agent reads questions, images and request threads, answers requests, proposes tag changes as review batches, and, after approval, changes the library through the same services the web pages use.

Decisions: [ADR-021](../decisions/ADR-021-agent-mcp-endpoint.md) (endpoint, token, services only, soft gate), [ADR-022](../decisions/ADR-022-requests-review-and-agent-writes.md) (review diffs, request states, `library_ops_service`). The agent's own instructions: [../agents/PLAYBOOK.md](../agents/PLAYBOOK.md).

## Files

| File | Role |
|---|---|
| `app/agent_mcp.py` | Blueprint `agent_mcp_bp` and the `/mcp` route: access checks, JSON-RPC 2.0 dispatch (`handle_message`, `handle_body`), MCP methods (`initialize`, `tools/list`, `tools/call`, `resources/list`, `resources/read`, `ping`, ...), the tool registry (`tool` decorator, `registry()`), `ToolError` / `ToolResult`, the action log (`_record_action`) and thread notes (`_thread_note`). Pure helpers `bearer_ok`, `ip_allowed`, `clean_agent_name`, `summarize_args`, `negotiate_version` |
| `app/agent_mcp_tools.py` | Every tool. Argument helpers (`_schema`, `_int_arg`, `_str_arg`, `find_question`, `_subject`, `encode_image`, `parse_since`) |
| `app/library_ops_service.py` | Library writes shared by the admin routes and the agent: `rename_question`, `set_parent`, `upload_assets` / `after_upload` (+ `BytesUpload`), `slot_assets`, `asset_bytes`, `write_md`, `replace_images`, `add_taxonomy_node`, `update_taxonomy_node`; `OpError(message, status)` |
| `app/question_edit_service.py`, `app/review_service.py`, `app/support_requests_service.py` | Services the tools call ([review.md](review.md), [requests.md](requests.md)) |
| `docs/agents/PLAYBOOK.md` | The agent playbook, served by `get_instructions` and as resource `oqb://agent/playbook` |
| `scripts/oqb_mcp_stdio.py` | Optional stdio bridge for clients that can only spawn a process: one JSON-RPC line in, one POST to `/mcp`, one line out. Token from `OQB_MCP_TOKEN` (never argv). Standard library only |
| `tests/test_agent_mcp.py` | Pure tests: token and IP checks, dispatch with stub methods, summaries, registry shape (every write takes `reason`, no delete tools) |

## Tables

| Table / model | Notes |
|---|---|
| `agent_action_log` (`AgentActionLog`) | One row per call of a write tool: `agent_name`, `tool`, `outcome` `ok` / `error`, `subject_id`, `question_id`, `request_id`, `batch_id`, `reason` (≤ 500), `summary` (arguments as JSON, long strings and lists cut; never the token), `error`, `created_at`. Written with its own commit after the tool's commit or rollback |

The tools also write the Review and Requests tables and the library tables through their services. Schema detail: [../core/03-data-model-and-migrations.md](../core/03-data-model-and-migrations.md).

## Routes

| Method | Path | Authz | Purpose |
|---|---|---|---|
| POST | `/mcp` | bearer token + IP allow-list (below) | One JSON-RPC message or a batch → `application/json`; notifications only → `202`. Body ≤ 64 MB |
| GET, DELETE | `/mcp` | same checks | `405 POST only` (no server-sent stream, no MCP session) |

Access, in order: **404** while `AGENT_MCP_ENABLED` is off or `AGENT_API_TOKEN` is empty; **403** when the client IP or the socket peer is not in `AGENT_MCP_ALLOWED_IPS` (with `PROXY_FIX_HOPS > 0` the client IP comes from `X-Forwarded-For`, which any LAN client can forge, so the raw peer from `werkzeug.proxy_fix.orig` must pass too: `peer_ip`), or an `Origin` header is not `PUBLIC_ORIGIN`; **429** over 1200 calls per minute per IP; **401** without `Authorization: Bearer <AGENT_API_TOKEN>`. `X-Agent-Name` (or `agent_name` in tool arguments) names the agent; default `AGENT_DEFAULT_NAME`. Exempt from CSRF and from the session layer.

## Tools

R = read, W = write (logged; needs `reason`), N = also posts a line to the thread named by `request_id`.

| Tool | | Purpose |
|---|---|---|
| `get_instructions` | R | The playbook |
| `list_subjects`, `get_taxonomy` | R | Subjects; one subject's topics / subtopics / chapters / subchapters (ids, English and Chinese names, hidden flags) |
| `search_questions`, `get_question` | R | Find questions in a subject (filters, text); one question with fields, labels, parts, stem, assets |
| `get_question_image`, `get_md_asset` | R | Question pages as images (a part comes with its stem pages); Markdown text of an asset |
| `create_review_batch`, `add_review_items` | W | New review batch (validated like the Import page; items with errors are reported, not saved), more items. A linked request gets a system line |
| `list_review_batches`, `get_review_batch` | R | Batches (`updated_since`), one batch with item states, admin notes and applied values |
| `list_requests`, `get_request`, `get_attachment` | R | Threads (`needs_agent=true` = the agent's queue, oldest first), one thread (reporter's levels, linked questions, approval state, messages `since_message_id`), one attachment (image content, text, or a blob ≤ 10 MB) |
| `post_message`, `set_request_status` | W | Reply (optional status, base64 attachments under the request limits); change status (agent statuses only) |
| `request_approval` | W | Post a plan; status `awaiting_approval` |
| `link_review_batch` | W | Link a batch to a thread (system line) |
| `update_question_fields` | W N | Field changes as `[{field, old, new}]` or `{field: new}`; stale `old` refused unless `confirm_stale`; restore point `agent-edit` for tag fields |
| `rename_question` | W N | `library_ops.rename_question` (parts follow; `rename_files` default true) |
| `set_parent` | W N | `library_ops.set_parent` (`parent_qid` / `parent_id`, null detaches) |
| `upload_asset` | W N | `.png` / `.jpg` / `.jpeg` / `.md` files as base64 into a QUE / ANS / SOL slot (≤ 25 MB each) |
| `replace_asset` | W N | Replace a slot's images (`format: IMG`, `images` in order) or Markdown (`format: MD`). Requires `request_id`; the old files are attached to the thread first |
| `add_taxonomy_node`, `rename_taxonomy_node` | W N | Topic / subtopic / chapter / subchapter (English `name`, optional `name_zh`); restore point `agent-edit` |
| `list_agent_actions` | R | The action log (filters: tool, request, subject, since) |

## Business rules / invariants

- **Writes go through services only** (ADR-021). A tool never writes a model attribute or a file under `SOURCE_PATH` itself: it calls `question_edit_service`, `review_service`, `support_requests_service` or `library_ops_service`, which hold the restore points, hierarchy rules and asset bookkeeping. A new write tool means a service function first, shared with the web route.
- **Restore points:** agent tag and taxonomy writes capture `agent-edit` (note `"<agent> <tool> <target>: <reason>"`) before mutating. Review applies by admins capture `review-apply`.
- **Transport commits.** `tools/call` commits after the handler returns and rolls back on `ToolError` or any exception. Tools that must commit midway (`upload_asset`, `replace_asset` IMG, which uses `replace_img_assets`) do so themselves, then run the thumbnail hooks after the commit.
- **Action log for every write call**, success or error, with its own commit; a thread note only after success.
- **No delete tools.** Deletes stay with people.
- **Super-admin power, no per-subject limit.** The playbook keeps the agent inside the request and asks it to get approval first for renames, tree moves, file changes, taxonomy changes and anything over 10 questions; OQB does not enforce that.
- Tool text (errors, descriptions, system lines) is English; `t()` returns English outside a browser request.

## Settings & config keys

`AGENT_MCP_ENABLED` (default off), `AGENT_MCP_ALLOWED_IPS` (default `127.0.0.1,::1`), `AGENT_DEFAULT_NAME` (System Settings, group "AI agent"); `AGENT_API_TOKEN`, `AGENT_WAKE_WEBHOOK_URL`, `AGENT_WAKE_WEBHOOK_SECRET` (`.env` only). See [../core/06-system-settings.md](../core/06-system-settings.md).

## Permissions

The token is a super admin for every tool. There is no agent user account; messages and batches carry `agent_name`, not a user id. See [../core/02-auth-and-permissions.md](../core/02-auth-and-permissions.md).

## Background work / SSE / threads

Every `/mcp` call runs inside its request; the agent polls on its own schedule.

**Wake webhook (optional, [ADR-023](../decisions/ADR-023-agent-wake-webhook.md)).** With `AGENT_WAKE_WEBHOOK_URL` set (a Grok Bot routine's webhook), the Requests routes POST a notice when a person's action makes a thread newly need the agent, so the routine can start at once instead of at its next scheduled run. The routine then works exactly as on a scheduled run: `POST /mcp`, `list_requests(needs_agent=true)`, `get_request`. The webhook is outbound only; it does not change `/mcp` access (`AGENT_MCP_ALLOWED_IPS` stays loopback). Payload, timing and dedupe: [requests.md](requests.md#background-work--sse--threads).

## Gotchas

- **Never put the token in a command line, a doc, a commit or a thread.** It lives in `.env`; the stdio bridge reads it from an environment variable.
- **Widening `AGENT_MCP_ALLOWED_IPS`** exposes super-admin writes to that network. Keep it on loopback unless the agent runs elsewhere.
- **Do not raise inside a tool for an expected refusal with anything but `ToolError`.** Other exceptions are logged as internal errors and their class name is returned to the agent.
- Tools that commit midway cannot be fully rolled back by a later error; keep their post-commit steps to hooks that never raise.
- `replace_asset` writes the backup files to the request folder before replacing; if the replace then fails, the backup files stay (harmless) while their message rows roll back.
- Base64 attachments and images inflate by a third; the body cap is 64 MB per call.
- A schema with `additionalProperties: false` is advertised for every tool; the server itself does not validate against it, so a tool must check its arguments.

## Related

- [review.md](review.md), [requests.md](requests.md), [subject-snapshots.md](subject-snapshots.md), [admin-questions.md](admin-questions.md) (rename, set parent, upload: the web side of `library_ops_service`), [question-hierarchy.md](question-hierarchy.md)
- [ADR-021](../decisions/ADR-021-agent-mcp-endpoint.md), [ADR-022](../decisions/ADR-022-requests-review-and-agent-writes.md), [ADR-023](../decisions/ADR-023-agent-wake-webhook.md), [../agents/PLAYBOOK.md](../agents/PLAYBOOK.md)
