# ADR-006 — "Version" replaces "language"; five versions with a user-ordered priority

**Status:** accepted, in force.

## Context

Assets were keyed by `language ∈ EN | CH | BI`. The department also keeps the **official** public-exam scans, which are neither a typed English nor a typed Chinese version: they are reference material (used for proofreading typed versions) that some users still want to view or print when nothing else exists. A single "preferred language" setting could not express "typed English, else typed bilingual, else official scan".

## Decision

- Rename the column and concept to **version** with the enum `EN, CH, BI, ENO, CHO` (`ENO`/`CHO` = English/Chinese official scans).
- Canonical list, labels, and default priority live in `app/utils.py` (`VERSIONS`, `VERSION_LABELS`, `DEFAULT_VERSION_PRIORITY`, `TYPED_VERSIONS`, `OFFICIAL_VERSIONS`) and reach templates via a context processor; no template or JS hardcodes the list.
- Users express preference as an **ordered priority list** (drag-to-reorder widget, hidden comma input `version_priority`); the first version present wins. Legacy `preferred_language` values are still accepted by `parse_version_priority`.
- Only typed versions count toward proofreading status rollups; official versions are the reference side of a check.
- Filename token order in the ingest regex lists `ENO|CHO` before `EN|CH`.

## Consequences

- Every place that iterates versions must use the canonical list (Edit modal tabs, ingest, generation, viewer, AI Tools).
- Adding a version is a one-line change in `app/utils.py` plus an enum widening boot patch — and a doc update here.
- Stored JSON blobs may still carry `preferred_language`; readers must keep accepting it.
