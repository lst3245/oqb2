"""Question stem/part hierarchy — QID grammar and tree helpers.

A `Question` row is either a standalone item (today's default), a **stem**
(shared background, possibly spanning a qno range), or a **part** (child of a
stem). See `docs/modules/question-hierarchy.md` and ADR-009.

This module is the single source of the QNO token grammar. Filename regexes,
admin QID validators, Smart Import heuristics, and create/rename all import
from here. Pure helpers at the top are unit-testable without `create_app()`.
ORM helpers (`ensure_question`, tree walks that hit the session) lazy-import
models so tests of the grammar do not need a live database.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional

# ---------------------------------------------------------------------------
# Grammar
#
# QNO token: Q<digits>[-<digits>][<part-path>]
#   Q5          standalone / root
#   Q23-24      range stem (shared MC preamble); qno=23, qno_end=24
#   Q3a         letter part under Q3
#   Q3ci        roman sub-part under Q3c  (path 'c'+'i')
#
# Range and part-path are mutually exclusive. IMG multi-file `part_number`
# (`_2.png`) is a different axis — never reuse that name here.
# ---------------------------------------------------------------------------

# Body only (no leading Q). Used inside filename / QID regexes.
QNO_BODY_PATTERN = r'\d+(?:-\d+)?(?:[a-z]+)?'

# Full token, case-sensitive for the part letters (filenames store lowercase).
QNO_TOKEN_PATTERN = r'Q' + QNO_BODY_PATTERN

# Search in a free string (filename stem, path). Digits required after Q so
# `QUE` / `CHO` never match. Not preceded/followed by alphanumerics so `P1Q5`
# is ignored and `Q5` in `..._Q5_EN_QUE` is found.
QNO_TOKEN_RE = re.compile(
    r'(?<![A-Za-z0-9])(Q\d+(?:-\d+)?(?:[a-zA-Z]+)?)(?![A-Za-z0-9])'
)

# Strict parse of one token (optional leading Q).
_TOKEN_PARSE_RE = re.compile(
    r'^Q?(?P<qno>\d+)(?:-(?P<qno_end>\d+))?(?P<part>[A-Za-z]+)?$',
    re.IGNORECASE,
)

PP_QID_RE = re.compile(
    r'^(?P<subj>[A-Z0-9]+)_(?P<source>DSE|CE|AL)_(?P<year>\d{4})_'
    r'(?P<paper>P[A-Za-z0-9]+)_(?P<token>Q\d+(?:-\d+)?(?:[A-Za-z]+)?)$'
)
QB_QID_RE = re.compile(
    r'^(?P<subj>[A-Z0-9]+)_QB_(?P<detail>[^_]+)_'
    r'(?P<token>Q\d+(?:-\d+)?(?:[A-Za-z]+)?)$'
)

# Longest-first so "cii" yields "ii" not a trailing "i".
_ROMAN_TOKENS = (
    'viii', 'iii', 'vii', 'ii', 'iv', 'vi', 'ix', 'i', 'v', 'x',
)
_ROMAN_VALUE = {
    'i': 1, 'ii': 2, 'iii': 3, 'iv': 4, 'v': 5,
    'vi': 6, 'vii': 7, 'viii': 8, 'ix': 9, 'x': 10,
}

HIERARCHY_MODE_SELECTED = 'selected'
HIERARCHY_MODE_WHOLE = 'whole'


class HierarchyError(ValueError):
    """User-facing grammar / tree constraint failure."""


@dataclass(frozen=True)
class ParsedQno:
    qno: int
    qno_end: Optional[int]
    part_path: Optional[str]
    token: str

    @property
    def own_part(self) -> Optional[str]:
        segs = part_segments(self.part_path)
        return segs[-1] if segs else None

    @property
    def parent_part_path(self) -> Optional[str]:
        segs = part_segments(self.part_path)
        if len(segs) <= 1:
            return None
        return ''.join(segs[:-1])


@dataclass(frozen=True)
class ParsedQid:
    subject: str
    source: str
    year: Optional[int]
    paper: Optional[str]
    detail: Optional[str]
    qno: ParsedQno

    @property
    def token(self) -> str:
        return self.qno.token

    @property
    def prefix(self) -> str:
        """Everything before `_{token}`."""
        if self.source == 'QB':
            return f'{self.subject}_QB_{self.detail}'
        return f'{self.subject}_{self.source}_{self.year}_{self.paper}'


@dataclass(frozen=True)
class RenderItem:
    question: Any
    role: str  # 'stem' | 'leaf'
    seq_owner_id: Any
    depth: int


def format_qno_token(
    qno: int,
    qno_end: Optional[int] = None,
    part_path: Optional[str] = None,
) -> str:
    """Canonical token: `Q5`, `Q23-24`, `Q3ci`. Raises HierarchyError if invalid."""
    if not isinstance(qno, int) or qno < 1:
        raise HierarchyError('Question number must be a positive integer')
    path = (part_path or '').strip().lower() or None
    end = qno_end if qno_end not in (None, 0, '') else None
    if end is not None:
        end = int(end)
        if end <= qno:
            raise HierarchyError('Range end must be greater than the start question number')
        if path:
            raise HierarchyError('A range stem cannot also have a part suffix')
        return f'Q{qno}-{end}'
    if path:
        if not path.isalpha():
            raise HierarchyError('Part labels must be letters only (e.g. a, ci)')
        return f'Q{qno}{path}'
    return f'Q{qno}'


def parse_qno_token(raw) -> Optional[ParsedQno]:
    """Parse `5`, `Q5`, `3a`, `Q23-24`, `Q3ci`. Returns None if invalid."""
    if raw is None or raw == '':
        return None
    if isinstance(raw, int):
        if raw < 1:
            return None
        try:
            token = format_qno_token(raw)
        except HierarchyError:
            return None
        return ParsedQno(qno=raw, qno_end=None, part_path=None, token=token)

    s = str(raw).strip()
    if not s:
        return None
    m = _TOKEN_PARSE_RE.match(s)
    if not m:
        return None
    qno = int(m.group('qno'))
    if qno < 1:
        return None
    end_raw = m.group('qno_end')
    qno_end = int(end_raw) if end_raw else None
    part = (m.group('part') or '').lower() or None
    try:
        token = format_qno_token(qno, qno_end, part)
    except HierarchyError:
        return None
    # Reject mixed-case leftovers that format would not round-trip (e.g. Q3A1).
    return ParsedQno(qno=qno, qno_end=qno_end, part_path=part, token=token)


# Pass-2 / plan labels: "stem" (shared background) vs lettered parts.
PART_STEM_ALIASES = frozenset({
    'stem', 'preamble', 'background', 'intro', 'shared', 'header',
    'q', 'question',
})


def normalize_plan_label(raw) -> Optional[str]:
    """Canonical plan label without a leading ``Q``: ``5``, ``5a``, ``23-24``.

    Returns ``None`` when ``raw`` is not a valid QNO token.
    """
    parsed = parse_qno_token(raw)
    if not parsed:
        return None
    tok = parsed.token
    return tok[1:] if tok.startswith('Q') else tok


def normalize_part_box_label(raw) -> Optional[str]:
    """Pass-2 box label relative to one question crop: ``stem`` or a letter path.

    Accepts ``stem`` aliases, letters (``a``, ``ci``), or a full token whose
    part path is stripped (``3a`` → ``a``, ``3`` → ``stem``).
    """
    if raw is None:
        return None
    s = str(raw).strip().lower().strip('()[]')
    if not s:
        return None
    if s in PART_STEM_ALIASES:
        return 'stem'
    parsed = parse_qno_token(s)
    if parsed:
        return parsed.part_path or 'stem'
    if s.isalpha():
        return s
    return None


def compose_part_label(parent_label: str, child_raw: str) -> Optional[str]:
    """Combine a pass-1 parent label with a pass-2 child label.

    ``parent_label`` is ``3`` / ``23-24`` / ``3c``. ``child_raw`` is ``stem``,
    ``a``, ``ci``, or a full token like ``3a``. Range parents cannot gain a
    part path. Returns a plan label (no leading ``Q``) or ``None``.
    """
    parent = parse_qno_token(parent_label)
    if not parent:
        return None
    child = normalize_part_box_label(child_raw)
    if not child:
        return None
    if child == 'stem':
        return parent.token[1:]
    if parent.qno_end:
        return None
    path = (parent.part_path or '') + child
    try:
        return format_qno_token(parent.qno, None, path)[1:]
    except HierarchyError:
        return None


def label_is_ancestor(parent_label, child_label) -> bool:
    """True when plan label ``parent_label`` is a strict ancestor of
    ``child_label`` (``5`` ⊃ ``5d`` ⊃ ``5di``). Ranges have no descendants
    other than the plain numbers they cover."""
    p = parse_qno_token(parent_label)
    c = parse_qno_token(child_label)
    if not p or not c:
        return False
    if p.qno_end:
        return (not c.qno_end and not c.part_path
                and p.qno <= c.qno <= p.qno_end)
    if c.qno_end or c.qno != p.qno:
        return False
    ps = part_segments(p.part_path)
    cs = part_segments(c.part_path)
    return len(cs) > len(ps) and cs[:len(ps)] == ps


def derive_roles(labels) -> dict:
    """Role of every plan label in one question, derived from the label set.

    A label is a ``stem`` when another label in the set descends from it
    (``5`` with ``5a`` present, ``5d`` with ``5di``); a label with a part
    path but no descendants is a ``part``; a plain number / range with no
    descendants is a ``question``. Roles are therefore never stored as
    model output — they are recomputed whenever labels change.
    """
    labs = []
    for raw in labels or []:
        lab = normalize_plan_label(raw)
        if lab and lab not in labs:
            labs.append(lab)
    out = {}
    for lab in labs:
        has_child = any(label_is_ancestor(lab, other) for other in labs
                        if other != lab)
        if has_child:
            out[lab] = 'stem'
        else:
            p = parse_qno_token(lab)
            out[lab] = 'part' if (p and p.part_path) else 'question'
    return out


def next_part_label(label) -> Optional[str]:
    """Next sibling label: ``4a`` → ``4b``, ``4di`` → ``4dii``, ``4`` → ``5``.

    Returns ``None`` for ranges or unparsable input; ``z`` and ``x`` have no
    successor and also return ``None``.
    """
    p = parse_qno_token(label)
    if not p:
        return None
    if p.qno_end:
        return None
    if not p.part_path:
        return str(p.qno + 1)
    segs = part_segments(p.part_path)
    own = segs[-1]
    if own in _ROMAN_VALUE:
        val = _ROMAN_VALUE[own] + 1
        nxt = next((k for k, v in _ROMAN_VALUE.items() if v == val), None)
        if not nxt:
            return None
    elif len(own) == 1 and own.isalpha():
        if own == 'z':
            return None
        nxt = chr(ord(own) + 1)
    else:
        return None
    path = ''.join(segs[:-1]) + nxt
    try:
        return format_qno_token(p.qno, None, path)[1:]
    except HierarchyError:
        return None


def parse_qid(qid: str) -> Optional[ParsedQid]:
    """Parse a full QID string. Subject/source tokens are case-sensitive."""
    if not qid:
        return None
    m = PP_QID_RE.match(qid)
    if m:
        qno = parse_qno_token(m.group('token'))
        if not qno:
            return None
        return ParsedQid(
            subject=m.group('subj'),
            source=m.group('source'),
            year=int(m.group('year')),
            paper=m.group('paper'),
            detail=None,
            qno=qno,
        )
    m = QB_QID_RE.match(qid)
    if m:
        qno = parse_qno_token(m.group('token'))
        if not qno:
            return None
        return ParsedQid(
            subject=m.group('subj'),
            source='QB',
            year=None,
            paper=None,
            detail=m.group('detail'),
            qno=qno,
        )
    return None


def build_qid(subject: str, source: str, year, paper, detail, token: str) -> str:
    """Assemble a QID from paper fields + a canonical QNO token (`Q5`)."""
    tok = token if str(token).startswith('Q') else f'Q{token}'
    if source == 'QB':
        return f'{subject}_QB_{detail}_{tok}'
    return f'{subject}_{source}_{year}_{paper}_{tok}'


def paper_prefix(subject: str, source: str, year, paper, detail) -> str:
    if source == 'QB':
        return f'{subject}_QB_{detail}'
    return f'{subject}_{source}_{year}_{paper}'


def qid_with_token(parsed: ParsedQid, token: str) -> str:
    tok = token if token.startswith('Q') else f'Q{token}'
    return f'{parsed.prefix}_{tok}'


def split_part_path(path: Optional[str]) -> tuple[str, Optional[str]]:
    """Split a part path into `(parent_path, own_label)`.

    Trailing roman numerals (`i`–`x`, longest match) are one segment; a
    remaining letter chain is walked one letter at a time from the right
    only when a roman was stripped. A path that is itself a roman (`i`,
    `ii`) attaches to the root (`parent_path` empty).
    """
    if not path:
        return ('', None)
    path = path.lower()
    if path in _ROMAN_VALUE:
        return ('', path)
    for roman in _ROMAN_TOKENS:
        if path.endswith(roman) and len(path) > len(roman):
            remainder = path[:-len(roman)]
            if remainder.isalpha():
                return (remainder, roman)
    if path.isalpha() and len(path) == 1:
        return ('', path)
    if path.isalpha() and len(path) > 1:
        return (path[:-1], path[-1])
    return ('', path)


def part_segments(path: Optional[str]) -> list[str]:
    """`ci` → `['c', 'i']`; `ii` → `['ii']`; empty → `[]`."""
    segs: list[str] = []
    rest = (path or '').lower()
    while rest:
        parent, own = split_part_path(rest)
        if not own:
            break
        segs.append(own)
        if not parent:
            break
        rest = parent
    segs.reverse()
    return segs


def part_sort_key(label: Optional[str]) -> tuple[int, int, str]:
    """Sort key for one segment: letters, then romans, then other."""
    if not label:
        return (2, 0, '')
    lab = label.lower()
    if len(lab) == 1 and lab.isalpha() and lab not in _ROMAN_VALUE:
        return (0, ord(lab) - ord('a') + 1, lab)
    if lab in _ROMAN_VALUE:
        return (1, _ROMAN_VALUE[lab], lab)
    if lab.isalpha() and len(lab) == 1:
        # lone 'i' / 'v' / 'x' already handled as roman
        return (0, ord(lab) - ord('a') + 1, lab)
    return (2, 0, lab)


def part_sort_value(label: Optional[str]) -> int:
    """Integer stored on `questions.part_sort` (letters 1–26, romans 101–110)."""
    kind, n, _ = part_sort_key(label)
    if kind == 0:
        return n
    if kind == 1:
        return 100 + n
    return 200


# ---------------------------------------------------------------------------
# Duck-typed tree walks (ORM Question or SimpleNamespace)
# ---------------------------------------------------------------------------

def _children(q) -> list:
    kids = getattr(q, 'children', None)
    if kids is None:
        return []
    all_fn = getattr(kids, 'all', None)
    if callable(all_fn):
        return list(all_fn())
    return list(kids)


def children(q) -> list:
    """Direct children of ``q`` (works for ORM rows and duck-typed nodes)."""
    return _children(q)


def is_stem(q) -> bool:
    """True for a range preamble or any node that already has children."""
    if getattr(q, 'qno_end', None):
        return True
    return bool(_children(q))


def root(q):
    seen = set()
    node = q
    while True:
        parent = getattr(node, 'parent', None)
        pid = getattr(node, 'parent_id', None)
        if parent is None and not pid:
            return node
        nid = getattr(node, 'id', id(node))
        if nid in seen:
            return node
        seen.add(nid)
        if parent is None:
            return node
        node = parent


def ancestors(q) -> list:
    """Root-first list of ancestors, excluding `q` itself."""
    chain = []
    seen = set()
    node = q
    while True:
        parent = getattr(node, 'parent', None)
        if parent is None:
            break
        nid = getattr(parent, 'id', id(parent))
        if nid in seen:
            break
        seen.add(nid)
        chain.append(parent)
        node = parent
    chain.reverse()
    return chain


def descendants(q) -> list:
    """Pre-order descendants (children, then their children)."""
    out = []
    stack = list(_children(q))
    seen = set()
    while stack:
        node = stack.pop(0)
        nid = getattr(node, 'id', id(node))
        if nid in seen:
            continue
        seen.add(nid)
        out.append(node)
        stack[0:0] = _children(node)
    return out


def depth_of(q) -> int:
    return len(ancestors(q))


def earlier_siblings(q) -> list:
    """Siblings of `q` (same parent) that sort before it, in order.
    Roots have no siblings here (whole-question context is not implied)."""
    parent = getattr(q, 'parent', None)
    if parent is None:
        return []
    me = sort_key(q)
    return sorted((c for c in _children(parent)
                   if getattr(c, 'id', id(c)) != getattr(q, 'id', id(q))
                   and sort_key(c) < me), key=sort_key)


def part_path_of(q) -> Optional[str]:
    """Reconstruct the full part path from this node up to (not including) the root."""
    segs = []
    node = q
    seen = set()
    while node is not None:
        part = getattr(node, 'part', None)
        if part:
            segs.append(part)
        parent = getattr(node, 'parent', None)
        nid = getattr(node, 'id', id(node))
        if nid in seen:
            break
        seen.add(nid)
        node = parent
    segs.reverse()
    return ''.join(segs) or None


def part_sort_chain(q) -> tuple:
    """Tuple of per-segment sort keys from the root down, for this node."""
    chain = []
    node = q
    seen = set()
    while node is not None:
        part = getattr(node, 'part', None)
        if part:
            chain.append(part_sort_key(part))
        nid = getattr(node, 'id', id(node))
        if nid in seen:
            break
        seen.add(nid)
        node = getattr(node, 'parent', None)
    chain.reverse()
    return tuple(chain)


def sort_key(q) -> tuple:
    """Stable order: paper identity, then qno, stem-before-children, then parts.

    Missing attributes (test stubs) default so existing standalone rows and
    SimpleNamespace fixtures keep working.
    """
    subject = getattr(q, 'subject', None) or ''
    source = getattr(q, 'source', None) or ''
    year = getattr(q, 'year', None) or 0
    paper = getattr(q, 'paper', None) or ''
    detail = ''
    qid = getattr(q, 'qid', None) or ''
    if source == 'QB' and qid:
        parsed = parse_qid(qid)
        if parsed and parsed.detail:
            detail = parsed.detail
    qno = int(getattr(q, 'qno', 0) or 0)
    parent_id = getattr(q, 'parent_id', None)
    header = 0 if not parent_id else 1
    end = int(getattr(q, 'qno_end', None) or 0)
    return (subject, source, year, paper, detail, qno, header, end) + part_sort_chain(q)


def qno_sort_key(q) -> tuple:
    """`SORT_FIELDS['qno']` — integer qno first, hierarchy as tie-break."""
    qno = int(getattr(q, 'qno', 0) or 0)
    parent_id = getattr(q, 'parent_id', None)
    header = 0 if not parent_id else 1
    end = int(getattr(q, 'qno_end', None) or 0)
    return (qno, header, end) + part_sort_chain(q)


def seq_owner_id(q):
    """Sequence-number owner: parts share the root; part-less leaves own theirs."""
    if getattr(q, 'part', None):
        r = root(q)
        return getattr(r, 'id', None)
    return getattr(q, 'id', None)


def rewrite_descendant_token(child_token: str, old_node_token: str,
                             new_node_token: str) -> str:
    """Map a descendant's QNO token when its ancestor is renamed.

    Raises HierarchyError when the tokens are different shapes (range vs
    part depth) in a way that would scramble the tree.
    """
    child = parse_qno_token(child_token)
    old = parse_qno_token(old_node_token)
    new = parse_qno_token(new_node_token)
    if not child or not old or not new:
        raise HierarchyError('Invalid QNO token in rename')
    if child.token == old.token:
        return new.token

    old_range = old.qno_end is not None
    new_range = new.qno_end is not None
    if old_range != new_range:
        raise HierarchyError(
            'Cannot change a range stem into a single question (or vice versa) '
            'while it has descendants'
        )
    if old_range:
        old_span = old.qno_end - old.qno
        new_span = new.qno_end - new.qno
        if old_span != new_span:
            raise HierarchyError(
                'Range span must stay the same when renaming a stem with descendants'
            )
        if child.part_path:
            raise HierarchyError('Range-stem children cannot have part labels')
        delta = new.qno - old.qno
        return format_qno_token(child.qno + delta, None, None)

    old_path = old.part_path or ''
    new_path = new.part_path or ''
    child_path = child.part_path or ''
    if old_path:
        if not child_path.startswith(old_path):
            raise HierarchyError(
                f'Child token {child.token} is not under {old.token}'
            )
        new_child_path = new_path + child_path[len(old_path):]
    else:
        if (old.part_path or '') != (new.part_path or ''):
            # Root Q3 → part Q3a is a depth change for descendants Q3b etc.
            if bool(old.part_path) != bool(new.part_path):
                raise HierarchyError(
                    'Cannot change part depth while descendants exist'
                )
        new_child_path = child_path
    if bool(old.part_path) != bool(new.part_path):
        raise HierarchyError('Cannot change part depth while descendants exist')
    return format_qno_token(new.qno, new.qno_end, new_child_path or None)


def rename_shape_ok(old_token: str, new_token: str, has_descendants: bool) -> Optional[str]:
    """Return an error message if this rename is illegal, else None."""
    old = parse_qno_token(old_token)
    new = parse_qno_token(new_token)
    if not old or not new:
        return 'Invalid QNO token'
    if not has_descendants:
        # Q3 → Q3a would need a parent that is still called Q3 (this row).
        if new.part_path and not old.part_path:
            parent_tok = format_qno_token(new.qno, None, new.parent_part_path)
            if parent_tok == old.token:
                return (
                    f'Cannot rename {old.token} to {new.token}: the parent of '
                    f'{new.token} would still be {old.token}'
                )
        return None
    if (old.qno_end is None) != (new.qno_end is None):
        return (
            'Cannot change a range stem into a single question (or vice versa) '
            'while it has descendants'
        )
    if old.qno_end is not None and new.qno_end is not None:
        if (old.qno_end - old.qno) != (new.qno_end - new.qno):
            return 'Range span must stay the same when renaming a stem with descendants'
        return None
    old_depth = len(part_segments(old.part_path))
    new_depth = len(part_segments(new.part_path))
    if old_depth != new_depth:
        return 'Cannot change part depth while descendants exist'
    return None


# ---------------------------------------------------------------------------
# Dashboard grouping / breadcrumbs (pure; ORM optional)
# ---------------------------------------------------------------------------

def breadcrumb_parts(q) -> list[dict]:
    """Root-first ``[{id, qid, label}]``. Root uses the QNO token; parts use ``(a)``."""
    chain = ancestors(q) + [q]
    out = []
    for node in chain:
        qid = getattr(node, 'qid', '') or ''
        parsed = parse_qid(qid) if qid else None
        part = getattr(node, 'part', None)
        if part and (getattr(node, 'parent_id', None) or getattr(node, 'parent', None)):
            label = f'({part})'
        elif parsed:
            label = parsed.qno.token
        elif qid:
            label = qid.rsplit('_', 1)[-1]
        else:
            label = ''
        out.append({
            'id': getattr(node, 'id', None),
            'qid': qid,
            'label': label,
        })
    return out


def group_for_dashboard(sorted_questions) -> list[dict]:
    """Wrap consecutive items that share a stem root.

    Returns ``[{stem, leaves}]``. A stem row in the input becomes the group's
    ``stem`` (not a leaf). Consecutive descendants of the same root wrap under
    that root even when the stem row is absent. Standalone roots (no children,
    no ``qno_end``) are ``{stem: None, leaves: [q]}``.
    """
    groups: list[dict] = []
    current = None

    def _rid(n):
        return getattr(n, 'id', id(n))

    def flush():
        nonlocal current
        if current is not None:
            groups.append({'stem': current['stem'], 'leaves': current['leaves']})
            current = None

    for q in sorted_questions:
        r = root(q)
        rid = _rid(r)
        q_is_root_stem = is_stem(q) and _rid(q) == rid

        if q_is_root_stem:
            if current and current['root_id'] == rid:
                current['stem'] = q
            else:
                flush()
                current = {'stem': q, 'leaves': [], 'root_id': rid}
            continue

        under_stem = is_stem(r) and _rid(q) != rid
        if under_stem:
            if current and current['root_id'] == rid:
                current['leaves'].append(q)
            else:
                flush()
                current = {'stem': r, 'leaves': [q], 'root_id': rid}
            continue

        flush()
        groups.append({'stem': None, 'leaves': [q]})

    flush()
    return groups


def token_fits_under(child_qid: str, parent_qid: str) -> bool:
    """True if the child's QNO token belongs under the parent (part path or range)."""
    child = parse_qid(child_qid)
    parent = parse_qid(parent_qid)
    if not child or not parent:
        return False
    if child.prefix != parent.prefix:
        return False
    if child.token == parent.token:
        return False
    ct, pt = child.qno, parent.qno
    if pt.qno_end is not None:
        if ct.part_path or ct.qno_end:
            return False
        return pt.qno <= ct.qno <= pt.qno_end
    if ct.qno != pt.qno or ct.qno_end:
        return False
    parent_path = pt.part_path or ''
    child_path = ct.part_path or ''
    if not child_path.startswith(parent_path):
        return False
    remainder = child_path[len(parent_path):]
    return bool(remainder)


