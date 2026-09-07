# My Files, Saved Search Profiles, Saved Generation Presets

> `user_bp` (`/user`): section-organised My Files for `GeneratedFile` rows (drag/drop, sharing, ZIP download, lazy PDF button), plus the two "options only" profile stores — `SavedFilter` (dashboard search profiles) and `SavedGenerationProfile` (Generate-page presets) — with shared starring / super-admin sharing conventions.

## Files

| File | Role |
|---|---|
| `app/user.py` | `user_bp`. Helpers: `_get_or_create_default_section`, `_ids_of_files_shared_with`, `_user_can_view_file`, `_user_owns_file`, `_serialise_section`, `_serialise_file_row`, `_apply_file_sort`, `_remove_file_and_pdf_sibling`, `_require_super_admin`. Route groups: `/profiles*`, `/gen-profiles*`, `/sets*` (see [question-sets.md](question-sets.md)), `/sections*`, `/files*`, `/shares*`. Constants `_VALID_SORT_FIELDS`, `_VALID_SORT_DIRS`, `_VALID_PAGE_SIZES = (5,10,25,50,100)`, `_SHARED_SECTION_ID = -1`. |
| `templates/my_files.html` | Sections + file rows UI: `sectionState` Map, SortableJS drag (files + sections), server-side search, bulk bar, share modal, lazy PDF button (`buildAndDownloadPdf`), 5 s auto-refresh while any `.status-generating` row exists, "Show all users" super-admin toggle. |
| `templates/saved_filters.html` | Manage page for search profiles (star / share / delete / bulk delete, Apply → `/dashboard/?profile_id=`). |
| `templates/saved_gen_profiles.html` | Manage page for generation presets. |
| `templates/dashboard.html` | Save-profile modal (`saveFilterProfile()` → `/user/profiles/save`), Load-profile dropdown (`#filterProfileMenu`, `loadFilterProfilesDropdown()`, `onFilterProfileSelect()`), `?profile_id=` / `?filter_data_id=` handlers. |
| `templates/generate.html` | Presets bar (load dropdown, Save-as-preset modal, Manage link); `getCurrentGenerationOptions()` / `restoreGenerationOptions()`. |
| `app/generator.py` | `generated_file_dir()`, `_pdf_sibling_filename()`, `download_pdf` (route `/generate/pdf/<id>`), and `create_document()` which assigns new files to the owner's default section. |
| `app/models.py` | `FileSection`, `FileShare`, `GeneratedFile`, `SavedFilter`, `SavedGenerationProfile`. |

## Tables

Schema: [../core/03-data-model-and-migrations.md](../core/03-data-model-and-migrations.md).

