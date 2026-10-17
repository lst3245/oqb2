"""
Bilingual UI (English / Traditional Chinese) and taxonomy display names.

Three independent per-device cookies, no DB column:

* ``oqb_lang`` (``en`` | ``zh``) — the UI language. Strings go through
  :func:`t` (Jinja global) / ``OQB.t()`` (``static/js/i18n.js``), both reading
  ``app/translations/zh_Hant.json`` keyed by the English text.
* ``oqb_tax_lang`` (``en`` | ``zh`` | ``both``) — how subject / topic /
  subtopic / chapter / subchapter names are shown (:func:`display_name`).
* ``oqb_paper_lang`` (``en`` | ``zh`` | ``bi``, unset = no preference) — the
  default version priority for previews, the viewer and new papers
  (:func:`paper_version_priority`). It only seeds defaults; an explicit
  ``version_priority`` (dashboard widget, preset, regenerate) still wins.

Generated documents do not read either cookie: the Generate page's
``heading_lang`` resolves through :func:`doc_lang` (``auto`` follows the top
paper version) and labels come from :func:`doc_name` / :func:`doc_label`.

UI language is not a paper version (``EN/CH/BI/ENO/CHO`` in ``app.utils``).
Spec: ``docs/modules/i18n.md``; decision: ADR-017.
"""
from __future__ import annotations

import json
import os
import re
import threading

from flask import Blueprint, Response, abort

UI_LANGS = ('en', 'zh')
TAX_MODES = ('en', 'zh', 'both')
HEADING_LANGS = ('auto', 'en', 'zh', 'both')
PAPER_LANGS = ('en', 'zh', 'bi')
UI_COOKIE = 'oqb_lang'
TAX_COOKIE = 'oqb_tax_lang'
PAPER_COOKIE = 'oqb_paper_lang'
DEFAULT_UI_LANG = 'en'
DEFAULT_TAX_MODE = 'en'
HTML_LANG = {'en': 'en', 'zh': 'zh-Hant-HK'}

# Paper language -> versions moved to the front of DEFAULT_VERSION_PRIORITY:
# typed version, then bilingual, then the official scan in that language.
PAPER_VERSION_HEAD = {
    'en': ('EN', 'BI', 'ENO'),
    'zh': ('CH', 'BI', 'CHO'),
    'bi': ('BI',),
}

# Top paper version -> document heading language for `heading_lang=auto`.
VERSION_DOC_LANG = {'EN': 'en', 'ENO': 'en', 'CH': 'zh', 'CHO': 'zh', 'BI': 'both'}

# Fixed words printed into generated documents, (English, Chinese).
DOC_LABELS = {
    'answers': ('ANSWERS', '答案'),
    'solutions': ('SOLUTIONS', '題解'),
    'answers_solutions': ('ANSWERS AND SOLUTIONS', '答案及題解'),
    'cross_topic': ('Cross Topic', '跨課題'),
    'unknown': ('Unknown', '未知'),
    'uncategorized': ('Uncategorized', '未分類'),
}

CATALOGUE_PATH = os.path.join(os.path.dirname(__file__), 'translations', 'zh_Hant.json')

_cache = {'mtime': None, 'data': {}}
_cache_lock = threading.Lock()

i18n_bp = Blueprint('i18n', __name__)


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------

def load_catalogue(path=CATALOGUE_PATH):
    """English -> Chinese dict, re-read when the file's mtime changes. Keys
    starting with ``_`` are comments and are dropped."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    if _cache['mtime'] == mtime and path == CATALOGUE_PATH:
        return _cache['data']
    try:
        with open(path, encoding='utf-8') as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return _cache['data'] if path == CATALOGUE_PATH else {}
    data = {k: v for k, v in raw.items()
            if isinstance(k, str) and not k.startswith('_') and isinstance(v, str) and v}
    if path == CATALOGUE_PATH:
        with _cache_lock:
            _cache['mtime'] = mtime
            _cache['data'] = data
    return data


def catalogue_version():
    """Cache-buster for ``/i18n/<lang>.js`` (catalogue mtime, 0 if missing)."""
    try:
        return int(os.path.getmtime(CATALOGUE_PATH))
    except OSError:
        return 0


def translate(text, lang, catalogue=None, **params):
    """``text`` in ``lang``, then ``{name}`` placeholders filled from
    ``params``. A missing key falls back to the English text. Pure when
    ``catalogue`` is given."""
    if text is None:
        return ''
    out = str(text)
    if lang == 'zh':
        cat = catalogue if catalogue is not None else load_catalogue()
        out = cat.get(out) or out
    if params:
        try:
            out = out.format(**params)
        except (KeyError, IndexError, ValueError):
            pass
    return out


# ---------------------------------------------------------------------------
# Request language
# ---------------------------------------------------------------------------

def _cookie(name):
    try:
        from flask import has_request_context, request
        if has_request_context():
            return request.cookies.get(name)
    except Exception:
        pass
    return None


def ui_lang():
    """UI language for this request (``oqb_lang`` cookie), ``en`` outside a
    request or for an unknown value."""
    v = _cookie(UI_COOKIE)
    return v if v in UI_LANGS else DEFAULT_UI_LANG


def tax_lang():
    """Taxonomy display mode for this request (``oqb_tax_lang`` cookie)."""
    v = _cookie(TAX_COOKIE)
    return v if v in TAX_MODES else DEFAULT_TAX_MODE


def paper_lang():
    """Preferred paper language for this request (``oqb_paper_lang`` cookie),
    or None when this device has not chosen one."""
    v = _cookie(PAPER_COOKIE)
    return v if v in PAPER_LANGS else None


def paper_version_priority(lang=None):
    """Default version order for ``lang`` (``en`` / ``zh`` / ``bi``): its
    ``PAPER_VERSION_HEAD`` first, the rest in ``DEFAULT_VERSION_PRIORITY``
    order. None or unknown -> ``DEFAULT_VERSION_PRIORITY``. Pure."""
    from app.utils import DEFAULT_VERSION_PRIORITY, parse_version_priority
    head = PAPER_VERSION_HEAD.get(lang)
    if not head:
        return list(DEFAULT_VERSION_PRIORITY)
    return parse_version_priority(','.join(head))


def t(text, **params):
    """Translate a UI string into the request's UI language."""
    return translate(text, ui_lang(), **params)


