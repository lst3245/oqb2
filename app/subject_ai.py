"""
Subject AI tuning — routes (``/admin/subject-ai``, ``/admin/subjects/<sid>/ai/*``,
``/admin/questions/<qid>/ai/tag-corrections``).

Subject-admin surface for shaping Auto Tag per subject: free-text
instructions (append / replace), taxonomy hints, prompt preview, a no-write
evaluation run against already-tagged questions, and the teacher-correction
report. Logic lives in ``app/subject_ai_service.py``; prompt assembly in
``ai_tools.build_tag_prompt``. Spec: ``docs/modules/subject-ai.md``.

Authz: every subject route carries ``subject_id`` in the URL so
``@subject_admin_required`` actually fires; the per-question route checks the
question's subject explicitly (the subject decorators pass through without
a ``subject_id``).
"""
from __future__ import annotations

from flask import (Blueprint, render_template, request, jsonify, redirect,
                   url_for, abort, current_app)
from flask_login import login_required, current_user

from app import db
from app import ai_prompts
from app import subject_ai_service as svc
from app.models import Subject, Question, LLMConfig
from app.utils import (admin_required, subject_admin_required,
                       get_user_admin_subjects, VERSIONS)


subject_ai_bp = Blueprint('subject_ai', __name__, url_prefix='/admin')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ai_enabled_or_400():
    if not current_app.config.get('AI_TOOLS_ENABLED', True):
        return jsonify({'error': 'AI Tools are disabled (see System Settings).'}), 400
    return None


def _subject_or_404(subject_id):
    return Subject.query.get_or_404(subject_id)


def _fields_from(values):
    valid = set(ai_prompts.TAG_FIELDS)
    out = []
    for v in values or []:
        v = str(v).strip()
        if v in valid and v not in out:
            out.append(v)
    return out


def _versions_from(values):
    valid = set(VERSIONS)
    out = []
    for v in values or []:
        v = str(v).strip().upper()
        if v in valid and v not in out:
            out.append(v)
    return out


def _csv(name):
    raw = request.args.get(name, '') or ''
    return [s for s in (p.strip() for p in raw.split(',')) if s]


def _require_question_admin(question):
    if current_user.is_super_admin:
        return
    if question.subject not in {s.id for s in get_user_admin_subjects()}:
        abort(403)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@subject_ai_bp.route('/subject-ai')
@login_required
@admin_required
def index():
    """Redirect to the first subject the user administers."""
    subjects = get_user_admin_subjects()
    if not subjects:
        abort(403)
    subjects = sorted(subjects, key=lambda s: s.id)
    return redirect(url_for('subject_ai.page', subject_id=subjects[0].id))


@subject_ai_bp.route('/subjects/<subject_id>/ai')
@login_required
@subject_admin_required
def page(subject_id):
    subject = _subject_or_404(subject_id)
    subjects = sorted(get_user_admin_subjects(), key=lambda s: s.id)
    return render_template(
        'admin_subject_ai.html',
        subject=subject,
        admin_subjects=subjects,
        tag_fields=list(ai_prompts.TAG_FIELD_LABELS.items()),
        default_fields=['q_type', 'major_topic', 'major_subtopic', 'chapter'],
        note_modes=svc.NOTE_MODES,
        max_note_chars=svc.MAX_NOTE_CHARS,
        max_desc_chars=svc.MAX_DESCRIPTION_CHARS,
        max_examples=svc.MAX_EXAMPLES_LIMIT,
    )


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@subject_ai_bp.route('/subjects/<subject_id>/ai/data')
@login_required
@subject_admin_required
def data(subject_id):
    subject = _subject_or_404(subject_id)
    days = request.args.get('days', type=int)
    endpoints = (LLMConfig.query.filter_by(enabled=True)
                 .order_by(LLMConfig.sort_order, LLMConfig.name).all())
    from app import llm_client
    default_ep = llm_client.resolve_default_endpoint('AUTOTAG_DEFAULT_LLM', vision_only=True)
    return jsonify({
        'subject': {'id': subject.id, 'name': subject.name},
        'note': svc.note_as_dict(svc.get_note(subject.id)),
        'builtin_system': ai_prompts.get_prompt('TAG_SYSTEM'),
        'taxonomy': svc.taxonomy_tree(subject.id),
        'report': svc.corrections_report(subject.id, days=days),
        'endpoints': [{'id': e.id, 'name': e.name, 'model_name': e.model_name,
                       'supports_vision': bool(e.supports_vision),
                       'kind': e.kind, 'max_concurrency': e.max_concurrency}
                      for e in endpoints],
        'default_endpoint_id': default_ep.id if default_ep else None,
        'ai_enabled': bool(current_app.config.get('AI_TOOLS_ENABLED', True)),
    })


