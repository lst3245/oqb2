"""Requests: issue threads between teachers, subject admins and the AI agent
(docs/modules/requests.md, ADR-022).

A request has a title, a category, optional linked questions, and a thread of
messages (with attachments under ``STORAGE_PATH/Requests/<id>/``). Who sees a
thread: the reporter, admins of any linked subject (``subject_id`` plus the
subjects of linked questions), and super admins. A general request (no
subject) is the reporter's and the super admins' only.

Roles inside a thread (:func:`access_for`):

* ``manager``: a super admin or an admin of a linked subject. Replies as an
  admin, sets any status, and approves / rejects the agent's plans when it
  administers every linked subject (general requests: super admins).
* ``reporter``: replies while the thread is not closed (a reply to
  ``needs_info`` / ``resolved`` reopens it), reopens ``resolved``, closes it.
* ``agent``: the MCP token. Any status except ``awaiting_approval`` (use
  :func:`request_approval`) and ``approved`` (an admin's decision).

The soft approval gate: the agent posts an ``approval_request`` (status
``awaiting_approval``); a manager approves (``approved``) or rejects
(``open``, the reason in the message); the agent reads the decision on its
next run. Nothing here enforces that the agent waited (ADR-022).

Pure helpers come first; callers commit.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime

from markupsafe import Markup, escape
from sqlalchemy import and_, func, or_, select

from app import db
from app.i18n import t as _t
from app.utils import utc_iso

log = logging.getLogger(__name__)

STATUSES = ('open', 'in_progress', 'needs_info', 'awaiting_approval', 'approved', 'resolved', 'closed')
ACTIVE_STATUSES = ('open', 'in_progress', 'needs_info', 'awaiting_approval', 'approved')
AGENT_STATUSES = ('open', 'in_progress', 'needs_info', 'resolved', 'closed')
CATEGORIES = ('question', 'general', 'app_bug', 'feature')
MESSAGE_KINDS = ('message', 'approval_request', 'approval', 'rejection', 'status_change')
ROLES = ('manager', 'reporter', 'agent')
REPORT_LEVELS = ('viewer', 'user', 'admin')

STATUS_LABELS = {
    'open': 'Open',
    'in_progress': 'In progress',
    'needs_info': 'Needs more info',
    'awaiting_approval': 'Awaiting approval',
    'approved': 'Approved',
    'resolved': 'Resolved',
    'closed': 'Closed',
}
STATUS_BADGES = {
    'open': 'bg-primary',
    'in_progress': 'bg-info text-dark',
    'needs_info': 'bg-warning text-dark',
    'awaiting_approval': 'bg-warning text-dark',
    'approved': 'bg-success',
    'resolved': 'bg-success',
    'closed': 'bg-secondary',
}
CATEGORY_LABELS = {
    'question': 'Problem with questions',
    'general': 'General',
    'app_bug': 'App bug',
    'feature': 'Feature request',
}

MAX_TITLE = 200
MAX_BODY = 20000
MAX_LINKED = 200
MAX_NAME = 120
INLINE_IMAGE_TYPES = frozenset({'image/png', 'image/jpeg', 'image/gif', 'image/webp'})


class RequestError(ValueError):
    pass


# ---------------------------------------------------------------- pure

def clip(value, limit) -> str:
    text = str(value or '').strip()
    return text[:limit]


def status_label(status) -> str:
    return _t(STATUS_LABELS.get(status, 'Open'))


def category_label(category) -> str:
    return _t(CATEGORY_LABELS.get(category, 'General'))


def needs_agent(status, last_actor_kind) -> bool:
    """The agent should act: an admin approved its plan, or a person spoke last
    on an open thread."""
    if status == 'approved':
        return True
    return status in ('open', 'in_progress') and last_actor_kind in ('user', 'admin')


def allowed_statuses(role, current) -> list[str]:
    if role == 'manager':
        return [s for s in STATUSES if s != current]
    if role == 'agent':
        return [s for s in AGENT_STATUSES if s != current]
    if role == 'reporter':
        out = []
        if current == 'resolved':
            out.append('open')
        if current != 'closed':
            out.append('closed')
        return out
    return []


def can_set_status(role, current, new) -> bool:
    return new in allowed_statuses(role, current)


def can_reply(role, status) -> bool:
    if role in ('manager', 'agent'):
        return True
    return role == 'reporter' and status != 'closed'


def status_after_reply(role, current) -> str:
    """A reporter's answer puts a ``needs_info`` / ``resolved`` thread back in
    the agent's queue; other replies keep the status."""
    if role == 'reporter' and current in ('needs_info', 'resolved'):
        return 'open'
    return current


