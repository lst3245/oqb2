"""Agent wake webhook (docs/modules/requests.md, ADR-023).

When a person's action puts a request into the agent queue (``needs_agent``
turns true), OQB POSTs one small JSON notice to ``AGENT_WAKE_WEBHOOK_URL`` so
a scheduled agent (a Grok Bot routine) can start now instead of waiting for
its next poll. The agent still reads and writes only through ``POST /mcp``.
The notice carries ids, status and the title: no message text, no
attachments, no tokens.

Best effort: :func:`notify` runs after the route's commit, posts on a daemon
thread with a short timeout, logs failures and never raises. A second notice
for the same request within ``DEDUPE_SECONDS`` is dropped (one process only).
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from urllib.parse import urlsplit

from app.utils import utc_iso

log = logging.getLogger(__name__)

EVENT = 'request.needs_agent'
TIMEOUT_SECONDS = 3
DEDUPE_SECONDS = 5
LOOPBACK_HOSTS = frozenset({'localhost', '127.0.0.1', '::1'})

_lock = threading.Lock()
_recent: dict[int, float] = {}


# ---------------------------------------------------------------- pure

def should_wake(was_needed: bool, status, last_actor_kind) -> bool:
    """The thread newly needs the agent: it did not before the action and
    does now (``support_requests_service.needs_agent``)."""
    from app.support_requests_service import needs_agent
    return not was_needed and needs_agent(status, last_actor_kind)


def url_ok(url) -> bool:
    """HTTPS, or plain HTTP to this computer (a local relay)."""
    try:
        parts = urlsplit(str(url or '').strip())
    except ValueError:
        return False
    if not parts.hostname:
        return False
    if parts.scheme == 'https':
        return True
    return parts.scheme == 'http' and parts.hostname in LOOPBACK_HOSTS


def payload(req, links) -> dict:
    return {
        'event': EVENT,
        'request_id': req.id,
        'status': req.status,
        'subject_id': req.subject_id,
        'title': req.title,
        'last_message_at': utc_iso(req.last_message_at),
        'questions': [{'qid': l.qid, 'question_id': l.question_id, 'subject_id': l.subject_id}
                      for l in links],
    }


def headers(secret) -> dict:
    out = {'Content-Type': 'application/json', 'User-Agent': 'OQB-agent-wake/1'}
    secret = str(secret or '').strip()
    if secret:
        out['Authorization'] = f'Bearer {secret}'
        out['X-Webhook-Secret'] = secret
    return out


def claim(request_id, now=None) -> bool:
    """True unless a notice for ``request_id`` went out in the last
    ``DEDUPE_SECONDS``; records this one."""
    now = time.monotonic() if now is None else now
    with _lock:
        for rid, at in list(_recent.items()):
            if now - at >= DEDUPE_SECONDS:
                del _recent[rid]
        if request_id in _recent:
            return False
        _recent[request_id] = now
        return True


# ---------------------------------------------------------------- sending

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: it would resend the secret header elsewhere."""

    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def send(url, body: bytes, hdrs: dict, request_id) -> None:
    """POST the notice. Logs only the host, never the path or the secret."""
    host = urlsplit(url).hostname or '?'
    try:
        req = urllib.request.Request(url, data=body, headers=hdrs, method='POST')
        with _opener.open(req, timeout=TIMEOUT_SECONDS) as resp:
            log.info('agent wake sent for request %s to %s: HTTP %s', request_id, host, resp.status)
    except Exception as exc:
        log.warning('agent wake for request %s to %s failed: %s', request_id, host,
                    getattr(exc, 'code', None) or type(exc).__name__)


def _start(target, *args) -> None:
    threading.Thread(target=target, args=args, name='agent-wake', daemon=True).start()


def notify(req, was_needed: bool, *, config=None) -> bool:
    """Call after the commit of a person's action on ``req``. Schedules the
    POST when the thread newly needs the agent and a URL is set; returns
    whether it did. Never raises."""
    try:
        if config is None:
            from flask import current_app
            config = current_app.config
        url = str(config.get('AGENT_WAKE_WEBHOOK_URL') or '').strip()
        if not url or not should_wake(was_needed, req.status, req.last_actor_kind):
            return False
        if not url_ok(url):
            log.warning('agent wake skipped: AGENT_WAKE_WEBHOOK_URL must be https (or http to localhost)')
            return False
        from app.support_requests_service import linked_questions
        links = linked_questions([req.id]).get(req.id, [])
        body = json.dumps(payload(req, links), ensure_ascii=False).encode('utf-8')
        if not claim(req.id):
            log.info('agent wake for request %s skipped: sent less than %ss ago', req.id, DEDUPE_SECONDS)
            return False
        _start(send, url, body, headers(config.get('AGENT_WAKE_WEBHOOK_SECRET')), req.id)
        return True
    except Exception:
        log.exception('agent wake not scheduled for request %s', getattr(req, 'id', None))
        return False
