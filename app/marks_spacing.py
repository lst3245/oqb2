"""Per-subject "spacing by marks" rules for document generation. Pure.

Stored as JSON in ``Subject.marks_spacing``::

    {"before": {"bands": [{"max": 7, "mode": "lines", "count": 0},
                          {"max": null, "mode": "pages", "count": 1}],
                "per_mark": null},
     "after":  {"bands": [{"max": 2, "mode": "lines", "count": 4},
                          {"max": null, "mode": "pages", "count": 2}],
                "per_mark": {"base": 1, "lines_per_mark": 2, "max_lines": 40},
                "per": "part"}}

``before`` is evaluated once per whole question on its total marks
(``hierarchy.question_marks``); ``after`` per printed leaf on its own marks,
or with ``"per": "question"`` once after the question's last printed entry
on the whole-question total.
Bands are sorted by ``max`` (inclusive upper bound, ``null`` = open-ended,
last). The first band with ``marks <= max`` wins; when none matches,
``per_mark`` (``round(base + lines_per_mark * marks)``, capped at
``max_lines``) applies; otherwise ``resolve`` returns None and the caller
uses its manual lines value. ``pages`` with ``count`` N means N page breaks.
"""
import json

SIDES = ('before', 'after')
MODES = ('lines', 'pages')
AFTER_PER = ('part', 'question')
MAX_BANDS = 12
MAX_LINES = 200
MAX_PAGES = 10
MAX_MARKS_BOUND = 999


class MarksSpacingError(ValueError):
    """Invalid rule set (message is user-facing)."""


def _int(value, label, lo, hi):
    if isinstance(value, bool):
        raise MarksSpacingError(f'{label} must be a whole number')
    try:
        f = float(value)
    except (TypeError, ValueError):
        raise MarksSpacingError(f'{label} must be a whole number') from None
    if not f.is_integer():
        raise MarksSpacingError(f'{label} must be a whole number')
    n = int(f)
    if not lo <= n <= hi:
        raise MarksSpacingError(f'{label} must be between {lo} and {hi}')
    return n


def _band(raw, side, i):
    if not isinstance(raw, dict):
        raise MarksSpacingError(f'{side} band {i}: expected an object')
    label = f'{side.capitalize()} band {i}'
    mx = raw.get('max')
    mx = None if mx in (None, '') else _int(mx, f'{label} "up to" marks', 0, MAX_MARKS_BOUND)
    mode = str(raw.get('mode') or 'lines').strip().lower()
    if mode in ('page', 'page_break', 'pagebreak'):
        mode = 'pages'
    if mode not in MODES:
        raise MarksSpacingError(f'{label}: mode must be "lines" or "pages"')
    hi = MAX_PAGES if mode == 'pages' else MAX_LINES
    lo = 1 if mode == 'pages' else 0
    count = _int(raw.get('count', 1 if mode == 'pages' else 0), f'{label} count', lo, hi)
    return {'max': mx, 'mode': mode, 'count': count}


def _per_mark(raw, side):
    if raw in (None, '', {}):
        return None
    if not isinstance(raw, dict):
        raise MarksSpacingError(f'{side} per-mark rule: expected an object')
    label = f'{side.capitalize()} per-mark rule'
    base = _int(raw.get('base', 0) or 0, f'{label} base lines', 0, MAX_LINES)
    try:
        lpm = float(raw.get('lines_per_mark', 0) or 0)
    except (TypeError, ValueError):
        raise MarksSpacingError(f'{label}: lines per mark must be a number') from None
    if not 0 <= lpm <= 50:
        raise MarksSpacingError(f'{label}: lines per mark must be between 0 and 50')
    mx = raw.get('max_lines')
    mx = None if mx in (None, '') else _int(mx, f'{label} max lines', 0, MAX_LINES)
    if base == 0 and lpm == 0:
        return None
    return {'base': base, 'lines_per_mark': lpm, 'max_lines': mx}


def _side(raw, side):
    if raw in (None, ''):
        return {'bands': [], 'per_mark': None}
    if not isinstance(raw, dict):
        raise MarksSpacingError(f'{side}: expected an object')
    bands_raw = raw.get('bands') or []
    if not isinstance(bands_raw, list):
        raise MarksSpacingError(f'{side}: bands must be a list')
    if len(bands_raw) > MAX_BANDS:
        raise MarksSpacingError(f'{side}: at most {MAX_BANDS} bands')
    bands = [_band(b, side, i + 1) for i, b in enumerate(bands_raw)]
    bounds = [b['max'] for b in bands]
    if bounds.count(None) > 1:
        raise MarksSpacingError(f'{side.capitalize()}: only one band may be open-ended')
    finite = [m for m in bounds if m is not None]
    if len(set(finite)) != len(finite):
        raise MarksSpacingError(f'{side.capitalize()}: two bands share the same "up to" marks')
    bands.sort(key=lambda b: (b['max'] is None, b['max'] or 0))
    out = {'bands': bands, 'per_mark': _per_mark(raw.get('per_mark'), side)}
    if side == 'after':
        per = str(raw.get('per') or 'part').strip().lower()
        if per not in AFTER_PER:
            raise MarksSpacingError('After: "per" must be "part" or "question"')
        out['per'] = per
    return out


def is_empty(rules) -> bool:
    if not rules:
        return True
    return all(not rules.get(s, {}).get('bands') and not rules.get(s, {}).get('per_mark')
               for s in SIDES)


def normalize_rules(raw):
    """Validate and canonicalise a rule set (dict or JSON text). Returns the
    canonical dict, or None when it holds no rule at all. Raises
    ``MarksSpacingError`` with a user-facing message."""
    if raw is None or raw == '':
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raise MarksSpacingError('Rules are not valid JSON') from None
    if not isinstance(raw, dict):
        raise MarksSpacingError('Rules must be an object')
    rules = {s: _side(raw.get(s), s) for s in SIDES}
    return None if is_empty(rules) else rules


def load_rules(text):
    """Stored JSON -> canonical rules, or None (blank or unreadable). Never
    raises: a bad stored value must not break document generation."""
    try:
        return normalize_rules(text)
    except MarksSpacingError:
        return None


def after_per(rules):
    """``'part'`` (after every printed part, on its own marks) or
    ``'question'`` (once after the last part, on the whole-question total)."""
    return ((rules or {}).get('after') or {}).get('per') or 'part'


def dumps(rules):
    return None if is_empty(rules) else json.dumps(rules, separators=(',', ':'))


def resolve(rules, side, marks):
    """``(mode, count)`` for ``marks`` on ``side`` (``'before'`` /
    ``'after'``), mode ``'lines'`` or ``'pages'``. None when there is no
    rule for it (no rules, marks unknown, no band and no per-mark rule)."""
    if not rules or marks is None:
        return None
    cfg = rules.get(side) or {}
    for band in cfg.get('bands') or ():
        if band['max'] is None or marks <= band['max']:
            return (band['mode'], band['count'])
    pm = cfg.get('per_mark')
    if pm:
        lines = int(round(pm['base'] + pm['lines_per_mark'] * marks))
        if pm.get('max_lines') is not None:
            lines = min(lines, pm['max_lines'])
        return ('lines', max(0, min(lines, MAX_LINES)))
    return None
