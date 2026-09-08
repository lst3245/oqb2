# Question hierarchy (stem + parts)
> A question may be a standalone item, a **stem** (shared background), or a **part** (child of a stem). Existing math-style rows are roots with no children and behave as before.

Implemented: schema, QID grammar, ingest/create/rename/delete, dashboard grouping, generator/viewer `resolve_render_plan`, admin tree UI, IMG Split tool with auto-detect, Combine (restore WHOLE / reconstruct), PDF two-pass part split, and AI ancestor-image context. See [ADR-009](../decisions/ADR-009-question-hierarchy-over-linking.md).

## Files

| File | Role |
|---|---|
| `app/hierarchy.py` | QNO token grammar (`parse_qno_token`, `QNO_TOKEN_PATTERN`), QID parse/build, part segmentation, `sort_key` / `qno_sort_key`, `ensure_question`, tree walks (`ancestors`, `descendants`, `earlier_siblings`), `group_for_dashboard`, `breadcrumb_parts`, `resolve_render_plan`, rename rewrite helpers, `stem_id_query`, `eager_load_tree`; plan-label helpers `label_is_ancestor`, `derive_roles`, `next_part_label`, `compose_part_label` |
| `app/question_split.py` | Stage/commit IMG crops for the Split-into-parts page (`SYSTEM_PATH/.question_split/<token>/`); `detect_boxes` reuses `pdf_import.detect_parts`. Root commit writes `WHOLE` from the staged PNG when that slot is empty, then crops QUE. |
| `app/question_combine.py` | Combine-parts: `choose_mode` / `preview_combine` / `combine_parts` / `combine_many`. Restore WHOLE→QUE on a root that has an archive; otherwise reconstruct IMG pages onto the survivor (optional stitch / `save_whole`). Range stems 409. |
| `app/pdf_import.py` | Plan labels; `detect_parts` / `iter_split_detect`; `whole_source_crops`; `iter_commit` via `ensure_question` (writes `WHOLE` for split roots before tight stem QUE) |
| `app/ai_tools.py` | `load_ancestor_que_images` / `prepend_ancestor_que`; auto-tag skips stems |
| `app/models.py` | `Question.parent_id` / `part` / `part_sort` / `qno_end`; `Subject.split_parts_default`; `QuestionAsset.asset_type` includes `WHOLE` |
| `app/ingestor.py` | Filename regexes use `QNO_TOKEN_PATTERN` and `QUE\|ANS\|SOL\|WHOLE`; `upsert_question` → `ensure_question`; `upsert_asset` skips WHOLE unless IMG on a root QID; sync skips stems that still have children; health anomalies exclude stems from untagged/no-type/no-level |
| `app/smart_import.py` | Heuristic qno match via `QNO_TOKEN_RE`; `_ensure_question` → `ensure_question`. Asset types stay `QUE/ANS/SOL` (WHOLE is not a Smart Import target). |
| `app/dashboard.py` | Leaves-only filter (unless `qids`/`ids`); `group_for_dashboard` after sort/paginate, then builds `rows` (substem + leaf, with `depth`) per group; Explain prepends ancestor QUE (never WHOLE) |
| `templates/partials/question_list.html` | Macros `tag_badges`, `que_preview`, `action_buttons(compact)`, `leaf_card` (standalone), `part_row`, `substem_row`; group = header card + `.hierarchy-children` |
| `app/generator.py` | `hierarchy_mode` + `resolve_render_plan` in create/viewer; seq owner; ANS/SOL ancestor fallback; QUE/ANS/SOL only |
| `app/admin.py` | Create/rename(cascade)/delete(guard); children/parent/split + split detect; combine preview/POST + bulk combine; list `tree_scope`; tag skip on stems; PDF `/split-detect`; WHOLE upload on roots |
| `app/utils.py` | `SORT_FIELDS['qid']` / `['qno']` use hierarchy sort keys |
| `templates/admin_question_split.html` | Canvas crop UI + Auto-detect parts |
| `templates/admin_pdf_import.html` | Split-into-parts checkbox, pass-2 Detect parts, Re-split / Unsplit |
| `tests/test_hierarchy.py` | Grammar, segmentation, rewrite, sort, render plan, dashboard grouping, split-box normalize (no live DB) |
| `tests/test_question_combine.py` | WHOLE filenames, `choose_mode`, `collapse_maximal_stems`, `whole_source_crops` (no live DB) |
| `tests/test_pdf_import_plan.py` | Plan labels / `sanitize_plan` (no live DB) |

