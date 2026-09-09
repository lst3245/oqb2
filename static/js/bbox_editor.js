/*
 * OQBBboxEditor — shared fractional bounding-box editor.
 *
 * One editor per page image: a `.pdf-page-wrap` holding an `<img>` and a
 * `.pdf-overlay`. Boxes are `[x1, y1, x2, y2]` fractions (0..1) of the image;
 * the host owns the model (items) and persistence, the editor owns pointer
 * sessions (move / 8-handle resize / rubber-band draw), the drag magnifier,
 * crop thumbnails, the full-size crop preview and the optional "page frame"
 * guide (dashed rectangle with draggable left/right rails).
 *
 * Used by templates/admin_pdf_import.html (one editor per page card) and
 * templates/admin_question_split.html (one editor on the stitched image).
 * Pair with static/css/bbox_editor.css. No bundler: exposes a global.
 *
 *   const ed = OQBBboxEditor.mount({
 *     wrap, img, overlay?,                 // DOM (overlay defaults to wrap .pdf-overlay)
 *     getBox: it => it.box,                // read fractional box from an item
 *     setBox: (it, box) => { it.box = box }, // write it back (live during drag)
 *     classNames: it => 'kind-que role-stem', // extra classes on the rect
 *     labelText: it => 'Q3a',              // chip text (empty string = no chip)
 *     boxId: it => 'box-' + it._uid,       // optional DOM id for the rect
 *     magnifierColor: it => '#d63384',
 *     minSize: 0.012,                      // discard smaller drawn boxes
 *     constrainBox: (it, box, ctx) => box, // ctx {type:'move'|'resize'|'draw', dir}
 *     onSelect: it => {},                  // rect pointerdown / editor.select
 *     onChange: (it, type, live) => {},    // live=true during drag, false at end
 *     onCreate: box => item | null,        // host inserts; return item to render it
 *     onTooSmall: () => {},
 *     onAddModeChange: on => {},           // keep the host's Add button in sync
 *     frameEditable: false,
 *     onFrameChange: frameBox => {},       // after a rail drag ends
 *   });
 *   ed.add(item); ed.remove(item); ed.restyle(item); ed.refreshLabel(item);
 *   ed.refreshClass(item); ed.select(item); ed.setAddMode(on); ed.isAdding();
 *   ed.setFrame([x1,y1,x2,y2] | null); ed.getFrame(); ed.clear(); ed.destroy();
 *
 * Module helpers: clamp01, applyBoxStyle, drawPreview(canvas, img, box, w),
 * showCropPreview(img, box, caption), selectNone().
 */
