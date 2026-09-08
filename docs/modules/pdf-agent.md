# PDF Import — AI agent
> Turns a staged past-paper PDF into a reviewed `plan.json` (questions, nested parts, dependencies) with an attention list of what still needs a human, using the pass-1/pass-2 tools of [pdf-import.md](pdf-import.md) as its hands.

## Files
| File | Role |
|---|---|
| `app/pdf_agent.py` | The state machine (`iter_agent`), pure helpers (`merge_outline`, `check_outline`, `reconcile_pages`, `compare_parts`, `fix_overlaps`, `apply_verify_fixes`, `mark_depends_prev`), renderers (`captioned_thumbnail`, `render_check_image`), staging files (`outline.json`, `attention.json`, `agent_log.jsonl`) |
| `app/ai_prompts.py` | `PDF_AGENT_OUTLINE_*`, `PDF_AGENT_VERIFY_*` prompts; `parse_agent_outline`, `parse_agent_verify`; `{{expected_note}}` slots on `PDF_QUE/SOL_BOX_SYSTEM`, `PDF_BOX_USER`, `PDF_PART_BOX_SYSTEM` |
| `app/pdf_import.py` | Hint parameters the agent drives: `iter_detect(expected_by_page, page_filter)`, `iter_split_detect(expected_by_parent, note_by_parent)`, `detect_page(expected_labels)`, `detect_parts(expected_labels, extra_note)`, `detect_single_page(expected_labels)`; `apply_derived_roles`; `sanitize_plan` keeps `depends_prev`; `iter_commit` sets `Question.needs_prev_parts` |
| `app/hierarchy.py` | `derive_roles`, `label_is_ancestor`, `next_part_label`, `earlier_siblings`; `resolve_render_plan` honours `needs_prev_parts` |
| `app/admin.py` | `/admin/pdf-import/agent` (SSE), `/admin/pdf-import/attention` (JSON), `/admin/questions/<id>/needs-prev-parts` |
| `templates/admin_pdf_import.html` | "Auto-process with AI agent" option, agent stage log, Attention panel, per-row "uses earlier parts" toggle, derived role chips, Add-part-below |
| `tests/test_pdf_agent.py` | Pure-logic tests (no LLM, no DB) |

## Tables
`questions.needs_prev_parts` (bool, default false) — see [../core/03-data-model-and-migrations.md](../core/03-data-model-and-migrations.md). No other schema. Staging artefacts live under `STORAGE_PATH/System/pdf_import/<token>/`.

## Routes
| Method | Path | Authz | Purpose |
|---|---|---|---|
| GET | `/admin/pdf-import/agent?token&endpoint_id&parallel&debug&restart&attach` | `@admin_required` + token subject check (`_pdf_load_token_meta`) | Spectator SSE over a **background** job. Events `{type: info\|success\|skip\|error\|attention\|heartbeat\|done, message, stage, current?, total?}`; `stage` ∈ `outline, locate, segment, verify, done`. `done` carries `plan`, `attention`, `outline` (per-side summary), `calls`. First event is `{type:'job', job_id, attached}`. `restart=1` cancels a live run and starts over; `attach=1` (or a second open of the same URL without `restart`) joins the live run and replays its events. Heartbeats every 15 s while a verify LLM call is in flight. |
| GET | `/admin/pdf-import/attention?token` | same | `{attention: [...], outline: {que: summary, sol: summary}}` for a session (re-opening the review step). |
| POST | `/admin/questions/<id>/needs-prev-parts` `{value}` | `@admin_required` + `_require_md_admin` | Toggle the dependency flag on a part (400 for roots). |

Attention item: `{kind: que|sol|null, label, page (0-based)|null, severity: info|warn|error, reason, stage}`.

