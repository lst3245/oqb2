"""Review pages (``/admin/review``): batches of proposed question changes,
worked through one question at a time (docs/modules/review.md).

Every route is ``@admin_required`` (admin somewhere) and then checks the
batch's subject at ``admin``. Writes go through ``review_service``, which
takes the ``review-apply`` restore point.
"""
from __future__ import annotations

from flask import (Blueprint, abort, flash, jsonify, redirect, render_template, request,
                   url_for)
from flask_login import current_user, login_required

from app import authz, db, hierarchy
from app import question_edit_service as qes
from app import review_service as rs
from app.i18n import t as _t
from app.i18n import tax_lang
from app.models import Question, ReviewBatch, ReviewItem, Subject, User
from app.utils import admin_required, get_user_admin_subjects

review_bp = Blueprint('review', __name__, url_prefix='/admin/review')

MAX_IMPORT_BYTES = 5 * 1024 * 1024
LIST_LIMIT = 500


@review_bp.app_context_processor
def _nav_globals():
    """``review_pending_count()`` for the Admin nav badge (one query, only
    when a template calls it)."""
    def review_pending_count():
        try:
            if current_user.is_authenticated:
                return rs.pending_count(current_user)
        except Exception:
            db.session.rollback()
        return 0
    return {'review_pending_count': review_pending_count}


def _batch_or_404(batch_id):
    batch = db.session.get(ReviewBatch, batch_id)
    if batch is None:
        abort(404)
    authz.require_subject(batch.subject_id, 'admin')
    return batch


def _item_or_404(item_id):
    item = db.session.get(ReviewItem, item_id)
    if item is None:
        abort(404)
    return item, _batch_or_404(item.batch_id)


def _usernames(ids):
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {u.id: (u.display_name or u.username) for u in User.query.filter(User.id.in_(ids)).all()}


def _question_for(item, batch, questions=None):
    if not item.question_id:
        return None
    q = (questions or {}).get(item.question_id) if questions is not None else db.session.get(Question, item.question_id)
    return q if (q is not None and q.subject == batch.subject_id) else None


def _item_json(item, batch, *, taxonomy=None, question=None, usernames=None):
    taxonomy = taxonomy or qes.Taxonomy.load(batch.subject_id)
    if question is None:
        question = _question_for(item, batch)
    out = rs.item_payload(item, question=question, taxonomy=taxonomy, mode=tax_lang(),
                          usernames=usernames if usernames is not None else _usernames([item.decided_by]))
    out['ancestors'] = ([{'id': a.id, 'qid': a.qid} for a in hierarchy.ancestors(question)]
                        if question is not None else [])
    return out


def _taxonomy_options(taxonomy):
    """``{kind: [{id, label, parent}]}`` for the per-field override pickers."""
    mode = tax_lang()
    out = {}
    for kind in ('topics', 'subtopics', 'chapters', 'subchapters'):
        nodes = sorted(taxonomy.nodes[kind].values(), key=lambda n: (getattr(n, 'sort_order', 0) or 0, n.id))
        out[kind] = [{'id': n.id, 'label': taxonomy.label(kind, n.id, mode),
                      'parent': taxonomy.parent.get(kind, {}).get(n.id)} for n in nodes]
    return out


def _json_body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _fail(message, status=400, **extra):
    db.session.rollback()
    return jsonify(dict({'success': False, 'error': str(message)}, **extra)), status


# ---------------------------------------------------------------- pages

@review_bp.route('/')
@login_required
@admin_required
def index():
    status = request.args.get('status', 'open')
    subject_id = (request.args.get('subject') or '').strip().upper()
    query = rs.scoped_batches(current_user)
    if status in rs.BATCH_STATUSES:
        query = query.filter(ReviewBatch.status == status)
    else:
        status = 'all'
    if subject_id:
        query = query.filter(ReviewBatch.subject_id == subject_id)
    batches = query.order_by(ReviewBatch.updated_at.desc(), ReviewBatch.id.desc()).limit(LIST_LIMIT).all()
    counts = rs.item_counts([b.id for b in batches])
    return render_template('admin_review.html',
                           batches=[rs.batch_payload(b, counts[b.id]) for b in batches],
                           status=status, subject_id=subject_id, subjects=get_user_admin_subjects())