def stem_id_query():
    """Distinct ``parent_id`` values — ids of nodes that already have children."""
    from app.models import Question
    from app import db
    return (
        db.session.query(Question.parent_id)
        .filter(Question.parent_id.isnot(None))
        .distinct()
    )


def eager_load_tree(query):
    """Load two levels of children and parents (covers the UI's 3-level tree)."""
    from sqlalchemy.orm import selectinload
    from app.models import Question
    return query.options(
        selectinload(Question.children).selectinload(Question.children),
        selectinload(Question.parent).selectinload(Question.parent),
    )


# ---------------------------------------------------------------------------
# Render plan (wired into generator / viewer)
# ---------------------------------------------------------------------------

def resolve_render_plan(questions: Iterable, mode: str = HIERARCHY_MODE_SELECTED) -> list[RenderItem]:
    """Expand a sorted selection into stem/leaf render items.

    `mode='selected'`: a selected stem expands to itself + all descendants;
    a selected leaf is emitted with its ancestors inserted once per
    contiguous run of that root. A leaf flagged `needs_prev_parts` also
    pulls in its earlier siblings (and their descendants) as `stem`-role
    background, once per run. `mode='whole'`: every selected node's root
    expands to the full tree.
    """
    selected = list(questions)
    if not selected:
        return []
    mode = mode if mode in (HIERARCHY_MODE_SELECTED, HIERARCHY_MODE_WHOLE) else HIERARCHY_MODE_SELECTED

    placed_ids = set()
    items_to_place = []

    def _nid(n):
        return getattr(n, 'id', id(n))

    def _add_chunk(nodes):
        for n in nodes:
            nid = _nid(n)
            if nid in placed_ids:
                continue
            placed_ids.add(nid)
            items_to_place.append(n)

    for q in selected:
        if mode == HIERARCHY_MODE_WHOLE:
            r = root(q)
            chunk = [r] + sorted(descendants(r), key=sort_key)
            _add_chunk(chunk)
        elif is_stem(q):
            chunk = [q] + sorted(descendants(q), key=sort_key)
            _add_chunk(chunk)
        else:
            _add_chunk([q])

    out: list[RenderItem] = []
    seen_in_run: set = set()
    prev_root_id = object()

    for node in items_to_place:
        r = root(node)
        rid = _nid(r)
        if rid != prev_root_id:
            seen_in_run = set()
            prev_root_id = rid
        for anc in ancestors(node):
            aid = _nid(anc)
            if aid in seen_in_run:
                continue
            out.append(RenderItem(
                question=anc,
                role='stem',
                seq_owner_id=None,
                depth=depth_of(anc),
            ))
            seen_in_run.add(aid)
        nid = _nid(node)
        if nid in seen_in_run:
            continue
        if (mode == HIERARCHY_MODE_SELECTED and not is_stem(node)
                and getattr(node, 'needs_prev_parts', False)):
            for sib in earlier_siblings(node):
                for ctx in [sib] + sorted(descendants(sib), key=sort_key):
                    cid = _nid(ctx)
                    if cid in seen_in_run:
                        continue
                    out.append(RenderItem(
                        question=ctx,
                        role='stem',
                        seq_owner_id=None,
                        depth=depth_of(ctx),
                    ))
                    seen_in_run.add(cid)
        role = 'stem' if is_stem(node) else 'leaf'
        out.append(RenderItem(
            question=node,
            role=role,
            seq_owner_id=None if role == 'stem' else seq_owner_id(node),
            depth=depth_of(node),
        ))
        seen_in_run.add(nid)
    return out


