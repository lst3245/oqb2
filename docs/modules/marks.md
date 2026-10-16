# Marks

> Per-question marks (`Question.marks`, stored on leaves; the whole-question total is derived), sorting by marks, and the per-subject "spacing by marks" rules that the Generate page's **By marks** mode applies.

## Files

| File | Role |
|---|---|
| `app/models.py` | `Question.marks` (SMALLINT NULL), `Subject.marks_spacing` (TEXT JSON NULL) |
| `app/__init__.py` | Boot patch adding both columns (`INFORMATION_SCHEMA` guard) |
| `app/hierarchy.py` | `question_root(q)` (walks up while `part` is set; stops at a range child), `whole_marks(node) -> (total, complete)`, `question_marks(q, memo)` (root total, memo keyed by root id) |
| `app/utils.py` | `MAX_MARKS = 999`, `parse_marks(raw)` (blank → None; whole numbers 0–999; `ValueError` otherwise), `SORT_FIELDS['marks']` / `['total_marks']` |
| `app/marks_spacing.py` | Pure rule module: `normalize_rules`, `load_rules` (never raises), `dumps`, `is_empty`, `resolve(rules, side, marks)`, `MarksSpacingError` |
| `app/generator.py` | `_spacing_mode`, `_uses_marks_spacing`, `_load_marks_rules`, `_marks_subjects_for`, `resolve_entry_spacing`; multi-page `add_before_spacing` / `add_after_spacing` |
| `app/admin.py` | Edit modal write (`update_question`), details payload `marks` / `whole_marks`, CSV `marks` column, routes `GET` / `POST /admin/subjects/<id>/marks-spacing` (`app/generator.py` has the per-paper check route and `parse_marks_rules_override`) |
| `app/batch_edit.py` | Bulk Edit `marks` field (`update_marks`); stems skipped |
| `app/dashboard.py`, `templates/partials/question_list.html` | Card badges: `Nm` on a part / question, `N marks` in a stem header |
| `app/ai_prompts.py`, `app/ai_tools.py`, `app/subject_ai_service.py` | Auto Tag `marks` field; `clean_marks`; PDF box contracts and parsers carry `marks` |
| `app/pdf_import.py`, `app/pdf_agent.py`, `templates/admin_pdf_import.html` | PDF import marks: plan item `marks`, Step 2 input + sum warning, commit writes leaf marks |
| `templates/partials/tag_editor_form.html`, `tag_editor_js.html`, `edit_question_modal_js.html` | Edit modal Marks input (disabled on stems, which show the derived total) |
| `templates/dashboard.html` | Bulk Edit Marks block; JS `SORT_FIELDS` |
| `templates/generate.html` | **By marks (subject rules)** radios, the subjects row, the **Marks spacing rules** modal |
| `tests/test_marks_spacing.py`, `tests/test_hierarchy.py` (`WholeMarksTests`), `tests/test_batch_edit.py` (`MarksTests`), `tests/test_subject_snapshot.py`, `tests/test_ai_prompts.py`, `tests/test_pdf_import_plan.py` (`PlanMarksTests`), `tests/test_pdf_agent.py` (`OutlineMarksTests`), `tests/test_generate_template_ux.py` | Pure tests |

## Tables

See [../core/03-data-model-and-migrations.md](../core/03-data-model-and-migrations.md) and [../reference/schema-history.md](../reference/schema-history.md).

| Column | Type | Notes |
|---|---|---|
| `questions.marks` | SMALLINT NULL | Leaf marks. NULL = unknown. Never set on stems or shared blocks. A restore-point field |
| `subjects.marks_spacing` | TEXT NULL | Rule JSON (below). NULL = no rules. Not part of a restore point (paper layout, not tags) |

Rule JSON (canonical form written by `marks_spacing.dumps`):

```json
{"before": {"bands": [{"max": 7, "mode": "lines", "count": 0},
                      {"max": null, "mode": "pages", "count": 1}],
            "per_mark": null},
 "after":  {"bands": [{"max": 2, "mode": "lines", "count": 4},
                      {"max": null, "mode": "pages", "count": 2}],
            "per_mark": {"base": 1, "lines_per_mark": 2, "max_lines": 40},
            "per": "part"}}
```

`after.per` (`marks_spacing.after_per`, default `part`): `part` = after every printed part on its own marks; `question` = once after the question's last printed entry on the whole-question total, with no gap between its parts (MATC style). `before` has no `per` (always once per question).

## Routes

| Method | Path | Authz | Purpose |
|---|---|---|---|
| GET | `/admin/subjects/<subject_id>/marks-spacing` | `@login_required` + `has_subject_access(subject_id)` | `{success, rules (canonical or null), can_edit}`. Read access so the Generate page can show the rules a paper will use |
| POST | `/admin/subjects/<subject_id>/marks-spacing` | `@login_required` + `@subject_admin_required` (+ explicit `_require_subject_admin`) | JSON `{rules}` (null / empty clears) → `{success, rules}`; 400 `{success:false, error}` on `MarksSpacingError` |
| POST | `/generate/api/marks-rules/check` | `@login_required` + `can_generate` | Validate rules edited for one paper, no write: JSON `{rules}` → `{success, rules (canonical or null)}`; 400 `{success:false, error}` |

