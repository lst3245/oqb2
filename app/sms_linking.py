"""Pure helpers for linking SMS identities to OQB users (contract v1.2
section 9; docs/modules/sms-sso.md). No DB, no Flask.

* ``derive_username``: the username for a JIT account (never renamed later);
* ``suggest_users``: link candidates for an admin, by staff code / name;
* ``link_refusal``: why a user may not be linked (privileged / already linked).
"""
from __future__ import annotations

import re
import unicodedata

from app.utils import validate_username

# Refusal reasons (also used as audit reason codes).
PRIVILEGED = 'privileged_account'
ALREADY_LINKED = 'already_linked'
DISABLED = 'disabled'

_UNSAFE = re.compile(r'[^A-Za-z0-9._-]+')
MAX_LEN = 80


def _ascii(text: str) -> str:
    return unicodedata.normalize('NFKD', text or '').encode('ascii', 'ignore').decode('ascii')


def _base_from_email(email) -> str:
    local = (email or '').split('@', 1)[0]
    name = _UNSAFE.sub('-', _ascii(local)).strip('.-_')
    name = re.sub(r'[-]{2,}', '-', name)
    return name[:MAX_LEN - 4].lower()


def derive_username(email, sub: str, taken) -> str:
    """Email local part (ASCII, ``validate_username``-clean, lower case), or
    ``sms-<sub[:8]>`` when that is unusable; ``-2``, ``-3`` ... on collision.
    ``taken(name)`` says whether a name is in use (compare
    case-insensitively: MariaDB does)."""
    base = _base_from_email(email)
    if not (base and validate_username(base)[0]):
        base = f'sms-{(sub or "").replace("-", "")[:8].lower() or "user"}'  # not a level
    if not taken(base):
        return base
    for n in range(2, 10000):
        name = f'{base}-{n}'
        if validate_username(name)[0] and not taken(name):
            return name
    raise RuntimeError('no free username')


def is_privileged(user) -> bool:
    """Super admins, break-glass and local-only accounts stay password-only."""
    return bool(getattr(user, 'is_super_admin', False) or getattr(user, 'local_only', False))


def link_refusal(user, issuer: str, linked_issuers=()) -> str | None:
    """``None`` when ``user`` may be linked for ``issuer``; else a reason.
    ``linked_issuers`` are the issuers ``user`` already has a link for."""
    if user is None:
        return DISABLED
    if is_privileged(user):
        return PRIVILEGED
    if issuer in set(linked_issuers or ()):
        return ALREADY_LINKED
    return None


def _norm(text) -> str:
    return re.sub(r'[^a-z0-9]+', ' ', _ascii(str(text or '')).lower()).strip()


def suggest_users(person: dict, users, *, limit: int = 5, min_score: int = 60) -> list:
    """Rank OQB users as link candidates for one roster ``person``
    (``staff_code``, ``name``, ``email``). ``users`` are objects with
    ``id``, ``username``, ``display_name``, ``staff_code``. Returns
    ``[(user, score, why)]``, best first. Suggestions only: a person confirms."""
    from rapidfuzz import fuzz
    code = _norm(person.get('staff_code'))
    name = _norm(person.get('name'))
    local = _norm((person.get('email') or '').split('@', 1)[0])
    out = []
    for u in users:
        if is_privileged(u):
            continue
        uname = _norm(u.username)
        best, why = 0, None
        if code and (code == _norm(getattr(u, 'staff_code', None)) or code == uname):
            best, why = 100, 'staff_code'
        if best < 100 and local and local == uname:
            best, why = 95, 'email'
        for label, other in (('name', getattr(u, 'display_name', None)), ('username', u.username)):
            if name and other:
                score = int(fuzz.token_sort_ratio(name, _norm(other)))
                if score > best:
                    best, why = score, label
        if best >= min_score:
            out.append((u, best, why))
    out.sort(key=lambda item: (-item[1], item[0].username.lower()))
    return out[:limit]
