# ADR-008 — Dashboard Selection is independent of the Filter

**Status:** accepted, in force.

## Context

Teachers build a paper by running several different filters and picking a few questions from each. Early behaviour cleared or trimmed the selection whenever the filter changed, and "select all" silently meant "select the current page". Users lost work and could not compose sets.

## Decision

- The **Selection** is a client-held set of question ids (`localStorage['oqb_selectedQuestions']`) that persists across filter changes, pagination, HTMX swaps, and page reloads until the user clears it. Changing the filter never mutates the selection. The **one exception** is changing the subject: selections are subject-tied (generation cannot mix subjects), so a subject switch clears the selection.
- **Show Selected Only** paginates the full selection (not the intersection with the current filter).
- **Set Operations** compose sets explicitly: chips for the live Selection, the current Filter Result, browser-only Scratch Sets, and saved Question Sets; operators are union, intersection, difference; the result can replace the selection or be saved as a Question Set.
- Saved Question Sets store materialised id lists per subject, not formulas.

## Consequences

- Selection state and filter state are separate objects in `dashboard.html`; never derive one from the other implicitly.
- Bulk actions (generate, export tags, batch update) must state which set they act on; "selected" always means the Selection.
- Stale selections (ids of deleted questions) must be tolerated by every consumer.
- Details: [../modules/dashboard.md](../modules/dashboard.md), [../modules/question-sets.md](../modules/question-sets.md).