def visible(*, user_id, is_super, admin_subjects, reporter_id, subjects) -> bool:
    if is_super:
        return True
    if user_id is not None and reporter_id is not None and int(user_id) == int(reporter_id):
        return True
    return bool(set(subjects or ()) & set(admin_subjects or ()))


def manages(*, is_super, admin_subjects, subjects) -> bool:
    return bool(is_super or (set(subjects or ()) & set(admin_subjects or ())))


def approves(*, is_super, admin_subjects, subjects) -> bool:
    """Approving an agent plan needs admin on every linked subject; a general
    request needs a super admin."""
    if is_super:
        return True
    subjects = set(subjects or ())
    return bool(subjects) and subjects <= set(admin_subjects or ())


def report_level_ok(level, minimum) -> bool:
    order = {name: i for i, name in enumerate(REPORT_LEVELS, start=1)}
    return order.get(level or '', 0) >= order.get(minimum or 'user', 2)


_URL = re.compile(r'https?://(?:[^\s<>&]|&amp;)+')
_CODE = re.compile(r'`([^`\n]+)`')
_BOLD = re.compile(r'\*\*([^*\n]+)\*\*')


def _link(match) -> str:
    url = match.group(0)
    tail = ''
    while url and url[-1] in '.,;:!?)':
        tail = url[-1] + tail
        url = url[:-1]
    return f'<a href="{url}" target="_blank" rel="noopener noreferrer">{url}</a>{tail}'


def render_body(text) -> Markup:
    """Plain text -> safe HTML: escaped, with ```code blocks```, `code`,
    **bold**, http(s) links and line breaks. Nothing else is interpreted."""
    parts = str(escape(text or '')).split('```')
    out = []
    for i, part in enumerate(parts):
        if i % 2 == 1 and i < len(parts) - 1:
            out.append(f'<pre class="oqb-msg-code">{part.strip(chr(10))}</pre>')
            continue
        if i % 2 == 1:
            part = '```' + part
        part = _CODE.sub(r'<code>\1</code>', part)
        part = _BOLD.sub(r'<strong>\1</strong>', part)
        part = _URL.sub(_link, part)
        out.append(part.replace('\r\n', '\n').replace('\n', '<br>'))
    return Markup(''.join(out))


_BAD_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def safe_filename(name) -> str:
    base = os.path.basename(str(name or '').replace('\\', '/')).strip().strip('.')
    base = _BAD_NAME.sub('_', base)
    if len(base) > MAX_NAME:
        stem, ext = os.path.splitext(base)
        base = stem[:MAX_NAME - len(ext[:12])] + ext[:12]
    return base or 'file'


def stored_name(message_id, original) -> str:
    ext = os.path.splitext(safe_filename(original))[1].lower()
    if not re.fullmatch(r'\.[a-z0-9]{1,10}', ext or ''):
        ext = ''
    return f'{int(message_id)}_{uuid.uuid4().hex[:10]}{ext}'


def guess_mime(name, given=None) -> str:
    given = str(given or '').split(';')[0].strip().lower()
    if given and given != 'application/octet-stream' and '/' in given:
        return given[:100]
    return (mimetypes.guess_type(str(name or ''))[0] or 'application/octet-stream')[:100]


def is_inline_image(mime) -> bool:
    return str(mime or '').lower() in INLINE_IMAGE_TYPES