**Per-paper edits.** Anyone who can generate may edit the rules in the Generate modal and click **Use for this paper**: the page keeps `{subject_id: rules | null}` (`window.oqbMarksRulesOverride`) and sends it as form field `marks_rules_override` with `POST /generate/create`. `parse_marks_rules_override` validates it (400 `{error}` on bad rules), drops subjects outside the selection, stores it in `generation_options['marks_rules_override']` (so **Regenerate** restores it) and in `spacing_config`; the job merges it over the saved rules (`null` = no rules for this paper). It never writes `Subject.marks_spacing`; only **Save as subject default** (subject admins, the POST above) does. Presets do not carry it.

Marks themselves are written through existing routes: `POST /admin/questions/<id>/update` (form field `marks`), `POST /admin/questions/batch-update` (`update_marks`, `marks`), the tags CSV import, Auto Tag, and the PDF import commit.

## Business rules / invariants

- **Marks live on leaves, like tags.** A stem's whole-question total is `whole_marks(stem)`: the sum of its non-stem descendants' marks (shared blocks add nothing). It is `None` only when no leaf has marks; `complete` is false when only some do. A part's whole-question total uses `question_root`, which stops at range children, so `Q16` under `Q16-17` is its own question.
- **Writers skip stems:** edit modal (input disabled; server ignores `marks` on a stem), Bulk Edit (`apply_resolved`), CSV import (warns and ignores), Auto Tag (stems skipped already), PDF commit (`plan_leaf_marks`).
- **Restore points:** `marks` is in `subject_snapshot.QUESTION_FIELDS`. Points taken before the column existed have no `marks` key, so a restore leaves live marks alone. Edit modal / Bulk Edit / CSV / Auto Tag capture as before; the PDF import commit captures one `pdf-import` point (`capture_committed`) before writing any marks, and writes none if that capture fails.
- **Sorting:** `marks` ("Marks (part)") sorts by the item's own marks; `total_marks` ("Marks (whole question)") by `question_marks(q)`, so every part of a 12-mark question sorts as 12. NULL follows `apply_multi_sort` (last ascending, first descending).
- **Rule resolution** (`marks_spacing.resolve`): bands sorted by `max` (inclusive; `null` = open-ended, last, at most one); first band with `marks <= max` wins; else `per_mark` gives `round(base + lines_per_mark × marks)` lines capped at `max_lines`; else `None`. `mode: "pages"` with `count` N = N page breaks (1–10). Lines 0–200, at most 12 bands per side.
- **Generator By-marks mode** (`resolve_entry_spacing`): rules are loaded once per job as `{subject_id: rules}` (`spacing_config['marks_rules']`), so a mixed-subject paper uses each question's own subject.
  - *Before* is evaluated on the whole-question total, only on the entry that starts a whole question (the stem, or the first leaf when the stem is not printed). Later parts get the fallback lines.
  - *After* is evaluated per leaf on its own marks. A stem has no after spacing in this mode.
  - With `after.per = question`: entries that do not end the whole question get 0 lines; the entry that ends it (`ends_question`, from a look-ahead over the render list by `question_root`) gets the rule on `question_marks`, or the card's lines when the total is unknown. The answers section (`apply_spacing_to_ans`) uses the same look-ahead over the leaf list.
  - No rules, unknown marks or no matching rule: the card's manual lines value.
  - A before-page after a page break emits N−1 more breaks (existing de-duplication); after-pages always emit N.
  - `apply_spacing_to_ans` applies the same resolution to the answers section; compact MC key blocks stay compact.
- **Options compatibility:** the mode values are `lines | page | marks`; anything else parses as `lines`. Old presets and `generation_options` stay valid.

## Settings & config keys

None. Limits are module constants (`marks_spacing.MAX_BANDS`, `MAX_LINES`, `MAX_PAGES`, `utils.MAX_MARKS`, `ai_prompts.TAG_MAX_MARKS = 100`). See [../core/06-system-settings.md](../core/06-system-settings.md).

## Permissions

Editing marks follows the existing question-tag authz (subject admin of the question's subject). Viewing rules: anyone with access to the subject. Editing rules: subject admins. See [../core/02-auth-and-permissions.md](../core/02-auth-and-permissions.md).

## Background work / SSE / threads

The generator thread loads the rules inside `_generate_in_background` (app context), after sorting. PDF import commit and Auto Tag are the existing SSE streams.

## Gotchas

- **A split question whose paper prints only one total:** put the total on the first part (there is no stem marks column). The whole-question total then still sums correctly.
- **Stem row in PDF import Step 2:** its number is the printed total used only for the parts-sum warning; commit never writes it. `carry_total_marks` keeps a pass-1 total on the stem when pass 2 splits a box.
- **Customised `TAG_FORMAT` / `PDF_*_JSON_CONTRACT` prompts** (AI Prompts page) do not get the new `marks` key. Reset the built-in or add `"marks"` by hand, otherwise the model never returns it (parsers tolerate its absence).
- **`clean_marks` never guesses:** `"2-3"`, fractions and values above 100 become `None`.
- The edit modal's whole-marks text comes from the details payload (`whole_marks: {total, complete}`); it does not update live while parts are edited elsewhere.

## Related

- [generator.md](generator.md) (spacing and options), [question-hierarchy.md](question-hierarchy.md), [subject-snapshots.md](subject-snapshots.md), [dashboard.md](dashboard.md), [admin-questions.md](admin-questions.md), [ai-tools.md](ai-tools.md), [ai-prompts.md](ai-prompts.md), [pdf-import.md](pdf-import.md), [pdf-agent.md](pdf-agent.md)
- [ADR-013](../decisions/ADR-013-subject-restore-points.md)