# ---------------------------------------------------------------------------
# Taxonomy names
# ---------------------------------------------------------------------------

def bilingual(name, name_zh, mode):
    """``name`` (English), ``name_zh`` (falls back to English) or both
    (``"Planet Earth 地球"``; just the English when the Chinese is blank or
    identical). Pure."""
    name = name or ''
    zh = (name_zh or '').strip()
    if mode == 'zh':
        return zh or name
    if mode == 'both' and zh and zh != name:
        return f'{name} {zh}'
    return name


def display_name(node, mode=None):
    """Display name of a Subject / Topic / Subtopic / Chapter / Subchapter
    (or any object with ``name`` / ``name_zh``) in ``mode`` (default: the
    request's ``oqb_tax_lang``). ``None`` for a missing node."""
    if node is None:
        return None
    if isinstance(node, dict):
        name, name_zh = node.get('name'), node.get('name_zh')
    else:
        name, name_zh = getattr(node, 'name', None), getattr(node, 'name_zh', None)
    return bilingual(name, name_zh, mode or tax_lang())


# The Auto Tag prompt shows a Chinese alias as `Name [中: 名稱]`; a model may
# echo it back verbatim.
_ALIAS_SUFFIX = re.compile(r'\s*\[\s*中\s*[:：]\s*[^\]]*\]\s*$')


def normalize_name(text):
    """Lower-cased, whitespace-collapsed name with any ``[中: …]`` alias
    suffix removed. Pure."""
    text = _ALIAS_SUFFIX.sub('', str(text or ''))
    return ' '.join(text.split()).lower()


def name_variants(name, name_zh):
    """Every normalised string that identifies a node: English, Chinese and
    the combined ``"English 中文"`` form (for name matching in Auto Tag / CSV
    import). Pure."""
    out = []
    for v in (name, name_zh, bilingual(name, name_zh, 'both')):
        v = normalize_name(v)
        if v and v not in out:
            out.append(v)
    return out


def build_name_index(nodes):
    """``{normalised name: node}`` over English names, then Chinese names,
    then combined forms. A later English duplicate wins (old behaviour); a
    Chinese or combined form never displaces an English match. Pure."""
    nodes = list(nodes or [])
    index = {}
    for n in nodes:
        key = normalize_name(getattr(n, 'name', None))
        if key:
            index[key] = n
    for n in nodes:
        for key in name_variants(getattr(n, 'name', None), getattr(n, 'name_zh', None)):
            index.setdefault(key, n)
    return index


def lookup_name(index, raw):
    """Node for a free-text name (either language, any case), or None. Pure."""
    key = normalize_name(raw)
    return index.get(key) if key else None


# ---------------------------------------------------------------------------
# Generated documents
# ---------------------------------------------------------------------------

def doc_lang(heading_lang, version_priority=None):
    """Resolve the Generate page's heading language. ``auto`` (or anything
    unknown) follows the first version in ``version_priority``: EN / ENO ->
    ``en``, CH / CHO -> ``zh``, BI -> ``both``. Pure."""
    if heading_lang in ('en', 'zh', 'both'):
        return heading_lang
    top = (list(version_priority or []) or ['EN'])[0]
    return VERSION_DOC_LANG.get(str(top).upper(), 'en')


def doc_name(node, lang):
    """Taxonomy name printed into a document in resolved ``lang``."""
    return display_name(node, lang or 'en')


def doc_label(key, lang):
    """Fixed document word (``DOC_LABELS``) in resolved ``lang``. Pure."""
    en, zh = DOC_LABELS[key]
    if lang == 'zh':
        return zh
    if lang == 'both':
        return f'{en} {zh}'
    return en


# ---------------------------------------------------------------------------
# Templates + JS catalogue
# ---------------------------------------------------------------------------

def template_globals():
    """Context-processor payload (see ``create_app``)."""
    lang = ui_lang()
    return {
        't': t,
        'OQB_UI_LANG': lang,
        'OQB_HTML_LANG': HTML_LANG[lang],
        'OQB_TAX_LANG': tax_lang(),
        'OQB_PAPER_LANG': paper_lang(),
        'OQB_I18N_VERSION': catalogue_version(),
        'tax_name': display_name,
    }


@i18n_bp.route('/i18n/<lang>.js')
def catalogue_js(lang):
    """``window.OQB_I18N = {...}`` for ``OQB.t()``. No login: the login page
    needs it and the strings are not sensitive. Versioned by ``?v=<mtime>``."""
    if lang != 'zh':
        abort(404)
    body = 'window.OQB_I18N = ' + json.dumps(load_catalogue(), ensure_ascii=False) + ';\n'
    resp = Response(body, mimetype='application/javascript')
    resp.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    return resp
