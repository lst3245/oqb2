# Markup (drawing PWA)

> Standalone, mobile-first drawing tool at `/admin/toolbox/markup` for handwriting solutions over imported images on an infinite Konva canvas. Available to every logged-in user, installable as a root-scoped PWA, autosaves locally in IndexedDB, exports a crop-to-content PNG, and receives Android Web Share Target images. No server-side persistence.

## Files

| File | Role |
|---|---|
| `app/toolbox/markup.py` | Routes: page, iOS Shortcut download (`build_ios_share_shortcut`), share-target fallback redirect. Registered on `toolbox_bp` (`/admin/toolbox`). |
| `app/pwa.py` | `pwa_bp` at root scope: `/manifest.webmanifest` and `/sw.js` (adds `Service-Worker-Allowed: /`, `Cache-Control: no-cache`). Flask's static route cannot set that header, hence the dedicated routes. |
| `templates/markup.html` | The whole app (HTML + CSS + JS). Does NOT extend `base.html` so the canvas owns the viewport and touch gestures. |
| `templates/admin_toolbox.html` | Hub card linking to `toolbox.markup_tool` (shown to all users). |
| `templates/viewer.html` | Present mode integration: `queMarkupBtn`/`ansMarkupBtn` → `openMarkupForPanel` opens `/admin/toolbox/markup?img=<url>` in a new tab. |
| `static/markup/manifest.webmanifest` | PWA manifest, `"scope": "/"`, `share_target.action = /admin/toolbox/markup/share-target`. |
| `static/markup/sw.js` | Service worker: intercepts share-target POSTs, stores the image in Cache API, redirects 303 to `?shared=1`. |
| `static/markup/icon.svg` | PWA icon. |
| `static/markup/Markup-OQB-signed.shortcut` | Optional. If present, served instead of the generated unsigned Shortcut. |

No build step: Konva (CDN), `perfect-freehand` (jsDelivr ESM via non-blocking dynamic `import()`), `idb-keyval@6` (CDN UMD), Bootstrap Icons (CDN).

## Tables

None. Markup stores nothing server-side. (Schema reference: `../core/03-data-model-and-migrations.md`.)

## Routes

| Method | Path | Authz | Purpose |
|---|---|---|---|
| GET | `/admin/toolbox/markup` | `@login_required` | Renders `markup.html` with `ios_shortcut_download_url` and `markup_normalized_max_dim` (int from `MARKUP_NORMALIZED_MAX_DIM`, default 2400). Query params handled client-side: `?img=<same-origin-url>`, `?shared=1`, `?ios-share=paste`, `?ios-share=hash#<BASE64>`. |
| GET | `/admin/toolbox/markup/ios-shortcut.shortcut` | `@login_required` | Downloads `Markup-OQB.shortcut`. Prefers the signed file in `static/markup/`; otherwise builds an unsigned binary plist via `build_ios_share_shortcut(request.url_root)` (`Cache-Control: no-store`). |
| POST | `/admin/toolbox/markup/share-target` | `@login_required` | Fallback when the service worker is not active: redirects to `toolbox.markup_tool?shared=1` (the image is lost; the SW path is the real one). |
| GET | `/manifest.webmanifest` | none | `application/manifest+json` from `static/markup/`. |
| GET | `/sw.js` | none | `application/javascript` with `Service-Worker-Allowed: /` and `Cache-Control: no-cache`. |

## Business rules / invariants

### Canvas and tools

