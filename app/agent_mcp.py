"""Agent endpoint: the Model Context Protocol (MCP) over streamable HTTP,
JSON responses only (docs/modules/agent-mcp.md, ADR-021).

``POST /mcp`` takes one JSON-RPC 2.0 message (or a batch) and answers with
``application/json``; notifications get ``202``. No server-sent events, no
MCP session id: every call stands alone.

Access, in this order: 404 while ``AGENT_MCP_ENABLED`` is off or
``AGENT_API_TOKEN`` (``.env``) is empty; 403 when the client address or the
socket peer (before ProxyFix) is not in ``AGENT_MCP_ALLOWED_IPS``, or a browser
``Origin`` is not ``PUBLIC_ORIGIN``;
429 over the rate limit; 401 without ``Authorization: Bearer <token>``. The
token acts as a super admin on every subject. The route is exempt from CSRF
and from the session limits (no cookies).

Tools register with :func:`tool` in ``app/agent_mcp_tools.py``. A tool raises
:class:`ToolError` to refuse its input (returned as ``isError``); the
transport rolls back on any error, and writes one ``agent_action_log`` row for
every call of a tool marked ``writes=True``.
"""
from __future__ import annotations

import hmac
import ipaddress
import json
import logging
import os
from dataclasses import dataclass, field, replace

from flask import Blueprint, Response, abort, current_app, request

from app import db, web_security
from app.rate_limit import RateLimiter

log = logging.getLogger(__name__)

agent_mcp_bp = Blueprint('agent_mcp', __name__)

PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
SERVER_INFO = {'name': 'oqb', 'title': 'OQB Question Bank', 'version': '1.0'}
SERVER_INSTRUCTIONS = (
    'OQB is a school question bank (exam questions tagged by subject, topic, chapter, '
    'level). Call get_instructions first: it returns the playbook (polling loop, '
    'request statuses, approval rules, review-batch format). Every change to the '
    'library must go through these tools, never raw SQL or file edits.'
)
PLAYBOOK_URI = 'oqb://agent/playbook'
PLAYBOOK_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             'docs', 'agents', 'PLAYBOOK.md')

MAX_BODY_BYTES = 64 * 1024 * 1024
RATE_LIMIT, RATE_WINDOW_SECONDS = 1200, 60
MAX_AGENT_NAME = 80
SUMMARY_VALUE_CHARS = 300
SUMMARY_CHARS = 4000

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

_limiter = RateLimiter(RATE_LIMIT, RATE_WINDOW_SECONDS)


class ToolError(Exception):
    """A tool refused its input. The message goes back to the agent."""


class RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    handler: object
    title: str | None = None
    writes: bool = False
    # Library writes post a short system note into the request thread named
    # by a ``request_id`` argument.
    thread_note: bool = False


@dataclass
class AgentContext:
    agent_name: str
    ip: str = ''
    # Filled by a tool for the action log: subject_id, question_id,
    # request_id, batch_id.
    touched: dict = field(default_factory=dict)

    def touch(self, **values) -> None:
        for key, value in values.items():
            if value is not None:
                self.touched[key] = value


TOOLS: dict[str, Tool] = {}


def tool(name, description, schema, *, title=None, writes=False, thread_note=False):
    """Register ``fn(args, ctx)`` as an MCP tool. ``schema`` is the JSON
    Schema of ``arguments`` (an object)."""
    def deco(fn):
        TOOLS[name] = Tool(name=name, description=description, schema=schema, handler=fn,
                           title=title, writes=writes, thread_note=thread_note)
        return fn
    return deco


def registry() -> dict[str, Tool]:
    from app import agent_mcp_tools  # noqa: F401  (registers the tools)
    return TOOLS


@dataclass
class ToolResult:
    """Explicit MCP content (images, mixed text) from a tool."""
    content: list
    structured: dict | None = None


# ---------------------------------------------------------------- pure helpers

def bearer_ok(header, token) -> bool:
    """``Authorization: Bearer <token>`` matches ``token`` (constant time).
    An empty configured token never matches. Pure."""
    if not token or not isinstance(header, str):
        return False
    scheme, _, sent = header.strip().partition(' ')
    if scheme.lower() != 'bearer' or not sent.strip():
        return False
    return hmac.compare_digest(sent.strip().encode('utf-8'), str(token).encode('utf-8'))


