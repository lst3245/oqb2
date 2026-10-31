# ADR-022 — Requests and Review are two features; review items are field diffs; agent writes share services with the web routes

**Status:** accepted, 2026-11-01.

## Context

Teachers wanted two things from the AI agent ([ADR-021](ADR-021-agent-mcp-endpoint.md)):

1. a place to **report a problem** (with or without questions, with screenshots and files) and talk about it with admins and the agent until it is fixed;
2. a way to **check the agent's tag proposals** one question at a time, with the question image beside the change, instead of reading a spreadsheet and editing questions by hand.

The two look similar (both are "the agent suggests, a person decides") but differ in shape: a report is an open-ended conversation about anything, including app bugs; a tag proposal is a precise change to known fields of known questions that should be applied mechanically once accepted.

The agent also needs to change the library after approval: rename a question, move it in the tree, upload or replace files, add or rename topics and chapters. Those changes lived inline in `app/admin.py` routes, bound to `current_user` and `request`.

## Decision

- **Two features, linked but separate.** **Requests** (`/requests`) are chat threads with a status. **Review** (`/admin/review`) holds batches of proposed changes. A batch can name the request it answers (`review_batches.linked_request_id`), and the thread lists it; neither depends on the other.
- **Review items are stored field diffs.** Each item is `[{field, old, new}]` over one fixed field list (`question_edit_service.FIELDS`, the same fields as the Edit modal), with taxonomy values as ids. Applying goes through `question_edit_service.apply_fields`, which ends in `write_values`, the same writer the Edit modal's `update_question` calls, so stem rules and value handling are identical; `apply_fields` takes the restore point (`review-apply`). The stored `old` value gives **stale detection**: if the question changed since the proposal, the admin is warned and must confirm. Anything that is not a field change is a **free-text item** the admin marks done or rejects; nothing is applied for it.
- **Applied and rejected tag decisions feed `tag_corrections`**, so Review improves Auto Tag the same way manual corrections do ([ADR-012](ADR-012-subject-prompt-layer-over-fine-tuning.md)).
- **The approval gate is a thread state**, not a lock: `awaiting_approval` → an approver's `approved` (or a rejection with a note → `open`). Approval needs admin on every subject of the thread (super admin for a general one). The agent reads the decision on its next run and acts. The write tools do not check it (ADR-021).
- **Visibility follows subjects:** reporter, admins of the thread's subjects, super admins, the agent. General threads: reporter and super admins. Who may report is a setting (`REQUESTS_MIN_REPORT_LEVEL`, default `user`). Notifications are an in-app unread badge only.
- **App bugs and feature requests are tracked, never acted on** through OQB: the agent acknowledges them and leaves them for the maintainers.
- **Library writes move into `app/library_ops_service.py`**: `rename_question`, `set_parent`, `upload_assets`, `write_md`, `replace_images`, `add_taxonomy_node`, `update_taxonomy_node`. They take explicit arguments, flush but do not commit (except where `replace_img_assets` already commits), and raise `OpError(message, status)`. The admin routes keep their URLs and responses and become thin callers; the MCP tools call the same functions. Taxonomy writes capture their restore point inside the service, still before the first mutation.
- **Naming:** the Requests module is `app/support_requests.py` / `app/support_requests_service.py` (blueprint `support_requests`) rather than `app/requests.py`, which would shadow the `requests` HTTP library for anyone writing `import requests` inside the package.

## Consequences

- A new kind of proposal (for example "replace this image") is a free-text item until someone adds a field or a dedicated item kind; Review will not apply it.
- Changing a field rule in `question_edit_service` changes the Edit modal, Review and the agent together. That is the point, but test all three.
- Requests store attachments permanently under `STORAGE_PATH\Requests\<id>\`; there is no retention job. Disk use grows with screenshots.
- Subject delete removes the subject's review batches but keeps its requests as general threads (reporter and super admins only).
- `admin.py` route behaviour for rename / set parent / upload / taxonomy add and edit now lives in the service; read `library_ops_service.py` before changing those routes.
- Because the gate is not enforced, a careless agent can write without approval. The action log, the thread notes and restore points are how a person notices and undoes it.