## Tables

See [../core/03-data-model-and-migrations.md](../core/03-data-model-and-migrations.md).

| Model | Columns |
|---|---|
| `Question` | `parent_id` (self-FK, `ON DELETE RESTRICT`, index), `part` (own label `a` / `i` / `ii`, not the full path), `part_sort` (letter 1–26, roman 101–110), `qno_end` (inclusive end of a range stem; NULL otherwise). `qno` remains the **integer start** of the token. Relationships `parent` / `children`. |
| `QuestionAsset` | `asset_type` includes `WHOLE` (IMG, root-only unsplit original). Working types stay `QUE/ANS/SOL`. |
| `Subject` | `split_parts_default` (bool, default false). On the Subjects form. Seeds the PDF-import **Split questions into parts** checkbox when a paper for that subject is staged. |

`QuestionAsset.part_number` is still IMG page N of one slot. Do not reuse that name for sub-questions.

## Routes

Changed behaviour on existing routes plus new admin routes:

| Method | Path | Authz | Change |
|---|---|---|---|
| POST | `/admin/questions/create` | A, scoped | `qno` is a string token (`5`, `5a`, `3ci`, `23-24`, optional leading `Q`). Creates missing ancestor rows via `ensure_question`. |
| POST | `/admin/questions/<id>/rename` | A, scoped to the **new** subject | Cascades to descendants (QIDs + optional files). 400 if the rename would change range vs part depth while children exist. 409 on QID collision in or outside the subtree. |
| POST | `/admin/questions/delete` | A | 409 if any selected row has children not also selected, unless `delete_children=true`. Deletes deepest-first (RESTRICT). |
| GET | `/admin/questions/<id>/details` | A | Extra `parent_id`, `part`, `qno_end`, `child_count`, `is_stem`, `needs_prev_parts`, `has_whole`, `breadcrumb`, `children`, `tag_union`. |
| GET | `/admin/questions/api/list` | A | Extra `parent_id`, `part`, `qno_end`, `depth`, `is_stem`. Query `tree_scope=all\|roots\|leaves`. |
| POST | `/admin/questions/<id>/update` | A | Topic/chapter/level/q_type writes are ignored when the row is a stem. Answer/comment still apply. |
| POST | `/admin/questions/<id>/children` | A, scoped | JSON `{part}` (`a`, `b`, `ci`). `ensure_question` under this QID. 400 on range stems. |
| POST | `/admin/questions/<id>/parent` | A, scoped | JSON `{parent_id}` (null detaches). Same subject; `token_fits_under`; no cycles. 400 if detaching a labelled `part`. |
| POST | `/admin/questions/<id>/needs-prev-parts` | A, scoped | JSON `{value}`. Toggles `needs_prev_parts`; 400 when turning it on for a root. Details payload carries `needs_prev_parts`; dashboard cards carry it too (badge). |
| GET | `/admin/questions/<id>/split` | A, scoped | Stages IMG QUE and renders the crop page. MD/DOC QUE flash-redirects to the question list. Hidden in the Edit modal when the row is already a stem. |
| GET | `/admin/questions/<id>/split/image/<version>?token=` | A, scoped | Staged PNG. |
| POST | `/admin/questions/<id>/split/detect` | A, scoped | JSON `{token, version, endpoint_id}`. Pass-2 vision detect on the staged IMG → `{boxes, raw}`. |
| POST | `/admin/questions/<id>/split/commit` | A, scoped | JSON `{token, boxes, copy_tags}`. Crops per version; optional copy tags then clear the stem. Root: writes WHOLE from the staged PNG if that slot is empty. |
| GET | `/admin/questions/<id>/combine/preview` | A, scoped | JSON from `preview_combine`: `{ok, mode: restore\|reconstruct, is_root, has_whole, whole_pages, reconstruct_pages, descendants, descendant_count, can_save_whole, md_doc_lost, tag_source_qid}`. 400/409 when `ok` is false (leaf / range stem). |
| POST | `/admin/questions/<id>/combine` | A, scoped | JSON `{stitch?, save_whole?}`. Restore or reconstruct, then delete descendants. |
| POST | `/admin/questions/combine` | A | JSON `{ids, stitch?, save_whole?}`. Collapses overlapping stems to the ancestor; per-row `{ok, ...}` in `results`. |
| GET | `/admin/pdf-import/split-detect` | A, token-scoped | SSE pass-2 on the staged PDF plan. Params `token`, `kind` (`que`\|`sol`\|`both`), `endpoint_id`, `debug`, optional `labels` csv (re-split those parents). |
| GET/POST | `/dashboard/filter` | login | Result set is **leaves** unless `qids`/`ids` override. Cards grouped under stem headers. `#allQuestionIds` is leaves only. |
| GET/POST | `/generate/` | login + can_generate | Option `hierarchy_mode` (`selected` default / `whole`). Stored in `generation_options` and presets. |
| POST | `/generate/create` | login + can_generate | Background job expands via `resolve_render_plan`. |
| GET/POST | `/generate/viewer` | login | Slides/drawer are **leaves**; each carries `context` = the `stem`-role items of `resolve_render_plan([leaf])` (root, nested stems, earlier parts), all shown in `#stemPanel`. |