- `SavedFilter` (`saved_filters`): `user_id`, `name` (200), `filter_data` (Text JSON), `is_starred` (idx), `is_shared` (idx), `created_at`. No `updated_at`. **No upsert** — `profiles_save` always inserts a new row.
- `SavedGenerationProfile` (`saved_generation_profiles`): `user_id`, `name`, `options_data` (Text JSON, never contains `question_ids`), `is_starred`, `is_shared`, `created_at`, `updated_at`. Upsert by `(user_id, name)`.
- `FileSection` (`file_sections`): `user_id`, `name` (120, UNIQUE per user `uq_section_user_name`), `sort_order`, `sort_field` ∈ `{name, created_at, completed_at, question_count, manual}` (default `created_at`), `sort_direction` ∈ `{asc, desc}` (default `desc`), `page_size` (default 10), `collapsed`, `is_default` (idx), timestamps. Cascade-deleted with the user.
- `FileShare` (`file_shares`): `file_id?` (FK `generated_files` ON DELETE CASCADE), `section_id?` (FK `file_sections` ON DELETE CASCADE), `shared_by_user_id`, `shared_with_user_id` (idx), `created_at`. CHECK `ck_share_xor` (exactly one of file/section), UNIQUE `(file_id, shared_with_user_id)` and `(section_id, shared_with_user_id)`.
- `GeneratedFile` (`generated_files`): `display_name`, `filename`, `status` (`pending|generating|completed|failed`), `error_message`, `filter_data`, `generation_options`, `question_count`, `section_id` (FK ON DELETE SET NULL), `manual_position` (used when the section's `sort_field == 'manual'`), `created_at`, `completed_at`.

## Routes

All `@login_required`. Authz is inline; "owner" means `user_id == current_user.id`; super admin passes every owner check.

### Saved Search Profiles

| Method | Path | Authz | Purpose |
|---|---|---|---|
| GET | `/user/profiles` | login | Renders `saved_filters.html`. |
| GET | `/user/profiles/list?show_all=0` | login | Own + any `is_shared` profile, ordered `is_starred DESC, name ASC`. Super admin `show_all=1` = every profile. Rows `{id, name, subject, source_type, is_starred, is_shared, is_own, username (owner; null on own rows unless show_all), created_at}`. |
| POST | `/user/profiles/save` | login | Body `{name, filter_data}` (dict or JSON string). Inserts a new row (no upsert). 400 if either missing. Returns `{success, id}`. |
| GET | `/user/profiles/<int:id>/data` | owner / super admin / **any logged-in user if `is_shared`** | `{id, name, filter_data}` for the dashboard restore. |
| DELETE | `/user/profiles/<int:id>` | owner / super admin | Delete. |
| POST | `/user/profiles/bulk-delete` | per-row owner / super admin | Body `{ids:[]}`. |
| POST | `/user/profiles/<int:id>/star` | owner / super admin | Body `{is_starred?: bool}`; toggles if omitted. |
| POST | `/user/profiles/<int:id>/share` | **super admin only** | Body `{is_shared?: bool}`; toggles if omitted. |

`filter_data` shape = the dashboard `getCurrentFilterValues()` dict: `subject`, `source_type`, `years` (csv), `section`, `topics` (csv), `topic_mode`, `subtopics`, `subtopic_mode`, `show_hidden_subtopics`, `is_crosstopic`, `is_crosssubtopic`, `chapters`, `subchapters`, `show_hidden_subchapters`, `levels`, `q_type`, `qid_search`, `qid_strict`, `sort_config`, `sort_group_order`. The dashboard restores it via `convertProfileToSettings()` (csv → arrays, string bools → bools) → `restoreFilterSettings()` → HTMX submit.

### Saved Generation Presets

| Method | Path | Authz | Purpose |
|---|---|---|---|
| GET | `/user/gen-profiles` | login | Renders `saved_gen_profiles.html`. |
| GET | `/user/gen-profiles/list?show_all=0` | login | Own + shared, ordered `is_starred DESC, name ASC`; super admin `show_all=1`. |
| POST | `/user/gen-profiles/save` | login | Body `{name, options_data}`. Upsert by `(user_id, name)`; `question_ids` stripped server-side (and client-side). |
| GET | `/user/gen-profiles/<int:id>/data` | owner / super admin / any logged-in user if shared | `{id, name, is_starred, options_data}`. |
| DELETE | `/user/gen-profiles/<int:id>` | owner / super admin | Delete. |
| POST | `/user/gen-profiles/bulk-delete` | per-row owner / super admin | Body `{ids:[]}`. |
| POST | `/user/gen-profiles/<int:id>/star` | owner / super admin | Body `{is_starred?: bool}`; toggles if omitted. |
| POST | `/user/gen-profiles/<int:id>/share` | **super admin only** | Body `{is_shared?: bool}`; toggles if omitted. |

`options_data` shape = `GeneratedFile.generation_options` minus `question_ids` (table in [generator.md](generator.md)), including `sort_config`, `sort_group_order`, `version_priority`, `format_priority`, compact-MC options. Loading a preset calls `restoreGenerationOptions()` and never touches `question_ids` or `filter_data` bound to the current selection; it does restore sort settings because they are part of the preset.

### Sections

| Method | Path | Authz | Purpose |
|---|---|---|---|
| GET | `/user/sections?show_all=0` | login | Ensures the default section exists, then lists own sections ordered `is_default DESC, sort_order, created_at`, each with `file_count`, sort + page state, `is_default`, `is_shared_in`, `owner_username`. Appends a virtual `{id:-1, name:'Shared with me', is_shared_in:true, file_count}` row only when at least one share targets the caller. Super admin `show_all=1`: every user's sections (own first, then by `user_id`), `owner_username` set on foreign rows, file counts across all owners, **no** virtual row. |
| POST | `/user/sections` | login + `can_generate()` (403) | Body `{name}`. 409 on name collision. Creates `is_default=False`. |
| PATCH | `/user/sections/<int:id>` | owner / super admin | Partial `{name?, sort_field?, sort_direction?, page_size?, collapsed?}`. Default section: 400 on rename, but sort / page_size / collapsed are editable. 409 on name collision; values validated against `_VALID_*`. |
| DELETE | `/user/sections/<int:id>` | owner / super admin | 400 for the default section. Contained files are moved to the owner's default section. |
| POST | `/user/sections/reorder` | caller's own sections only (foreign IDs silently dropped) | Body `{ids:[...]}` → `sort_order = index + 1`; the default section is skipped and pinned at 0. |

### Files

| Method | Path | Authz | Purpose |
|---|---|---|---|
| GET | `/user/files` | login + `can_generate()` (403 otherwise) | Renders `my_files.html`; auto-creates the default section. |
| GET | `/user/files/list?section_id=&page=&show_all=&q=&page_size=` | section owner / super admin (403); `-1` for shared | Paginated single-section listing → `{section_id, page, page_size, total, pages, files:[row]}`. `q` = case-insensitive substring on `display_name` OR `filename`, applied before sort + paginate. `section_id=-1` returns files visible via `_ids_of_files_shared_with`, sorted `created_at desc`, `page_size` clamp 5–100 (default 10), rows flagged `is_read_only=true` with `shared_by`. Real sections use the section's `sort_field/sort_direction/page_size`; `show_all=1` (super admin) includes other owners' files in that section with `username` set. |
| GET | `/user/files/<int:id>/filter` | `_user_can_view_file` | `{filter_data}` for the dashboard **Re-filter** button (`?filter_data_id=`); 404 when the row has no filter data. |
| GET | `/user/files/<int:id>/generation_options` | `_user_can_view_file` | `{generation_options, display_name, filter_data}` for regeneration. |
| POST | `/user/files/<int:id>/move` | `_user_owns_file`; target section must belong to the file's owner (403 "Section belongs to another user") | Body `{section_id}`. |
| POST | `/user/files/bulk-move` | per-row `_user_owns_file` | Body `{ids:[], section_id}`; non-owned IDs skipped silently. |
| POST | `/user/files/<int:id>/rename` | `_user_owns_file` | Body `{display_name}`; DB only, disk filename unchanged; 400 if empty. |
| POST | `/user/files/reorder` | section owner / super admin; each file must be in that section and owned | Body `{section_id, ids:[]}` → each file's `manual_position = index`; the section switches to `sort_field='manual', sort_direction='asc'`. |
| DELETE | `/user/files/<int:id>` | `_user_owns_file` | Deletes the row plus the on-disk file **and any cached PDF sibling** (`_remove_file_and_pdf_sibling`). |
| POST | `/user/files/bulk-delete` | per-row `_user_owns_file` | Body `{ids:[]}`. |
| POST | `/user/files/bulk-download` | per-row `_user_can_view_file` | Body `{ids:[]}` → in-memory ZIP `my-files-<ts>.zip` (`application/zip`). Skips non-completed, non-visible and missing-on-disk rows; names are `display_name + ext` with illegal chars replaced and ` (2)`, ` (3)` dedupe. 400 if nothing downloadable. |
| GET | `/generate/pdf/<int:id>` (in `generator_bp`) | `_user_can_view_file` | **Lazy PDF**: serve cached sibling or build synchronously via Word COM. JSON errors 403 / 409 / 400 / 404 / 503 / 500. See [generator.md](generator.md). |

File row shape (`_serialise_file_row`): `{id, display_name, filename, file_ext, status, error_message, question_count, has_filter, has_generation_options, format_priority_top, output_format, pdf_supported, pdf_available, username, shared_by, is_read_only, section_id, size_bytes, created_at, completed_at}`. `pdf_supported` = filename is `.docx`/`.zip` and status completed; `pdf_available` = sibling exists on disk (checked in `generated_file_dir(gf)`).

### Sharing (super admin only — `_require_super_admin()` returns 403 JSON)

| Method | Path | Purpose |
|---|---|---|
| GET | `/user/shares?file_id=` or `?section_id=` | Exactly one param (400 otherwise). `{shares:[{id, user_id, username, created_at}], available_users:[{id, username}]}`; `available_users` excludes the owner and current targets. 400 for the default section. |
| POST | `/user/shares` | Body `{file_id? | section_id?, user_ids:[]}`. Replace semantics: adds missing rows, deletes rows not in `user_ids`, no-ops existing. Owner cannot be a target. 400 for the default section. |
| DELETE | `/user/shares/<int:id>` | Revoke one row. |
| GET | `/user/shares/users` | All users for the picker. |

## Business rules / invariants

- **Default ("Latest") section invariant**: `_get_or_create_default_section(user_id)` lazily creates the `is_default=True` row on `/user/files`, `/user/sections`, and in `create_document()`. It is race-safe (rollback + re-query on the unique-constraint race) and attaches any of the user's `section_id IS NULL` files to the new default. New files always land there; deleting another section moves its files back to the default. The default cannot be renamed, deleted, reordered, or shared.
- **Sharing is transitive for sections**: `_ids_of_files_shared_with` resolves section shares at query time, so files moved into a shared section become visible to recipients automatically (and disappear when moved out).
- **Read vs write gates**: `_user_can_view_file` (owner / super admin / direct or section share) for read + PDF + bulk download; `_user_owns_file` (owner / super admin) for move / rename / delete / reorder. Recipients see `is_read_only` rows in the virtual "Shared with me" section and cannot mutate them.
- **View-only users** (`can_generate() == False`) get 403 on `/user/files` and cannot create sections, even if shares target them.
- **Status lifecycle**: `pending → generating → completed | failed`. The page polls every 5 s but only re-fetches sections that contain a `.status-generating` row.
- **Manual ordering**: `/files/reorder` writes `manual_position` and flips the section to `sort_field='manual'`, so the order the user just made is what they see.
- **Rename is DB-only**: disk `filename` never changes; download names use `display_name`.
- **Lazy PDF**: rows expose `pdf_supported` / `pdf_available`; the button is an `<a href>` to `/generate/pdf/<id>` when cached, else a `<button>` running `buildAndDownloadPdf()` (fetch + spinner + blob save + section reload). Hidden when `pdf_supported=false` (legacy `.pdf` rows). Deleting a row removes the sibling too.
- **Starring**: `is_starred` on all three profile tables; list endpoints return starred first; Dashboard / Generate dropdowns group them under a Starred optgroup.
- **Sharing profiles/presets**: only super admins toggle `is_shared`; shared rows appear in every user's lists and dropdowns ("Shared by admins" group with the owner's name), show a green Shared badge + owner username on manage pages, and non-owners cannot delete or star them. `/data` for shared profiles/presets is open to any logged-in user (unlike question sets, which also require subject access).
- **Profiles are options only**: `SavedGenerationProfile.options_data` never contains `question_ids` (stripped both sides); `SavedFilter` never stores a selection.

