# ADR-003 — Microsoft Word COM for DOC merge, PDF export, and DOC thumbnails

**Status:** accepted, in force. Windows + Word are hard requirements for these features.

## Context

Teachers author many solutions in Word with MathType OLE equations, embedded images, drawings, and native tables. Pure-Python approaches (python-docx, docxcompose, LibreOffice headless) either drop or corrupt MathType objects and reflow layouts. The host is a Windows machine with Word installed.

## Decision

Use Word itself through COM automation (`pywin32`) in `app/word_com.py`:

- **Merge** DOC/DOCX source assets into the generated master document with `Selection.InsertFile`, stripping the source's section/page setup so the master layout wins.
- **PDF output** via `Document.ExportAsFixedFormat` in the same Word session, produced lazily on request from My Files.
- **DOC thumbnails** by exporting the first page to PDF and rasterising with PyMuPDF (`app/doc_thumbnails.py`), cached at `DOC_THUMBNAIL_PATH/<asset_id>.png`.

Word is a single global resource: all COM work serialises on one lock (bounded wait `WORD_COM_LOCK_TIMEOUT`), each session starts a fresh `WINWORD.EXE` and quits it on exit, and orphaned Word processes are cleaned with `psutil`. A per-job watchdog (`WORD_COM_TIMEOUT`) is registered as a setting but not yet enforced.

## Consequences

- Fidelity for MathType/OLE content is exact; this is the reason the department can use the tool.
- The app cannot run these features on Linux or without Word; DOC slots render as placeholders and PDF requests are rejected there.
- Throughput is bounded by one Word instance; long queues manifest as lock waits, not parallelism.
- Agents must never spawn Word directly or run COM outside `app/word_com.py`, and must read the module doc before touching it: [../modules/doc-format.md](../modules/doc-format.md).
