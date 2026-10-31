"""Requests pages (``/requests``): issue threads between teachers, subject
admins and the AI agent (docs/modules/requests.md).

Every route is ``@login_required``; visibility and roles come from
``support_requests_service.access_for`` (reporter, admins of a linked subject,
super admins). A thread someone may not see answers 404. The whole blueprint
answers 404 while ``REQUESTS_ENABLED`` is off.
"""
from __future__ import annotations

import os

from flask import (Blueprint, abort, current_app, jsonify, redirect, render_template, request,
                   send_file, url_for)
from flask_login import current_user, login_required
from sqlalchemy import or_, select

from app import authz, db
from app import support_requests_service as srs
from app.i18n import t as _t
from app.models import (Question, ReviewBatch, Subject, SupportRequest, SupportRequestAttachment,
                        SupportRequestQuestion)

support_requests_bp = Blueprint('support_requests', __name__, url_prefix='/requests')

LIST_LIMIT = 300


@support_requests_bp.before_request
def _gate():
    if not srs.enabled():
        abort(404)


@support_requests_bp.app_context_processor
def _nav_globals():
    """``requests_enabled()``, ``requests_can_report()`` and
    ``requests_unread_count()`` for the navbar and Report buttons."""
    def _on():
        try:
            return current_user.is_authenticated and srs.enabled()
        except Exception:
            return False

    def requests_can_report():
        try:
            return bool(_on() and srs.can_report(current_user))
        except Exception:
            db.session.rollback()
            return False

    def requests_unread_count():
        try:
            return srs.unread_count(current_user) if _on() else 0
        except Exception:
            db.session.rollback()
            return 0

    return {'requests_enabled': _on, 'requests_can_report': requests_can_report,
            'requests_unread_count': requests_unread_count}


# ---------------------------------------------------------------- helpers

def _ok(**extra):
    return jsonify(dict({'success': True}, **extra))


def _fail(message, status=400):
    db.session.rollback()
    return jsonify(authz.deny_payload(str(message))), status


def _payload():
    if request.is_json:
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}
    return request.form


def _request_or_404(request_id):
    req = db.session.get(SupportRequest, request_id)
    if req is None:
        abort(404)
    access = srs.access_for(current_user, req)
    if not access.visible:
        abort(404)
    return req, access


def _id_list(raw, limit=srs.MAX_LINKED):
    out = []
    for part in str(raw or '').replace(' ', ',').split(','):
        part = part.strip()
        if part.isdigit() and int(part) not in out:
            out.append(int(part))
        if len(out) >= limit:
            break
    return out


def _questions_in_order(ids):
    if not ids:
        return []
    found = {q.id: q for q in Question.query.filter(Question.id.in_(ids)).all()}
    return [found[i] for i in ids if i in found]


def _uploads():
    max_count = int(current_app.config.get('REQUESTS_MAX_ATTACHMENTS_PER_MESSAGE', 10) or 10)
    max_bytes = int(current_app.config.get('REQUESTS_MAX_ATTACHMENT_MB', 20) or 20) * 1024 * 1024
    uploads = [f for f in request.files.getlist('files') if f and f.filename]
    if len(uploads) > max_count:
        raise srs.RequestError(_t('At most {count} files per message.', count=max_count))
    files = [{'name': f.filename, 'data': f.stream.read(max_bytes + 1), 'mime': f.mimetype} for f in uploads]
    srs.check_files(files, max_count=max_count, max_bytes=max_bytes)
    return files


def _report_subjects():
    """Subjects the user may file a subject-level report on (no questions)."""
    minimum = current_app.config.get('REQUESTS_MIN_REPORT_LEVEL') or 'user'
    query = Subject.query.order_by(Subject.id)
    if not authz.is_super_admin(current_user):
        query = query.filter(Subject.id.in_(authz.subjects_with_level(current_user, minimum) or ['']))
    return query.all()


def _limits():
    return {
        'max_files': int(current_app.config.get('REQUESTS_MAX_ATTACHMENTS_PER_MESSAGE', 10) or 10),
        'max_mb': int(current_app.config.get('REQUESTS_MAX_ATTACHMENT_MB', 20) or 20),
    }


# ---------------------------------------------------------------- list

