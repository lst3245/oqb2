# ADR-011 — Printed page frame as the crop x-anchor

**Status:** accepted, in force. `pdf_layout.detect_page_frame`, `pdf_import.detect_frames` / `consolidate_frames` / `snap_boxes_to_frames` / `FrameCropper`, `POST /admin/pdf-import/frame`, and the **Snap boxes to page frame** review control are live. Shared overlay editing is `OQBBboxEditor`.

## Context

DSE answer books print a rectangle on every page ("answers written in the margins will not be marked"). Two facts break per-question left/right alignment:

- Scans drift a few percent left/right between pages. Odd and even booklet pages often differ (e.g. left rail at 0.052 vs 0.087 of page width on ICT 2023 P1B).
- Continuation pages have no margin question number, so the vision model boxes only the indented body. `_stitch_continuations` used to copy the head question's `x1/x2` onto the tail; that helps only when every page shares one offset.

"Uniform width per side" (lock every box to the widest box, left-anchored to each box's own `x1`) cannot fix either problem: a continuation that starts too far right stays too far right, and odd/even pages still disagree. White-trim on commit then re-narrows a snapped-looking crop back to the indented body.

The printed frame is a per-page fact that already exists on the paper. The library of past papers is mostly DSE answer-book scans; generic extraction has no such frame.

## Decision

- **Detect the printed frame per page at stage time.** `pdf_layout.detect_page_frame(gray)` is NumPy-only: column/row scores are the minimum dark fraction over three bands of the page (a full-height rule scores, text does not); only thin runs; a vertical rail is accepted only if a horizontal rule starts or ends at it (perpendicular partner) so scanner shadows are rejected. Returns `{left, top, right, bottom}` fractions (inner edges), `None` per rail if unconfirmed.
- **Consolidate per paper, not per page in isolation.** A paper has one physical frame width (median of pages with both rails). A page missing one rail gets it from the other + that width; a page with none inherits same-parity page medians (odd/even booklet offset), else the overall median. If fewer than 40% of pages show a frame the paper is frameless. Result lives in `meta[kind]['frames']` as `{box, source: detected|inferred|manual}` or `null`. `ensure_frames` computes this lazily for staging dirs created before frames existed.
- **Snap box `x1/x2` to the frame** (`snap_boxes_to_frames`, inset `PDF_IMPORT_FRAME_INSET_PCT`) after `_stitch_continuations` when `frame_snap=1` on `/detect` and `/agent` (exam only). The per-page frame wins over inherited head-question x. y is never touched. The review UI's **Snap boxes to page frame** (default ON) sends that flag so the saved plan matches the overlay. Rails are user-editable (`POST /admin/pdf-import/frame`); a **Frame** button can mark a page frameless or seed a frame from same-parity pages.
- **Commit through `FrameCropper`.** A box whose `x1/x2` sit within `tol` of the frame inner extent is "snapped": `crop_page(..., trim_axis='vertical')` so white-trim never re-narrows it, and when `PDF_IMPORT_FRAME_NORMALISE_WIDTH` is on the crop is LANCZOS-resampled (≤15% change) to the paper's median frame pixel width. Hand-widened boxes crop as before. `iter_commit`, `whole_source_crops`, and `export_zip_bytes` all go through it.
- **Demote uniform width to legacy.** `PDF_IMPORT_UNIFORM_WIDTH_DEFAULT` is OFF. Framed pages ignore it while frame snap is on; it still applies to frameless pages.
- **One overlay editor.** Review and Split-into-parts mount `OQBBboxEditor`; hosts own the item model and call `ed.restyle(item)`. No second canvas painter.

Rejected alternatives: widest-box uniform width (cannot align drifted pages or indented continuations); head-question x inheritance alone (fails when odd/even offsets differ); asking the LLM to return the frame (noisy, per-call, no paper-wide width); OpenCV Hough lines (adds an OpenCV dependency — NumPy projection profiles are enough).

## Consequences

- Staging `meta.json` grows a `frames` list aligned with `pages`. Old tokens without it are filled by `ensure_frames` on detect / commit / export / manual frame save.
- Detect and agent URLs accept `frame_snap`. Generic extraction does not snap (no exam frame).
- QUE box prompts now say the left edge is the numbered margin / inner frame edge, not the indented body (`_DEFAULT_PDF_QUE_BOX_SYSTEM`).
- New tunables: `PDF_IMPORT_FRAME_SNAP_DEFAULT`, `PDF_IMPORT_FRAME_INSET_PCT`, `PDF_IMPORT_FRAME_NORMALISE_WIDTH`. Spec: [../modules/pdf-import.md](../modules/pdf-import.md).