# ---------------------------------------------------------------------------
# ORM helpers
# ---------------------------------------------------------------------------

def _qb_detail_from_qid(qid: str) -> Optional[str]:
    parsed = parse_qid(qid)
    return parsed.detail if parsed else None


def _apply_new_row_fields(question, *, subject, source, year, paper, qno_parsed,
                          parent, extra):
    question.subject = subject
    question.source = source
    if source in ('DSE', 'CE', 'AL'):
        question.year = int(year) if year is not None else None
        question.paper = paper
    else:
        question.year = None
        question.paper = None
    question.qno = qno_parsed.qno
    question.qno_end = qno_parsed.qno_end
    question.part = qno_parsed.own_part
    question.part_sort = part_sort_value(qno_parsed.own_part) if qno_parsed.own_part else None
    question.parent_id = getattr(parent, 'id', None) if parent is not None else None
    for key, val in extra.items():
        if val is not None and hasattr(question, key):
            setattr(question, key, val)


def _find_covering_range(prefix: str, qno: int):
    from app.models import Question
    stems = (
        Question.query
        .filter(Question.qid.startswith(prefix + '_'))
        .filter(Question.qno_end.isnot(None))
        .filter(Question.parent_id.is_(None))
        .filter(Question.qno <= qno)
        .filter(Question.qno_end >= qno)
        .all()
    )
    if not stems:
        return None
    stems.sort(key=lambda s: ((s.qno_end - s.qno), s.id))
    return stems[0]


