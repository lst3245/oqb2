"""Markup tool routes."""
from __future__ import annotations

from flask import redirect, render_template, url_for
from flask_login import login_required

from app.toolbox import toolbox_bp


@toolbox_bp.route('/markup')
@login_required
def markup_tool():
    """Mobile-friendly drawing and image-markup workspace."""
    return render_template('markup.html')


@toolbox_bp.route('/markup/share-target', methods=['POST'])
@login_required
def markup_share_target():
    """Fallback for Web Share Target POSTs when the service worker is inactive.

    Android normally routes share-target POSTs through ``/sw.js`` first, where
    the uploaded image can be stashed in Cache API before the browser opens the
    tool. If that interception has not happened yet, still open the tool so the
    user can import with the in-app upload button.
    """
    return redirect(url_for('toolbox.markup_tool', shared='1'))