## Business rules / invariants

- **Superset, not a mode.** There is no per-subject "flat vs tree" flag in the data model. Math stays a forest of roots. `split_parts_default` only seeds the PDF-import split checkbox.
- **Tags live on leaves.** The tag editor disables topic/type/level fields on stems and shows the descendant union read-only. Dashboard topic filters match **leaves**; a stem appears only as a header wrapping matching parts.
- **QNO token** `Q<digits>(-<digits>)?(<part-path>)?`. Range and part-path are mutually exclusive. Canonical forms: `Q5`, `Q23-24`, `Q3a`, `Q3ci`. Source of truth: `app/hierarchy.py`.
- **Part path segmentation:** longest trailing roman (`i`–`x`) is one segment; a remainder letter chain is the parent path. A path that *is* a roman (`i`, `ii`) attaches to the root. UI intends three levels: question → letter → roman.
- **Selecting a stem = whole question**; **selecting some leaves = stem + those leaves** (`hierarchy_mode=selected`). `hierarchy_mode=whole` expands every selected node's root to the full tree. Expansion is `resolve_render_plan` only (ADR-009). Dashboard Selection stays ADR-008.
- **Seq numbers:** a leaf with `part IS NULL` (standalone or MC under a preamble) owns its seq; a leaf with a `part` shares its root's seq. Stems are not numbered. Info line / section headings / `[Cross Topic]` apply to leaves.
- **ANS/SOL fallback:** a leaf without that asset uses the nearest ancestor that has it, **once per ancestor id** in a stem run. Compact MC keys iterate leaves (and their assigned seq), not stems.
- **Rename cascade** rewrites descendant tokens relative to the renamed node, then `relink_parent`. Cannot rename `Q3` → `Q3a` while children exist.
- **Delete** is blocked while children exist unless `delete_children` is set. Sync never deletes a question that still has children.
- **`ensure_question`** find-or-creates the root, intermediate parents, and the node; flushes, does not commit. New range stems **adopt** existing parentless same-paper rows whose `qno` sits in `[qno, qno_end]` and `part` is NULL. New plain `Qn` rows **attach** to a covering range stem when one exists.
- **Dashboard selection is leaves only.** `#allQuestionIds` and `selectedQuestions` hold leaf ids; stem header / substem checkboxes are aggregates over `data-leaf-ids` (tick = select all those parts, indeterminate = some). Stem ids can still reach the generator/viewer from saved sets or `ids` overrides — `resolve_render_plan` expands and dedupes them.
- **Dashboard tree view.** A split question renders as one `.hierarchy-group`: a header card (root QID, part count, shared background shown **once**) followed by `.hierarchy-children` rows built by `filter_questions` as `group.rows = [{kind: 'substem'|'leaf', card}]`. Intermediate stems (e.g. `Q1c`, the shared intro of `(c)(i)`/`(c)(ii)`) are emitted once, right before their first part, via `ancestors(leaf)` minus the root; every card carries `depth` (root 0, its parts 1, sub-parts 2) and the CSS indents with `--depth`. Part rows are compact (label `(a)`, tags, own QUE only, icon actions) and never repeat the background; a substem row has an aggregate `stem-checkbox` over its sub-parts and an Explain button that sends the intro plus every sub-part. `stem_preview` on a leaf is rendered only when the leaf is shown **outside** its group (standalone `leaf_card`, e.g. `ids`/`qids` overrides).
- **Split tool** is IMG QUE only. Requires a `stem` box plus ≥1 part label. Range stems cannot be split here. Auto-detect uses the same pass-2 prompt as PDF import (`PDF_PART_*`). On a root, commit snapshots the staged full PNG as `WHOLE` (if that slot is empty) before cropping QUE.
- **Combine** (`app/question_combine.py`) folds a stem's parts back onto that stem, then deletes descendants (MD/DOC on children die with those rows). Modes:
  | Target | Action |
  |---|---|
  | Root with WHOLE | **Restore**: copy WHOLE → QUE (keep pages). Reconstruct ANS/SOL from the subtree. Delete descendants. WHOLE stays. `save_whole` is ignored. |
  | Root without WHOLE | **Reconstruct**: fold IMG QUE/ANS/SOL onto the root in tree order (multi-image default; `stitch` optional). `save_whole` snapshots the unstitched QUE pages as WHOLE. |
  | Nested stem (e.g. Q1c) | Reconstruct that subtree only. Never read or write WHOLE. |
  | Range stem / leaf | 409 / 400. |
  Bulk Combine collapses overlapping selection to maximal stems. Tags copy from the first tagged descendant leaf if the survivor has none; empty answer/comment copy the same way; `needs_prev_parts` is cleared on the survivor.