@subject_ai_bp.route('/subjects/<subject_id>/ai/note', methods=['POST'])
@login_required
@subject_admin_required
def save_note(subject_id):
    subject = _subject_or_404(subject_id)
    body = request.get_json(silent=True) or {}
    try:
        row = svc.save_note(subject.id, body.get('mode'), body.get('content'),
                            body.get('examples_limit'), user_id=current_user.id)
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    return jsonify({'success': True, 'note': svc.note_as_dict(row)})


@subject_ai_bp.route('/subjects/<subject_id>/ai/taxonomy-description', methods=['POST'])
@login_required
@subject_admin_required
def taxonomy_description(subject_id):
    subject = _subject_or_404(subject_id)
    body = request.get_json(silent=True) or {}
    try:
        desc = svc.set_description(subject.id, body.get('kind'), body.get('id'),
                                   body.get('description'))
    except LookupError as e:
        return jsonify({'success': False, 'error': str(e)}), 404
    except (ValueError, TypeError) as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    return jsonify({'success': True, 'description': desc})


@subject_ai_bp.route('/subjects/<subject_id>/ai/corrections')
@login_required
@subject_admin_required
def corrections(subject_id):
    subject = _subject_or_404(subject_id)
    days = request.args.get('days', type=int)
    return jsonify(svc.corrections_report(subject.id, days=days))


@subject_ai_bp.route('/subjects/<subject_id>/ai/corrections/clear', methods=['POST'])
@login_required
@subject_admin_required
def corrections_clear(subject_id):
    subject = _subject_or_404(subject_id)
    n = svc.clear_corrections(subject.id)
    return jsonify({'success': True, 'deleted': n})


# ---------------------------------------------------------------------------
# Prompt preview + evaluation
# ---------------------------------------------------------------------------

def _load_subject_question(subject, body):
    """Resolve ``question_id`` or ``qid`` from a JSON body to a Question of
    ``subject``. Returns (question, error_response, status)."""
    q = None
    if body.get('question_id') not in (None, ''):
        try:
            q = Question.query.get(int(body['question_id']))
        except (TypeError, ValueError):
            return None, jsonify({'error': 'question_id must be an integer'}), 400
    elif (body.get('qid') or '').strip():
        q = Question.query.filter_by(qid=body['qid'].strip()).first()
    else:
        return None, jsonify({'error': 'question_id or qid is required'}), 400
    if q is None:
        return None, jsonify({'error': 'question not found'}), 404
    if q.subject != subject.id:
        return None, jsonify({'error': 'question belongs to another subject'}), 400
    return q, None, None


@subject_ai_bp.route('/subjects/<subject_id>/ai/preview', methods=['POST'])
@login_required
@subject_admin_required
def preview(subject_id):
    """Assemble (but do not send) the exact Auto Tag prompt for one question
    of this subject, so the admin can see what the model would see."""
    guard = _ai_enabled_or_400()
    if guard:
        return guard
    subject = _subject_or_404(subject_id)
    body = request.get_json(silent=True) or {}
    q, err, code = _load_subject_question(subject, body)
    if err:
        return err, code
    fields = _fields_from(body.get('fields'))
    if not fields:
        return jsonify({'error': 'Select at least one tag field.'}), 400
    versions = _versions_from(body.get('versions')) or list(VERSIONS)

    from app.admin import _ai_load_endpoint_from_body
    cfg, err, code = _ai_load_endpoint_from_body(body, 'AUTOTAG_DEFAULT_LLM')
    if err:
        return err, code

    from app import ai_tools
    prompt = ai_tools.build_tag_prompt(
        q, versions, fields, cfg,
        image_max_dim=int(current_app.config.get('LLM_IMAGE_MAX_DIM', 1600)),
        source_path=current_app.config['SOURCE_PATH'],
        include_images=False)
    if prompt.get('error'):
        return jsonify({'success': False, 'error': prompt['error']}), 200
    return jsonify({
        'success': True,
        'question': {'id': q.id, 'qid': q.qid},
        'endpoint': {'id': cfg.id, 'name': cfg.name, 'model_name': cfg.model_name},
        'system': prompt['system'],
        'user': prompt['user'],
        'image_count': prompt['image_count'],
        'text_block_count': len(prompt['text_blocks']),
        'subject_note': prompt['subject_note'],
        'existing': svc.question_labels(q, fields),
    })


