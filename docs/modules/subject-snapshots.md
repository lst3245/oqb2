# Subject restore points (snapshots)

> Per-subject undo for tagging and taxonomy edits. Before every save that changes a subject's topic / chapter lists or its questions' tags, the subject's previous state is stored as a restore point; a subject admin can preview the difference against now and restore it in one transaction, without touching other subjects.

Built after an incident where a bulk edit overwrote the tags of hundreds of MATC questions and the only way back was a full `mysqldump` from two weeks earlier (binlog is off on this MariaDB, so the server keeps no change history). See [ADR-013](../decisions/ADR-013-subject-restore-points.md).

## Files

| File | Role |
|---|---|
| `app/subject_snapshot.py` | The service. State read (`read_state`), JSON codec (`encode_state` / `decode_state`), capture (`capture`, `capture_state`, `capture_many`, `capture_committed`), pruning (`select_prunable`, `prune`), boot `ensure_baselines`, `delete_for_subject`, listing (`list_points`, `point_meta`), restore planning (`plan_restore`, `summarize`, `gather_context`) and execution (`preview`, `apply_plan`, `restore`). Pure parts take plain dicts |
| `app/restore_points.py` | Blueprint `restore_points_bp` (`url_prefix='/admin'`): page, manual capture, preview, restore |
| `app/models.py` | `SubjectRestorePoint` |
| `app/__init__.py` | Boot patch: `create checkfirst` the table, then `subject_snapshot.ensure_baselines()` |
| `app/admin.py` | Capture hooks in every taxonomy / tag write route (list below); `_subjects_of(model, ids)` helper; `delete_subject` clears the subject's points |
| `app/subject_ai_service.py` | `set_description` captures before changing a taxonomy hint |
| `templates/admin_restore_points.html` | The page: subject picker, "Take snapshot" with note, points table, preview / restore modal |
| `templates/base.html`, `templates/admin_index.html`, `templates/admin_topics.html`, `templates/admin_chapters.html` | Admin dropdown → **Restore Points**; Content Management hub card; **Restore Points** header button on Topics / Chapters (same `subject_id`) |
| `tests/test_subject_snapshot.py` | Pure tests: codec, plan rules, summary, prune selection, tag-change detection |

## Tables

Schema detail: [../core/03-data-model-and-migrations.md](../core/03-data-model-and-migrations.md).

| Table / model | Columns | Notes |
|---|---|---|
| `subject_restore_points` (`SubjectRestorePoint`) | `id`, `subject_id` VARCHAR(10) idx (**no FK**), `user_id` FK `users.id` `ON DELETE SET NULL`, `action` VARCHAR(30), `note` VARCHAR(200) NULL, `payload` MEDIUMTEXT, `created_at` | One row = one subject's state **before** a save. `payload` is JSON from `encode_state`; never edit it by hand |

Payload v1 (`PAYLOAD_VERSION = 1`), compact JSON:

```json
{"v": 1, "subject_id": "MATC",
 "topics":      [{"id": 1, "name": "...", "sort_order": 1, "description": null}],
 "subtopics":   [{"id": 10, "topic_id": 1, "name": "...", "hidden": false, "sort_order": 1, "description": null}],
 "chapters":    [...], "subchapters": [{"id": 50, "chapter_id": 5, ...}],
 "question_columns": ["id","qid","major_topic_id","major_subtopic_id","chapter_id","subchapter_id",
                      "level","q_type","section","correct_percentage","minor_topic_ids","subtopic_ids"],
 "questions": [[1891,"MATC_DSE_2012_P2_Q1",33,28,null,null,null,"MC","A",94,[],[]], ...]}
```

What is **not** stored: `answer`, `comment`, question `description`, `verified*`, asset rows / check state, files, hierarchy (`parent_id`, `part`), other subjects.

Size on this host (Oct 2026): MATC 961 questions ≈ 90 KB, ECON 669 ≈ 55 KB; ~65 bytes per question. MariaDB `max_allowed_packet` is 1 MB here, so one subject tops out around ~14k questions before inserts fail.

## Routes

All `@login_required`. Prefix `/admin`.