def ip_allowed(ip, spec) -> bool:
    """``ip`` is in the comma list ``spec`` of addresses / CIDR networks
    (``*`` = any). Unparseable entries are skipped; an empty list allows
    nobody. IPv4-mapped IPv6 addresses match their IPv4 form. Pure."""
    entries = [p.strip() for p in str(spec or '').split(',') if p.strip()]
    if '*' in entries:
        return True
    try:
        addr = ipaddress.ip_address(str(ip or '').strip())
    except ValueError:
        return False
    if getattr(addr, 'ipv4_mapped', None):
        addr = addr.ipv4_mapped
    for entry in entries:
        try:
            net = ipaddress.ip_network(entry, strict=False)
        except ValueError:
            continue
        if addr.version == net.version and addr in net:
            return True
    return False


def peer_ip(environ) -> str:
    """The socket peer: the address before ProxyFix rewrote ``REMOTE_ADDR``
    from ``X-Forwarded-For`` (which any LAN client can send). Pure."""
    orig = environ.get('werkzeug.proxy_fix.orig') or {}
    return str(orig.get('REMOTE_ADDR') or environ.get('REMOTE_ADDR') or '')


def clean_agent_name(value, default) -> str:
    """One-line display name, at most ``MAX_AGENT_NAME`` chars. Pure."""
    text = ' '.join(str(value or '').split())[:MAX_AGENT_NAME]
    return text or (' '.join(str(default or '').split())[:MAX_AGENT_NAME] or 'AI agent')


def negotiate_version(requested) -> str:
    return requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]


def summarize_args(args) -> str:
    """Arguments for the action log: long strings (base64, bodies) cut to
    ``SUMMARY_VALUE_CHARS``, the whole JSON to ``SUMMARY_CHARS``. Pure."""
    def cut(value):
        if isinstance(value, str):
            return value if len(value) <= SUMMARY_VALUE_CHARS else value[:SUMMARY_VALUE_CHARS] + f'... ({len(value)} chars)'
        if isinstance(value, dict):
            return {k: cut(v) for k, v in value.items()}
        if isinstance(value, list):
            return [cut(v) for v in value[:50]] + ([f'... ({len(value)} items)'] if len(value) > 50 else [])
        return value
    text = json.dumps(cut(args or {}), ensure_ascii=False, default=str)
    return text if len(text) <= SUMMARY_CHARS else text[:SUMMARY_CHARS] + '...'


def text_result(payload) -> dict:
    """MCP tool result for a JSON payload: text content + structuredContent."""
    structured = payload if isinstance(payload, dict) else {'items': payload}
    return {
        'content': [{'type': 'text', 'text': json.dumps(payload, ensure_ascii=False, default=str)}],
        'structuredContent': structured,
        'isError': False,
    }


def error_result(message) -> dict:
    return {'content': [{'type': 'text', 'text': str(message)}], 'isError': True}


def _rpc_error(mid, code, message) -> dict:
    return {'jsonrpc': '2.0', 'id': mid, 'error': {'code': code, 'message': message}}


# ---------------------------------------------------------------- methods

def _initialize(params, ctx):
    return {
        'protocolVersion': negotiate_version((params or {}).get('protocolVersion')),
        'capabilities': {
            'tools': {'listChanged': False},
            'resources': {'subscribe': False, 'listChanged': False},
        },
        'serverInfo': SERVER_INFO,
        'instructions': SERVER_INSTRUCTIONS,
    }


def _empty(params, ctx):
    return {}


def _tools_list(params, ctx):
    out = []
    for t in registry().values():
        entry = {'name': t.name, 'description': t.description, 'inputSchema': t.schema}
        if t.title:
            entry['title'] = t.title
        out.append(entry)
    return {'tools': out}


def read_playbook() -> str:
    try:
        with open(PLAYBOOK_PATH, encoding='utf-8') as fh:
            return fh.read()
    except OSError:
        return '# OQB agent playbook\n\nThe playbook file is missing (docs/agents/PLAYBOOK.md).\n'


def _resources_list(params, ctx):
    return {'resources': [{
        'uri': PLAYBOOK_URI, 'name': 'playbook', 'title': 'OQB agent playbook',
        'description': 'How to work with OQB requests, reviews and library changes.',
        'mimeType': 'text/markdown',
    }]}