### Frontend conventions (`my_files.html`)

- Per-section state in a `sectionState` Map keyed by `section_id`; polling, bulk bar and toasts are plain JS (no HTMX).
- File drag: `Sortable({group:'oqb-files'})` per `<tbody class="section-files-tbody" data-section-id>`. Cross-section drop → `onAdd` → `/user/files/<id>/move`; same-section → `onUpdate` → `/user/files/reorder`. Rows with `data-readonly="1"` are filter-blocked; the Shared-with-me tbody sets `put:false, pull:false`.
- Section drag: `Sortable({group:'oqb-sections'})` on `#sectionsContainer`, filtered on `.is-default, .is-shared` so those are pinned; their `.section-drag-handle` is hidden via CSS.
- Both sortables use **`preventOnFilter: false`** — otherwise SortableJS calls `preventDefault()` on every mouse event inside filtered cards and kills native `<select>` and dropdown toggles in their headers.
- `onStart`/`onEnd` toggle `body.oqb-dragging` (`user-select: none !important`) to suppress text highlighting during drags.
- **Search is server-side**: the top-bar input sets `currentSearch`, debounced 250 ms, and reloads every loaded section with `q=`; badges pick up the filtered `total`; empty state reads `No files match "<query>"`; match highlighting inside rows is client-side (`<mark class="oqb-search-hit">`).
- **Show all users** (`#showAllToggle`, super admin): `reloadAll()` passes `show_all=1` to `/user/sections` and every `/user/files/list`; foreign sections show a blue `@username` pill; the Move-to menu lists every visible section so admins can reorganise other users' files in place.
- Header layout: title row (Show-all switch + Refresh on the right) above a `.card` toolbar with the search input (`flex: 1 1 240px`) and **New section** button.
- Row actions: Download (`/generate/download/<id>`), PDF, Re-filter (`/dashboard/?filter_data_id=<id>`), Regenerate (`/generate/?regen_file_id=<id>`), Rename, Move, Share (super admin), Delete.