| Method | Path | Authz | Purpose / shapes |
|---|---|---|---|
| GET | `/admin/restore-points` | admin; subject list from `get_user_admin_subjects()` | Page. Subject via `?subject_id=` → session `admin_taxonomy_subject` (shared with Topics / Chapters, `_resolve_taxonomy_subject`) → first admin subject; redirects to the canonical `?subject_id=` |
| POST | `/admin/subjects/<subject_id>/restore-points/capture` | subject-admin (id in URL) | Manual snapshot. JSON `{note?}` (≤ 200 chars, whitespace-collapsed) → `{success, point{id, subject_id, created_at, action, action_label, note, username}}`; 500 `{success:false, error}` |
| GET | `/admin/restore-points/<int:point_id>/preview` | admin + explicit `is_subject_admin(point.subject_id)` | Read-only diff vs now → `{success, point, preview}` where `preview = {subject_id, has_changes, conflicts[], questions_in_point, questions_now, questions_changed, questions_deleted_since, questions_added_since, field_counts[{field, count}], dropped_refs, taxonomy{topics|subtopics|chapters|subchapters: {label, recreate[], rename[{from,to}], other_updates, delete[], keep_newer[]}}, samples[{qid, changes[{field, from, to}]}] (≤ 40), samples_truncated}`; 404 / 403 / 409 (bad payload) |
| POST | `/admin/restore-points/<int:point_id>/restore` | admin + explicit subject check | Restore → `{success, message, before_point_id, summary}` (`summary` = preview shape, computed before applying). 409 `{success:false, error}` on conflicts (nothing written); 500 on failure (rolled back) |

## Business rules / invariants

- **A point is the state just before a save.** Every route that changes a field in the payload calls `subject_snapshot.capture(subject_id, action)` **before its first mutation**, in the same `db.session` transaction (no commit of its own). The route's commit persists the point and the edit together; a rollback drops both.
- **Read before mutate, always.** `read_state` uses Core column selects under `no_autoflush`, so it reads what is in the DB (plus anything already flushed in this transaction). Capturing after mutating would store the *new* state and silently defeat the feature.
- **Hooked writers** (action key → where):

  | Action | Writer |
  |---|---|
  | `topic-edit` | `add_topic`, `edit_topic`, `delete_topic`, `add_subtopic`, `edit_subtopic`, `toggle_subtopic_hidden`, `delete_subtopic`, `reorder_topics`, `reorder_subtopics` |
  | `chapter-edit` | `add_chapter`, `edit_chapter`, `delete_chapter`, `add_subchapter`, `edit_subchapter`, `toggle_subchapter_hidden`, `delete_subchapter`, `reorder_chapters`, `reorder_subchapters` |
  | `taxonomy-hint` | `subject_ai_service.set_description` |
  | `question-tags` | `update_question` (edit modal) — state read first, point kept only if `question_tags_differ` (answer / comment-only saves add no point) |
  | `batch-update` | `batch_update_questions` (dashboard Bulk Edit) — one point per subject in the selection, only when a tag flag is set |
  | `tag-import` | `import_question_tags` — on each subject's first CSV row, only when a snapshotted column is imported |
  | `topic-import` / `chapter-import` | `import_topics` / `import_chapters` — on each subject's first CSV row |
  | `auto-tag` | `ai_auto_tag` — `capture_committed` (own session, committed) **before** the stream starts, because `apply_tags` commits per question; failure → 500, nothing tagged |
  | `manual` | Take snapshot button |
  | `baseline` | boot, for any subject with no point yet |
  | `before-restore` | `restore`, same transaction as the restore |