def _resources_read(params, ctx):
    uri = (params or {}).get('uri')
    if uri != PLAYBOOK_URI:
        raise RpcError(INVALID_PARAMS, f'Unknown resource: {uri}')
    return {'contents': [{'uri': PLAYBOOK_URI, 'mimeType': 'text/markdown', 'text': read_playbook()}]}


def _record_action(t: Tool, args, ctx: AgentContext, outcome, error=None) -> None:
    """One ``agent_action_log`` row (own commit; never raises)."""
    from app.models import AgentActionLog
    try:
        touched = ctx.touched
        reason = args.get('reason') if isinstance(args, dict) else None
        db.session.add(AgentActionLog(
            agent_name=ctx.agent_name, tool=t.name, outcome=outcome,
            subject_id=(str(touched['subject_id'])[:10] if touched.get('subject_id') else None),
            question_id=touched.get('question_id'),
            request_id=touched.get('request_id') or _int_or_none(args.get('request_id')),
            batch_id=touched.get('batch_id') or _int_or_none(args.get('batch_id')),
            reason=(str(reason)[:500] if reason else None),
            summary=summarize_args(args),
            error=(str(error)[:500] if error else None),
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()
        log.exception('agent_action_log write failed for %s', t.name)


def _int_or_none(value):
    try:
        return int(value) if value not in (None, '') else None
    except (TypeError, ValueError):
        return None


def _thread_note(t: Tool, args, ctx: AgentContext) -> None:
    """System line in the request thread named by ``request_id`` (best effort)."""
    rid = _int_or_none(args.get('request_id'))
    if not rid:
        return
    try:
        from app import support_requests_service as srs
        target = ctx.touched.get('qid') or ctx.touched.get('question_id') or ''
        reason = str(args.get('reason') or '').strip()
        body = f'{ctx.agent_name} ran {t.name}' + (f' on {target}' if target else '')
        if reason:
            body += f': {reason}'
        srs.add_system_note(rid, body[:2000], meta={'tool': t.name})
        db.session.commit()
    except Exception:
        db.session.rollback()
        log.exception('thread note failed for request %s', rid)


def _tools_call(params, ctx):
    params = params or {}
    name = params.get('name')
    args = params.get('arguments')
    if args is None:
        args = {}
    t = registry().get(name)
    if t is None:
        raise RpcError(INVALID_PARAMS, f'Unknown tool: {name}')
    if not isinstance(args, dict):
        raise RpcError(INVALID_PARAMS, 'arguments must be an object')
    ctx = replace(ctx, agent_name=clean_agent_name(args.get('agent_name'), ctx.agent_name),
                  touched={})
    try:
        out = t.handler(args, ctx)
        db.session.commit()
    except ToolError as exc:
        db.session.rollback()
        if t.writes:
            _record_action(t, args, ctx, 'error', exc)
        return error_result(str(exc))
    except Exception as exc:
        db.session.rollback()
        log.exception('agent tool %s failed', name)
        if t.writes:
            _record_action(t, args, ctx, 'error', f'{exc.__class__.__name__}: {exc}')
        return error_result(f'Internal error in {name}: {exc.__class__.__name__}: {exc}')
    if t.writes:
        _record_action(t, args, ctx, 'ok')
        if t.thread_note:
            _thread_note(t, args, ctx)
    if isinstance(out, ToolResult):
        result = {'content': out.content, 'isError': False}
        if out.structured is not None:
            result['structuredContent'] = out.structured
        return result
    return text_result(out)


METHODS = {
    'initialize': _initialize,
    'notifications/initialized': _empty,
    'notifications/cancelled': _empty,
    'ping': _empty,
    'logging/setLevel': _empty,
    'tools/list': _tools_list,
    'tools/call': _tools_call,
    'resources/list': _resources_list,
    'resources/read': _resources_read,
    'resources/templates/list': lambda params, ctx: {'resourceTemplates': []},
    'prompts/list': lambda params, ctx: {'prompts': []},
}


def handle_message(msg, ctx: AgentContext, methods=None):
    """One JSON-RPC message -> response dict, or ``None`` for a notification
    or a client response. Never raises."""
    methods = methods if methods is not None else METHODS
    if not isinstance(msg, dict) or msg.get('jsonrpc') != '2.0':
        return _rpc_error(None, INVALID_REQUEST, 'Invalid Request')
    mid = msg.get('id')
    notification = 'id' not in msg
    method = msg.get('method')
    if method is None:
        return None  # a response to a server request; this server sends none
    if not isinstance(method, str):
        return None if notification else _rpc_error(mid, INVALID_REQUEST, 'Invalid Request')
    handler = methods.get(method)
    if handler is None:
        return None if notification else _rpc_error(mid, METHOD_NOT_FOUND, f'Method not found: {method}')
    params = msg.get('params')
    if params is not None and not isinstance(params, (dict, list)):
        return None if notification else _rpc_error(mid, INVALID_PARAMS, 'params must be an object')
    try:
        result = handler(params if isinstance(params, dict) else {}, ctx)
    except RpcError as exc:
        return None if notification else _rpc_error(mid, exc.code, exc.message)
    except Exception:
        log.exception('mcp method %s failed', method)
        try:
            db.session.rollback()
        except Exception:
            pass
        return None if notification else _rpc_error(mid, INTERNAL_ERROR, 'Internal error')
    if notification:
        return None
    return {'jsonrpc': '2.0', 'id': mid, 'result': result}


def handle_body(body, ctx: AgentContext, methods=None):
    """Parsed request body (a message or a batch) -> response payload, or
    ``None`` when nothing needs an answer."""
    if isinstance(body, list):
        if not body:
            return _rpc_error(None, INVALID_REQUEST, 'Invalid Request')
        out = [r for r in (handle_message(m, ctx, methods) for m in body) if r is not None]
        return out or None
    return handle_message(body, ctx, methods)


# ---------------------------------------------------------------- HTTP

def _json_response(payload, status=200, headers=None):
    resp = Response(json.dumps(payload, ensure_ascii=False, default=str), status=status,
                    mimetype='application/json')
    resp.headers['Cache-Control'] = 'no-store'
    for key, value in (headers or {}).items():
        resp.headers[key] = value
    return resp


def _http_error(status, message, headers=None):
    return _json_response({'error': message}, status, headers)


def _origin_ok(origin) -> bool:
    """A browser Origin must be OQB's own public origin (DNS-rebinding guard)."""
    got = web_security.normalise_origin(origin)
    expected = web_security.public_origin()
    return bool(got) and got == expected


@agent_mcp_bp.route('/mcp', methods=['GET', 'POST', 'DELETE'])
def mcp():
    cfg = current_app.config
    token = str(cfg.get('AGENT_API_TOKEN') or '').strip()
    if not cfg.get('AGENT_MCP_ENABLED') or not token:
        abort(404)
    ip = web_security.client_ip(request)
    peer = peer_ip(request.environ)
    allowed = cfg.get('AGENT_MCP_ALLOWED_IPS')
    if not (ip_allowed(ip, allowed) and ip_allowed(peer, allowed)):
        log.warning('mcp refused: address %s (peer %s) not allowed', ip, peer)
        return _http_error(403, 'address not allowed')
    origin = request.headers.get('Origin')
    if origin and not _origin_ok(origin):
        return _http_error(403, 'origin not allowed')
    wait = _limiter.hit(ip or '-')
    if wait:
        return _http_error(429, 'rate limited', {'Retry-After': str(int(wait) + 1)})
    if not bearer_ok(request.headers.get('Authorization'), token):
        log.warning('mcp refused: bad or missing token from %s', ip)
        return _http_error(401, 'unauthorized', {'WWW-Authenticate': 'Bearer'})
    if request.method != 'POST':
        return _http_error(405, 'POST only', {'Allow': 'POST'})
    length = request.content_length
    if length is not None and length > MAX_BODY_BYTES:
        return _http_error(413, 'request too large')
    raw = request.get_data(cache=False)
    if len(raw) > MAX_BODY_BYTES:
        return _http_error(413, 'request too large')
    try:
        body = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        return _json_response(_rpc_error(None, PARSE_ERROR, 'Parse error'), 400)
    ctx = AgentContext(
        agent_name=clean_agent_name(request.headers.get('X-Agent-Name'), cfg.get('AGENT_DEFAULT_NAME')),
        ip=ip or '',
    )
    out = handle_body(body, ctx)
    if out is None:
        return Response(status=202)
    return _json_response(out)
