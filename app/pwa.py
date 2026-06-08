"""Progressive Web App endpoints.

The service worker needs root scope so Android can deliver Web Share Target
POSTs to it. Flask's built-in static route cannot attach the required
Service-Worker-Allowed header, so these tiny routes serve the PWA files.
"""
from __future__ import annotations

import os

from flask import Blueprint, current_app, send_from_directory


pwa_bp = Blueprint('pwa', __name__)


def _markup_static_dir() -> str:
    return os.path.join(current_app.static_folder, 'markup')


@pwa_bp.route('/manifest.webmanifest')
def manifest():
    return send_from_directory(
        _markup_static_dir(),
        'manifest.webmanifest',
        mimetype='application/manifest+json',
    )


@pwa_bp.route('/sw.js')
def service_worker():
    response = send_from_directory(
        _markup_static_dir(),
        'sw.js',
        mimetype='application/javascript',
    )
    response.headers['Service-Worker-Allowed'] = '/'
    response.headers['Cache-Control'] = 'no-cache'
    return response
