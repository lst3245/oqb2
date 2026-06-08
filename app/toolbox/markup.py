"""Markup tool routes."""
from __future__ import annotations

import plistlib
from pathlib import Path

from flask import Response, current_app, redirect, render_template, request, send_from_directory, url_for
from flask_login import login_required

from app.toolbox import toolbox_bp

_SIGNED_SHORTCUT_NAME = 'Markup-OQB-signed.shortcut'


def _markup_static_dir() -> Path:
    return Path(current_app.root_path) / 'static' / 'markup'


def build_ios_share_shortcut(base_url: str) -> bytes:
    """Build an unsigned iOS Shortcut plist (PairDrop-style share → clipboard → open URL).

    Note: iOS 15+ requires Apple-signed shortcuts for direct file import. This file is
    useful for ``shortcuts://import-shortcut`` links, Mac ``shortcuts sign``, or iOS 14.
    """
    paste_url = f"{base_url.rstrip('/')}/admin/toolbox/markup?ios-share=paste"
    plist = {
        'WFWorkflowActions': [
            {
                'WFWorkflowActionIdentifier': 'is.workflow.actions.image.convert',
                'WFWorkflowActionParameters': {
                    'WFImageFormat': 'JPEG',
                    'WFImageCompressionQuality': 0.85,
                },
            },
            {
                'WFWorkflowActionIdentifier': 'is.workflow.actions.base64encode',
                'WFWorkflowActionParameters': {
                    'WFEncodeMode': 'Encode',
                },
            },
            {
                'WFWorkflowActionIdentifier': 'is.workflow.actions.setclipboard',
                'WFWorkflowActionParameters': {},
            },
            {
                'WFWorkflowActionIdentifier': 'is.workflow.actions.url',
                'WFWorkflowActionParameters': {
                    'WFURLActionURL': paste_url,
                },
            },
            {
                'WFWorkflowActionIdentifier': 'is.workflow.actions.openurl',
                'WFWorkflowActionParameters': {},
            },
        ],
        'WFWorkflowClientRelease': '2.0',
        'WFWorkflowClientVersion': '1302.1.3',
        'WFWorkflowMinimumClientVersion': 700,
        'WFWorkflowMinimumClientVersionString': '700',
        'WFWorkflowName': 'Markup (OQB)',
        'WFWorkflowDescription': (
            'Share an image to Markup. Converts to JPEG, copies base64 to the clipboard, '
            'then opens Markup where you tap Import → Paste from clipboard.'
        ),
        'WFWorkflowIcon': {
            'WFWorkflowIconStartColor': 4282601983,
            'WFWorkflowIconGlyphNumber': 59511,
        },
        'WFWorkflowImportQuestions': [],
        'WFWorkflowTypes': ['ActionExtension'],
        'WFWorkflowInputContentItemClasses': [
            'WFImageContentItem',
            'WFAVAssetContentItem',
            'WFGenericFileContentItem',
        ],
    }
    return plistlib.dumps(plist, fmt=plistlib.FMT_BINARY)


def _shortcut_download_url() -> str:
    return url_for('toolbox.markup_ios_shortcut', _external=True)


@toolbox_bp.route('/markup')
@login_required
def markup_tool():
    """Mobile-friendly drawing and image-markup workspace."""
    return render_template(
        'markup.html',
        ios_shortcut_download_url=_shortcut_download_url(),
    )


@toolbox_bp.route('/markup/ios-shortcut.shortcut')
@login_required
def markup_ios_shortcut():
    """Download the iOS Share Sheet shortcut for this server (signed copy preferred)."""
    signed_path = _markup_static_dir() / _SIGNED_SHORTCUT_NAME
    if signed_path.is_file():
        return send_from_directory(
            signed_path.parent,
            signed_path.name,
            mimetype='application/octet-stream',
            as_attachment=True,
            download_name='Markup-OQB.shortcut',
        )

    base_url = request.url_root.rstrip('/')
    data = build_ios_share_shortcut(base_url)
    return Response(
        data,
        mimetype='application/octet-stream',
        headers={
            'Content-Disposition': 'attachment; filename="Markup-OQB.shortcut"',
            'Cache-Control': 'no-store',
        },
    )


@toolbox_bp.route('/markup/share-target', methods=['POST'])
@login_required
def markup_share_target():
    """Fallback for Web Share Target POSTs when the service worker is inactive."""
    return redirect(url_for('toolbox.markup_tool', shared='1'))
