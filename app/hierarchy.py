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
# QNO token: Q<digits>[-<digits>][<part-path>][~<anchor>]
#   Q5          standalone / root
#   Q23-24      range stem (shared MC preamble); qno=23, qno_end=24
#   Q3a         letter part under Q3
#   Q3ci        roman sub-part under Q3c  (path 'c'+'i')
#   Q7~c        shared block: unlettered text printed between parts, before
#               (c); parent of Q7c, Q7d, ... up to the next block at that level
#   Q7d~iii     shared block inside (d), before (d)(iii)
#
# Range and part-path are mutually exclusive. A block segment is always last
# and is never part of a descendant's token (Q7c stays Q7c under Q7~c). IMG
# multi-file `part_number` (`_2.png`) is a different axis — never reuse that
# name here. See docs/decisions/ADR-015-shared-blocks.md.
# ---------------------------------------------------------------------------

BLOCK_MARK = '~'

# Body only (no leading Q). Used inside filename / QID regexes.
QNO_BODY_PATTERN = r'\d+(?:-\d+)?(?:[a-z]+)?(?:~[a-z]+)?'

# Full token, case-sensitive for the part letters (filenames store lowercase).
QNO_TOKEN_PATTERN = r'Q' + QNO_BODY_PATTERN

# Search in a free string (filename stem, path). Digits required after Q so
# `QUE` / `CHO` never match. Not preceded/followed by alphanumerics so `P1Q5`
# is ignored and `Q5` in `..._Q5_EN_QUE` is found.
QNO_TOKEN_RE = re.compile(
    r'(?<![A-Za-z0-9~])(Q\d+(?:-\d+)?(?:[a-zA-Z]+)?(?:~[a-zA-Z]+)?)(?![A-Za-z0-9~])'
)

# Strict parse of one token (optional leading Q).
_TOKEN_PARSE_RE = re.compile(
    r'^Q?(?P<qno>\d+)(?:-(?P<qno_end>\d+))?(?P<part>[A-Za-z]*(?:~[A-Za-z]+)?)$',
    re.IGNORECASE,
)

_PART_PATH_RE = re.compile(r'^(?P<letters>[a-z]*)(?:~(?P<anchor>[a-z]+))?$')