- Layers: background images `bgLayer`, drawing nodes `drawLayer`, selection UI `uiLayer`.
- Infinite canvas = stage position + scale. One pointer draws; two pointers pan/pinch-zoom. Hand tool pans with one pointer.
- Tools: pen, highlighter, stroke eraser, lasso select/move/delete, text, line/rect/ellipse. Pen and highlighter keep independent size/colour settings; lock-size starts enabled. Highlighter defaults to a larger yellow-first palette. Brush size auto-defaults relative to the first imported image (`autoConfigureSizes(maxWorldDim)`); the size slider shows a live preview circle (`showSizePreview`).
- Eraser and lasso hit-test **actual geometry**, not bounding boxes: eraser uses point-to-segment / shape-outline distance (`eraserHitsNode`); lasso uses `nodeIntersectsPolygon` and supports tap-to-select. Stroke selections draw a dashed highlight rect (`updateSelectionRect`); image-only selections use the Konva `Transformer` with resize anchors (`syncSelectionUi`, `selectionIsImageOnly`).
- Snap mode shows a grey preview after the hold threshold, then replaces the held pen stroke with a recognised perfect shape on release. `buildSnappedShape(points, width, opts)` compares `rectangleScore` vs `ellipseScore` so squares stay squares and circles stay circles; returns `null` (keep freehand) when neither fits.
- Undo/redo is operation-based (`add`, `remove`, `modify`) over serialised Konva node attrs.
- Imported background images live on `bgLayer`, are listening-enabled, and can be selected/moved/resized/deleted with the lasso tool.
- Imports are **resolution-normalised** in `loadBackgroundImage`: node world width/height are scaled so the longest edge equals `NORMALIZED_MAX_DIM` (Jinja-injected from `MARKUP_NORMALIZED_MAX_DIM`), regardless of source pixel size. The full-res source image is still drawn, so export quality is preserved. `autoConfigureSizes` derives default pen/highlighter sizes from that world dimension.

### Autosave

- IndexedDB via `idb-keyval`, key `AUTOSAVE_KEY = 'oqb:markup:autosave:v1'`. Per device/browser; nothing is sent to the server.
- Saves are debounced (400 ms) and serialised through `saveInFlight`; `readAutosave()` awaits any pending write and times out after 5 s so a rapid mobile reload cannot hang init. Flushes on tab close/hide.
- On load, if a non-empty autosave exists, the restore card is shown **before** any `?img=`/share-target import; the user picks **Restore** or **Start new**. iOS share import (`?ios-share=`) skips the restore prompt.

### Export

`exportMarkup()` computes the union bounding box over image + drawing nodes via `contentBounds()`, then renders directly from the live stage: save transform → reset to identity → add a white background rect → hide `uiLayer` → `stage.toDataURL({x,y,width,height})` → restore. Result is a cropped white-background PNG, shared via Web Share when available, else downloaded. Do **not** call `node.isDestroyed()` (not a function on Konva nodes in this build); test attachment with `node.getLayer()` truthiness.

### Imports

- **Android share target**: `sw.js` listens for POSTs to `/admin/toolbox/markup/share-target`, stores the file in Cache API (`SHARE_CACHE = 'markup-share-v1'`, `SHARE_KEY = '/__oqb_markup_shared_image__'`), and redirects 303 to `/admin/toolbox/markup?shared=1`. The page's `loadSharedImage()` reads that cache entry.
- **Upload / paste**: **Import** opens `#importBackdrop` with a file picker, **Paste from clipboard** (`readClipboardAsImageBlob`: `clipboard.read()` image types, else `readText` + base64 decode), and a tap-to-paste zone. Global paste on the canvas also works.
- **Present mode** (`viewer.html`): `markupUrlFromAsset(data)` returns the first IMG part URL, or `thumbnail_url` for DOC assets; MD assets return `null` so the Markup button stays unavailable. `openMarkupForPanel` opens `/admin/toolbox/markup?img=<encoded url>` in a new tab.
- `?img=` is resolved with `new URL(imgUrl, location.href)`; a different `origin` is rejected with a toast ("Only same-origin images can be imported safely") to avoid canvas-taint export failures.
- **iOS Share Shortcut** (PairDrop-style): the generated Shortcut is an `ActionExtension` accepting images/AV assets/files: convert image → JPEG 0.85 → base64 encode → set clipboard → open `{base}/admin/toolbox/markup?ios-share=paste`. That URL opens `#importBackdrop` in share mode (`.ios-share-sheet`); `?ios-share=hash#<BASE64>` inlines small images directly. After import the page strips `ios-share`/`shared` params and the hash via `history.replaceState`.

