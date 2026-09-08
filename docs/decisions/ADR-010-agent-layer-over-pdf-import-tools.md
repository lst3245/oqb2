# ADR-010 — An agent layer over the PDF import tools, not a free-running agent

**Status:** accepted, in force. `app/pdf_agent.py`, the `/admin/pdf-import/agent` SSE route, the Attention panel in the wizard, derived plan roles, nested stems and `needs_prev_parts` are live.

## Context

The two-pass PDF import (page detection, then per-question part split) is a fixed procedure: one page at a time, one crop at a time, no memory of the paper as a whole. It mis-numbers continuation pages, cannot tell that `(d)` has its own sub-parts `(i)(ii)`, and has no way to notice that part `(b)` was skipped. Every miss costs a teacher a manual fix in the review step. The goal is "drop a past paper in, review only what is doubtful, commit".

The LLM endpoints in use are OpenAI-compatible vision models without reliable native tool calling, and each call is slow (tens of seconds) and paid.

## Decision

- **Fixed state machine, LLM inside each state.** Outline (whole paper from captioned thumbnails) → locate (existing `iter_detect` with per-page expected labels) → segment (existing `iter_split_detect` with the outline's part tree as hints) → verify (boxes redrawn on the crop, critiqued, deterministic fixes applied, bounded repair rounds). The model never chooses the next tool; it only answers strict-JSON questions. This keeps cost bounded (`PDF_AGENT_MAX_LLM_CALLS`), makes every step replayable from `agent_log.jsonl`, and reuses the tested pass-1/pass-2 code instead of a second detector.
- **Code owns every mutation.** The verify reply is a vocabulary of fixes (`relabel`, `drop`, `extend_top/bottom`, `redetect`); the agent applies them itself and never lets the model emit new coordinates for a repair — redetection goes back through `detect_parts` with the complaint appended as a note.
- **Attention list instead of autonomy.** Anything the agent cannot reconcile (outline gaps, questions it could not find, parts missing after repairs, unexpected numbers) is written to `attention.json` with a severity and shown as a panel in the review step. The human reviews flagged rows and clicks Commit; the agent never writes to the library or the DB.
- **Roles are derived, never stored.** A plan label is a stem because another label descends from it (`5` ⊃ `5a`, `5d` ⊃ `5di`). `hierarchy.derive_roles` (server) and `deriveRoles` (browser) recompute on every change, which is what makes nested stems and manual part editing coherent with pass 2.
- **Dependency between sibling parts is a per-part flag, not an edge.** `questions.needs_prev_parts` makes `resolve_render_plan` prepend the earlier siblings as background; the agent sets it from wording ("using your answer in (a)"), admins toggle it in the edit modal. No general graph (ADR-009 stands).

Rejected alternatives: a tool-calling loop where the model picks actions (unbounded cost, non-reproducible, poor tool-calling support on the endpoints in use); a separate end-to-end "whole PDF → JSON" prompt (context limits, no per-question crops for verification, no reuse of pass 1/2); storing `role` from model output (drifts as soon as a label is edited).

## Consequences

- An agent run replaces `plan.json` for the session; manual edits made before running it are lost (the wizard warns).
- New prompts (`PDF_AGENT_*`) and parsers (`parse_agent_outline`, `parse_agent_verify`) live in `app/ai_prompts.py` and are editable per endpoint like every other prompt.
- `iter_detect` / `iter_split_detect` / `detect_page` / `detect_parts` gained optional hint parameters (`expected_by_page`, `page_filter`, `expected_by_parent`, `note_by_parent`, `extra_note`); plain wizard runs pass none and behave as before.
- Future improvements go into the state machine (better outline batching, OCR text layer as a second signal, per-subject prompt variants) — not into letting the model drive.