PP_QID_RE = re.compile(
    r'^(?P<subj>[A-Z0-9]+)_(?P<source>DSE|CE|AL)_(?P<year>\d{4})_'
    r'(?P<paper>P[A-Za-z0-9]+)_(?P<token>Q\d+(?:-\d+)?(?:[A-Za-z]+)?(?:~[A-Za-z]+)?)$'
)
QB_QID_RE = re.compile(
    r'^(?P<subj>[A-Z0-9]+)_QB_(?P<detail>[^_]+)_'
    r'(?P<token>Q\d+(?:-\d+)?(?:[A-Za-z]+)?(?:~[A-Za-z]+)?)$'
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
        """Lettered parent path. A part inside a shared block still names its
        lettered parent here (``Q7c`` → root); ``structural_parent`` resolves
        the block."""
        segs = part_segments(self.part_path)
        if len(segs) <= 1:
            return None
        return ''.join(segs[:-1])

    @property
    def is_block(self) -> bool:
        return is_block_part(self.own_part)

    @property
    def block_anchor(self) -> Optional[str]:
        return block_anchor(self.own_part)


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
        m = _PART_PATH_RE.match(path)
        if not m or not (m.group('letters') or m.group('anchor')):
            raise HierarchyError('Part labels must be letters only (e.g. a, ci, or ~c for a shared block)')
        anchor = m.group('anchor')
        if anchor:
            if part_segments(anchor) != [anchor]:
                raise HierarchyError(
                    f'A shared block names one part it comes before (~c, ~iii), not {anchor!r}')
            if anchor in ('a', 'i'):
                raise HierarchyError(
                    'Text before the first part is the stem (or that part\'s own intro), not a shared block')
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


_BLOCK_LABEL_RE = re.compile(
    r'^(?:~|(?:intro|before|pre|stem|shared|lead-?in|preamble|background|block)'
    r'(?:[\s_:\-~]+(?:(?:to|for)[\s_:\-]+)?|\s*(?=\()))'
    r'\(?(?P<anchor>[a-z]{1,6})\)?$'
)


def block_label_for(anchor_path: str) -> Optional[str]:
    """Relative block label for text printed before part ``anchor_path``
    (``c`` → ``~c``, ``diii`` → ``d~iii``). Text before a first part
    (``a`` / ``i``) is the stem or that part's own intro, so ``a`` →
    ``stem`` and ``di`` → ``d``."""
    segs = part_segments(anchor_path)
    if not segs or any(is_block_part(s) for s in segs):
        return None
    prefix, own = ''.join(segs[:-1]), segs[-1]
    if own in ('a', 'i'):
        return prefix or 'stem'
    return f'{prefix}{BLOCK_MARK}{own}'


def normalize_part_box_label(raw) -> Optional[str]:
    """Pass-2 box label relative to one question crop: ``stem``, a letter path,
    or a shared block (``~c``).

    Accepts ``stem`` aliases, letters (``a``, ``ci``), a full token whose
    part path is stripped (``3a`` → ``a``, ``3`` → ``stem``), and block
    forms the model may write for text printed between parts (``intro-c``,
    ``before (c)``, ``~c``, ``3~c`` → ``~c``).
    """
    if raw is None:
        return None
    s = str(raw).strip().lower().strip('()[]')
    if not s:
        return None
    if s in PART_STEM_ALIASES:
        return 'stem'
    m = _BLOCK_LABEL_RE.match(s)
    if m:
        return block_label_for(m.group('anchor'))
    parsed = parse_qno_token(s)
    if parsed:
        return parsed.part_path or 'stem'
    pm = _PART_PATH_RE.match(s)
    if pm and pm.group('anchor'):
        return block_label_for((pm.group('letters') or '') + pm.group('anchor'))
    if s.isalpha():
        return s
    return None


def normalize_range_box_label(raw, first: int, last: int) -> Optional[str]:
    """Pass-2 box label on a range crop (``23-24``): ``stem`` for the shared
    stimulus, else a plain question number inside ``first..last`` as a string
    (``Q23`` / ``(23)`` → ``23``). Lettered parts and out-of-range numbers are
    rejected; the range token itself is the stem."""
    if raw is None:
        return None
    s = str(raw).strip().lower().strip('()[].')
    if not s:
        return None
    if s in PART_STEM_ALIASES:
        return 'stem'
    p = parse_qno_token(s)
    if not p or p.part_path:
        return None
    if p.qno_end:
        return 'stem' if (p.qno == first and p.qno_end == last) else None
    return str(p.qno) if first <= p.qno <= last else None


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


def label_is_ancestor(parent_label, child_label, labels=None, ends=None) -> bool:
    """True when plan label ``parent_label`` is a strict ancestor of
    ``child_label`` (``5`` ⊃ ``5d`` ⊃ ``5di``). Ranges have no descendants
    other than the plain numbers they cover.

    A shared block (``7~c``) is an ancestor of the parts at its level that
    print at or after its anchor (``7c``, ``7d``, ``7di``), up to the next
    block. That cut-off needs the other labels: pass ``labels`` (the
    question's label set) whenever two blocks may share a level. ``ends``
    maps a block label to its end segment (``{'4~b': 'b'}``)."""
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
    if ps and is_block_part(ps[-1]):
        level = ps[:-1]
        if len(cs) <= len(level) or cs[:len(level)] != level:
            return False
        seg = cs[len(level)]
        if not block_covers(block_anchor(ps[-1]), seg):
            return False
        ends = {normalize_plan_label(k): v for k, v in (ends or {}).items()}
        seg_ends = {ps[-1]: ends.get(p.token[1:])}
        if labels is None:
            return pick_block([ps[-1]], seg, seg_ends) == ps[-1]
        rivals = []
        for other in labels:
            o = parse_qno_token(other)
            if not o or o.qno != p.qno or o.qno_end:
                continue
            os_ = part_segments(o.part_path)
            if len(os_) == len(ps) and os_[:-1] == level and is_block_part(os_[-1]):
                rivals.append(os_[-1])
                seg_ends.setdefault(os_[-1], ends.get(o.token[1:]))
        if ps[-1] not in rivals:
            rivals.append(ps[-1])
        return pick_block(rivals, seg, seg_ends) == ps[-1]
    return len(cs) > len(ps) and cs[:len(ps)] == ps


def derive_roles(labels, ends=None) -> dict:
    """Role of every plan label in one question, derived from the label set.

    A label is a ``stem`` when another label in the set descends from it
    (``5`` with ``5a`` present, ``5d`` with ``5di``); a label with a part
    path but no descendants is a ``part``; a plain number / range with no
    descendants is a ``question``. Roles are therefore never stored as
    model output — they are recomputed whenever labels change. ``ends``
    (block label → end segment) narrows a shared block's scope.
    """
    labs = []
    for raw in labels or []:
        lab = normalize_plan_label(raw)
        if lab and lab not in labs:
            labs.append(lab)
    out = {}
    for lab in labs:
        p = parse_qno_token(lab)
        has_child = any(label_is_ancestor(lab, other, labs, ends) for other in labs
                        if other != lab)
        if has_child or (p and p.is_block):
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
    if BLOCK_MARK in path:
        letters, anchor = path.split(BLOCK_MARK, 1)
        return (letters, BLOCK_MARK + anchor)
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


def is_block_part(label: Optional[str]) -> bool:
    """True for a shared-block segment (``~c``)."""
    return bool(label) and str(label).startswith(BLOCK_MARK)


def block_anchor(label: Optional[str]) -> Optional[str]:
    """``~c`` → ``c``; anything else → None."""
    if not is_block_part(label):
        return None
    return str(label)[1:] or None


def is_block(q) -> bool:
    """True for a shared-block node (``part`` = ``~c``)."""
    return is_block_part(getattr(q, 'part', None))


def block_covers(anchor: Optional[str], seg: Optional[str],
                 end: Optional[str] = None) -> bool:
    """True when part segment ``seg`` prints at or after a block's ``anchor``
    and, when the block has an ``end``, not after that end."""
    if not anchor or not seg or is_block_part(seg):
        return False
    if part_sort_key(seg) < part_sort_key(anchor):
        return False
    return not end or part_sort_key(seg) <= part_sort_key(end)


def normalize_block_end(raw) -> Optional[str]:
    """``'(c)'`` / ``' C '`` → ``'c'``; blank / ``none`` → None."""
    if raw is None:
        return None
    s = str(raw).strip().lower().strip('()~ ')
    return None if s in ('', 'none', 'null') else s


def effective_block_end(block_part: Optional[str], end: Optional[str]) -> Optional[str]:
    """``end`` when it can bound block ``block_part`` (one plain segment of the
    anchor's kind, at or after it), else None — a stale end left behind by an
    anchor rename behaves like no end."""
    end = normalize_block_end(end)
    anchor = block_anchor(block_part)
    if not end or not anchor or part_segments(end) != [end] or is_block_part(end):
        return None
    ek, ak = part_sort_key(end), part_sort_key(anchor)
    if ek[0] != ak[0] or ek[0] == 2 or ek < ak:
        return None
    return end


def block_end_error(block_part: str, end, level_block_parts: Iterable = ()) -> Optional[str]:
    """Why ``end`` cannot bound block ``block_part`` (``~b``), or None.

    Rules: one plain part label of the anchor's kind (letter / roman), at or
    after the anchor, and before the next block's anchor at that level
    (``level_block_parts``: the block segments that share the level). Blank
    clears the end and is always valid."""
    end = normalize_block_end(end)
    if end is None:
        return None
    anchor = block_anchor(block_part)
    if not anchor:
        return 'Only a shared block has an end.'
    if part_segments(end) != [end] or is_block_part(end):
        return f'"{end}" is not a single part label such as b or iii.'
    ek, ak = part_sort_key(end), part_sort_key(anchor)
    if ek[0] == 2 or ek[0] != ak[0]:
        kind = 'roman numeral' if ak[0] == 1 else 'letter'
        return f'The end must be a {kind} at the same level as ({anchor}).'
    if ek < ak:
        return f'The end ({end}) is before the block starts at ({anchor}).'
    later = sorted((block_anchor(bp) for bp in level_block_parts
                    if bp != block_part and block_anchor(bp)
                    and part_sort_key(block_anchor(bp)) > ak),
                   key=part_sort_key)
    if later and ek >= part_sort_key(later[0]):
        return (f'The end ({end}) must come before the next shared block, '
                f'which starts at ({later[0]}).')
    return None


def pick_block(block_parts: Iterable, seg: Optional[str],
               ends: Optional[dict] = None) -> Optional[str]:
    """The block segment (``~c``) among ``block_parts`` that owns part ``seg``:
    the one with the latest anchor at or before ``seg``. None when ``seg``
    prints before every block (it stays under the lettered parent).

    ``ends`` maps a block segment to its end (``{'~b': 'b'}``). When the
    chosen block ends before ``seg``, no block owns it — an earlier block
    never resumes after a later one."""
    best = None
    for bp in block_parts:
        anchor = block_anchor(bp)
        if not block_covers(anchor, seg):
            continue
        if best is None or part_sort_key(anchor) > part_sort_key(block_anchor(best)):
            best = bp
    if best is not None and ends:
        end = effective_block_end(best, ends.get(best))
        if end and not block_covers(block_anchor(best), seg, end):
            return None
    return best


def part_sort_key(label: Optional[str]) -> tuple[int, int, str]:
    """Sort key for one segment: letters, then romans, then other.

    A block (``~c``) shares its anchor's position with an empty tie-break so
    it sorts after the parts before it and before the parts it owns."""
    if not label:
        return (2, 0, '')
    lab = label.lower()
    if is_block_part(lab):
        kind, n, _ = part_sort_key(lab[1:])
        return (kind, n, '')
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
    """True for a range preamble, a shared block, or any node that already
    has children."""
    if getattr(q, 'qno_end', None) or is_block(q):
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
    Roots have no siblings here (whole-question context is not implied).

    A shared block does not change what "earlier parts" means: for a part
    inside a block the result also holds the parts printed before the block
    at that level (``Q7c`` under ``Q7~c`` → ``Q7a``, ``Q7b``)."""
    parent = getattr(q, 'parent', None)
    if parent is None:
        return []
    me = sort_key(q)
    own = sorted((c for c in _children(parent)
                  if getattr(c, 'id', id(c)) != getattr(q, 'id', id(q))
                  and sort_key(c) < me), key=sort_key)
    if is_block(parent) and not is_block(q):
        return earlier_siblings(parent) + own
    return own


def part_path_of(q) -> Optional[str]:
    """Reconstruct the full part path from this node up to (not including) the root.
    An ancestor block's segment is skipped (``Q7c`` under ``Q7~c`` is ``c``)."""
    segs = []
    node = q
    seen = set()
    while node is not None:
        part = getattr(node, 'part', None)
        if part and (node is q or not is_block_part(part)):
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
    if old.is_block or new.is_block:
        # A block's segment never appears in its descendants' tokens, so
        # renaming the block (moving its anchor) leaves them unchanged.
        if old.is_block != new.is_block:
            raise HierarchyError('Cannot turn a shared block into a part (or vice versa)')
        return child.token

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
    if old.is_block != new.is_block:
        return 'Cannot turn a shared block into a part (or vice versa)'
    if old.is_block:
        if (old.qno != new.qno
                or part_segments(old.part_path)[:-1] != part_segments(new.part_path)[:-1]):
            return ('A shared block can only move its anchor (Q7~c → Q7~d); '
                    'it stays in the same question and level')
        return None
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

def block_display_label(part: Optional[str], end: Optional[str] = None) -> str:
    """Human label for a block segment: ``~c`` → ``before (c)``; with an end,
    ``before (c), (c) only`` / ``before (c), up to (d)``."""
    anchor = block_anchor(part)
    if not anchor:
        return ''
    end = effective_block_end(part, end)
    if not end:
        return f'before ({anchor})'
    if end == anchor:
        return f'before ({anchor}), ({anchor}) only'
    return f'before ({anchor}), up to ({end})'


def breadcrumb_parts(q) -> list[dict]:
    """Root-first ``[{id, qid, label, kind}]``. Root uses the QNO token; parts
    use ``(a)``; a shared block is ``before (c)`` with ``kind='block'``.

    Code that prints a part's position (``(c)(i)``) skips ``kind='block'``
    crumbs other than the node itself — the block is not a printed label."""
    chain = ancestors(q) + [q]
    out = []
    for node in chain:
        qid = getattr(node, 'qid', '') or ''
        parsed = parse_qid(qid) if qid else None
        part = getattr(node, 'part', None)
        has_parent = bool(getattr(node, 'parent_id', None) or getattr(node, 'parent', None))
        kind = 'part' if has_parent else 'root'
        if part and is_block_part(part):
            label = block_display_label(part, getattr(node, 'block_end', None))
            kind = 'block'
        elif part and has_parent:
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
            'kind': kind,
        })
    return out


