"""Shared path/filename helpers for Toolbox tools (PDF and future tools)."""
from __future__ import annotations

import os
import re

from flask import current_app


def pdf_source_root() -> str:
    return os.path.abspath(current_app.config.get('PDF_SOURCE_PATH', ''))


def safe_join(base: str, *paths) -> str | None:
    # Delegate to the central hardened join (fixes the sibling-prefix trap).
    from app import storage
    return storage.safe_join(base, *paths)


def safe_filename(name: str, fallback: str) -> str:
    # Replace characters that are unsafe in filenames; keep spaces.
    name = re.sub(r'[^\w\-. ]+', '_', (name or '').strip())
    # Collapse runs of whitespace to a single space (don't turn them into _).
    name = re.sub(r'\s+', ' ', name).strip('. ')
    return name[:80] or fallback
