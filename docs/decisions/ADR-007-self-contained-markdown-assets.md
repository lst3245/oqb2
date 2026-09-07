# ADR-007 — Markdown assets are single, self-contained files converted with pandoc

**Status:** accepted, in force.

## Context

Typed questions and solutions with mathematics needed an editable, diff-able source format that renders inline in the browser and still ends up in Word documents. Word sources (DOC) are opaque and Windows-bound; images are not editable. Multi-file Markdown (with sibling image files) breaks the one-file-per-asset-slot model the ingestor and asset routes rely on.

## Decision

- An `MD` asset is **one `.md` file** containing LaTeX math (`$...$`, `$$...$$`) and **base64 data-URI images**. No sibling files; `part_number` is always 1; a second MD for the same `(question, type, version)` slot is rejected.
- Browser rendering: `app/md_render.py` (markdown-it-py + bleach allow-list) produces sanitised HTML; math is typeset client-side with KaTeX. Rendered HTML is cached keyed on file mtime.
- Editing: EasyMDE in the Edit modal and a fullscreen page, with optimistic concurrency on file mtime so two admins cannot silently overwrite each other.
- Generation: pandoc converts MD to a temporary `.docx` which docxcompose appends to the master document; `PANDOC_PATH` locates the binary, `MD_MAX_SIZE_BYTES` caps upload size.
- Format priority for display/generation defaults to `IMG > MD > DOC` and is user-reorderable per generation.

## Consequences

- MD files can balloon (base64 images); the size cap and the LLM "generate Markdown" feature's image-embedding option exist because of this.
- Sanitisation is a security boundary: any change to the bleach allow-list or KaTeX handling needs review ([../modules/md-format.md](../modules/md-format.md)).
- pandoc is an external dependency; without it MD slots fall back to a placeholder in generated documents.
- Literal dollar signs in text must be escaped or normalised so they are not parsed as math.