def part_position_label(q, relative_to=None) -> str:
    """Printed position of ``q``: ``(c)(i)`` (blocks skipped), relative to
    the ancestor ``relative_to`` when given. A block itself reads
    ``before (c)``."""
    crumbs = breadcrumb_parts(q)
    if relative_to is not None:
        rid = getattr(relative_to, 'id', None)
        for i, c in enumerate(crumbs):
            if c['id'] == rid:
                crumbs = crumbs[i + 1:]
                break
    else:
        crumbs = [c for c in crumbs if c['kind'] != 'root']
    if crumbs and crumbs[-1]['kind'] == 'block':
        head = ''.join(c['label'] for c in crumbs[:-1] if c['kind'] == 'part')
        return (head + ' ' + crumbs[-1]['label']).strip()
    return ''.join(c['label'] for c in crumbs if c['kind'] == 'part')


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


def _bucket_question_weight(stem, rows) -> int:
    """How many *questions* a dashboard bucket represents.

    A lettered-part tree (Q5 / Q5a / Q5b) is one question. A range stem
    (Q16-17: shared MC data, independent paper numbers) is one question per
    matched leaf. A standalone is one. An explicit stem-only row (ids/qids
    override, no leaves on this result) still counts as one so the header
    occupies a slot.
    """
    node = stem
    if node is None and rows:
        node = root(rows[0])
    if node is not None and getattr(node, 'qno_end', None):
        return max(len(rows), 1)
    return 1


