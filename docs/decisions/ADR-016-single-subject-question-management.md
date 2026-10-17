# ADR-016 — Question Management works on one subject at a time

**Status:** accepted, in force since 2026-10-17. Page, APIs, bulk guard and tests are live.

## Context

`/admin/questions` listed every question of every subject the user administers in one flat table. Search, "select all N matching" and the bulk tools all worked across subjects, so a super admin could select the whole library and delete it, or run Auto-tag with overwrite on every subject, in a few clicks. The bulk routes only intersected the ids with the caller's admin subjects; a super admin administers all of them, so nothing stopped a cross-subject action. Parts were flat rows next to their stems, so "select a question" meant hunting for its parts by hand. Each tool had its own modal with its own confirm rules (an "I understand" tick below 10 rows, typing `DELETE` above).

Options considered:

1. **Keep the global list, add warnings.** A typed confirm is still one paste away from wiping every subject, and the list query scanned the whole table.
2. **Subject filter with an "All" option.** Same risk; "All" becomes the default people leave on.
3. **One subject per page state, enforced on the server too.** Chosen.

## Decision

- The page has a subject picker with **no "All" option**. Nothing loads until a subject is chosen; the last one is remembered (`localStorage.oqb_adminQuestionsState`, with filters kept per subject). Changing subject clears the selection after a confirm.
- List APIs (`/questions/api/tree`, `/facets`, `/ids`, legacy `/list`) **require** `subject` and 403 a subject the caller does not administer.
- Every bulk route the page calls runs `_bulk_subject_guard(ids)`: with `subject_id` every id must be in it, without one the ids must not span subjects; otherwise 400 `outside_subject` / `mixed_subjects`. This holds for super admins too. Pure check: `app/question_scope.ids_subject_violation`.
- The list is a **tree**: one row per whole question, parts nested and collapsed; ticking a row selects its subtree. Paging is by root.
- Hand-offs that carry ids (dashboard selection, Health / Subject AI QID links) are resolved with `/questions/api/subjects-of`: one subject → switch and pin; several → the user picks one.
- All bulk tools run in one panel (Configure → Review → Run). Review lists the affected rows and runs the server dry run where one exists; destructive actions (delete questions / assets, combine, move / overwrite copies, Auto-tag overwrite) need the subject code typed; any other write over 100 rows needs a tick.

## Consequences

- A cross-subject bulk action is no longer possible from this page or by replaying its requests with mixed ids. Doing the same thing in two subjects means two runs.
- The dashboard Bulk Edit (`/questions/batch-update`) is a different page and is not covered by the guard; its preview-then-apply flow is the safeguard there.
- `/questions/api/tree` reads all `(id, parent_id)` pairs of the subject to group matches by root (~0.2 s for 3.6k ICT rows). That is fine per subject and is one more reason not to bring back a global list.
- Old bookmarks with `?qids=` keep working; they now land in the subject the QIDs belong to.

Details: [../modules/admin-questions.md](../modules/admin-questions.md). Related: [ADR-008](ADR-008-selection-independent-of-filter.md) (dashboard selection), [ADR-009](ADR-009-question-hierarchy-over-linking.md) (tree).
