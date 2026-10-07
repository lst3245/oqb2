# ADR-014 — One crop width mode for PDF import (frame > uniform width > model)

## Status

Accepted. `#cropWidthSel` in `templates/admin_pdf_import.html`, setting `PDF_IMPORT_WIDTH_MODE_DEFAULT`. Amends the "demote uniform width" bullet of [ADR-011](ADR-011-page-frame-crop-anchor.md).

## Context

After ADR-011 the review step had two independent checkboxes: **Snap boxes to page frame** (default on) and **Uniform width per side** (default off, "legacy"). Their four combinations were hard to predict, and the defaults were wrong for a whole class of paper:

- DSE answer books print a frame — snap works and uniform width is irrelevant.
- DSE multiple-choice papers (and many textbooks) have **no frame**. With snap on and uniform width off, every box kept the model's tight width, so each question was cropped to a different width. The teacher had to remember to tick the legacy box.
- Nobody could tell from the UI which of the two would win on a given page.

The underlying preference is a fixed priority: a printed frame is the best x-anchor; failing that, one width per side (the widest box, each box keeping its own left edge) gives consistent crops; the model's own edges are the last resort.

## Decision

- Replace both checkboxes with one select, **Crop width**:
  - `auto` (default): framed pages snap to the frame; frameless pages share the side's uniform width.
  - `uniform`: uniform width on every page; frames are ignored.
  - `model`: the model's own x, nothing applied.
- The server contract is unchanged: the client sends `frame_snap=1` only in `auto` (exam mode). `FrameCropper` at commit still decides "snapped" from the box geometry.
- One setting, `PDF_IMPORT_WIDTH_MODE_DEFAULT` (`auto` \| `uniform` \| `model`), replaces `PDF_IMPORT_UNIFORM_WIDTH_DEFAULT` and `PDF_IMPORT_FRAME_SNAP_DEFAULT`.
- A status line under the buttons says, per side, how many pages have a frame and what the mode will do with them, so the outcome is visible before detection runs.

Rejected alternatives:

- **Per-subject width profiles.** Papers inside one subject differ (Paper 1 MC frameless, Paper 2 framed), and `auto` already adapts per page from the detected frames.
- **Keep both toggles and only change the uniform default to on.** Still four combinations, and still unclear which one wins.

## Consequences

- Client code asks `frameSnapActive()` (auto, exam) and `uniformActive()` (auto or uniform). Uniform width in `auto` mode skips framed pages (`uniformApplies`).
- Generic extraction has no frames; Load PDF forces `model` there.
- Anyone who had set the two removed settings in System Settings loses those values; the DB rows are inert because the keys are no longer in `REGISTRY`.
- Spec: [../modules/pdf-import.md](../modules/pdf-import.md#crop-width-mode).