@review_bp.route('/import', methods=['GET', 'POST'])
@login_required
@admin_required
def import_page():
    subjects = get_user_admin_subjects()
    ctx = {'subjects': subjects, 'payload_text': '', 'subject_id': (request.args.get('subject') or '').upper(),
           'title': '', 'preview': None, 'error': None}
    if request.method == 'GET':
        return render_template('admin_review_import.html', **ctx)

    text = request.form.get('payload') or ''
    upload = request.files.get('file')
    if upload is not None and upload.filename:
        raw = upload.read(MAX_IMPORT_BYTES + 1)
        if len(raw) > MAX_IMPORT_BYTES:
            ctx['error'] = _t('The file is larger than {n} MB', n=MAX_IMPORT_BYTES // (1024 * 1024))
            return render_template('admin_review_import.html', **ctx)
        text = raw.decode('utf-8-sig', errors='replace')
    ctx.update(payload_text=text, subject_id=(request.form.get('subject_id') or '').strip().upper(),
               title=(request.form.get('title') or '').strip())
    try:
        data = rs.parse_payload(text)
    except rs.ReviewError as exc:
        ctx['error'] = str(exc)
        return render_template('admin_review_import.html', **ctx)
    subject_id = ctx['subject_id'] or data['subject']
    if not subject_id:
        ctx['error'] = _t('Choose a subject (or put "subject" in the JSON)')
        return render_template('admin_review_import.html', **ctx)
    if not authz.has_level(current_user, subject_id, 'admin'):
        ctx['error'] = _t('You are not an admin of {subject}', subject=subject_id)
        return render_template('admin_review_import.html', **ctx)
    ctx['subject_id'] = subject_id
    ctx['title'] = ctx['title'] or data['title']
    valid, problems = rs.prepare(subject_id, data['items'])
    if request.form.get('action') == 'save' and valid:
        try:
            batch = rs.create_batch(subject_id, ctx['title'] or _t('Imported review'),
                                    summary=data['summary'], source='import', created_by=current_user.id,
                                    linked_request_id=_linked_request(data.get('request_id')))
            rs.add_items(batch, valid)
            db.session.commit()
        except rs.ReviewError as exc:
            db.session.rollback()
            ctx['error'] = str(exc)
            return render_template('admin_review_import.html', **ctx)
        flash(_t('Review batch saved with {n} items.', n=len(valid)), 'success')
        return redirect(url_for('review.batch_page', batch_id=batch.id))
    taxonomy = qes.Taxonomy.load(subject_id)
    mode = tax_lang()
    ctx['preview'] = {
        'valid': len(valid), 'total': len(data['items']), 'problems': problems,
        'errors': sum(1 for p in problems if p['level'] == 'error'),
        'rows': [{'qid': it['qid'], 'kind': it['kind'], 'title': it['title'], 'confidence': it['confidence'],
                  'changes': [{'label': qes.FIELDS[c['field']]['label'],
                               'old': qes.label(c['field'], c['old'], taxonomy, mode),
                               'new': qes.label(c['field'], c['new'], taxonomy, mode)}
                              for c in (it['changes'] or [])]}
                 for it in valid[:300]],
    }
    return render_template('admin_review_import.html', **ctx)


def _linked_request(raw):
    try:
        rid = int(raw)
    except (TypeError, ValueError):
        return None
    from app.models import SupportRequest
    return rid if rid > 0 and db.session.get(SupportRequest, rid) is not None else None


@review_bp.route('/<int:batch_id>')
@login_required
@admin_required
def batch_page(batch_id):
    batch = _batch_or_404(batch_id)
    return render_template('admin_review_batch.html', batch=rs.batch_payload(batch),
                           subject=db.session.get(Subject, batch.subject_id),
                           start_item=request.args.get('item', type=int))


# ---------------------------------------------------------------- JSON

@review_bp.route('/<int:batch_id>/items')
@login_required
@admin_required
def batch_items(batch_id):
    batch = _batch_or_404(batch_id)
    items = ReviewItem.query.filter_by(batch_id=batch.id).order_by(ReviewItem.position, ReviewItem.id).all()
    taxonomy = qes.Taxonomy.load(batch.subject_id)
    ids = [i.question_id for i in items if i.question_id]
    questions = {}
    for start in range(0, len(ids), 500):
        for q in Question.query.filter(Question.id.in_(ids[start:start + 500])).all():
            questions[q.id] = q
    names = _usernames([i.decided_by for i in items])
    return jsonify({
        'success': True,
        'batch': rs.batch_payload(batch),
        'taxonomy': _taxonomy_options(taxonomy),
        'items': [_item_json(i, batch, taxonomy=taxonomy, question=_question_for(i, batch, questions),
                             usernames=names) for i in items],
    })


@review_bp.route('/item/<int:item_id>')
@login_required
@admin_required
def item_detail(item_id):
    item, batch = _item_or_404(item_id)
    return jsonify({'success': True, 'item': _item_json(item, batch), 'batch': rs.batch_payload(batch)})


@review_bp.route('/item/<int:item_id>/apply', methods=['POST'])
@login_required
@admin_required
def item_apply(item_id):
    item, batch = _item_or_404(item_id)
    data = _json_body()
    fields = data.get('fields')
    overrides = data.get('overrides')
    try:
        out = rs.apply_item(item, batch, user_id=current_user.id,
                            fields=[str(f) for f in fields] if isinstance(fields, list) and fields else None,
                            overrides=overrides if isinstance(overrides, dict) else None,
                            confirm_stale=bool(data.get('confirm_stale')), note=data.get('note'))
        rs.refresh_batch_status(batch)
        db.session.commit()
    except qes.StaleError as exc:
        return _fail(exc, 409, stale=exc.fields)
    except (rs.ReviewError, qes.EditError) as exc:
        return _fail(exc)
    rs.log_corrections(item, batch, out['question'], out['taxonomy'], user_id=current_user.id)
    return jsonify({'success': True, 'item': _item_json(item, batch), 'batch': rs.batch_payload(batch)})


@review_bp.route('/item/<int:item_id>/reject', methods=['POST'])
@login_required
@admin_required
def item_reject(item_id):
    item, batch = _item_or_404(item_id)
    data = _json_body()
    try:
        rs.reject_item(item, user_id=current_user.id, note=data.get('note'))
        rs.refresh_batch_status(batch)
        db.session.commit()
    except rs.ReviewError as exc:
        return _fail(exc)
    rs.log_corrections(item, batch, _question_for(item, batch), user_id=current_user.id)
    return jsonify({'success': True, 'item': _item_json(item, batch), 'batch': rs.batch_payload(batch)})


@review_bp.route('/item/<int:item_id>/skip', methods=['POST'])
@login_required
@admin_required
def item_skip(item_id):
    item, batch = _item_or_404(item_id)
    try:
        rs.skip_item(item, user_id=current_user.id)
        db.session.commit()
    except rs.ReviewError as exc:
        return _fail(exc)
    return jsonify({'success': True, 'item': _item_json(item, batch), 'batch': rs.batch_payload(batch)})


@review_bp.route('/item/<int:item_id>/reopen', methods=['POST'])
@login_required
@admin_required
def item_reopen(item_id):
    item, batch = _item_or_404(item_id)
    try:
        rs.reopen_item(item)
        rs.refresh_batch_status(batch)
        db.session.commit()
    except rs.ReviewError as exc:
        return _fail(exc)
    return jsonify({'success': True, 'item': _item_json(item, batch), 'batch': rs.batch_payload(batch)})


@review_bp.route('/<int:batch_id>/apply-all', methods=['POST'])
@login_required
@admin_required
def batch_apply_all(batch_id):
    batch = _batch_or_404(batch_id)
    data = _json_body()
    try:
        threshold = rs.parse_confidence(data.get('min_confidence'))
        summary, applied = rs.apply_all(batch, user_id=current_user.id, min_confidence=threshold)
        db.session.commit()
    except (rs.ReviewError, qes.EditError) as exc:
        return _fail(exc)
    taxonomy = qes.Taxonomy.load(batch.subject_id)
    for item, question in applied:
        rs.log_corrections(item, batch, question, taxonomy, user_id=current_user.id)
    return jsonify({'success': True, 'summary': summary, 'batch': rs.batch_payload(batch)})


@review_bp.route('/<int:batch_id>/status', methods=['POST'])
@login_required
@admin_required
def batch_status(batch_id):
    batch = _batch_or_404(batch_id)
    status = (_json_body().get('status') or request.form.get('status') or '').strip()
    if status not in rs.BATCH_STATUSES:
        return _fail(_t('Unknown status'))
    batch.status = status
    db.session.commit()
    if request.is_json:
        return jsonify({'success': True, 'batch': rs.batch_payload(batch)})
    return redirect(url_for('review.batch_page', batch_id=batch.id))
