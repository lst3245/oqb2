"""
Server-side Markdown rendering for QuestionAsset (file_format='MD').

Pipeline:
  raw .md text
    -> markdown-it-py + plugins (GFM tables, footnotes, deflist, dollarmath)
    -> bleach.clean() with an allowlist that permits:
         * KaTeX-emitted spans / classes / data-* attrs
         * `data:image/<type>;base64,...` URIs in <img src>
         * `$...$` / `$$...$$` text passed through to the browser
    -> client (KaTeX auto-render) typesets math from the literal `$...$` tokens.

Math note:
  We do NOT render math server-side (would require Node/KaTeX). The dollarmath
  plugin keeps `$x$` / `$$x$$` intact (or wraps it). KaTeX auto-render on the
  client typesets it. This matches the editor's live-preview pipeline.

Cache:
  In-memory dict keyed by (asset_id, file_mtime_ns). Invalidate by calling
  `invalidate(asset_id)` after a save / delete.
"""
from __future__ import annotations

import os
from threading import Lock
from typing import Optional

import bleach
from markdown_it import MarkdownIt
from mdit_py_plugins.dollarmath import dollarmath_plugin
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.deflist import deflist_plugin


# ---- markdown-it instance ----------------------------------------------------

def _build_md() -> MarkdownIt:
    md = (
        # `linkify` requires the optional `linkify-it-py` dep — leave off so we
        # don't add another runtime dependency just for autolinking bare URLs.
        MarkdownIt('gfm-like', {'html': False, 'linkify': False, 'typographer': True})
        .enable('table')
        .enable('strikethrough')
        .use(footnote_plugin)
        .use(deflist_plugin)
        # dollarmath: tokenises `$...$` and `$$...$$` so we can re-emit them
        # verbatim (with delimiters) into the HTML for client-side KaTeX auto-render.
        .use(
            dollarmath_plugin,
            allow_space=True,
            double_inline=True,
            renderer=_render_math,
            label_renderer=lambda label: '',
        )
    )
    return md


def _render_math(content: str, options: dict) -> str:
    """dollarmath renderer: re-emit math with its `$` / `$$` delimiters intact so
    the client's KaTeX auto-render picks it up. dollarmath already wraps the
    output in `<span class="math inline">` / `<div class="math block">`, so this
    only emits the inner text content."""
    display = bool(options.get('display_mode'))
    safe = (content or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    if display:
        return f'$$\n{safe}\n$$'
    return f'${safe}$'


_MD = _build_md()


# ---- bleach allowlist --------------------------------------------------------

# Base allowlist on top of bleach's defaults.
_ALLOWED_TAGS = sorted(set(bleach.sanitizer.ALLOWED_TAGS) | {
    'p', 'pre', 'span', 'div', 'br', 'hr',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption',
    'img', 'figure', 'figcaption',
    'ul', 'ol', 'li',
    'dl', 'dt', 'dd',
    'blockquote', 'code', 'sup', 'sub',
    'a', 'em', 'strong', 'del', 'ins', 'mark', 'small',
    'section',  # footnote plugin emits <section class="footnotes">
    # MathML elements (in case pandoc-rendered MathML ever flows through here):
    'math', 'semantics', 'mrow', 'mi', 'mn', 'mo', 'ms', 'mtext', 'mspace',
    'mfrac', 'msqrt', 'mroot', 'msub', 'msup', 'msubsup', 'mover', 'munder',
    'munderover', 'mtable', 'mtr', 'mtd', 'mfenced', 'mstyle', 'annotation',
    # KaTeX-emitted (mostly span/div but include svg/path for upgrade headroom):
    'svg', 'path', 'g', 'use', 'defs', 'rect', 'line', 'polyline',
})

# Star-attrs allowed on every tag.
_STAR_ATTRS = ['class', 'id', 'style', 'aria-hidden', 'aria-label', 'role',
               'title', 'data-line', 'data-line-start', 'data-line-end']

# Per-tag attrs.
_ALLOWED_ATTRS = {
    '*': _STAR_ATTRS,
    'a': ['href', 'rel', 'target'] + _STAR_ATTRS,
    'img': ['src', 'alt', 'width', 'height', 'loading'] + _STAR_ATTRS,
    'th': ['colspan', 'rowspan', 'scope', 'align'] + _STAR_ATTRS,
    'td': ['colspan', 'rowspan', 'align'] + _STAR_ATTRS,
    'ol': ['start', 'reversed', 'type'] + _STAR_ATTRS,
    'li': ['value'] + _STAR_ATTRS,
    'svg': ['xmlns', 'viewBox', 'preserveAspectRatio', 'width', 'height',
            'fill', 'stroke'] + _STAR_ATTRS,
    'path': ['d', 'fill', 'stroke', 'stroke-width', 'transform'] + _STAR_ATTRS,
    'use': ['href', 'xlink:href', 'x', 'y'] + _STAR_ATTRS,
    'math': ['xmlns', 'display'] + _STAR_ATTRS,
    'annotation': ['encoding'] + _STAR_ATTRS,
}

# Permit very narrow set of CSS properties (KaTeX uses many but we keep this small —
# bleach-css-sanitizer is optional; here we just allow them by allowing 'style' above
# and trusting markdown-it output. Authors are admins, not anonymous.)

# Allowed URI schemes for <a href> etc.
_ALLOWED_PROTOCOLS = ['http', 'https', 'mailto', 'tel', '#', 'data']


def _allow_data_image(tag, name, value):
    """Custom attr filter: permit `data:image/...;base64,...` only on <img src>."""
    if tag == 'img' and name == 'src':
        if value.startswith('data:image/'):
            # Restrict to base64 image data URIs to block other data: schemes.
            return ';base64,' in value
        return value.startswith(('http://', 'https://', '/'))
    if name in _STAR_ATTRS:
        return True
    allowed = _ALLOWED_ATTRS.get(tag, [])
    return name in allowed


def sanitize(html: str) -> str:
    """Run bleach with the project's allowlist."""
    return bleach.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_allow_data_image,
        protocols=_ALLOWED_PROTOCOLS,
        strip=True,
    )


# ---- public API --------------------------------------------------------------

def render_text(md_text: str) -> str:
    """Render raw markdown text to sanitized HTML (no caching)."""
    if not md_text:
        return ''
    rendered = _MD.render(md_text)
    return sanitize(rendered)


# ---- mtime cache -------------------------------------------------------------

# (asset_id, mtime_ns) -> sanitized html
_CACHE: dict[int, tuple[int, str]] = {}
_CACHE_LOCK = Lock()


def render_file(asset_id: int, abs_path: str) -> str:
    """Render a .md file from disk with mtime-keyed cache."""
    try:
        mtime_ns = os.stat(abs_path).st_mtime_ns
    except OSError:
        return ''

    with _CACHE_LOCK:
        hit = _CACHE.get(asset_id)
        if hit and hit[0] == mtime_ns:
            return hit[1]

    try:
        with open(abs_path, 'r', encoding='utf-8') as f:
            text = f.read()
    except OSError:
        return ''

    html = render_text(text)
    with _CACHE_LOCK:
        _CACHE[asset_id] = (mtime_ns, html)
    return html


def invalidate(asset_id: Optional[int] = None) -> None:
    """Drop one entry, or the whole cache when asset_id is None."""
    with _CACHE_LOCK:
        if asset_id is None:
            _CACHE.clear()
        else:
            _CACHE.pop(asset_id, None)