- **WHOLE** is never in generate / Present / Explain / auto-tag allow-lists. Upload (Assets tab) is IMG-only on roots. Ingest skips WHOLE on a part-path or range stem.
- **PDF import two-pass.** Pass 1 still one box per numbered question (or range). Pass 2 (opt-in, auto-runs when the checkbox is on) splits each crop into stem + lettered parts. SOL pass 2 uses QUE labels as `expected_labels`; unmatched lettered SOL is skipped and leftover whole-question SOL attaches to the stem. At commit, a **root** QUE that has split children writes `WHOLE` from pass-1 `source_box` pages (`whole_source_crops`) before the tight stem QUE.
- **AI ancestor QUE.** Auto-tag, MD transcription of QUE, and Explain prepend ancestor QUE images (root first) labelled as shared background. Proofread stays per-row. Auto-tag skips stems.
- **Nested stems are ordinary nodes.** `Q4d` with children `Q4di`, `Q4dii` is a part of `Q4` *and* the stem of its sub-parts; nothing marks it specially. In plan space `derive_roles(labels)` recomputes `stem` / `part` / `question` from the label set (`label_is_ancestor`), and `next_part_label` supplies the next sibling (`4a → 4b`, `4di → 4dii`, `4 → 5`).
- **`needs_prev_parts`** (bool on `Question`): a leaf that refers to earlier sibling parts ("using your answer in (a)"). `resolve_render_plan(mode=selected)` then emits `earlier_siblings(node)` (plus their descendants) as `stem`-role background before the leaf, once per run and without duplicating a sibling that is itself selected. `whole` mode is unaffected (everything is already there). Roots cannot carry the flag (route returns 400). Set from the edit modal switch or by the PDF import agent via a plan item's `depends_prev`.
- Uniqueness remains `questions.qid` only. `(subject, source, year, paper, qno)` is **not** unique.