### PWA / install

- The service worker must stay root-scoped (`/sw.js` + `Service-Worker-Allowed: /`) so Web Share Target POSTs to `/admin/toolbox/markup/share-target` are intercepted.
- Android Chrome supports installed-PWA Web Share Target. iOS Safari does not register Web Share Target; iOS relies on installed PWA + upload/paste, with the Shortcut as a documented workaround. iOS 15+ rejects unsigned `.shortcut` downloads unless replaced by `Markup-OQB-signed.shortcut` or imported via `shortcuts://import-shortcut/?url=…` (or signed with `shortcuts sign` on a Mac).
- When not in standalone mode, a floating `#installInfoBtn` (bottom-right) opens `#installHelpBackdrop` with platform-highlighted instructions (`detectInstallPlatform`, `isStandaloneApp`). The button is hidden once installed (`display-mode: standalone` or `navigator.standalone`).

## Settings & config keys

See `../core/06-system-settings.md`.

| Key | Where | Default | Meaning |
|---|---|---|---|
| `MARKUP_NORMALIZED_MAX_DIM` | System Settings (group "Markup"); `.env` default in `app/config.py` | 2400 | World units for the longest edge of an imported image. Larger = imports appear bigger at fit zoom and default pen sizes grow. |

## Permissions

- `GET /admin/toolbox/markup`, `/markup/ios-shortcut.shortcut`, `POST /markup/share-target`: `@login_required` only (every role, including `viewer`).
- `/manifest.webmanifest` and `/sw.js`: unauthenticated (must be fetchable by the browser before login for install/SW registration).
- The hub `GET /admin/toolbox/` is `@login_required`; the Markup card is shown to everyone.

## Background work / SSE / threads

None server-side. Client-side: the service worker (`static/markup/sw.js`) and debounced IndexedDB autosave.

## Gotchas

1. Never block init on the `perfect-freehand` ESM import; it is loaded with a non-blocking dynamic `import()` and the app must remain usable if the CDN is slow.
2. Do not move Markup into `base.html`; the page relies on owning the viewport, touch-action, and the full-screen canvas.
3. Keep `/sw.js` served with `Service-Worker-Allowed: /` — without it the SW scope is `/static/markup/` and share-target POSTs are never intercepted.
4. External `?img=` URLs are rejected on purpose; a cross-origin image taints the canvas and `toDataURL` throws at export.
5. Restore-or-new must run before any incoming import so a shared image never silently clobbers unsaved work (exception: `?ios-share=` flows).
6. `readAutosave()` has a 5 s timeout; if you change the save path keep the `saveInFlight` serialisation or rapid reloads on mobile hang.
7. Use `node.getLayer()` to detect detached nodes; `isDestroyed()` does not exist on this Konva build.
8. The `POST /markup/share-target` Flask route is only a fallback redirect; it cannot recover the shared image. The SW path is authoritative.
9. Only IMG parts and DOC thumbnails can be sent from the viewer; MD assets have no image URL and must not show a Markup button.
10. Changing `MARKUP_NORMALIZED_MAX_DIM` affects fit zoom and default brush sizes for new imports only; existing autosaved scenes keep their world coordinates.

## Related

- `toolbox-pdf.md` — Toolbox hub and the admin-only PDF Tool.
- `generator.md` — viewer / present mode where the Markup buttons live.
- `doc-format.md` — DOC thumbnails (`thumbnail_url`) that Markup can open.
- `../core/06-system-settings.md` — `MARKUP_NORMALIZED_MAX_DIM` registry entry.
- `../core/01-runtime-and-ops.md` — HTTPS requirement for service workers / PWA install.
