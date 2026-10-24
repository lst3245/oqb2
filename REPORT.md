# Classical CV detection for PDF import

Worktree only (`feature/pdf-import-cv-detect`). Nothing here was pushed, and the live checkout at `D:\oqb2` was not edited.

## What changed

`DETECT_METHODS` is now `llm` and `cv`. The old assisted names `refine` and `segment` are gone from the UI and from the method list. If a request or a saved setting still says `refine` or `segment`, it is treated as `llm`.

`method=llm` is the same pipeline as before: the same prompts, one vision call per page, one retry on a model error, and the AI agent still hard-codes `llm`. The default in Setup stays **LLM only**.

`method=cv` never imports `llm_client`. For each staged page it:

1. Deskews the PNG in place when the page is tilted by at least 0.3°, so the boxes and the review image share the same pixels.
2. Limits the search to the printed page frame when one was found at staging time (or detects one).
3. Finds question-start anchors in the left number column (digit-sized ink, not a full-width rule, not a centred header). A part marker indented just under a question number is dropped. An optional local Tesseract reads that strip only. With no reading, numbers are assumed 1..N and the page is marked low-confidence.
4. Builds a box from each anchor (`segment_page`), then snaps the edges to ink (`refine_box`) without crossing the next anchor.
5. Applies a subject profile: trim blank ruling lines, extend a marks cluster, trim a centred footer. SOL keeps full width. The `mcq` profile also lowers confidence when the bottom does not look like options A–D. No subject is mapped to `mcq` yet; maths / science use `auto`, M1/M2, ICT and languages use `cq`.
6. Scores the page. At or below `PDF_IMPORT_CV_LOW_CONFIDENCE` (default 0.55) the page is low. Reasons include assumed numbers, gaps, overlaps, empty ink, a guessed number column, and a custom prompt that CV ignored.

Generic extraction cannot use CV (the route returns an error and does not call the model). A custom-prompt exam still runs CV, ignores the instruction, and caps confidence so the banner offers the LLM.

## How to try it

This worktree was not started as a server (the live app is already on port 5000, and starting this tree would run schema patches against the live database). To try the UI, check this branch out in a place that is allowed to serve, then:

1. Admin → PDF Batch Import. Load an exam PDF (not Generic extraction).
2. Open **Advanced** and choose **Classical CV (no model call)**. NumPy is required; the option is disabled without it. A vision endpoint is not required for this method.
3. **Detect questions**. The log should say `classical CV (no model call)`. Each page badge shows `CV 0.xx`. A deskewed page reloads its image.
4. If any page is low, a yellow banner appears. **Re-detect low pages with LLM** asks first, then calls the existing LLM method for those pages only. Nothing is sent until you click.
5. A single page can be re-run from its **Re-run** menu with either method.

Without Tesseract, every CV page is low (`qno_assumed`) and the banner offers the LLM. That is expected until the printed numbers can be read locally.

Pass 2 (split into parts) and the AI agent are still LLM-only. On the IMG Split page, Classical CV is only the optional “find the question on a full page” pass.

## Tests

`python -m unittest tests.test_pdf_cv_detect tests.test_page_frame tests.test_pdf_import_plan tests.test_i18n`

These do not call `create_app()`. The wizard itself was not clicked in a browser from this worktree.
