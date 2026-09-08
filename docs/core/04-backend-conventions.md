# 04 — Backend conventions

> The patterns every blueprint and service follows. When you add code, match these; when you invent a new convention, write it here in the same turn.

## Blueprints

| Blueprint | Module | Prefix | Owns |
|---|---|---|---|
| `auth_bp` | `app/auth.py` | `/` | `/`, `/login`, `/logout`, `/register` |
| `dashboard_bp` | `app/dashboard.py` | `/dashboard` | browse / filter / previews / Explain |
| `generator_bp` | `app/generator.py` | `/generate` | generation, status, download, lazy PDF, viewer |
| `user_bp` | `app/user.py` | `/user` | My Files, sections, shares, profiles, gen-profiles, sets |
| `admin_bp` | `app/admin.py` | `/admin` | all admin pages and admin JSON/SSE routes |
| `files_bp` | `app/files.py` | `/files` | root-aware file API + user browser page |
| `toolbox_bp` | `app/toolbox/__init__.py` (+ `pdf.py`, `markup.py`) | `/admin/toolbox` | hub, PDF Tool, Markup |
| `pwa_bp` | `app/pwa.py` | `/` | `/manifest.webmanifest`, `/sw.js` |

Register new blueprints in `create_app()`; keep one blueprint per area. Prefer adding a new module over growing `app/admin.py`.

## Route shape

```python
@admin_bp.route('/questions/<int:question_id>/something', methods=['POST'])
@login_required
@subject_admin_required          # or explicit object check, see core/02
def something(question_id):
    question = Question.query.get_or_404(question_id)
    denial = _require_md_admin(question)   # object-level authz when subject_id is not a param
    if denial:
        return denial
    data = request.get_json(silent=True) or {}
    ...
    db.session.commit()
    return jsonify({'success': True, 'message': '...', ...})
```

- Decorator order: `route` → `login_required` → authz decorator.
- Read params from URL kwargs, then `request.args`, `request.form`, or JSON (`request.get_json(silent=True) or {}`).
- Commit at the route (or service) boundary; on error `db.session.rollback()` and return an error.

## Response envelopes

There are two co-existing JSON styles; pick the one the surrounding blueprint uses and never mix within one route family:

| Style | Success | Failure | Used by |
|---|---|---|---|
| **`success` flag** | `{'success': True, ...payload}` | `{'success': False, 'message': '...'}` (often HTTP 200 or 400) | Edit-modal / asset routes, per-slot AI routes, My Files, sets |
| **`error` key** | `{...payload}` with 2xx | `{'error': '...'}` with 4xx/5xx | most list/data APIs (`/dashboard/api/*`, `/files/api/*`, settings, PDF tool) |

Datetimes in JSON: serialise naive-UTC with `utils.utc_iso(dt)` (`YYYY-MM-DDTHH:MM:SSZ`); the frontend formats with `oqbFormatLocalTime`.

HTMX: when `request.headers.get('HX-Request')` is present return only the partial (`templates/partials/...`); otherwise render the full page. Target containers by `id`.

## SSE (long-running admin operations)

Used by ingest, sync, batch IMG, MCQ ANS, AI check / generate-MD / solve / auto-tag, PDF import detect, PDF Tool export, Explain and chat streaming.

```python
def generate():
    yield f"data: {json.dumps({'type': 'info', 'message': 'Starting'})}\n\n"
    ...
    yield f"data: {json.dumps({'type': 'done', 'message': 'Finished', 'current': n, 'total': n})}\n\n"

return Response(generate(), mimetype='text/event-stream',
                headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
```

- Event shape: `{type: 'info'|'success'|'skip'|'error'|'done', message, current?, total?, ...extras}`; `done` ends the stream and the client closes the `EventSource`.
- Gather work **inside the request context**, then stream inside `with app.app_context():` where `app = current_app._get_current_object()` was captured before the generator runs (the request context is gone once streaming starts).
- Cancellation: the AI ops register a `job_id` in an in-process cancel registry (`app/ai_tools.py`) and expose `POST .../ai/cancel`; if you add a cancellable op, reuse that registry.
- Parallel fan-out for cloud LLM endpoints goes through `app/parallel.run_parallel(app, cancel, items, worker_fn, max_workers)`.
- Reverse proxies need `proxy_buffering off`.
- Long LLM-bound jobs that can sit silent for > ~60 s (PDF import AI agent) must **not** run on the SSE request thread: a dropped EventSource raises `GeneratorExit` and would abort the work. Run the pipeline on a daemon thread, keep events in memory, and have the SSE generator heartbeat (the Toolbox export stream is the same shape). The PDF agent keys the job by staging token so `/agent?attach=1` can rejoin.

## Background threads

```python
app = current_app._get_current_object()
def _worker():
    with app.app_context():
        ... read app.config[...] here, not before ...
threading.Thread(target=_worker, daemon=True).start()
```