## Business rules / invariants
- **Pipeline order is fixed**: A1 outline → A2 locate → reconcile → A3 segment → A4 verify/repair → finish. The model never picks the next step (ADR-010).
- **A1 outline**: page thumbnails (`PDF_AGENT_THUMB_MAX_DIM`) get a black "PAGE n" caption strip so the model refers to staging page numbers, not the exam footer. Batches of `PDF_AGENT_OUTLINE_BATCH_PAGES`; each batch receives the running outline (questions only) and is merged by `merge_outline` (page union, part-tree union). Invalid JSON is retried once, then flagged. `check_outline` flags number gaps, out-of-order starts, non-consecutive pages, questions on non-question pages.
- **A2 locate**: pages the outline marks `blank / instructions / answer_sheet / formula` *with no question listed* are skipped (no call). Every detected page gets `expected_labels` in the prompt. After `iter_detect`, `assign_unlabelled_from_outline` names continuation boxes when exactly one outline question spans the page boundary; `reconcile_pages` re-detects a page once for each outline question found nowhere (`error` if still missing) and flags labels the outline does not know (`warn`).
- **A3 segment**: only questions whose outline tree has parts (plus whole-question boxes the outline does not know about) go to pass 2, with `expected_by_parent` = `['stem', 'a', 'ai', ...]`. SOL uses the QUE outline's tree.
- **A4 verify**: per question, deterministic first (`fix_overlaps` trims same-page overlaps > 25 % of the smaller box and drops boxes < 0.8 % page height; `compare_parts` flags missing/extra vs the outline — a grouping letter such as `a` is not missing when `ai`/`aii` were found), then per page a `render_check_image` crop with numbered coloured boxes is critiqued. The verify prompt is told only the parts **on this page**; parts that already exist on another page of the same question are named in `page_note` and ignored by `apply_verify_fixes(..., ignore_missing=...)` so a multi-page Q2 cannot trigger a whole-question redetect because (d) is on the next page. `apply_verify_fixes` applies `relabel / drop / extend_top / extend_bottom` itself; a *real* `missing` / `redetect` re-runs pass 2 for that question with the complaints as `note_by_parent`. Up to `PDF_AGENT_MAX_REPAIR_ROUNDS`; a question that still changes on the last round is flagged. Plan + `attention.json` are saved after each question so a dropped browser still has the work.
- **Dependencies**: `depends_prev` from the verify reply and from the outline mark plan items (`mark_depends_prev`; never `a`/`i`). Commit turns the flag into `Question.needs_prev_parts` for parts that have a parent.
- **Roles are derived** (`apply_derived_roles` on every save path; `sanitize_plan` ignores browser roles). Nested stems (`4d` above `4di`) fall out of the label set.
- **Budget**: `Budget.charge` before every call group; `BudgetExhausted` ends the run with an `error` attention item and whatever plan exists.
- Nothing here writes to the DB or `SOURCE_PATH`; only Commit does.

## Settings & config keys
`PDF_AGENT_OUTLINE_BATCH_PAGES`, `PDF_AGENT_THUMB_MAX_DIM`, `PDF_AGENT_MAX_REPAIR_ROUNDS`, `PDF_AGENT_MAX_LLM_CALLS` — [../core/06-system-settings.md](../core/06-system-settings.md). Detection/verification images use `LLM_IMAGE_MAX_DIM`; box axis order `PDF_IMPORT_COORD_ORDER`.

## Permissions
Same as PDF import: subject admin of the session's subject (super admin bypass). Generic-extraction sessions are refused.

## Background work / SSE / threads
The pipeline runs on a **daemon thread** keyed by staging token (`start_agent_job` / `iter_job_events`). The SSE response is a spectator: it replays stored events, emits `{type:heartbeat}` every 15 s while the worker is blocked in an LLM call, and a client disconnect (`GeneratorExit`) does **not** cancel the job. A later `/agent?attach=1` (or the same URL without `restart`) rejoins. Pass 1 / pass 2 fan out through `run_parallel` when `parallel=1` and the endpoint is cloud with `max_concurrency > 1` (same gate as the wizard). Outline and verify calls are sequential (they depend on the running outline / current plan). Cancel via the shared `/pdf-import/cancel` job registry; the agent finishes with the plan as it stands. In-memory jobs die if the Flask process reloads.

## Gotchas
- An agent run **replaces `plan.json`** (it starts from `iter_detect`). Run it before manual edits, or re-run knowing edits are lost. The wizard confirms before **Run AI agent** when boxes exist (that click sends `restart=1`); the Setup-step **Auto-process** tick starts the agent straight after Load PDF without asking (the session is empty then). If the EventSource drops, the UI rejoins once with `attach=1` instead of treating the run as dead.
- The Attention panel is browser state seeded from the `done` event (or `GET /attention` if the stream drops). Dismissing an entry does not touch `attention.json`; row highlights (`.attention-error` / `.attention-warn`) are re-derived on every render by matching `kind` + `label` against the current plan, so relabelling a box clears its highlight.
- The per-row link button (`depends_prev`) and Re-split on a nested stem (sends the stem's own label, e.g. `4d`) are browser-only edits until the plan is saved (`/plan` on Detect parts, or `/commit`).
- Outline page numbers are 1-based staging pages (caption), plan `page` is 0-based; the conversion lives in `iter_agent` only.
- `expected_by_parent` is keyed by label and shared by both sides on purpose (SOL mirrors QUE).
- `render_check_image` legend numbers are per page; a multi-page question is verified page by page. Never pass the whole-question outline as `expected` on one page — that is what made ICT 2023 P1B Q2 ask to redetect (d)/(e) that already existed on the next page.
- The verify prompt may report `ok: true` while listing fixes — the parser overrides `ok` to false whenever any issue has a fix ≠ `none`.
- `fix_overlaps` mutates the filtered list; pass `pool=plan[kind]` so degenerate boxes are also removed from the plan.
- `agent_log.jsonl` stores raw replies only with `debug=1`.

## Related
[ADR-010](../decisions/ADR-010-agent-layer-over-pdf-import-tools.md), [pdf-import.md](pdf-import.md), [question-hierarchy.md](question-hierarchy.md), [ai-prompts.md](ai-prompts.md).
