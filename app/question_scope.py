"""Pure helpers for the single-subject Question Management page (ADR-016)
and the dashboard QID search pattern.

No DB imports: `app/admin.py` feeds these plain rows and request
args, and `tests/test_admin_questions_scope.py` exercises them directly.
`_t` stays English when there is no request.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Iterable, Optional

from app.i18n import t as _t

LIST_PAGE_SIZES = (10, 20, 50, 100, 200)
DEFAULT_PAGE_SIZE = 50
# all = no filter; with_parts = rows inside a tree; standalone = childless
# roots; leaves = printable rows; roots = top-level rows only.
TREE_SCOPES = ('all', 'with_parts', 'standalone', 'leaves', 'roots')
ROOT_SORTS = ('qid', 'qno', 'year', 'q_type', 'created_at')
Q_TYPES = ('MC', 'CQ')
CHECK_STATUSES = ('issues', 'ok', 'unchecked')

_TRUE = ('1', 'true', 'yes', 'on')
_FALSE = ('0', 'false', 'no', 'off')


def ids_subject_violation(rows: Iterable, subject_id: Optional[str] = None) -> Optional[dict]:
    """Check that a bulk id set stays inside one subject.

    ``rows`` are ``(question_id, subject)`` pairs for the ids that exist.
    With ``subject_id`` every row must belong to it; without one the rows
    must not span more than one subject. Returns None when the set is fine,
    else ``{error_code, message, ...}`` for a 400 response."""
    counts = Counter(subject for _qid, subject in rows)
    if subject_id:
        outside = {s: n for s, n in sorted(counts.items()) if s != subject_id}
        if not outside:
            return None
        n = sum(outside.values())
        names = ', '.join(f'{s} ({c})' for s, c in outside.items())
        return {
            'error_code': 'outside_subject',
            'subject_id': subject_id,
            'outside_count': n,
            'outside_subjects': outside,
            'message': _t('{n} selected question(s) are not in {subject_id} ({names}). Bulk actions work on one subject at a time.', n=n, subject_id=subject_id, names=names),
        }
    if len(counts) > 1:
        names = ', '.join(f'{s} ({c})' for s, c in sorted(counts.items()))
        return {
            'error_code': 'mixed_subjects',
            'subjects': dict(sorted(counts.items())),
            'message': _t('The selection spans several subjects ({names}). Bulk actions work on one subject at a time.', names=names),
        }
    return None


def subjects_of(rows: Iterable) -> dict:
    """``{subject: count}`` for ``(question_id, subject)`` rows, largest first."""
    counts = Counter(subject for _qid, subject in rows)
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def _csv(raw) -> list:
    return [t.strip() for t in str(raw or '').split(',') if t.strip()]


def _int_or(raw, default: int) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def parse_list_args(args) -> dict:
    """Normalise the list / tree / ids query string into plain values.

    ``args`` is any mapping with ``.get`` (Flask ``request.args``). Unknown
    or malformed values fall back to "no filter" rather than erroring; the
    required ``subject`` is returned as-is (the route checks access)."""
    get = args.get
    verified_raw = (get('verified') or '').strip().lower()
    verified = True if verified_raw in _TRUE else (False if verified_raw in _FALSE else None)

    years = []
    for tok in _csv(get('years')):
        if tok.isdigit():
            years.append(int(tok))

    selected_ids = []
    for tok in _csv(get('selected_ids')):
        if tok.isdigit():
            selected_ids.append(int(tok))

    tree_scope = (get('tree_scope') or 'all').strip().lower()
    if tree_scope not in TREE_SCOPES:
        tree_scope = 'all'

    q_type = (get('q_type') or '').strip().upper()
    if q_type not in Q_TYPES:
        q_type = ''

    check_status = (get('check_status') or '').strip().lower()
    if check_status not in CHECK_STATUSES:
        check_status = ''

    sort = (get('sort') or 'qid').strip().lower()
    if sort not in ROOT_SORTS:
        sort = 'qid'
    direction = 'desc' if (get('dir') or 'asc').strip().lower() == 'desc' else 'asc'

    page_size = _int_or(get('page_size'), DEFAULT_PAGE_SIZE)
    if page_size not in LIST_PAGE_SIZES:
        page_size = DEFAULT_PAGE_SIZE

    return {
        'subject': (get('subject') or '').strip(),
        'qids': _csv(get('qids')),
        'selected_ids': selected_ids,
        'qid_search': (get('qid_search') or '').strip(),
        'source': (get('source') or '').strip().upper(),
        'years': years,
        'paper': (get('paper') or '').strip().upper(),
        'q_type': q_type,
        'verified': verified,
        'tree_scope': tree_scope,
        'has_whole': (get('has_whole') or '').strip().lower() in _TRUE,
        'check_status': check_status,
        'sort': sort,
        'dir': direction,
        'page': max(1, _int_or(get('page'), 1)),
        'page_size': page_size,
    }


def qid_search_pattern(term: str) -> str:
    """SQL ILIKE pattern for the QID search box (`*` / `%` are wildcards,
    otherwise a substring match)."""
    term = (term or '').strip()
    if '*' in term or '%' in term:
        return term.replace('*', '%')
    return f'%{term}%'


def dashboard_qid_pattern(term: str, strict: bool) -> Optional[str]:
    """SQL ILIKE pattern for the dashboard QID box, or None for no filter.

    Strict, or any `*` / `%` typed: the user's own pattern (`*` = `%`, no
    wildcard = substring). Loose: words / numbers matched in order
    (`2025 q1` -> `%2025%Q1%`); punctuation only separates them."""
    term = (term or '').strip()
    if not term:
        return None
    if strict or '*' in term or '%' in term:
        return qid_search_pattern(term)
    tokens = [t for t in re.split(r'[^A-Z0-9]+', term.upper()) if t]
    return '%' + '%'.join(tokens) + '%' if tokens else None


def root_sort_key(sort: str):
    """Key function over root summary dicts for :data:`ROOT_SORTS`.
    Always tie-breaks on paper identity so paging is stable."""
    def identity(r):
        return (r.get('source') or '', r.get('year') or 0, r.get('paper') or '',
                r.get('detail') or '', int(r.get('qno') or 0), int(r.get('qno_end') or 0),
                r.get('id') or 0)

    if sort == 'qno':
        return lambda r: (int(r.get('qno') or 0),) + identity(r)
    if sort == 'year':
        return lambda r: (r.get('year') or 0,) + identity(r)
    if sort == 'q_type':
        return lambda r: (r.get('q_type') or '~',) + identity(r)
    if sort == 'created_at':
        return lambda r: (r.get('created_at') or '',) + identity(r)
    return identity


def page_slice(items: list, page: int, per_page: int) -> tuple[list, int, int]:
    """``(page_items, page, total_pages)`` with ``page`` clamped into range."""
    total_pages = max(1, (len(items) + per_page - 1) // per_page)
    page = min(max(1, page), total_pages)
    start = (page - 1) * per_page
    return items[start:start + per_page], page, total_pages
