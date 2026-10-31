# ADR-021 — The AI agent talks to OQB only through an MCP endpoint inside Flask

**Status:** accepted, 2026-11-01.

## Context

Teachers use an external AI agent (a scheduled Grok bot today) to answer issue reports and to review question tags. The agent runs on the OQB host and could in principle reach the database, the library folder and the code directly. Direct writes would skip the rules the app enforces on every change: restore points before tag and taxonomy edits ([ADR-013](ADR-013-subject-restore-points.md)), asset path bookkeeping on rename, hierarchy rules, and the correction log that improves auto-tagging.

The agent needs a way to read questions (including their images), read and answer requests, propose tag changes for review, and, when asked, change the library.

## Decision

- OQB exposes a **Model Context Protocol** server as one Flask route, `POST /mcp` (streamable HTTP, JSON responses, no server-sent stream). It is `app/agent_mcp.py` (transport, auth, dispatch) plus `app/agent_mcp_tools.py` (tools). There is no second process and no separate API.
- **Auth:** one shared bearer token `AGENT_API_TOKEN` (`.env` only), compared in constant time, plus a client IP allow-list `AGENT_MCP_ALLOWED_IPS` (default loopback) and the master switch `AGENT_MCP_ENABLED` (default off). An empty token or a disabled switch makes `/mcp` answer 404. A request with an `Origin` header must come from `PUBLIC_ORIGIN`. There is no agent user account. The token acts as a super admin, so tools do not check subject levels.
- `/mcp` is exempt from CSRF and from the session layer (no cookie is read or written). These are the only reasons it is on those exemption lists.
- **Writes go through services.** Every write tool calls the same service functions as the web routes (for example `question_edit_service`, `review_service`, `support_requests_service`), so restore points, the hierarchy rules and asset bookkeeping apply. Agents must not write to the database or `SOURCE_PATH` by any other path. There are no delete tools.
- Every write tool takes a `reason`, is logged to `agent_action_log` (tool, outcome, subject, question, request, batch, short argument summary, error) and, when it names a `request_id`, leaves a system note in that request thread.
- **Soft approval gate.** For changes a subject admin should agree to first, the agent posts an approval request in the thread. An admin clicks Approve or Reject. The agent reads the decision on its next run and then acts. OQB does not block the write tools on approval; the gate is a rule in the agent playbook (`docs/agents/PLAYBOOK.md`), which the endpoint serves as `get_instructions` and as the resource `oqb://agent/playbook`.

## Consequences

- Anyone with the token and a permitted IP can change the library as a super admin. The token belongs in `.env` only. Widening `AGENT_MCP_ALLOWED_IPS` beyond loopback widens that exposure.
- The agent cannot be limited per subject. If that is ever needed, it means an agent user account with grants instead of the shared token (a new decision).
- Because writes reuse services, a service change automatically changes what the agent can do. New write tools must call a service rather than repeat route logic.
- An agent that does not read the playbook may ignore the approval gate. The action log and restore points are the safety net.
- Clients that only speak stdio MCP need a small bridge. `scripts/oqb_mcp_stdio.py` relays stdin and stdout to `/mcp`.