### Dashboard "Load profile" dropdown

A `bi-folder2-open` button next to the save (`bi-floppy`) button in the Filters card header opens `#filterProfileMenu`, built by `loadFilterProfilesDropdown()` on `DOMContentLoaded` and refreshed after a save. Sections: **Starred**, **My profiles**, **Shared by admins**, then a fixed "Manage profiles" footer link. Items carry `data-profile-id`; click → `onFilterProfileSelect(id)` → `GET /user/profiles/<id>/data` → `convertProfileToSettings()` → `restoreFilterSettings()` → `htmx.trigger(filterForm, 'submit')`.

## Settings & config keys

| Key | Read in | Purpose |
|---|---|---|
| `OUTPUT_PATH` | `files_list` (passed as the "do disk checks" gate to `_serialise_file_row`), `generator.generated_file_dir` | Legacy output dir; actual per-file dir is resolved by `generated_file_dir()` (`User/<name>/generated` first). |
| `STORAGE_PATH` (via `app/storage.py`) | `generated_file_dir` | Root of the per-user storage tree. |
| `WORD_COM_LOCK_TIMEOUT` | lazy PDF build in `app/generator.py` | Word lock wait. |

Runtime-tunable keys: [../core/06-system-settings.md](../core/06-system-settings.md).

## Permissions