def _adopt_range_children(stem, prefix: str):
    from app import db
    from app.models import Question
    qno, qno_end = stem.qno, stem.qno_end
    if not qno_end:
        return
    orphans = (
        Question.query
        .filter(Question.qid.startswith(prefix + '_'))
        .filter(Question.id != stem.id)
        .filter(Question.parent_id.is_(None))
        .filter(Question.part.is_(None))
        .filter(Question.qno_end.is_(None))
        .filter(Question.qno >= qno)
        .filter(Question.qno <= qno_end)
        .all()
    )
    for child in orphans:
        child.parent_id = stem.id
    if orphans:
        db.session.flush()


def ensure_question(subject: str, source: str, token: str, *,
                    year=None, paper=None, detail=None, qid: Optional[str] = None,
                    **cols):
    """Find-or-create the node for `token` and any missing ancestors.

    Returns `(question, created)` where `created` is True only when **this**
    node's row was inserted (parents created along the way do not flip it).
    Flushes so `id` / `parent_id` are assigned; does not commit.

    Extra kwargs (`q_type`, `level`, `section`, ...) are applied only on
    newly inserted rows.
    """
    from app import db
    from app.models import Question

    parsed = parse_qno_token(token)
    if not parsed:
        raise HierarchyError(f'Invalid question number token: {token!r}')

    extra = {k: v for k, v in cols.items() if k in (
        'q_type', 'level', 'section', 'description', 'answer', 'comment',
    )}
    prefix = paper_prefix(subject, source, year, paper, detail)

    def _lookup_or_create(tok: ParsedQno, parent) -> tuple:
        full_qid = qid if (qid and tok.token == parsed.token) else f'{prefix}_{tok.token}'
        existing = Question.query.filter_by(qid=full_qid).first()
        if existing:
            return existing, False
        row = Question(qid=full_qid)
        _apply_new_row_fields(
            row, subject=subject, source=source, year=year, paper=paper,
            qno_parsed=tok, parent=parent, extra=extra,
        )
        if row.level is None:
            row.level = None
        if row.section is None:
            row.section = None
        db.session.add(row)
        db.session.flush()
        return row, True

    # Root (no part path), possibly a range stem.
    root_tok = ParsedQno(
        qno=parsed.qno, qno_end=parsed.qno_end if not parsed.part_path else None,
        part_path=None,
        token=format_qno_token(parsed.qno, parsed.qno_end if not parsed.part_path else None, None),
    )
    root_row, root_created = _lookup_or_create(root_tok, None)
    if root_created and root_row.qno_end:
        _adopt_range_children(root_row, prefix)

    if not parsed.part_path:
        if root_created and not root_row.qno_end and root_row.parent_id is None:
            cover = _find_covering_range(prefix, root_row.qno)
            if cover is not None:
                root_row.parent_id = cover.id
                db.session.flush()
        created = root_created if parsed.token == root_tok.token else False
        # If the requested token is the root token, created follows root_created.
        return root_row, created

    current = root_row
    segs = part_segments(parsed.part_path)
    built = ''
    last_created = False
    for i, seg in enumerate(segs):
        built += seg
        node_tok = parse_qno_token(format_qno_token(parsed.qno, None, built))
        current, last_created = _lookup_or_create(node_tok, current)
    return current, last_created


