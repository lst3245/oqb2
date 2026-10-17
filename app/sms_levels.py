"""SMS level labels (contract v1.2 section 5) and how they map onto OQB levels.

The only module that spells the SMS level names. The verifier, trust list,
feed, admin UI and tests import these constants; if SMS (or the school)
renames a label, this file is the one place to change. Stored SMS levels
(``user_sms_levels.level``) hold the *OQB* level name returned by
``to_oqb_level``, so a label rename never needs a data migration.
"""
from __future__ import annotations

NONE = 'none'
VIEWER = 'viewer'
USER = 'user'
ADMIN = 'admin'

SMS_LEVELS = (NONE, VIEWER, USER, ADMIN)
# ``default_level`` (every subject) and ``subjects[].level`` (one subject).
DEFAULT_LEVELS = (NONE, VIEWER, ADMIN)
SUBJECT_LEVELS = (VIEWER, USER, ADMIN)
RANK = {NONE: 0, VIEWER: 1, USER: 2, ADMIN: 3}

# Non-primary issuers: default_level at most viewer (none stays none),
# subject levels at most user. Never admin.
CAP_DEFAULT = VIEWER
CAP_SUBJECT = USER

# Display names (contract v1.2: SMS shows OQB's names). Catalogue keys for t().
DISPLAY = {NONE: 'No access', VIEWER: 'Viewer', USER: 'User', ADMIN: 'Admin'}

# SMS label -> OQB internal level (``user_subject_permissions.role`` names).
# ``None`` means no access from this row.
_TO_OQB = {NONE: None, VIEWER: 'viewer', USER: 'user', ADMIN: 'admin'}


def rank(level) -> int:
    return RANK.get(level, 0)


def is_default_level(value) -> bool:
    return isinstance(value, str) and value in DEFAULT_LEVELS


def is_subject_level(value) -> bool:
    return isinstance(value, str) and value in SUBJECT_LEVELS


def above(subject_level, default_level) -> bool:
    """Subject entries are listed only when strictly above ``default_level``."""
    return rank(subject_level) > rank(default_level)


def cap(level, kind: str):
    """Apply the non-primary cap. ``kind`` is ``'default'`` or ``'subject'``."""
    limit = CAP_DEFAULT if kind == 'default' else CAP_SUBJECT
    return level if rank(level) <= rank(limit) else limit


def to_oqb_level(level):
    """OQB level name for an SMS label, or ``None`` for no access."""
    return _TO_OQB.get(level)


def display(level) -> str:
    """English display name (pass through ``t()`` for the UI language)."""
    return DISPLAY.get(level, str(level or ''))


def oqb_cap(kind: str):
    """The cap expressed as an OQB level name (used by ``authz``)."""
    return to_oqb_level(CAP_DEFAULT if kind == 'default' else CAP_SUBJECT)
