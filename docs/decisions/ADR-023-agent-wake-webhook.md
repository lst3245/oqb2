# ADR-023 — Wake the agent with an outbound webhook, not by opening /mcp to it

**Status:** accepted, 2026-11-01.

## Context

The AI agent (a Grok Bot routine) runs on a schedule and polls `list_requests(needs_agent=true)` over `POST /mcp` ([ADR-021](ADR-021-agent-mcp-endpoint.md)). A teacher who answers the agent's question, or an admin who approves its plan, waits until the next run. The routine can be started on demand through a webhook URL its platform provides.

## Decision

- When a **person's** action makes a request **newly** need the agent (`needs_agent` false before the action, true after), OQB POSTs one JSON notice to `AGENT_WAKE_WEBHOOK_URL` (`app/agent_wake.py`). The actions are the Requests routes that commit a person's change: new request, reply, status change and approval decision. Agent messages, agent status changes and system notes never fire it, because they never make a person the last actor.
- The notice is a **wake-up, not a channel**: `{event: "request.needs_agent", request_id, status, subject_id, title, last_message_at, questions: [{qid, question_id, subject_id}]}`. No message text, attachments, tokens or user names. The agent then reads the thread and acts through `POST /mcp` as before. `AGENT_MCP_*` is unchanged; the allow-list stays loopback.
- `AGENT_WAKE_WEBHOOK_URL` and the optional `AGENT_WAKE_WEBHOOK_SECRET` are **`.env` only**: a routine webhook URL usually embeds a capability token. The secret goes out as `Authorization: Bearer …` and `X-Webhook-Secret`. HTTPS only, except plain HTTP to `localhost` (a local relay).
- **Best effort.** The POST runs after the commit on a daemon thread with a 3 s timeout and no redirects; failures are logged (host only) and never reach the person. One notice per request per 5 s (in-process).

## Consequences

- A wake can be lost (endpoint down, server restart mid-send, a second process). The routine's normal schedule still picks the thread up; the webhook only shortens the wait.
- A thread that already needed the agent does not fire again when the person adds a second message. If the routine ignored the first wake, the next scheduled run handles both.
- An admin's status change to `open` / `in_progress` also fires, because it puts the thread in the same queue the agent polls.
- Anyone who learns the URL can start the routine, which then only reads OQB through the token-protected `/mcp`. Rotate the URL / secret in `.env` if it leaks.