def relink_parent(question) -> None:
    """Recompute `parent_id` from `question.qid`. Creates missing ancestors.

    Roots attach to a covering range stem when one exists; otherwise
    `parent_id` is cleared. Flushes; does not commit.
    """
    from app import db

    parsed = parse_qid(question.qid)
    if not parsed:
        return
    prefix = parsed.prefix
    tok = parsed.qno
    if tok.part_path:
        parent_path = tok.parent_part_path
        parent_token = format_qno_token(tok.qno, None, parent_path)
        parent, _ = ensure_question(
            parsed.subject, parsed.source, parent_token,
            year=parsed.year, paper=parsed.paper, detail=parsed.detail,
        )
        question.parent_id = parent.id
        db.session.flush()
        return
    cover = _find_covering_range(prefix, tok.qno)
    if cover is not None and cover.id != question.id:
        question.parent_id = cover.id
    else:
        question.parent_id = None
    db.session.flush()


def collect_subtree_ids(question) -> list[int]:
    """`[question.id]` plus every descendant id (query-based, session-safe)."""
    from app.models import Question
    ids = [question.id]
    queue = [question.id]
    while queue:
        kids = Question.query.filter(Question.parent_id.in_(queue)).all()
        queue = []
        for k in kids:
            if k.id not in ids:
                ids.append(k.id)
                queue.append(k.id)
    return ids


def subtree_deepest_first(questions: list) -> list:
    """Order rows so children are deleted before their parents (RESTRICT)."""
    by_id = {q.id: q for q in questions}

    def _depth(q):
        d = 0
        seen = set()
        node = q
        while node is not None and node.parent_id:
            if node.id in seen:
                break
            seen.add(node.id)
            d += 1
            parent = by_id.get(node.parent_id)
            if parent is None:
                parent = getattr(node, 'parent', None)
            node = parent
            if node is None:
                break
        return d

    return sorted(questions, key=_depth, reverse=True)
