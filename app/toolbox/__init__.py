"""
Admin **Toolbox** — a home for self-service utilities (subject-admins + super
admins).

The blueprint is defined here; each tool registers its routes in a submodule
(see :mod:`app.toolbox.pdf`). Shared path helpers live in
:mod:`app.toolbox.common`.
"""
from __future__ import annotations

from flask import Blueprint, render_template
from flask_login import login_required

from app.utils import admin_required

toolbox_bp = Blueprint('toolbox', __name__, url_prefix='/admin/toolbox')


@toolbox_bp.route('/')
@login_required
@admin_required
def index():
    """Toolbox landing page — a grid of available tools."""
    return render_template('admin_toolbox.html')


# Register tool routes (import after blueprint exists).
from app.toolbox import pdf as _pdf  # noqa: E402,F401