- **Not hooked (by decision):** Split / Combine / create child / set parent (they move tags between a stem and its parts as part of a structural change), PDF import commit and ingestion (create new questions; nothing existing to lose), `batch_mcq_ans` (assets only). Question deletes are not undone by a restore.
- **Retention:** after each capture, `prune` keeps the newest `KEEP_PER_SUBJECT` (100) points **plus** the first point of each UTC day within `DAILY_ANCHOR_DAYS` (30), so a busy day cannot push out "start of day". Restore passes `keep_ids={point being restored}`. Pruning only ever deletes rows of the same subject.
- **Restore is subject-scoped and one transaction:** decode → read live → `gather_context` → `plan_restore` → abort on conflicts → `capture_state(live, 'before-restore')` → `apply_plan` (Core statements, every one filtered by `subject_id` / parent subject) → commit.
- **Restore rules** (`plan_restore`, pure):
  - Taxonomy rows keep their ids. Rows deleted since the point are **re-inserted with the original id**; rows in both are updated field by field (name, order, hidden, hint, parent).
  - Rows created since the point are deleted only if nothing references them after the restore (this subject's questions, other subjects' questions via `external_refs`, or a surviving child row); otherwise they are kept and listed as `keep_newer`.
  - A point id that now belongs to another subject (or is orphaned) is a **conflict**; restore refuses (409) and writes nothing.
  - Questions in both: scalar fields set to the point's values; minor-topic / subtopic links replaced. Questions deleted since are skipped. Questions created since keep their tags.
  - A point reference to a row that no longer exists anywhere is cleared (`dropped_refs`) rather than inserted.
  - Only fields listed in the payload's `question_columns` are touched — an older payload never blanks a field it did not record. Adding a field = append to `QUESTION_FIELDS` (and `REF_KIND` if it is a taxonomy ref); keep `decode_state` tolerant.
- **Restore is itself undoable:** the `before-restore` point holds the pre-restore state.

## Settings & config keys

None. Limits are module constants in `app/subject_snapshot.py`: `KEEP_PER_SUBJECT = 100`, `DAILY_ANCHOR_DAYS = 30`, `MAX_NOTE_CHARS = 200`, `PREVIEW_SAMPLE_LIMIT = 40`, `PAYLOAD_VERSION = 1`. See [../core/06-system-settings.md](../core/06-system-settings.md) if one ever becomes a tunable.

## Permissions

Subject admins (and super admins) of the point's subject. The page lists only `get_user_admin_subjects()`; capture uses `@subject_admin_required` with the id in the URL; `point_id` routes check `current_user.is_subject_admin(point.subject_id)` explicitly because the subject decorators pass through without a `subject_id`. See [../core/02-auth-and-permissions.md](../core/02-auth-and-permissions.md).

## Background work / SSE / threads

None of its own. `capture_committed` opens a private `Session(db.engine)` so Auto Tag's point is durable before the SSE stream starts and the request's `db.session` (whose loaded `qs` the stream reuses) is not committed or expired.

## Gotchas

- **Forgetting the hook is the main failure mode.** Any new route (or service) that writes topics / subtopics / chapters / subchapters, or a question's `major_topic_id`, `major_subtopic_id`, `chapter_id`, `subchapter_id`, `level`, `q_type`, `section`, `correct_percentage`, minor topics or subtopics, must capture first. Grep for the writer list in `.cursor/rules/subject-snapshots.mdc`.
- **Do not use ORM attribute history to decide "tags changed".** Autoflush (any `Topic.query.get` mid-route) resets history; `question_tags_differ` compares values against the pre-read state instead.
- **Writers that commit per row** (Auto Tag, any future SSE tagger) must use `capture_committed` before the first row, not `capture` — a pending point in `db.session` would be committed with the first row only by luck, and committing `db.session` early expires the objects the stream reuses.
- **Cost:** `read_state` is 5 queries per subject (~20–130 ms for MATC). Fine per save; do not call it per row inside a loop — capture once per subject per request.
- **Concurrent saves during a restore** by other users may land after the restore's read and be overwritten by it (no table locks). Restore when nobody else is editing the subject.
- **Payload size** is bounded by `max_allowed_packet` (1 MB on this host).
- Pruning and `delete_for_subject` are the only deletes. Never truncate or hand-edit `subject_restore_points`; never "restore" a subject by importing a full dump over the live DB when a point exists.
- Datetimes are naive UTC (`utc_iso`); the page formats with `oqbFormatLocalTime`. Daily anchors use UTC days (08:00 Hong Kong time).

## Related

- [ADR-013](../decisions/ADR-013-subject-restore-points.md) — why per-subject JSON snapshots before each save
- [admin-panel.md](admin-panel.md) (Topics / Chapters / Export-Import / subject delete), [admin-questions.md](admin-questions.md) (edit modal, Bulk Edit), [ai-tools.md](ai-tools.md) (Auto Tag), [subject-ai.md](subject-ai.md) (taxonomy hints)
- [../core/03-data-model-and-migrations.md](../core/03-data-model-and-migrations.md), [../core/04-backend-conventions.md](../core/04-backend-conventions.md), [../core/01-runtime-and-ops.md](../core/01-runtime-and-ops.md) (backups)