@support_requests_bp.route('/')
@login_required
def index():
    user = current_user
    is_super = authz.is_super_admin(user)
    is_admin = is_super or bool(authz.subjects_with_level(user, 'admin'))
    tabs = ['mine'] + (['queue'] if is_admin else []) + (['all'] if is_super else [])
    tab = request.args.get('tab') or ('queue' if is_admin else 'mine')
    if tab not in tabs:
        tab = tabs[0]
    status = request.args.get('status') or 'active'
    category = request.args.get('category') or ''
    text = (request.args.get('q') or '').strip()

    query = SupportRequest.query
    if tab == 'mine':
        query = query.filter(SupportRequest.reporter_id == user.id)
    elif tab == 'queue':
        query = query.filter(srs.queue_filter(user))
    if status == 'active':
        query = query.filter(SupportRequest.status.in_(srs.ACTIVE_STATUSES))
    elif status == 'needs_agent':
        query = query.filter(SupportRequest.status.in_(('open', 'in_progress', 'approved')))
    elif status in srs.STATUSES:
        query = query.filter(SupportRequest.status == status)
    else:
        status = 'all'
    if category in srs.CATEGORIES:
        query = query.filter(SupportRequest.category == category)
    else:
        category = ''
    if text:
        conds = [SupportRequest.title.contains(text, autoescape=True),
                 SupportRequest.id.in_(select(SupportRequestQuestion.request_id).where(
                     SupportRequestQuestion.qid.contains(text, autoescape=True)))]
        if text.lstrip('#').isdigit():
            conds.append(SupportRequest.id == int(text.lstrip('#')))
        query = query.filter(or_(*conds))

    rows = query.order_by(SupportRequest.last_message_at.desc(), SupportRequest.id.desc()).limit(LIST_LIMIT).all()
    if status == 'needs_agent':
        rows = [r for r in rows if srs.needs_agent(r.status, r.last_actor_kind)]
    ids = [r.id for r in rows]
    return render_template(
        'requests_list.html', rows=rows, tab=tab, tabs=tabs, status=status, category=category, q=text,
        unread=srs.unread_ids(user, ids), links=srs.linked_questions(ids),
        names=srs.display_names(r.reporter_id for r in rows),
        statuses=srs.STATUSES, status_labels=srs.STATUS_LABELS, status_badges=srs.STATUS_BADGES,
        categories=srs.CATEGORIES, category_labels=srs.CATEGORY_LABELS,
        can_report=srs.can_report(user))


@support_requests_bp.route('/mark-all-read', methods=['POST'])
@login_required
def mark_all_read():
    srs.mark_all_read(current_user)
    db.session.commit()
    if authz.wants_json():
        return _ok()
    return redirect(request.referrer or url_for('.index'))


# ---------------------------------------------------------------- new

@support_requests_bp.route('/new', methods=['GET'])
@login_required
def new():
    picked = _questions_in_order(_id_list(request.args.get('ids')))
    questions = [q for q in picked if srs.can_report(current_user, [q.subject])]
    if not questions and not srs.can_report(current_user):
        authz.deny()
    return render_template(
        'requests_new.html', questions=questions, dropped=len(picked) - len(questions),
        subjects=_report_subjects(), categories=srs.CATEGORIES, category_labels=srs.CATEGORY_LABELS,
        default_category='question' if questions else (request.args.get('category') or 'general'),
        subject_id=(request.args.get('subject') or '').upper(), limits=_limits())


@support_requests_bp.route('/new', methods=['POST'])
@login_required
def create():
    form = request.form
    questions = _questions_in_order(_id_list(','.join(form.getlist('question_ids'))))
    if any(not srs.can_report(current_user, [q.subject]) for q in questions):
        return _fail(_t('You cannot report on one of these questions.'), 403)
    subject_id = None
    if not questions:
        subject_id = (form.get('subject_id') or '').strip().upper() or None
        if subject_id and db.session.get(Subject, subject_id) is None:
            return _fail(_t('Unknown subject: {subject}', subject=subject_id))
        if not srs.can_report(current_user, [subject_id] if subject_id else []):
            return _fail(_t('You cannot report issues here.'), 403)
    category = form.get('category') or None
    if category not in srs.CATEGORIES:
        category = None
    subjects = {q.subject for q in questions} | ({subject_id} if subject_id else set())
    manage = srs.manages(is_super=authz.is_super_admin(current_user),
                         admin_subjects=authz.subjects_with_level(current_user, 'admin'), subjects=subjects)
    try:
        files = _uploads()
        req, msg = srs.create_request(reporter_id=current_user.id, title=form.get('title'),
                                      body=form.get('body'), category=category, subject_id=subject_id,
                                      questions=questions, files=files,
                                      actor_kind='admin' if manage else 'user')
        srs.mark_read(current_user.id, req.id, msg.id)
        db.session.commit()
    except srs.RequestError as exc:
        return _fail(exc)
    url = url_for('.thread', request_id=req.id)
    if authz.wants_json():
        return _ok(id=req.id, url=url)
    return redirect(url)


