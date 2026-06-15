"""
Central storage path helpers for the unified Storage tree.

This module is the single source of truth for resolving the Shared / System /
User roots and for deriving per-user and per-subject directories. It also
provides a hardened ``safe_join`` that fixes the sibling-prefix trap present in
the older ``startswith``-based joins (e.g. ``Q:\\Source`` vs ``Q:\\SourceBackup``).

Layout (defaults; each child overridable via its own env var):

    STORAGE_PATH/                 (Q:\\Storage)
      Shared/                     per-subject shared files (role-gated)
        <SUBJECT_ID>/
        _archive/                 migrated legacy Source_PDF (super-admin only)
      System/                     server-internal caches/temp
        doc_thumbnails/
        .pdf_import/  .toolbox/
      User/
        <username>/               personal home (full CRUD by owner)
          generated/              relocated generated documents

Everything reads from ``current_app.config`` so DB-backed System Settings and
``.env`` overrides are both honoured.
"""
from __future__ import annotations

import os
import re

from flask import current_app


# ---------------------------------------------------------------------------
# Hardened path join
# ---------------------------------------------------------------------------

def safe_join(base: str, *paths) -> str | None:
    """Join ``*paths`` under ``base`` and return the absolute result, or
    ``None`` if the result escapes ``base``.

    Uses ``os.path.commonpath`` (case-insensitive via ``normcase``) so a path
    that merely shares a string prefix with ``base`` (``Q:\\Source`` vs
    ``Q:\\SourceBackup``) is correctly rejected, and a different drive raises
    ``ValueError`` (also rejected).
    """
    base_abs = os.path.abspath(base)
    rel = [str(p) for p in paths if p not in (None, '')]
    target = os.path.abspath(os.path.join(base_abs, *rel)) if rel else base_abs

    base_n = os.path.normcase(base_abs)
    target_n = os.path.normcase(target)
    try:
        if os.path.commonpath([base_n, target_n]) != base_n:
            return None
    except ValueError:
        # Different drives / mix of abs+rel — never safe.
        return None
    return target


# ---------------------------------------------------------------------------
# Root accessors (read from current_app.config)
# ---------------------------------------------------------------------------

def source_path() -> str:
    return os.path.abspath(current_app.config['SOURCE_PATH'])


def storage_path() -> str:
    return os.path.abspath(current_app.config['STORAGE_PATH'])


def shared_path() -> str:
    return os.path.abspath(current_app.config['SHARED_PATH'])


def system_path() -> str:
    return os.path.abspath(current_app.config['SYSTEM_PATH'])


def user_path() -> str:
    return os.path.abspath(current_app.config['USER_PATH'])


def output_path() -> str:
    """Legacy generated-files base (fallback for un-migrated files)."""
    return os.path.abspath(current_app.config['OUTPUT_PATH'])


# ---------------------------------------------------------------------------
# Username -> filesystem-safe folder name
# ---------------------------------------------------------------------------

# Allowed characters in a (validated) username. Folder names derive from this;
# anything outside the set is collapsed to '_' for legacy/unsafe names so we
# never place a raw, unvalidated username on disk.
USERNAME_SAFE_RE = re.compile(r'[^A-Za-z0-9._-]+')


def safe_username(user) -> str:
    """Return a filesystem-safe folder name for ``user``.

    For usernames that pass ``app.utils.validate_username`` this equals the
    username verbatim. Legacy/unsafe names are sanitised (and never empty).
    Accepts a ``User`` object or a raw username string.
    """
    if hasattr(user, 'username'):
        name = user.username or ''
        uid = getattr(user, 'id', None)
    else:
        name = str(user or '')
        uid = None
    safe = USERNAME_SAFE_RE.sub('_', name).strip('. ')
    safe = safe[:80]
    if not safe or safe in ('.', '..'):
        safe = f'user_{uid}' if uid is not None else 'user_unknown'
    return safe


# ---------------------------------------------------------------------------
# Derived directories
# ---------------------------------------------------------------------------

def user_home(user) -> str:
    """Absolute path to a user's personal home folder (not auto-created)."""
    return os.path.join(user_path(), safe_username(user))


def user_generated_dir(user) -> str:
    """Absolute path to a user's ``generated/`` subfolder (not auto-created)."""
    return os.path.join(user_home(user), 'generated')


def ensure_user_generated_dir(user) -> str:
    """Create (if needed) and return the user's ``generated/`` directory."""
    path = user_generated_dir(user)
    os.makedirs(path, exist_ok=True)
    return path


def shared_subject_dir(subject_id: str) -> str | None:
    """Absolute path to a subject's Shared folder, or ``None`` if the id is
    unsafe. Subject IDs are constrained to ``^[A-Z0-9]{1,10}$`` elsewhere, so
    this is defence-in-depth."""
    sid = (subject_id or '').strip()
    if not sid:
        return None
    return safe_join(shared_path(), sid)


def shared_archive_dir() -> str:
    """Folder for migrated legacy Source_PDF content (super-admin only)."""
    return os.path.join(shared_path(), '_archive')


# ---------------------------------------------------------------------------
# Tree creation (startup + migration)
# ---------------------------------------------------------------------------

def ensure_storage_tree() -> None:
    """Create the base Storage tree (Shared / System / System/doc_thumbnails /
    User). Best-effort: failures are swallowed so a misconfigured path during
    early boot does not crash the app (paths are validated again on use)."""
    try:
        for path in (storage_path(), shared_path(), system_path(), user_path(),
                     os.path.join(system_path(), 'doc_thumbnails')):
            os.makedirs(path, exist_ok=True)
    except OSError:
        pass
