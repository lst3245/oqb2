# ADR-001 — Server-rendered Jinja + HTMX, no SPA

**Status:** accepted, in force.

## Context

OQB2 is an internal tool maintained mostly by AI agents in short sessions. Pages are form-heavy (filters, tag editors, admin tables) with a few rich islands (drag-to-reorder, modals, SSE logs, canvas tools). There is no frontend build toolchain on the host and no dedicated frontend developer.

## Decision

Render everything server-side with Jinja2 and Bootstrap 5. Use HTMX for partial updates (`hx-post` → partial template targeted by `id`), vanilla JS modules inline in templates, and a small set of shared helpers in `base.html`. Rich islands use CDN libraries (SortableJS, KaTeX, EasyMDE, Konva) loaded per page. No React/Vue, no bundler, no TypeScript.

## Consequences

- Any agent can change a page by editing one template and one route; no build step to break.
- Shared behaviour must be centralised deliberately (`base.html` helpers, `templates/partials/`) because there is no component system — see [../frontend/conventions.md](../frontend/conventions.md).
- The viewer and Markup pages opt out of `base.html` for fullscreen use and duplicate a few helpers on purpose.
- Do not introduce an SPA framework or build pipeline without superseding this ADR.
