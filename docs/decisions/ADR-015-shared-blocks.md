# ADR-015 — Shared blocks for text printed between parts

**Status:** accepted, in force. Grammar, tree, render plan, PDF import, prompts, Split tool, dashboard/Present/Explain labels and the Edit-modal repair action are live. Existing ICT questions were repaired on 2026-10-09 (see Consequences). Amended 2026-10-09: optional block end (`questions.block_end`).

## Context

A structured question can print a new passage, table or scenario **between** two parts, and the later parts use it. ICT 2025 P1B Q7: the stem is a web form, (a) and (b) use it; then "Mr Li uses the database table SS …" is printed, and (c) and (d) use that table.

ADR-009 has one shared background per node: the stem, printed above (a). PDF import pass 2 labelled the mid-question passage `stem`, `compose_part_label` mapped it to the root label, and `_group_plan` merged it into the root's QUE as page 2. The dashboard, the .docx and Present then printed the SS table at the very top, above (a). A scan found 11 split ICT roots with the same shape.

Options considered:

1. **Extra QUE page on the root** (status quo). Wrong order everywhere; the parts that need it cannot be told apart from the ones that do not.
2. **Put the text into (c)'s QUE.** Right order for (c), but (d) loses it unless it also gets `needs_prev_parts`, and `needs_prev_parts` drags in (a)/(b) too. Selecting (d) alone would show it without the table.
3. **A per-part "context asset" list** (peer links). The general graph ADR-009 rejected.
4. **A transparent tree node** that owns the text and is the parent of the parts it introduces. Chosen.

## Decision

- **Token** `Q<n>[<path>]~<anchor>`: `Q7~c` is the text printed before (c) at the top level; `Q7d~iii` is text inside (d) before (d)(iii). It is a child of the lettered parent (`Q7`, `Q7d`) and the parent of every part at its level from the anchor onward, up to the next block at that level (`Q7~c` owns `c`, `d`; with `Q7~e` present, `e` moves to `Q7~e`).
- **Transparent.** Parts keep their QIDs and printed labels: `Q7c` stays `Q7c` under `Q7~c`. The block segment never appears in a descendant's token, so ancestors cannot be derived from a QID alone; `relink_parent` / `relink_block_level` re-home parts by query (`block_child_for`, `pick_block`).
- **Storage.** `part = '~c'`, `part_sort` = the anchor's value; "is a block" is derived from the leading `~` (`hierarchy.is_block`). No collision with `c`, because `c` sits under the block, not beside it. The one column added (2026-10-09) is the optional end below.
- **Optional end** (amended 2026-10-09). `questions.block_end` (NULL = old behaviour) is the last same-level part segment the block owns: `Q4~b` with end `b` owns (b) only. Ownership of a part = the block with the latest anchor ≤ the part; if that block's end sorts before the part, **no** block owns it and it sits under the lettered parent. An earlier block never resumes after a later one; an end only shortens a block. Valid end: one plain segment of the anchor's kind, ≥ the anchor, < the next block's anchor at that level (`hierarchy.block_end_error`). An end that no longer fits (anchor renamed past it) is ignored, not an error (`effective_block_end`). Ends are contiguous: a block cannot skip a part. Set through `hierarchy.set_block_end` only (Edit modal "Ends after part", move-to-block, PDF import `block_end`, `cli.py set-block-end`); a manual set-parent that disagrees is refused with 409 rather than silently undone.
- **Constraints.** No blocks at the root or under a range stem; no `~a` / `~i` (text before the first part is the stem or that part's own intro); a block is always a stem (`derive_roles`, `is_stem`), never selectable, never tagged, no WHOLE archive, no `needs_prev_parts`. Deleting a block alone **dissolves** it (its parts move up); combine on a block is refused.
- **Order.** `part_sort_key('~c')` ties the anchor with an empty tie-break, so the block sorts after (b) and before (c). `resolve_render_plan` emits all background (ancestors + `needs_prev_parts` siblings) in `sort_key` order, so (d) with "uses earlier parts" prints form, (a), (b), SS table, (c), (d). `earlier_siblings` crosses the block boundary.
- **Import.** Pass 2 may return `intro-c` (prompt rule in `PDF_PART_QUE_SYSTEM`; parser aliases `intro-c`, `before (c)`, `~c`, `3~c`). Independently of the prompt, `pdf_import.reclassify_mid_stems` turns a `stem` box that sits below a part, or on a continuation crop page, into the block before the next part. A marking scheme never has blocks (SOL block labels fold into the stem).
- **One grammar.** Python `app/hierarchy.py` is the source; `templates/admin_pdf_import.html` mirrors `parseLabel` / `partSegments` / `labelIsAncestor(labels)` / `deriveRoles`.

## Consequences

- QID charset gains `~`. It is safe in Windows filenames, URLs (unreserved), Jinja/JS strings, and is not used in CSS selectors or DOM ids. `werkzeug.secure_filename` strips it, but that is only used for My Files uploads.
- Rows imported before this change kept the passage as a root QUE page (the 13 ICT cases were repaired on 2026-10-09). Repair is per question by a subject admin: **Edit → Assets → (layers icon on QUE page 2) → "printed before which part?"** — `POST /admin/questions/<id>/assets/move-to-block` dry-runs, then moves that page (every IMG version) into the new block through `replace_img_assets`. Never by hand on `SOURCE_PATH`.
- Dashboard depth ignores blocks: (c) lines up with (a); the block row reads "Shared · before (c)".
- A future feature that walks "parts of a question" must treat `is_block` nodes as background, not as answerable parts (`leaf_clause()` in SQL, `is_stem` in Python).
- **2026-10-09, block ends.** Several repaired blocks introduce text used by one part only (ICT 2017 P2D Q4: the table before (b) is for (b); (c)–(e) are unrelated). Without an end, selecting (c) printed that table. Adding `block_end` fixes render, dashboard, .docx, Present and Explain with no render-code change, because context is still derived from `parent_id`. With `needs_prev_parts` on (d), the earlier siblings are (a), the block (+ (b)), (c): the block prints only as background in front of (b). Labels read "before (b), (b) only" / "before (b), up to (c)". The 14 ICT candidates are listed for `cli.py set-block-end --from-csv` (dry run by default); applying is the user's call.

Details: [../modules/question-hierarchy.md](../modules/question-hierarchy.md#shared-blocks-text-between-parts), [../modules/pdf-import.md](../modules/pdf-import.md). Extends [ADR-009](ADR-009-question-hierarchy-over-linking.md).