def paginate_by_root(sorted_questions, page: int, per_page: int) -> tuple[list, int, int]:
    """Slice a sorted result list by *whole question* instead of by row.

    Every row is bucketed under its root id (first-seen order along the sort).
    A lettered-part question occupies one slot; a range stem occupies one slot
    per matched leaf (Q16 and Q17 are two questions that stay on the same
    page). Matched rows of a root are always contiguous and never split across
    pages. An explicit stem row (``ids`` / ``qids`` overrides) shares the
    bucket of its leaves and is placed first in it.

    Returns ``(page_items, total_questions, total_rows)`` where ``total_rows``
    is the number of non-stem rows (parts / standalones / range leaves).
    ``page`` is 1-based; ``per_page`` is a question-weight budget. A bucket
    that does not fit the remaining budget starts the next page (a single
    overweight bucket still gets a page of its own). Topic sorts that
    interleave a root's parts snap them together at the first sibling.
    """
    per_page = max(1, int(per_page or 1))
    page = max(1, int(page or 1))
    order: list = []
    buckets: dict = {}
    for q in sorted_questions:
        r = root(q)
        rid = getattr(r, 'id', id(r))
        if rid not in buckets:
            buckets[rid] = {'stem': None, 'rows': []}
            order.append(rid)
        b = buckets[rid]
        if is_stem(q) and getattr(q, 'id', id(q)) == rid:
            b['stem'] = q
        else:
            b['rows'].append(q)

    pages: list[list] = []
    current: list = []
    current_w = 0
    total_questions = 0
    for rid in order:
        w = _bucket_question_weight(buckets[rid]['stem'], buckets[rid]['rows'])
        total_questions += w
        if current and current_w + w > per_page:
            pages.append(current)
            current = [rid]
            current_w = w
        else:
            current.append(rid)
            current_w += w
    if current:
        pages.append(current)

    page_rids = pages[page - 1] if 1 <= page <= len(pages) else []
    page_items: list = []
    for rid in page_rids:
        b = buckets[rid]
        if b['stem'] is not None:
            page_items.append(b['stem'])
        page_items.extend(b['rows'])
    total_rows = sum(len(b['rows']) for b in buckets.values())
    return page_items, total_questions, total_rows


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
    if pt.is_block:
        return label_is_ancestor(pt.token, ct.token)
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