window.OQBBboxEditor = (function () {
    'use strict';

    const HANDLES = ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'];
    let activeDrag = null;
    let dragMagnifier = null;
    let addingEditor = null;       // only one editor is in draw mode at a time
    const editors = new Set();

    // ---------------------------------------------------------------- geometry
    function clamp01(v) { return Math.min(1, Math.max(0, v)); }

    function applyBoxStyle(el, box) {
        const [x1, y1, x2, y2] = box;
        el.style.left = (x1 * 100) + '%';
        el.style.top = (y1 * 100) + '%';
        el.style.width = ((x2 - x1) * 100) + '%';
        el.style.height = ((y2 - y1) * 100) + '%';
    }

    function normBox(box) {
        return [Math.min(box[0], box[2]), Math.min(box[1], box[3]),
                Math.max(box[0], box[2]), Math.max(box[1], box[3])];
    }

    function fracFromEvent(overlay, e) {
        const rect = overlay.getBoundingClientRect();
        return {
            x: clamp01((e.clientX - rect.left) / Math.max(1, rect.width)),
            y: clamp01((e.clientY - rect.top) / Math.max(1, rect.height)),
        };
    }

    // ---------------------------------------------------------------- thumbnails
    function drawPreview(canvas, img, box, thumbW) {
        if (!canvas || !img || !img.naturalWidth || !img.naturalHeight) return;
        const nw = img.naturalWidth, nh = img.naturalHeight;
        const sx = box[0] * nw, sy = box[1] * nh;
        const sw = Math.max(1, (box[2] - box[0]) * nw), sh = Math.max(1, (box[3] - box[1]) * nh);
        const w = thumbW || 130;
        const scale = w / sw;
        canvas.width = w;
        canvas.height = Math.max(10, Math.round(sh * scale));
        const ctx = canvas.getContext('2d');
        ctx.fillStyle = '#fff';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        try { ctx.drawImage(img, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height); } catch (e) { /* not loaded */ }
    }

    // Full-size crop preview overlay (click a thumbnail to enlarge). Draws the
    // box region from the high-res image at native resolution; click or Esc closes.
    function showCropPreview(img, box, caption) {
        if (!img || !img.naturalWidth || !img.naturalHeight) return;
        const nw = img.naturalWidth, nh = img.naturalHeight;
        const x1 = clamp01(box[0]), y1 = clamp01(box[1]), x2 = clamp01(box[2]), y2 = clamp01(box[3]);
        const sx = x1 * nw, sy = y1 * nh;
        const sw = Math.max(1, (x2 - x1) * nw), sh = Math.max(1, (y2 - y1) * nh);
        const canvas = document.createElement('canvas');
        canvas.width = Math.round(sw);
        canvas.height = Math.round(sh);
        const ctx = canvas.getContext('2d');
        ctx.fillStyle = '#fff';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        try { ctx.drawImage(img, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height); } catch (e) { /* ignore */ }

        const overlay = document.createElement('div');
        overlay.className = 'pdf-fullsize-overlay';
        const cap = document.createElement('div');
        cap.className = 'pdf-fullsize-cap';
        cap.textContent = `${caption ? caption + ' — ' : ''}${canvas.width}×${canvas.height}px · click or press Esc to close`;
        overlay.appendChild(canvas);
        overlay.appendChild(cap);
        const close = () => { overlay.remove(); document.removeEventListener('keydown', onEsc); };
        const onEsc = (e) => { if (e.key === 'Escape') close(); };
        overlay.addEventListener('click', close);
        document.addEventListener('keydown', onEsc);
        document.body.appendChild(overlay);
    }

    // ---------------------------------------------------------------- magnifier
    function ensureDragMagnifier() {
        if (dragMagnifier) return dragMagnifier;
        const wrap = document.createElement('div');
        wrap.className = 'pdf-magnifier d-none';
        const canvas = document.createElement('canvas');
        canvas.width = 190;
        canvas.height = 190;
        const cap = document.createElement('div');
        cap.className = 'pdf-mag-caption';
        wrap.appendChild(canvas);
        wrap.appendChild(cap);
        document.body.appendChild(wrap);
        dragMagnifier = { wrap, canvas, cap };
        return dragMagnifier;
    }

    function hideDragMagnifier() {
        if (dragMagnifier) dragMagnifier.wrap.classList.add('d-none');
    }

    function clampSourceWindow(v, max, size) {
        return Math.max(0, Math.min(Math.max(0, max - size), v));
    }

    function setMagnifierCanvasSize(canvas, w, h) {
        if (canvas.width !== w) canvas.width = w;
        if (canvas.height !== h) canvas.height = h;
    }

    function activeBoxForMagnifier(d, f) {
        if (!d) return null;
        if (d.type === 'draw') {
            return [Math.min(d.startFx, f.x), Math.min(d.startFy, f.y),
                    Math.max(d.startFx, f.x), Math.max(d.startFy, f.y)];
        }
        if (d.type === 'frame') return d.frameBox || null;
        return d.item ? d.editor.getBox(d.item) : null;
    }

    function magnifierSourceWindow(d, f, img, imgRect, box) {
        const natW = img.naturalWidth, natH = img.naturalHeight;
        const nx = clamp01(f.x) * natW;
        const ny = clamp01(f.y) * natH;
        const zoom = 3;
        const defaultW = Math.min(natW, Math.max(36, (520 / zoom) * (natW / Math.max(1, imgRect.width))));
        const defaultH = Math.min(natH, Math.max(36, (420 / zoom) * (natH / Math.max(1, imgRect.height))));
        let canvasW = 520, canvasH = 420;
        let srcW = defaultW, srcH = defaultH;
        let sx = nx - srcW / 2, sy = ny - srcH / 2;
        let caption = d.type === 'resize' ? 'Zoomed edge/handle placement'
            : d.type === 'draw' ? 'Zoomed new box corner'
            : d.type === 'frame' ? 'Page frame rail'
            : 'Zoomed box position';

        if (box && (d.type === 'resize' || d.type === 'frame')) {
            const [x1, y1, x2, y2] = box;
            const bx1 = clamp01(x1) * natW, bx2 = clamp01(x2) * natW;
            const by1 = clamp01(y1) * natH, by2 = clamp01(y2) * natH;
            const boxW = Math.max(24, bx2 - bx1);
            const boxH = Math.max(24, by2 - by1);
            const hasN = d.dir && d.dir.includes('n');
            const hasS = d.dir && d.dir.includes('s');
            const hasW = d.dir && d.dir.includes('w');
            const hasE = d.dir && d.dir.includes('e');
            const horizontalEdge = hasN || hasS;
            const verticalEdge = hasW || hasE;

            if (horizontalEdge && !verticalEdge) {
                canvasW = 920; canvasH = 420;
                srcW = Math.min(natW, Math.max(boxW, 120));
                srcH = Math.min(natH, Math.max(48, srcW * canvasH / canvasW));
                sx = bx1 - Math.max(0, (srcW - boxW) / 2);
                sy = ny - srcH * (hasS ? 0.68 : 0.32);
                caption = hasS ? 'Full width near bottom edge' : 'Full width near top edge';
            } else if (verticalEdge && !horizontalEdge) {
                canvasW = 460; canvasH = 720;
                srcH = Math.min(natH, Math.max(Math.min(boxH, natH * 0.5), 120));
                srcW = Math.min(natW, Math.max(54, srcH * canvasW / canvasH));
                sx = nx - srcW * (hasE ? 0.68 : 0.32);
                sy = ny - srcH / 2;
                caption = d.type === 'frame'
                    ? (hasE ? 'Frame right rail' : 'Frame left rail')
                    : (hasE ? 'Full height near right edge' : 'Full height near left edge');
            } else if (horizontalEdge && verticalEdge) {
                canvasW = 900; canvasH = 620;
                const aspect = canvasW / canvasH;
                srcW = Math.max(boxW, boxH * aspect, 120);
                srcH = srcW / aspect;
                if (srcH < boxH) { srcH = boxH; srcW = srcH * aspect; }
                srcW = Math.min(natW, srcW);
                srcH = Math.min(natH, srcH);
                sx = bx1 - Math.max(0, (srcW - boxW) * (hasE ? 0.35 : 0.65));
                sy = by1 - Math.max(0, (srcH - boxH) * (hasS ? 0.35 : 0.65));
                caption = 'Wide corner context';
            }
        }

        srcW = Math.min(natW, srcW);
        srcH = Math.min(natH, srcH);
        sx = clampSourceWindow(sx, natW, srcW);
        sy = clampSourceWindow(sy, natH, srcH);
        return { sx, sy, srcW, srcH, canvasW, canvasH, nx, ny, caption };
    }

    function positionDragMagnifier(e) {
        if (!dragMagnifier) return;
        const pad = 12;
        const rect = dragMagnifier.wrap.getBoundingClientRect();
        let left = e.clientX + 22;
        let top = e.clientY + 22;
        if (left + rect.width + pad > window.innerWidth) left = e.clientX - rect.width - 22;
        if (top + rect.height + pad > window.innerHeight) top = e.clientY - rect.height - 22;
        dragMagnifier.wrap.style.left = Math.max(pad, left) + 'px';
        dragMagnifier.wrap.style.top = Math.max(pad, top) + 'px';
    }

    function updateDragMagnifier(d, f, e) {
        const img = d.editor.img;
        if (!img || !img.naturalWidth || !img.naturalHeight) return;
        const mag = ensureDragMagnifier();
        const canvas = mag.canvas;
        const ctx = canvas.getContext('2d');
        const imgRect = img.getBoundingClientRect();
        const box = activeBoxForMagnifier(d, f);
        const view = magnifierSourceWindow(d, f, img, imgRect, box);
        const { sx, sy, srcW, srcH, canvasW, canvasH, nx, ny, caption } = view;
        setMagnifierCanvasSize(canvas, canvasW, canvasH);

        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.imageSmoothingEnabled = true;
        ctx.imageSmoothingQuality = 'high';
        try {
            ctx.drawImage(img, sx, sy, srcW, srcH, 0, 0, canvas.width, canvas.height);
        } catch (err) {
            return;
        }
        const tx = (x) => (x - sx) * canvas.width / srcW;
        const ty = (y) => (y - sy) * canvas.height / srcH;
        const cx = tx(nx), cy = ty(ny);
        if (box) {
            const [x1, y1, x2, y2] = box;
            let edgeColor = '#d63384';
            if (d.type === 'frame') edgeColor = '#20c997';
            else if (d.item && d.editor.opts.magnifierColor) edgeColor = d.editor.opts.magnifierColor(d.item) || edgeColor;
            ctx.save();
            ctx.strokeStyle = edgeColor;
            ctx.lineWidth = 2.5;
            ctx.setLineDash([7, 4]);
            const bx1 = tx(x1 * img.naturalWidth), by1 = ty(y1 * img.naturalHeight);
            const bx2 = tx(x2 * img.naturalWidth), by2 = ty(y2 * img.naturalHeight);
            ctx.strokeRect(bx1, by1, bx2 - bx1, by2 - by1);
            ctx.restore();
        }
        ctx.save();
        ctx.strokeStyle = '#ffc107';
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.moveTo(cx, 0); ctx.lineTo(cx, canvas.height);
        ctx.moveTo(0, cy); ctx.lineTo(canvas.width, cy);
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(cx, cy, 5, 0, Math.PI * 2);
        ctx.stroke();
        ctx.restore();

        mag.cap.textContent = caption;
        mag.wrap.classList.remove('d-none');
        positionDragMagnifier(e);
    }

    // ---------------------------------------------------------------- drag session
    function beginSession(d, e, f) {
        activeDrag = d;
        updateDragMagnifier(d, f, e);
        document.body.style.userSelect = 'none';
        document.addEventListener('pointermove', onDragMove);
        document.addEventListener('pointerup', onDragEnd, { once: true });
    }

    function onDragMove(e) {
        const d = activeDrag;
        if (!d) return;
        const ed = d.editor;
        const f = fracFromEvent(ed.overlay, e);

        if (d.type === 'draw') {
            d.curX = f.x; d.curY = f.y;
            let box = normBox([d.startFx, d.startFy, f.x, f.y]);
            if (ed.opts.constrainBox) box = ed.opts.constrainBox(null, box, { type: 'draw', live: true }) || box;
            d.draftBox = box;
            applyBoxStyle(d.draftEl, box);
            updateDragMagnifier(d, f, e);
            return;
        }
        if (d.type === 'frame') {
            const fb = d.startFrame.slice();
            if (d.dir === 'w') fb[0] = Math.min(f.x, fb[2] - 0.05);
            else fb[2] = Math.max(f.x, fb[0] + 0.05);
            d.frameBox = fb;
            ed._styleFrame(fb);
            updateDragMagnifier(d, f, e);
            return;
        }
        let box;
        if (d.type === 'move') {
            const [x1, y1, x2, y2] = d.startBox;
            const w = x2 - x1, h = y2 - y1;
            const nx1 = Math.min(Math.max(0, x1 + (f.x - d.startFx)), 1 - w);
            const ny1 = Math.min(Math.max(0, y1 + (f.y - d.startFy)), 1 - h);
            box = [nx1, ny1, nx1 + w, ny1 + h];
        } else {
            let [x1, y1, x2, y2] = d.startBox;
            if (d.dir.includes('w')) x1 = f.x;
            if (d.dir.includes('e')) x2 = f.x;
            if (d.dir.includes('n')) y1 = f.y;
            if (d.dir.includes('s')) y2 = f.y;
            box = normBox([x1, y1, x2, y2]);
        }
        if (ed.opts.constrainBox) {
            box = ed.opts.constrainBox(d.item, box, { type: d.type, dir: d.dir, startBox: d.startBox, live: true }) || box;
        }
        ed.setBox(d.item, box);
        applyBoxStyle(d.boxEl, box);
        if (ed.opts.onChange) ed.opts.onChange(d.item, d.type, true);
        updateDragMagnifier(d, f, e);
    }

    function onDragEnd() {
        document.removeEventListener('pointermove', onDragMove);
        document.body.style.userSelect = '';
        hideDragMagnifier();
        const d = activeDrag;
        activeDrag = null;
        if (!d) return;
        const ed = d.editor;
        if (d.type === 'draw') { ed._finishDraw(d); return; }
        if (d.type === 'frame') {
            if (d.frameBox) {
                ed.frame = d.frameBox;
                ed._styleFrame(ed.frame);
                if (ed.opts.onFrameChange) ed.opts.onFrameChange(ed.frame.slice());
            }
            return;
        }
        if (ed.opts.onChange) ed.opts.onChange(d.item, d.type, false);
    }

    // ---------------------------------------------------------------- selection
    function selectNone() {
        document.querySelectorAll('.pdf-box.selected').forEach(b => b.classList.remove('selected'));
        editors.forEach(ed => { ed.selected = null; });
    }

    // ---------------------------------------------------------------- editor
    class Editor {
        constructor(opts) {
            this.opts = opts || {};
            this.wrap = opts.wrap;
            this.img = opts.img || this.wrap.querySelector('.pdf-page-img, img');
            this.overlay = opts.overlay || this.wrap.querySelector('.pdf-overlay');
            if (!this.overlay) {
                this.overlay = document.createElement('div');
                this.overlay.className = 'pdf-overlay';
                this.wrap.appendChild(this.overlay);
            }
            this.els = new Map();     // item -> {boxEl, lbl}
            this.selected = null;
            this.frame = null;
            this.frameEl = null;
            this.minSize = Number.isFinite(opts.minSize) ? opts.minSize : 0.012;
            this._onOverlayDown = (e) => this._startDraw(e);
            this.overlay.addEventListener('pointerdown', this._onOverlayDown);
            editors.add(this);
        }

        getBox(item) { return this.opts.getBox ? this.opts.getBox(item) : item.box; }
        setBox(item, box) {
            if (this.opts.setBox) this.opts.setBox(item, box);
            else item.box = box;
        }

        // ---- boxes
        add(item) {
            if (this.els.has(item)) { this.restyle(item); return this.els.get(item).boxEl; }
            const boxEl = document.createElement('div');
            boxEl.className = 'pdf-box';
            if (this.opts.boxId) boxEl.id = this.opts.boxId(item);
            const lbl = document.createElement('span');
            lbl.className = 'pdf-box-label';
            boxEl.appendChild(lbl);
            HANDLES.forEach(dir => {
                const h = document.createElement('div');
                h.className = 'pdf-handle h-' + dir;
                h.addEventListener('pointerdown', (e) => {
                    e.stopPropagation();
                    e.preventDefault();
                    this.select(item);
                    const f = fracFromEvent(this.overlay, e);
                    beginSession({ editor: this, type: 'resize', item, dir, boxEl,
                                   startFx: f.x, startFy: f.y,
                                   startBox: this.getBox(item).slice() }, e, f);
                });
                boxEl.appendChild(h);
            });
            boxEl.addEventListener('pointerdown', (e) => {
                e.preventDefault();
                this.select(item);
                const f = fracFromEvent(this.overlay, e);
                beginSession({ editor: this, type: 'move', item, boxEl,
                               startFx: f.x, startFy: f.y,
                               startBox: this.getBox(item).slice() }, e, f);
            });
            this.overlay.appendChild(boxEl);
            this.els.set(item, { boxEl, lbl });
            this.refreshClass(item);
            this.refreshLabel(item);
            applyBoxStyle(boxEl, this.getBox(item));
            return boxEl;
        }

        remove(item) {
            const rec = this.els.get(item);
            if (!rec) return;
            rec.boxEl.remove();
            this.els.delete(item);
            if (this.selected === item) this.selected = null;
        }

        clear() {
            this.els.forEach(rec => rec.boxEl.remove());
            this.els.clear();
            this.selected = null;
        }

        has(item) { return this.els.has(item); }
        elementFor(item) { const r = this.els.get(item); return r ? r.boxEl : null; }
        items() { return Array.from(this.els.keys()); }

        restyle(item) {
            const rec = this.els.get(item);
            if (rec) applyBoxStyle(rec.boxEl, this.getBox(item));
        }

        refreshLabel(item) {
            const rec = this.els.get(item);
            if (!rec) return;
            const text = this.opts.labelText ? this.opts.labelText(item) : '';
            rec.lbl.textContent = text || '';
            rec.lbl.classList.toggle('d-none', !text);
        }

        refreshClass(item) {
            const rec = this.els.get(item);
            if (!rec) return;
            const extra = this.opts.classNames ? (this.opts.classNames(item) || '') : '';
            const keepSel = rec.boxEl.classList.contains('selected');
            rec.boxEl.className = 'pdf-box ' + extra;
            if (keepSel) rec.boxEl.classList.add('selected');
        }

        refreshAll() {
            this.els.forEach((rec, item) => {
                applyBoxStyle(rec.boxEl, this.getBox(item));
                this.refreshClass(item);
                this.refreshLabel(item);
            });
        }

        select(item) {
            selectNone();
            if (!item) return;
            const rec = this.els.get(item);
            if (rec) rec.boxEl.classList.add('selected');
            this.selected = item;
            if (this.opts.onSelect) this.opts.onSelect(item);
        }

        // ---- draw mode
        isAdding() { return this.wrap.classList.contains('adding'); }

        setAddMode(on) {
            if (on && addingEditor && addingEditor !== this) addingEditor.setAddMode(false);
            this.wrap.classList.toggle('adding', !!on);
            addingEditor = on ? this : (addingEditor === this ? null : addingEditor);
            if (this.opts.onAddModeChange) this.opts.onAddModeChange(!!on);
        }

        _startDraw(e) {
            if (!this.isAdding()) return;
            if (e.target !== this.overlay) return;
            e.preventDefault();
            const f = fracFromEvent(this.overlay, e);
            const draftEl = document.createElement('div');
            draftEl.className = 'pdf-box-draft';
            applyBoxStyle(draftEl, [f.x, f.y, f.x, f.y]);
            this.overlay.appendChild(draftEl);
            beginSession({ editor: this, type: 'draw', draftEl,
                           startFx: f.x, startFy: f.y, curX: f.x, curY: f.y,
                           draftBox: [f.x, f.y, f.x, f.y] }, e, f);
        }

        _finishDraw(d) {
            if (d.draftEl) d.draftEl.remove();
            this.setAddMode(false);
            let box = d.draftBox || normBox([d.startFx, d.startFy, d.curX, d.curY]);
            if (this.opts.constrainBox) box = this.opts.constrainBox(null, box, { type: 'draw', live: false }) || box;
            if ((box[2] - box[0]) < this.minSize || (box[3] - box[1]) < this.minSize) {
                if (this.opts.onTooSmall) this.opts.onTooSmall();
                return;
            }
            const item = this.opts.onCreate ? this.opts.onCreate(box) : null;
            if (item) { this.add(item); this.select(item); }
        }

        // ---- page frame guide
        setFrame(frameBox) {
            this.frame = frameBox ? frameBox.slice() : null;
            if (!this.frame) {
                if (this.frameEl) { this.frameEl.remove(); this.frameEl = null; }
                return;
            }
            if (!this.frameEl) {
                const el = document.createElement('div');
                el.className = 'pdf-frame';
                if (this.opts.frameEditable) {
                    ['w', 'e'].forEach(dir => {
                        const rail = document.createElement('div');
                        rail.className = 'pdf-frame-rail rail-' + dir;
                        rail.title = 'Drag to move the page frame rail (boxes snap to it)';
                        rail.addEventListener('pointerdown', (e) => {
                            e.stopPropagation();
                            e.preventDefault();
                            const f = fracFromEvent(this.overlay, e);
                            beginSession({ editor: this, type: 'frame', dir,
                                           startFrame: this.frame.slice(), frameBox: this.frame.slice(),
                                           startFx: f.x, startFy: f.y }, e, f);
                        });
                        el.appendChild(rail);
                    });
                }
                this.overlay.appendChild(el);
                this.frameEl = el;
            }
            this._styleFrame(this.frame);
        }

        getFrame() { return this.frame ? this.frame.slice() : null; }

        _styleFrame(fb) {
            if (this.frameEl) applyBoxStyle(this.frameEl, fb);
        }

        destroy() {
            this.clear();
            this.setFrame(null);
            this.overlay.removeEventListener('pointerdown', this._onOverlayDown);
            if (addingEditor === this) addingEditor = null;
            editors.delete(this);
        }
    }

    function mount(opts) { return new Editor(opts); }

    return { mount, clamp01, applyBoxStyle, fracFromEvent, drawPreview, showCropPreview, selectNone,
             get active() { return activeDrag; } };
})();
