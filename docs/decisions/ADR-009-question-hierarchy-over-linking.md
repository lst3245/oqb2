# ADR-009 — Question hierarchy over peer linking

**Status:** accepted, in force. Schema, grammar, dashboard grouping, generator/viewer expansion, PDF two-pass import, Split-tool auto-detect, and AI ancestor images are live.

## Context

OQB2 was built for DSE maths papers in which each numbered question is standalone and tagged with one or two topics. Other subjects need two extra shapes:

1. **Shared preamble.** "Refer to the following information for Questions 23 and 24" — two attemptable MC items, one background.
2. **Long structured questions.** Q3 (a)(b)(c)(i)(ii) sharing a stem; teachers want to pull *stem + only the parts that match a topic* into an exercise.

Peer-to-peer "Q6 requires Q5" edges would be a general graph (cycles, transitive closure, ordering). Almost every real paper is a **tree**.

## Decision

- Model the tree as `questions.parent_id` (self-FK, `ON DELETE RESTRICT`). A node is a full `Question` (own QID, assets, tags, answers).
- Extend the QNO token, not a parallel ID scheme: `Q5`, `Q5a`, `Q3ci`, `Q23-24` (range stem). Integer `qno` stays the start of the token; `qno_end` / `part` / `part_sort` carry the rest.
- Do **not** introduce a per-subject "flat vs tree" data-model mode. Hierarchy is a strict superset; math rows stay roots. Per-subject `split_parts_default` only seeds an import checkbox.
- Shared grammar lives in `app/hierarchy.py`. Filename parsers, admin QID validation, Smart Import, and create/rename all import it.
- Generator expansion is a single helper `resolve_render_plan` (not client-side selection rewriting; ADR-008 stands).

Rejected alternatives: asset-level parts (tags/answers/verification hang off `Question`); peer `depends_on` edges; stuffing extra "necessary info" blobs onto leaves.

## Consequences

- `(subject, source, year, paper, qno)` is no longer a unique identity. Lookups stay on `qid`.
- Ingest of `..._Q3a_...` creates an empty `Q3` parent when needed. Sync must not delete stems that still have children.
- Rename of a stem cascades; delete of a stem is blocked unless children are included.
- Follow-on work must not invent a second QNO regex or a second generator expansion path besides `resolve_render_plan`.

Details: [../modules/question-hierarchy.md](../modules/question-hierarchy.md), [../reference/filename-convention.md](../reference/filename-convention.md).
