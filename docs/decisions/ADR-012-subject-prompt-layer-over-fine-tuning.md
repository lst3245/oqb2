# ADR-012 — Per-subject prompt layer and correction feedback instead of model fine-tuning

## Status

Accepted.

## Context

Auto Tag classifies questions against a subject's Topic / Subtopic / Chapter / Subchapter taxonomy. The prompt was global (super-admin `PROMPTS_REGISTRY` variants, optionally pinned per endpoint) and the only subject-specific input was the bare list of node names. Subject admins wanted to "fine-tune" tagging for their subject and have the system "learn" from their corrections.

Options considered:

1. **Fine-tune a model per subject.** Rejected: endpoints are arbitrary OpenAI-compatible servers (local and cloud), per-subject volume is small, the taxonomy changes over time, and it would need a training pipeline per subject per model.
2. **Let subject admins edit the registry prompts.** Rejected: the prompt page is a super-admin surface; a subject-scoped edit there would leak into every subject, and free edits can break the JSON contract the parser depends on.
3. **A subject layer under the registry** — structured hints on taxonomy nodes, free-text instructions per subject (append or replace the system body), a correction log fed back as aggregated patterns, and an evaluation loop against already-tagged questions. **Chosen.**

## Decision

- Add `SubjectPromptNote (subject_id, feature, mode, content, examples_limit)` and a `description` hint on every taxonomy table. `ai_tools.build_tag_prompt` is the single assembler: registry frame + subject note + node hints + aggregated correction patterns.
- `replace` mode swaps only the `TAG_SYSTEM` **body**; the `TAG_FORMAT` block is always re-attached server-side (`ai_prompts.system_prompt_with_body`). The JSON contract is never editable from the subject tier.
- Record `TagCorrection (field, suggested, saved, agreed)` when a teacher saves tags immediately after an Auto Tag suggestion. "Learning" = aggregating disagreements into a short "teacher correction patterns" block (`examples_limit` most frequent `suggested → saved` pairs) that is injected on the next call. No question content is replayed (most questions are images).
- Provide a no-write **evaluate** run that compares suggestions with existing tags and reports agreement per field, so tuning is measured rather than guessed.
- Ask the model for optional per-field `confidence` / `reasons`; they are diagnostics for the report and the modal, never applied.

## Consequences

- Subject admins get a self-service loop (hint → preview → evaluate → report → refine) without super-admin involvement and without a new model.
- The subject tier is lower trust; its influence is bounded to its own subject and to the prompt body / user turn, never the output contract.
- Correction rows store names, not IDs, so taxonomy renames leave stale text in the patterns block until the log is cleared.
- Hidden taxonomy nodes are no longer offered to the model; that was an accidental behaviour of the old taxonomy block.
- If real few-shot with question content is ever wanted, the correction log already holds the `(question_id, field, saved)` triples to build it from; an embeddings endpoint would be the missing piece.

## Related

- [ADR-004](ADR-004-db-backed-settings-with-env-bootstrap.md) — DB-backed overrides over bootstrap defaults (same layering idea)
- [../modules/subject-ai.md](../modules/subject-ai.md), [../modules/ai-prompts.md](../modules/ai-prompts.md), [../modules/ai-tools.md](../modules/ai-tools.md)