## Settings & config keys

None. `Subject.split_parts_default` is a column, not a System Setting. See [../core/06-system-settings.md](../core/06-system-settings.md).

## Permissions

Create / rename / delete / children / parent / split / combine stay subject-admin scoped as before. Rename still checks admin access on the **new** subject. Delete expands children only within the caller's admin subjects. Dashboard grouping is login + subject access (same as filter).

## Background work / SSE / threads

None in this module's own routes except Split detect (synchronous JSON) and PDF `/split-detect` (SSE; see [pdf-import.md](pdf-import.md)). Ingest/Smart Import SSE unchanged. Split commit and Combine are synchronous JSON (`replace_img_assets` per version/slot).

## Gotchas

- **IMG `part_number` vs question `part`.** Filename `..._QUE_2.png` is still a second image of the same QID. `..._Q3a_QUE.png` is a different question.
- **Smart Import `_SPLIT_RE` still splits on hyphen** for year/paper tokens; qno is taken from the **unsplit** stem via `QNO_TOKEN_RE` so `Q23-24` is not `Q23` + `24`.
- **Two regex families** (filename `PP_PATTERN` / `QB_PATTERN` vs QID `PP_QID_RE` / `QB_QID_RE`) both import `QNO_TOKEN_PATTERN` / `parse_qid`. Do not add a third `Q\\d+` copy.
- **`qid` sort is no longer natsort of the string.** `SORT_FIELDS['qid']` uses a tuple so `Q3ci` / `Q3civ` / `Q23-24` order correctly.
- A stem whose QUE image is still the **full** question plus children will double-render. Only the Split tool or PDF pass 2 should create children from a full-page crop.
- **`replace_img_assets` commits per version/slot during split and combine.** A later version failing can leave earlier crops already on disk. Combine loads subtree pages into memory first, writes the survivor, then deletes descendants — do not reverse that order.
- Combine on a root **with** WHOLE always restores (it does not reconstruct QUE from parts). Delete the archive in the Assets tab first if you want a reconstruct from the current parts.
- PDF re-import without overwrite skips a root that already has QUE, so a historical split paper will not gain a WHOLE archive until you upload one or re-import with overwrite.
- Empty parent rows from ingesting `..._Q3a` with no `Q3` file are load-bearing. Sync's 24 h grace plus the "has children" guard keep that row. Health **no-assets / untagged / no-type / no-level** lists skip stems.
- `ensure_question` may insert an empty parent. Database Health **stems_with_tags**, **parts_missing_que**, **empty_range_stems** are the hierarchy-aware anomalies.
- Boot patch adds the self-FK on the **live** DB at reload. All new columns are nullable / defaulted; existing rows stay standalone.
- PDF pass-2 crops with `pad_frac=0` and `trim_white=False`, then `full_width_child_box` (wraps `map_crop_box_to_page` but keeps the parent's x1/x2) so part boxes stay page-relative and aligned with the stem. Do not pad before mapping.

## Related

- [../decisions/ADR-009-question-hierarchy-over-linking.md](../decisions/ADR-009-question-hierarchy-over-linking.md)
- [../reference/filename-convention.md](../reference/filename-convention.md)
- [ingestion.md](ingestion.md), [admin-questions.md](admin-questions.md), [admin-panel.md](admin-panel.md), [generator.md](generator.md), [dashboard.md](dashboard.md), [pdf-import.md](pdf-import.md), [ai-tools.md](ai-tools.md)
- [../core/03-data-model-and-migrations.md](../core/03-data-model-and-migrations.md), [../core/04-backend-conventions.md](../core/04-backend-conventions.md)