| Action | Owner | Super admin | Share recipient | Viewer role (`can_generate()==False`) |
|---|---|---|---|---|
| Open `/user/files`, create sections | yes | yes | yes if they can generate | 403 |
| List / read own rows | yes | yes (+ `show_all`) | read-only rows in "Shared with me" | n/a |
| Move / rename / reorder / delete files, edit sections | yes | yes | no | no |
| Bulk download, lazy PDF, `/filter`, `/generation_options` | yes | yes | yes | n/a |
| `/generate/download/<id>` | yes | yes | **no** (owner/super-admin only — see gotchas) | no |
| Manage shares | no | yes | no | no |
| Star / delete profiles & presets | yes | yes | no | yes for own |
| Toggle `is_shared` | no | yes | no | no |

Details: [../core/02-auth-and-permissions.md](../core/02-auth-and-permissions.md).

## Background work / SSE / threads

- No SSE. `my_files.html` polls `/user/files/list` for sections containing `.status-generating` rows every 5 s.
- Lazy PDF (`/generate/pdf/<id>`) runs Word COM synchronously in the request thread under the global Word lock.
- File generation itself happens on the generator's background thread ([generator.md](generator.md)); this module only reads `status`.

## Gotchas

1. **Never bypass `generated_file_dir()`** when touching files on disk — rows may live in `User/<name>/generated` or legacy `OUTPUT_PATH`.
2. **Delete must remove the PDF sibling** (`_remove_file_and_pdf_sibling`); otherwise orphaned `.pdf` / `.pdf.zip` files accumulate.
3. **Default section is special everywhere**: cannot be renamed, deleted, reordered (pinned at 0) or shared; it is created lazily on three code paths — keep them consistent.
4. **Section shares are resolved at query time**, not materialised; do not cache `_ids_of_files_shared_with` across requests.
5. **`preventOnFilter: false` on both Sortables is load-bearing**; removing it breaks selects/dropdowns in the Latest and Shared-with-me headers.
6. **`/files/reorder` flips the section to manual sort**; a later PATCH to another `sort_field` discards the manual order visually but keeps `manual_position` values.
7. **`profiles_save` is insert-only** (duplicate names allowed); `gen_profiles_save` and `sets_save` are upserts. Do not assume symmetry.
8. **Shared profile/preset `/data` is open to any logged-in user**; shared question sets additionally require subject access.
9. **Download asymmetry**: `/generate/download/<id>` rejects share recipients while bulk download and lazy PDF accept them. Known inconsistency; fix in `download_file` (use `_user_can_view_file`) if it is ever addressed.
10. **`show_all` omits the virtual Shared-with-me row** by design; the admin sees the source files directly.
11. **Saved search profiles store the dashboard's raw `getCurrentFilterValues()`**, including the forced `is_crosstopic=true` when AND mode implied it (see [dashboard.md](dashboard.md)).
12. **Presets carrying `output_format: 'PDF'` are legacy** and ignored by `restoreGenerationOptions()`; PDF is lazy from My Files.

## Related

- [generator.md](generator.md) — creation of `GeneratedFile` rows, `/generate/download`, `/generate/pdf`, regen (`?regen_file_id=`), presets bar.
- [dashboard.md](dashboard.md) — `?profile_id=` / `?filter_data_id=` handlers, `getCurrentFilterValues()`.
- [question-sets.md](question-sets.md) — sibling `SavedQuestionSet` API with the same star/share conventions.
- [../decisions/ADR-001-server-rendered-htmx-no-spa.md](../decisions/ADR-001-server-rendered-htmx-no-spa.md) (My Files is plain fetch + JS, not HTMX)
- [../decisions/ADR-003-word-com-for-doc-merge-and-pdf.md](../decisions/ADR-003-word-com-for-doc-merge-and-pdf.md)
- [../core/02-auth-and-permissions.md](../core/02-auth-and-permissions.md), [../core/04-backend-conventions.md](../core/04-backend-conventions.md), [../core/06-system-settings.md](../core/06-system-settings.md)
