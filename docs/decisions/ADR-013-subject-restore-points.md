# ADR-013 — Per-subject restore points captured before every tag / taxonomy save

## Status

Accepted.

## Context

In October 2026 a teacher editing the MATC topic list selected hundreds of questions in Bulk Edit and overwrote their tags. MariaDB runs with the binary log and general log off, so the server kept no change history; the only way back was a phpMyAdmin dump from two weeks earlier, parsed by hand and restored for one subject in a single transaction. A full-database restore was not an option because other subjects had legitimate edits since the dump.

Requirements that came out of it: undo must be **per subject**, cover the topic / chapter lists **and** every question's short tag fields (topics, subtopics, chapter, level, type, section, correct %), be cheap enough to run on every save, and be usable by the subject admin without a DBA.

Options considered:

1. **Turn on the binlog / scheduled `mysqldump`.** Useful as infrastructure backup, but point-in-time recovery is whole-database, needs a DBA, and does not answer "put MATC back the way it was ten minutes ago".
2. **Audit log of field changes** (row-level history table, or triggers). Precise, but restore means replaying inverse changes across many writers (ORM, Core bulk updates, cascades); triggers are invisible to the code and to agents.
3. **Snapshot the subject's whole tag + taxonomy state before each save, as one JSON row.** Simple to capture (5 column selects), simple to restore (diff and rewrite one subject), size is bounded (~65 bytes per question). **Chosen.**

## Decision

- New table `subject_restore_points (subject_id, user_id, action, note, payload MEDIUMTEXT, created_at)`; `payload` is versioned JSON written by `app/subject_snapshot.encode_state`.
- Every writer of the snapshotted fields calls `subject_snapshot.capture(subject_id, action)` **before mutating**, inside the caller's transaction. Per-row-committing writers (Auto Tag) use `capture_committed` up front. Subject admins can also take a manual point with a note; boot writes one `baseline` per subject that has none.
- Retention: newest 100 per subject plus the first point of each UTC day for 30 days.
- Restore is whole-subject, previewed first, one transaction, refused on cross-subject id conflicts, and always preceded by a `before-restore` point so it can be undone. Taxonomy rows keep their ids; newer rows survive when still referenced; deleted questions are not recreated.

## Consequences

- Undo granularity is "the state before save N" for one subject; there is no per-question or per-field undo. A restore reverts every hooked change made to that subject after the point.
- Every hooked save costs one extra state read (tens of ms) and ~50–90 KB of row for the larger subjects; 100 points ≈ 10 MB per large subject.
- The guarantee is only as good as the hooks. A new tag / taxonomy writer that forgets `capture` silently produces gaps; this is called out in the module doc, `docs/core/04-backend-conventions.md`, and a scoped rule.
- Long text fields (answer, comment, description) and asset state are out of scope; they need their own mechanism if ever required.
- Structural hierarchy operations (split / combine / set parent) do not capture; their tag moves are part of a structural change that a tag restore cannot express.

## Related

- [ADR-002](ADR-002-no-migration-framework-boot-patches.md) — boot-patch table creation
- [../modules/subject-snapshots.md](../modules/subject-snapshots.md)