Used by generation (`app/generator.py`), DOC thumbnails (`app/doc_thumbnails.py`), and batch ops. Read tunables inside the context so hot-reloaded System Settings apply. Word COM work must go through `app/word_com.py`, which serialises on a global lock.

## Identifiers

- **QID** `SUBJ_SOURCE_YEAR_PAPER_QNO` (past paper) or `SUBJ_QB_DETAIL_QNO` (question bank). `QNO` is `Q<n>`, `Q<n><part-path>`, or `Q<n>-<n>` (range stem). Parse/build only via `app.hierarchy.parse_qid` / `build_qid` / `QNO_TOKEN_PATTERN` — do not add another `Q\\d+` regex. Filename rules: [../reference/filename-convention.md](../reference/filename-convention.md). Tree rules: [../modules/question-hierarchy.md](../modules/question-hierarchy.md). `Question.id` (int) is used in URLs and JSON; `qid` is the stable human identifier.
- **Subject ids** are short uppercase strings and are immutable.
- **Asset identity** is the unique tuple `(question_id, asset_type, version, file_format, part_number)`.
- **Versions**: `app/utils.VERSIONS = ['EN','CH','BI','ENO','CHO']`, `VERSION_LABELS`, `TYPED_VERSIONS = ['EN','CH','BI']`, `OFFICIAL_VERSIONS = ['ENO','CHO']`, `DEFAULT_VERSION_PRIORITY`. `parse_version_priority(raw, legacy_preferred)` parses the comma list from forms and accepts the legacy single `preferred_language` value. Templates receive `OQB_VERSIONS` / `OQB_VERSION_LABELS` / `OQB_DEFAULT_VERSION_PRIORITY` from a context processor — build version UIs from these, never a hardcoded list.
- Asset selection for display/generation ranks candidates by `(format priority, version priority)`; default format priority `IMG > MD > DOC` is user-reorderable per generation (see [../modules/generator.md](../modules/generator.md)).

## Sorting (`app/utils.py`)

- `SORT_FIELDS` maps field → `{label, key(q), natural}`. Fields: `qid, qno, year, level, topic, subtopic, source, section, q_type, correct_percentage, chapter, subchapter, created_time`. `correct_percentage` sorts NULLs last via a `(0, v)/(1, 0)` tuple key. `qid` and `qno` use `hierarchy.sort_key` / `qno_sort_key` (tuples, `natural=False`) so `Q3ci` / `Q3civ` / `Q23-24` order correctly — do not natsort the raw QID string. `qno` is the integer start of the paper token (distinct from generation numbering). The admin list SQL `ORDER BY` for `qid`/`qno` mirrors that (roots before children, then `part_sort`).
- `apply_multi_sort(items, sort_config, group_order=None)` sorts **in Python** (natsort for `natural` fields, per-level direction via `cmp_to_key`). Do not move it to SQL `ORDER BY`.
- Manual block ordering: when the sort includes a grouping field (`GROUPING_FIELDS = topic, subtopic, chapter, subchapter`), `group_order = {"fields": [...], "order": [[ids...], ...]}` (integer ids, `0` = untagged) is prepended to the key; it is ignored unless `group_order['fields'] == grouping_fields_in_config(sort_config)`. `enumerate_sort_groups()` powers `POST /dashboard/api/sort-groups` and `POST /generate/api/sort-groups`. The frontend persists it as `sort_group_order` inside `SavedFilter.filter_data`, `SavedGenerationProfile.options_data`, and `GeneratedFile` blobs.
- Adding a sort field: add to `SORT_FIELDS` and to the dropdowns in `templates/dashboard.html` and `templates/generate.html`.

## Files and paths

- Every path under a root is produced by `storage.safe_join(base, *rel)`; a `None` result means "outside the root" → 403/404. Never use `startswith` checks.
- Stored `file_path` values are forward-slash relative paths from `SOURCE_PATH`.
- Serve files only through routes (`/dashboard/files/<path>`, `/generate/download/<id>`, `/files/api/download`, ...). Never expose `SOURCE_PATH` or `STORAGE_PATH` as a static folder.
- Details: [05-storage-and-paths.md](05-storage-and-paths.md).

## Configuration

Read tunables via `current_app.config[KEY]` (or `app.config` inside a thread context). Never `os.getenv` or `Config.X` at call time. Adding a tunable: [06-system-settings.md](06-system-settings.md).

## Errors and logging

- `abort(403)` for authz, `get_or_404` for lookups; JSON routes return the envelope above with a 4xx.
- Use `current_app.logger`; long operations report progress through SSE events, not print.
- Boot-time code (`create_app`) swallows exceptions on purpose so the app starts with a degraded DB; do not copy that pattern into request handlers.

## Tests

`unittest` modules in `tests/`, run with `python -m unittest discover -s tests`. Test pure helpers (parsers, sort keys, layout maths, prompt builders) with plain inputs; avoid `create_app()` (it touches the live DB). Template-level UX assertions exist in `tests/test_generate_template_ux.py` as a pattern for checking rendered HTML without a DB.