def leaf_clause():
    """SQL criterion for leaf rows: no children and not a shared block (a
    childless block is still background text, never a selectable leaf)."""
    from sqlalchemy import and_, or_
    from app.models import Question
    return and_(
        ~Question.id.in_(stem_id_query()),
        or_(Question.part.is_(None), ~Question.part.like(BLOCK_MARK + '%')),
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
        nid = _nid(node)
        context = list(ancestors(node))
        if (nid not in seen_in_run and mode == HIERARCHY_MODE_SELECTED
                and not is_stem(node) and getattr(node, 'needs_prev_parts', False)):
            for sib in earlier_siblings(node):
                context += [sib] + list(descendants(sib))
        # Paper order: with a shared block, an earlier part (a) prints before
        # the block (Q7~c) that is this part's ancestor.
        for ctx in sorted(context, key=sort_key):
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
        if nid in seen_in_run:
            continue
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
        parent = current
        if not is_block_part(seg):
            parent = block_child_for(current, seg) or current
        current, last_created = _lookup_or_create(node_tok, parent)
        if last_created and is_block_part(seg):
            relink_block_level(parent)
    return current, last_created


def block_child_for(parent, seg: Optional[str]):
    """The shared-block child of ``parent`` that owns part segment ``seg``
    (``Q7~c`` for ``c`` / ``d``), or None. Query-based (sees flushed rows)."""
    from app.models import Question
    if parent is None or getattr(parent, 'id', None) is None or not seg:
        return None
    blocks = (Question.query
              .filter(Question.parent_id == parent.id)
              .filter(Question.part.like(BLOCK_MARK + '%'))
              .all())
    by_part = {b.part: b for b in blocks}
    ends = {b.part: getattr(b, 'block_end', None) for b in blocks}
    chosen = pick_block(by_part.keys(), seg, ends)
    return by_part.get(chosen) if chosen else None


def plan_block_level(parent_id, members, blocks, exclude_id=None) -> list:
    """Pure: ``[(member, target_id)]`` for every lettered node at one level
    whose parent must change so it sits under the block that owns it (block
    ends honoured), or under ``parent_id`` when none does. ``members`` are
    the level's parts (the parent's non-block children plus every block's
    children); ``blocks`` the level's block rows (``id``, ``part``,
    ``block_end``). ``exclude_id`` is a block being dissolved."""
    live = [b for b in blocks if getattr(b, 'id', None) != exclude_id]
    by_part = {b.part: b for b in live}
    ends = {b.part: getattr(b, 'block_end', None) for b in live}
    moves = []
    for m in sorted(members, key=lambda m: part_sort_key(getattr(m, 'part', None))):
        if not getattr(m, 'part', None) or is_block(m):
            continue
        chosen = pick_block(by_part.keys(), m.part, ends)
        target = by_part[chosen].id if chosen else parent_id
        if m.parent_id != target:
            moves.append((m, target))
    return moves


def _block_level_rows(parent):
    from app.models import Question
    kids = Question.query.filter(Question.parent_id == parent.id).all()
    blocks = [k for k in kids if is_block(k)]
    members = [k for k in kids if not is_block(k)]
    for b in blocks:
        members += Question.query.filter(Question.parent_id == b.id).all()
    return blocks, members


def relink_block_level(parent, exclude_id=None) -> int:
    """Re-home every lettered node at ``parent``'s level (its own children
    and its blocks' children) under the block that owns it, or ``parent``
    when none does (see :func:`plan_block_level`). ``exclude_id`` is a block
    being deleted: its parts move up. Returns the number of rows moved.
    Flushes; does not commit."""
    from app import db
    if parent is None or getattr(parent, 'id', None) is None:
        return 0
    blocks, members = _block_level_rows(parent)
    moves = plan_block_level(parent.id, members, blocks, exclude_id=exclude_id)
    for m, target in moves:
        m.parent_id = target
    if moves:
        db.session.flush()
    return len(moves)


def parse_block_end_rows(lines) -> list:
    """Pure: ``[(block_qid, end_or_None)]`` from ``block_qid,end_part`` CSV
    lines. Blank lines, ``#`` comments and a ``block_qid`` header are skipped;
    an empty / ``none`` end clears it. Raises :class:`HierarchyError` on a
    row without a QID."""
    import csv
    out = []
    for n, row in enumerate(csv.reader(lines), start=1):
        cells = [c.strip() for c in row]
        if not cells or not any(cells) or cells[0].startswith('#'):
            continue
        if cells[0].lower() == 'block_qid':
            continue
        if not cells[0]:
            raise HierarchyError(f'Line {n}: missing block_qid.')
        out.append((cells[0], normalize_block_end(cells[1] if len(cells) > 1 else None)))
    return out


def block_end_choices(block_part, member_parts, level_block_parts=()) -> list:
    """Pure: the part segments a block may end after, in print order
    (existing parts at its level that pass :func:`block_end_error`)."""
    out = []
    for p in sorted(set(p for p in member_parts if p and not is_block_part(p)),
                    key=lambda p: (part_sort_value(p), p)):
        if block_end_error(block_part, p, level_block_parts) is None:
            out.append(p)
    return out


def block_end_options(block) -> list:
    """Valid ``block_end`` values for a block row (see :func:`block_end_choices`)."""
    if not is_block(block) or block.parent is None:
        return []
    blocks, members = _block_level_rows(block.parent)
    return block_end_choices(block.part, [m.part for m in members],
                             [b.part for b in blocks])


def set_block_end(block, end, *, dry_run: bool = False) -> dict:
    """Set (or clear, ``end=None``) a shared block's end and re-home its
    level. Raises :class:`HierarchyError` on an invalid end. Returns
    ``{block_qid, end, old_end, changed, moves: [{qid, from_qid, to_qid}],
    leaving: [qid], joining: [qid]}``. ``dry_run`` computes the same result
    without touching the session. Flushes; does not commit."""
    from types import SimpleNamespace
    from app import db
    if not is_block(block):
        raise HierarchyError(f'{getattr(block, "qid", "?")} is not a shared block.')
    parent = block.parent
    if parent is None:
        raise HierarchyError(f'{block.qid} has no parent question.')
    end = normalize_block_end(end)
    blocks, members = _block_level_rows(parent)
    err = block_end_error(block.part, end, [b.part for b in blocks])
    if err:
        raise HierarchyError(err)
    proxies = [SimpleNamespace(id=b.id, part=b.part,
                               block_end=end if b.id == block.id else b.block_end)
               for b in blocks]
    moves = plan_block_level(parent.id, members, proxies)
    qids = {parent.id: parent.qid, **{b.id: b.qid for b in blocks}}
    old_end = normalize_block_end(block.block_end)
    out = {
        'block_qid': block.qid,
        'end': end,
        'old_end': old_end,
        'changed': end != old_end or bool(moves),
        'moves': [{'qid': m.qid, 'from_qid': qids.get(m.parent_id),
                   'to_qid': qids.get(t)} for m, t in moves],
        'leaving': [m.qid for m, t in moves if m.parent_id == block.id],
        'joining': [m.qid for m, t in moves if t == block.id],
    }
    if not dry_run:
        block.block_end = end
        for m, target in moves:
            m.parent_id = target
        db.session.flush()
    return out


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
        if tok.is_block:
            question.parent_id = parent.id
            db.session.flush()
            relink_block_level(parent)
            return
        owner = block_child_for(parent, tok.own_part)
        question.parent_id = (owner or parent).id
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