def check_files(files, *, max_count, max_bytes) -> None:
    """``files``: ``[{'name', 'data', 'mime'}]``."""
    if len(files) > max_count:
        raise RequestError(_t('At most {count} files per message.', count=max_count))
    for f in files:
        if len(f['data']) > max_bytes:
            raise RequestError(_t('{name} is larger than {mb} MB.', name=f['name'],
                                  mb=max_bytes // (1024 * 1024)))
        if not f['data']:
            raise RequestError(_t('{name} is empty.', name=f['name']))


def load_meta(text) -> dict:
    if not text:
        return {}
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


# ---------------------------------------------------------------- access

@dataclass
class Access:
    role: str | None
    visible: bool
    manage: bool
    approve: bool
    reporter: bool

    @property
    def actor_kind(self) -> str:
        return 'admin' if self.manage else 'user'


def request_subjects(req) -> set[str]:
    from app.models import SupportRequestQuestion
    ids = {req.subject_id} if req.subject_id else set()
    rows = (db.session.query(SupportRequestQuestion.subject_id)
            .filter(SupportRequestQuestion.request_id == req.id).distinct())
    ids |= {row[0] for row in rows if row[0]}
    return ids


def access_for(user, req, subjects=None) -> Access:
    from app import authz
    if user is None or not getattr(user, 'is_authenticated', False):
        return Access(None, False, False, False, False)
    subjects = request_subjects(req) if subjects is None else set(subjects)
    is_super = authz.is_super_admin(user)
    admin_subjects = set(authz.subjects_with_level(user, 'admin'))
    is_reporter = req.reporter_id is not None and int(req.reporter_id) == int(user.id)
    see = visible(user_id=user.id, is_super=is_super, admin_subjects=admin_subjects,
                  reporter_id=req.reporter_id, subjects=subjects)
    manage = manages(is_super=is_super, admin_subjects=admin_subjects, subjects=subjects)
    approve = approves(is_super=is_super, admin_subjects=admin_subjects, subjects=subjects)
    role = 'manager' if manage else ('reporter' if is_reporter else None)
    return Access(role, see, manage, approve, is_reporter)


def visible_filter(user):
    """SQL condition for the requests ``user`` may see (``None`` = all)."""
    from app import authz
    from app.models import SupportRequest, SupportRequestQuestion
    if authz.is_super_admin(user):
        return None
    conds = [SupportRequest.reporter_id == user.id]
    admin = authz.subjects_with_level(user, 'admin')
    if admin:
        conds.append(SupportRequest.subject_id.in_(admin))
        conds.append(SupportRequest.id.in_(
            select(SupportRequestQuestion.request_id).where(SupportRequestQuestion.subject_id.in_(admin))))
    return or_(*conds)


def queue_filter(user):
    """Requests linked to a subject ``user`` administers (all subject-linked
    requests for a super admin)."""
    from app import authz
    from app.models import SupportRequest, SupportRequestQuestion
    linked = select(SupportRequestQuestion.request_id)
    if authz.is_super_admin(user):
        return or_(SupportRequest.subject_id.isnot(None), SupportRequest.id.in_(linked))
    admin = authz.subjects_with_level(user, 'admin')
    if not admin:
        return SupportRequest.id < 0
    return or_(SupportRequest.subject_id.in_(admin),
               SupportRequest.id.in_(linked.where(SupportRequestQuestion.subject_id.in_(admin))))


def can_report(user, subject_ids=(), *, minimum=None) -> bool:
    """``REQUESTS_MIN_REPORT_LEVEL`` on every given subject, or on any subject
    for a general report."""
    from flask import current_app
    from app import authz
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    if authz.is_super_admin(user):
        return True
    minimum = minimum or current_app.config.get('REQUESTS_MIN_REPORT_LEVEL') or 'user'
    if minimum not in REPORT_LEVELS:
        minimum = 'user'
    subject_ids = [s for s in subject_ids if s]
    if not subject_ids:
        return authz.any_level(user, minimum)
    return all(authz.has_level(user, sid, minimum) for sid in set(subject_ids))


def enabled() -> bool:
    from flask import current_app
    return bool(current_app.config.get('REQUESTS_ENABLED', True))


# ---------------------------------------------------------------- writes

def _now():
    return datetime.utcnow()


def _set_status(req, new, user_id=None) -> None:
    req.status = new
    if new in ('resolved', 'closed'):
        req.closed_at = _now()
        req.closed_by = user_id
    else:
        req.closed_at = None
        req.closed_by = None


def create_request(*, reporter_id, title, body, category=None, subject_id=None, questions=(),
                   files=(), actor_kind='user'):
    """New thread with its first message. ``questions``: Question rows the
    reporter may see. Returns ``(request, message)``."""
    from app.models import SupportRequest, SupportRequestQuestion
    title = clip(title, MAX_TITLE)
    if not title:
        raise RequestError(_t('Give the request a short title.'))
    questions = list(questions)[:MAX_LINKED]
    category = category or ('question' if questions else 'general')
    if category not in CATEGORIES:
        raise RequestError(_t('Unknown category: {category}', category=category))
    if questions:
        subject_id = questions[0].subject
    req = SupportRequest(title=title, category=category, subject_id=subject_id or None,
                         reporter_id=reporter_id, status='open', last_actor_kind=actor_kind,
                         last_message_at=_now())
    db.session.add(req)
    db.session.flush()
    for q in questions:
        db.session.add(SupportRequestQuestion(request_id=req.id, question_id=q.id, qid=q.qid,
                                              subject_id=q.subject))
    msg = add_message(req, author_kind='user', user_id=reporter_id, body=body, files=files,
                      actor_kind=actor_kind)
    return req, msg


def add_message(req, *, author_kind, body='', user_id=None, agent_name=None, kind='message',
                meta=None, files=(), actor_kind=None, new_status=None):
    from app.models import SupportRequestMessage
    body = str(body or '').strip()
    if len(body) > MAX_BODY:
        raise RequestError(_t('The message is too long ({count} characters at most).', count=MAX_BODY))
    if kind == 'message' and not body and not files:
        raise RequestError(_t('Write a message or attach a file.'))
    msg = SupportRequestMessage(request_id=req.id, author_kind=author_kind, author_user_id=user_id,
                                agent_name=clip(agent_name, 80) or None, kind=kind, body=body,
                                meta=json.dumps(meta, ensure_ascii=False) if meta else None,
                                created_at=_now())
    db.session.add(msg)
    db.session.flush()
    if files:
        save_attachments(req, msg, files)
    req.last_message_at = msg.created_at
    if actor_kind:
        req.last_actor_kind = actor_kind
    if new_status and new_status != req.status:
        _set_status(req, new_status, user_id)
    return msg


def reply(req, access: Access, *, user_id, body, files=()):
    """A person's reply in the thread (status per :func:`status_after_reply`)."""
    if not can_reply(access.role, req.status):
        raise RequestError(_t('This request is closed.'))
    return add_message(req, author_kind='user', user_id=user_id, body=body, files=files,
                       actor_kind=access.actor_kind,
                       new_status=status_after_reply(access.role, req.status))


def add_system_note(request_id, body, meta=None):
    """An automatic line in the thread (agent tool runs, linked batches).
    Does not change the status or who spoke last."""
    from app.models import SupportRequest
    req = db.session.get(SupportRequest, int(request_id)) if request_id else None
    if req is None:
        return None
    return add_message(req, author_kind='system', body=clip(body, MAX_BODY), kind='message',
                       meta=meta)


def set_status(req, new_status, *, role, user_id=None, agent_name=None, note=''):
    if new_status not in STATUSES:
        raise RequestError(_t('Unknown status: {status}', status=new_status))
    if not can_set_status(role, req.status, new_status):
        raise RequestError(_t('This request cannot be set to {status} by you.',
                              status=status_label(new_status)))
    old = req.status
    actor = 'agent' if role == 'agent' else ('admin' if role == 'manager' else 'user')
    msg = add_message(req, author_kind='agent' if role == 'agent' else 'user', user_id=user_id,
                      agent_name=agent_name, body=note, kind='status_change',
                      meta={'from': old, 'to': new_status}, actor_kind=actor)
    _set_status(req, new_status, user_id)
    return msg


def pending_approval(req):
    """The newest approval request with no decision after it, or None."""
    from app.models import SupportRequestMessage as M
    latest = (M.query.filter(M.request_id == req.id, M.kind == 'approval_request')
              .order_by(M.id.desc()).first())
    if latest is None:
        return None
    decided = (M.query.filter(M.request_id == req.id, M.kind.in_(('approval', 'rejection')),
                              M.id > latest.id).first())
    return None if decided else latest


def request_approval(req, *, agent_name, plan, meta=None):
    if req.status == 'closed':
        raise RequestError('the request is closed')
    if not str(plan or '').strip():
        raise RequestError('describe the plan to approve')
    msg = add_message(req, author_kind='agent', agent_name=agent_name, body=plan,
                      kind='approval_request', meta=meta, actor_kind='agent')
    _set_status(req, 'awaiting_approval')
    return msg


def decide_approval(req, *, approve, user_id, note=''):
    pending = pending_approval(req)
    if pending is None:
        raise RequestError(_t('There is no approval request waiting.'))
    if not approve and not str(note or '').strip():
        raise RequestError(_t('Say why you reject the plan, so the agent can change it.'))
    msg = add_message(req, author_kind='user', user_id=user_id, body=note,
                      kind='approval' if approve else 'rejection',
                      meta={'approval_request_id': pending.id}, actor_kind='admin')
    _set_status(req, 'approved' if approve else 'open', user_id)
    return msg


def save_attachments(req, msg, files):
    """Write ``[{'name', 'data', 'mime'}]`` under ``Requests/<id>/``; removes
    what it wrote if one fails."""
    from app import storage
    from app.models import SupportRequestAttachment
    folder = storage.request_dir(req.id)
    if not folder:
        raise RequestError('no attachment folder')
    os.makedirs(folder, exist_ok=True)
    written = []
    try:
        for f in files:
            name = safe_filename(f['name'])
            stored = stored_name(msg.id, name)
            path = storage.safe_join(folder, stored)
            with open(path, 'wb') as fh:
                fh.write(f['data'])
            written.append(path)
            db.session.add(SupportRequestAttachment(
                message_id=msg.id, request_id=req.id, original_name=name, stored_name=stored,
                mime=guess_mime(name, f.get('mime')), size=len(f['data'])))
    except Exception:
        for path in written:
            try:
                os.remove(path)
            except OSError:
                pass
        raise
    return written


def attachment_path(att) -> str | None:
    from app import storage
    folder = storage.request_dir(att.request_id)
    if not folder:
        return None
    return storage.safe_join(folder, att.stored_name)


def mark_read(user_id, request_id, message_id) -> None:
    from app.models import SupportRequestRead
    if not user_id or not message_id:
        return
    row = db.session.get(SupportRequestRead, (int(user_id), int(request_id)))
    if row is None:
        db.session.add(SupportRequestRead(user_id=int(user_id), request_id=int(request_id),
                                          last_read_message_id=int(message_id)))
    elif row.last_read_message_id < int(message_id):
        row.last_read_message_id = int(message_id)


def detach_subject(subject_id) -> int:
    """Subject delete: its requests become general (reporter + super admins).
    The question links lose the subject too, so a later subject with the same
    id does not inherit them."""
    from app.models import SupportRequest, SupportRequestQuestion
    (SupportRequestQuestion.query.filter(SupportRequestQuestion.subject_id == subject_id)
     .update({'subject_id': None}, synchronize_session=False))
    return (SupportRequest.query.filter(SupportRequest.subject_id == subject_id)
            .update({'subject_id': None}, synchronize_session=False))


# ---------------------------------------------------------------- reads

def _others_messages(user_id):
    from app.models import SupportRequestMessage as M
    return or_(M.author_user_id.is_(None), M.author_user_id != user_id)


def unread_count(user) -> int:
    """Threads visible to ``user`` with a message from someone else after
    their read marker."""
    from app.models import SupportRequest as R, SupportRequestMessage as M, SupportRequestRead as RD
    query = (db.session.query(func.count(func.distinct(M.request_id)))
             .join(R, R.id == M.request_id)
             .outerjoin(RD, and_(RD.request_id == M.request_id, RD.user_id == user.id))
             .filter(M.id > func.coalesce(RD.last_read_message_id, 0))
             .filter(_others_messages(user.id)))
    cond = visible_filter(user)
    if cond is not None:
        query = query.filter(cond)
    return int(query.scalar() or 0)


def unread_ids(user, request_ids) -> set[int]:
    from app.models import SupportRequestMessage as M, SupportRequestRead as RD
    ids = [int(i) for i in request_ids]
    if not ids:
        return set()
    rows = (db.session.query(M.request_id)
            .outerjoin(RD, and_(RD.request_id == M.request_id, RD.user_id == user.id))
            .filter(M.request_id.in_(ids))
            .filter(M.id > func.coalesce(RD.last_read_message_id, 0))
            .filter(_others_messages(user.id))
            .distinct())
    return {row[0] for row in rows}


def mark_all_read(user) -> int:
    from app.models import SupportRequest as R, SupportRequestMessage as M
    query = db.session.query(M.request_id, func.max(M.id)).join(R, R.id == M.request_id)
    cond = visible_filter(user)
    if cond is not None:
        query = query.filter(cond)
    rows = query.group_by(M.request_id).all()
    for rid, mid in rows:
        mark_read(user.id, rid, mid)
    return len(rows)


def display_names(ids) -> dict[int, str]:
    from app.models import User
    ids = {int(i) for i in ids if i}
    if not ids:
        return {}
    return {u.id: (u.display_name or u.username) for u in User.query.filter(User.id.in_(ids)).all()}


def linked_questions(request_ids) -> dict[int, list]:
    from app.models import SupportRequestQuestion
    ids = [int(i) for i in request_ids]
    out = {i: [] for i in ids}
    if not ids:
        return out
    rows = (SupportRequestQuestion.query.filter(SupportRequestQuestion.request_id.in_(ids))
            .order_by(SupportRequestQuestion.id).all())
    for row in rows:
        out[row.request_id].append(row)
    return out


def message_payload(msg, attachments=(), names=None) -> dict:
    names = names or {}
    return {
        'id': msg.id,
        'kind': msg.kind,
        'author_kind': msg.author_kind,
        'author': (msg.agent_name if msg.author_kind == 'agent'
                   else names.get(msg.author_user_id) if msg.author_user_id else None),
        'author_user_id': msg.author_user_id,
        'body': msg.body or '',
        'meta': load_meta(msg.meta),
        'created_at': utc_iso(msg.created_at),
        'attachments': [{'id': a.id, 'name': a.original_name, 'mime': a.mime, 'size': a.size}
                        for a in attachments],
    }


def request_payload(req, *, links=None, names=None) -> dict:
    names = names or {}
    links = links if links is not None else linked_questions([req.id]).get(req.id, [])
    return {
        'id': req.id,
        'title': req.title,
        'category': req.category,
        'subject_id': req.subject_id,
        'status': req.status,
        'reporter_id': req.reporter_id,
        'reporter': names.get(req.reporter_id) if req.reporter_id else None,
        'last_actor_kind': req.last_actor_kind,
        'needs_agent': needs_agent(req.status, req.last_actor_kind),
        'created_at': utc_iso(req.created_at),
        'last_message_at': utc_iso(req.last_message_at),
        'closed_at': utc_iso(req.closed_at),
        'questions': [{'qid': l.qid, 'question_id': l.question_id, 'subject_id': l.subject_id}
                      for l in links],
    }


def thread(req):
    """``(messages, attachments_by_message_id)`` in order."""
    from app.models import SupportRequestAttachment as A, SupportRequestMessage as M
    messages = M.query.filter(M.request_id == req.id).order_by(M.id).all()
    atts = {}
    for att in A.query.filter(A.request_id == req.id).order_by(A.id).all():
        atts.setdefault(att.message_id, []).append(att)
    return messages, atts