@subject_ai_bp.route('/subjects/<subject_id>/ai/evaluate')
@login_required
@subject_admin_required
def evaluate(subject_id):
    """SSE: run Auto Tag over already-tagged questions and report agreement
    with their existing tags. Writes nothing.

    Query: ``question_ids`` (csv) OR ``sample`` (int, random tagged
    questions), ``fields`` (csv), ``versions`` (csv), ``endpoint_id?``,
    ``parallel`` (1/0).
    """
    guard = _ai_enabled_or_400()
    if guard:
        return guard
    subject = _subject_or_404(subject_id)
    fields = _fields_from(_csv('fields'))
    if not fields:
        return jsonify({'error': 'fields must include at least one tag field'}), 400
    versions = _versions_from(_csv('versions')) or list(VERSIONS)

    from app.admin import _ai_load_endpoint, _ai_parallel, _ai_stream
    cfg, err, code = _ai_load_endpoint('AUTOTAG_DEFAULT_LLM')
    if err:
        return err, code

    raw_ids = _csv('question_ids')
    if raw_ids:
        try:
            ids = [int(s) for s in raw_ids]
        except ValueError:
            return jsonify({'error': 'question_ids must be integers'}), 400
        qs = (Question.query.filter(Question.id.in_(ids))
              .filter(Question.subject == subject.id).all())
    else:
        qs = svc.sample_tagged_questions(subject.id, fields,
                                         request.args.get('sample', 20, type=int))
    if not qs:
        return jsonify({'error': 'No tagged questions found to evaluate.'}), 400

    want_parallel = request.args.get('parallel', '0') in ('1', 'true', 'yes')

    def factory(app, cancel):
        image_max_dim = int(app.config.get('LLM_IMAGE_MAX_DIM', 1600))
        source_path = app.config['SOURCE_PATH']
        live_cfg = LLMConfig.query.get(cfg.id)
        live_cfg._batch = True
        on, workers = _ai_parallel(live_cfg, want_parallel)
        return svc.iter_tag_evaluate(qs, versions, fields, live_cfg, image_max_dim,
                                     source_path, cancel, parallel=on, app=app,
                                     max_workers=workers)

    return _ai_stream(factory)


# ---------------------------------------------------------------------------
# Correction logging (edit modal → Save Tags after Auto Tag)
# ---------------------------------------------------------------------------

@subject_ai_bp.route('/questions/<int:question_id>/ai/tag-corrections', methods=['POST'])
@login_required
@admin_required
def log_tag_corrections(question_id):
    """Record what the model suggested vs what the teacher saved.

    Body: ``{fields[], suggested_display{field: name|[names]|int},
    saved{...readCurrentTagFormState shape...}, model?, reasons{field: str}?}``.
    ``suggested_display`` is the ``display`` dict returned by
    ``/ai/suggest-tags``; ``saved`` is the form state at Save time.
    """
    question = Question.query.get_or_404(question_id)
    _require_question_admin(question)
    body = request.get_json(silent=True) or {}
    fields = _fields_from(body.get('fields'))
    if not fields:
        return jsonify({'success': False, 'error': 'fields is required'}), 400
    suggested = svc.display_to_labels(body.get('suggested_display') or {}, fields)
    saved = svc.labels_from_form_state(body.get('saved') or {}, fields)
    try:
        n = svc.log_corrections(question, suggested, saved, fields,
                                model=body.get('model'), reasons=body.get('reasons'),
                                user_id=current_user.id)
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception('tag-corrections log failed for q%s', question_id)
        return jsonify({'success': False, 'error': str(e)}), 500
    return jsonify({'success': True, 'logged': n})
