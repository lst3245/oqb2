"""Shared path/filename helpers for Toolbox tools (PDF and future tools)."""
from __future__ import annotations

import os
import re

from flask import current_app


def pdf_source_root() -> str:
    return os.path.abspath(current_app.config.get('PDF_SOURCE_PATH', ''))


def safe_join(base: str, *paths) -> str | None:
    base = os.path.abspath(base)
    target = os.path.abspath(os.path.join(base, *paths))
    if not os.path.normcase(target).startswith(os.path.normcase(base)):
        return None
    return target


def safe_filename(name: str, fallback: str) -> str:
    name = re.sub(r'[^\w\-. ]+', '_', (name or '').strip())
    name = re.sub(r'\s+', '_', name).strip('._')
    return name[:80] or fallback