# ---------------------------------------------------------------- thread

@support_requests_bp.route('/<int:request_id>')
@login_required
def thread(request_id):
    req, access = _request_or_404(request_id)
    messages, attachments = srs.thread(req)
    links = srs.linked_questions([req.id])[req.id]
    live = {q.id: q for q in Question.query.filter(
        Question.id.in_([l.question_id for l in links if l.question_id] or [0])).all()}
    viewable = {qid for qid, q in live.items() if authz.has_level(current_user, q.subject, 'viewer')}
    editable = {qid for qid, q in live.items() if authz.has_level(current_user, q.subject, 'admin')}
    names = srs.display_names({m.author_user_id for m in messages} | {req.reporter_id, req.closed_by})
    batches = []
    if access.manage:
        batches = [b for b in ReviewBatch.query.filter_by(linked_request_id=req.id).order_by(ReviewBatch.id).all()
                   if authz.has_level(current_user, b.subject_id, 'admin')]
    if messages:
        srs.mark_read(current_user.id, req.id, messages[-1].id)
        db.session.commit()
    pending = srs.pending_approval(req)
    return render_template(
        'requests_thread.html', req=req, access=access, messages=messages, attachments=attachments,
        links=links, live=live, viewable=viewable, editable=editable, names=names, batches=batches,
        pending_id=pending.id if pending else None,
        statuses=srs.allowed_statuses(access.role, req.status), can_reply=srs.can_reply(access.role, req.status),
        status_labels=srs.STATUS_LABELS, status_badges=srs.STATUS_BADGES, category_labels=srs.CATEGORY_LABELS,
        render_body=srs.render_body, load_meta=srs.load_meta, is_inline_image=srs.is_inline_image,
        limits=_limits())


@support_requests_bp.route('/<int:request_id>/messages', methods=['POST'])
@login_required
def post_message(request_id):
    req, access = _request_or_404(request_id)
    try:
        files = _uploads()
        msg = srs.reply(req, access, user_id=current_user.id, body=request.form.get('body'), files=files)
        srs.mark_read(current_user.id, req.id, msg.id)
        db.session.commit()
    except srs.RequestError as exc:
        return _fail(exc)
    if authz.wants_json():
        return _ok(message_id=msg.id, status=req.status)
    return redirect(url_for('.thread', request_id=req.id))


@support_requests_bp.route('/<int:request_id>/status', methods=['POST'])
@login_required
def set_status(request_id):
    req, access = _request_or_404(request_id)
    data = _payload()
    try:
        msg = srs.set_status(req, (data.get('status') or '').strip(), role=access.role,
                             user_id=current_user.id, note=srs.clip(data.get('note'), srs.MAX_BODY))
        srs.mark_read(current_user.id, req.id, msg.id)
        db.session.commit()
    except srs.RequestError as exc:
        return _fail(exc, 403 if access.role is None else 400)
    if authz.wants_json():
        return _ok(status=req.status)
    return redirect(url_for('.thread', request_id=req.id))


@support_requests_bp.route('/<int:request_id>/approval', methods=['POST'])
@login_required
def decide(request_id):
    req, access = _request_or_404(request_id)
    if not access.approve:
        authz.deny(_t('Only an admin of every subject in this request can approve or reject the plan.'))
    data = _payload()
    decision = (data.get('decision') or '').strip().lower()
    if decision not in ('approve', 'reject'):
        return _fail(_t('Choose approve or reject.'))
    try:
        msg = srs.decide_approval(req, approve=decision == 'approve', user_id=current_user.id,
                                  note=srs.clip(data.get('note'), srs.MAX_BODY))
        srs.mark_read(current_user.id, req.id, msg.id)
        db.session.commit()
    except srs.RequestError as exc:
        return _fail(exc)
    if authz.wants_json():
        return _ok(status=req.status)
    return redirect(url_for('.thread', request_id=req.id))


@support_requests_bp.route('/attachment/<int:attachment_id>')
@login_required
def attachment(attachment_id):
    att = db.session.get(SupportRequestAttachment, attachment_id)
    if att is None:
        abort(404)
    _request_or_404(att.request_id)
    path = srs.attachment_path(att)
    if not path or not os.path.isfile(path):
        abort(404)
    inline = srs.is_inline_image(att.mime) and request.args.get('download') != '1'
    response = send_file(path, mimetype=att.mime if inline else 'application/octet-stream',
                         as_attachment=not inline, download_name=att.original_name, max_age=0)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Cache-Control'] = 'private, no-store'
    return response
