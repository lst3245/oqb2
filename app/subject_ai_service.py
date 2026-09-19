"""
Subject AI tuning — service layer.

Lets a subject admin shape how Auto Tag behaves for THEIR subject without
touching the super-admin prompt registry:

* :class:`SubjectPromptNote` — free-text instructions (``append`` to the user
  turn, or ``replace`` the ``TAG_SYSTEM`` body; the JSON format block is
  always re-attached by ``ai_prompts.system_prompt_with_body``).
* Taxonomy hints — ``description`` on Topic / Subtopic / Chapter / Subchapter,
  rendered by ``ai_prompts.build_tag_taxonomy`` after an em dash.
* :class:`TagCorrection` log — written when a teacher saves tags right after
  an Auto Tag suggestion; drives the disagreement report and the aggregated
  "correction patterns" block that ``examples_limit`` feeds back into the
  prompt.
* Evaluation — ``iter_tag_evaluate`` runs ``suggest_tags`` over already-tagged
  questions WITHOUT writing and reports per-field agreement, so an admin can
  tune a hint and measure the effect.

Routes live in ``app/subject_ai.py``; prompt assembly is
``ai_tools.build_tag_prompt``. Spec: ``docs/modules/subject-ai.md``.
"""
from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from sqlalchemy import func

from app import db
from app import ai_prompts
from app.models import (Question, Topic, Subtopic, Chapter, Subchapter,
                        SubjectPromptNote, TagCorrection)

logger = logging.getLogger(__name__)

FEATURE_TAG = 'tag'
NOTE_MODES = ('append', 'replace')
MAX_NOTE_CHARS = 8000
MAX_DESCRIPTION_CHARS = 300
MAX_EXAMPLES_LIMIT = 30

# Field key -> (scalar column | relationship) on Question; list fields are M2M.
LIST_FIELDS = ('minor_topics', 'subtopics')


# ---------------------------------------------------------------------------
# Subject notes
# ---------------------------------------------------------------------------

def get_note(subject_id, feature=FEATURE_TAG):
    """The subject's note row for ``feature`` or None. Never raises — a DB
    hiccup here must not block a tagging call."""
    try:
        return (SubjectPromptNote.query
                .filter_by(subject_id=subject_id, feature=feature).first())
    except Exception:  # pragma: no cover - defensive
        logger.exception('SubjectPromptNote lookup failed for %s/%s', subject_id, feature)
        try:
            db.session.rollback()
        except Exception:
            pass
        return None


def save_note(subject_id, mode, content, examples_limit, user_id=None,
              feature=FEATURE_TAG):
    """Upsert the subject's note. Validates and commits. Returns the row.
    Blank content with ``examples_limit == 0`` deletes the row (nothing to
    layer)."""
    mode = (mode or 'append').strip().lower()
    if mode not in NOTE_MODES:
        raise ValueError(f'mode must be one of {", ".join(NOTE_MODES)}')
    content = (content or '').strip('\ufeff').strip()
    if len(content) > MAX_NOTE_CHARS:
        raise ValueError(f'instructions exceed {MAX_NOTE_CHARS} characters')
    try:
        examples_limit = int(examples_limit or 0)
    except (TypeError, ValueError):
        raise ValueError('examples_limit must be an integer')
    examples_limit = max(0, min(MAX_EXAMPLES_LIMIT, examples_limit))
    if mode == 'replace' and not content:
        raise ValueError('replace mode needs a non-empty system prompt body')

    row = get_note(subject_id, feature)
    if not content and examples_limit == 0:
        if row is not None:
            db.session.delete(row)
            db.session.commit()
        return None
    if row is None:
        row = SubjectPromptNote(subject_id=subject_id, feature=feature)
        db.session.add(row)
    row.mode = mode
    row.content = content or None
    row.examples_limit = examples_limit
    row.updated_by = user_id
    row.updated_at = datetime.utcnow()
    db.session.commit()
    return row


def note_as_dict(row):
    if row is None:
        return {'mode': 'append', 'content': '', 'examples_limit': 0,
                'updated_at': None, 'updated_by': None}
    from app.utils import utc_iso
    return {
        'mode': row.mode or 'append',
        'content': row.content or '',
        'examples_limit': int(row.examples_limit or 0),
        'updated_at': utc_iso(row.updated_at) if row.updated_at else None,
        'updated_by': (row.updated_by_user.username
                       if getattr(row, 'updated_by_user', None) else None),
    }


# ---------------------------------------------------------------------------
# Taxonomy hints
# ---------------------------------------------------------------------------

_KIND_MODEL = {'topic': Topic, 'subtopic': Subtopic,
               'chapter': Chapter, 'subchapter': Subchapter}


def taxonomy_tree(subject_id):
    """Nested dict of the subject's taxonomy with descriptions, for the
    hints editor."""
    def _node(n, kind):
        return {'id': n.id, 'kind': kind, 'name': n.name,
                'description': n.description or '',
                'hidden': bool(getattr(n, 'hidden', False))}

    topics = []
    for t in Topic.query.filter_by(subject_id=subject_id).order_by(Topic.sort_order, Topic.name):
        d = _node(t, 'topic')
        d['children'] = [_node(s, 'subtopic') for s in
                         t.subtopics.order_by(Subtopic.sort_order, Subtopic.name)]
        topics.append(d)
    chapters = []
    for c in Chapter.query.filter_by(subject_id=subject_id).order_by(Chapter.sort_order, Chapter.name):
        d = _node(c, 'chapter')
        d['children'] = [_node(s, 'subchapter') for s in
                         c.subchapters.order_by(Subchapter.sort_order, Subchapter.name)]
        chapters.append(d)
    return {'topics': topics, 'chapters': chapters}


def _node_subject(kind, node):
    if kind in ('topic', 'chapter'):
        return node.subject_id
    if kind == 'subtopic':
        return node.topic.subject_id if node.topic else None
    return node.chapter.subject_id if node.chapter else None


def set_description(subject_id, kind, node_id, description):
    """Set / clear the tagging hint on one taxonomy node. Validates that the
    node belongs to ``subject_id``. Returns the cleaned description."""
    model = _KIND_MODEL.get((kind or '').lower())
    if model is None:
        raise ValueError('kind must be topic | subtopic | chapter | subchapter')
    node = model.query.get(int(node_id))
    if node is None or _node_subject(kind, node) != subject_id:
        raise LookupError('node not found in this subject')
    desc = ' '.join((description or '').split())
    if len(desc) > MAX_DESCRIPTION_CHARS:
        raise ValueError(f'description exceeds {MAX_DESCRIPTION_CHARS} characters')
    node.description = desc or None
    db.session.commit()
    return desc


# ---------------------------------------------------------------------------
# Labels + comparison (pure where possible)
# ---------------------------------------------------------------------------

def normalize_label(value):
    """Canonical comparable form: None for empty, a sorted tuple of strings
    for lists, else the stripped string. Pure."""
    if value is None:
        return None
    if isinstance(value, (list, tuple, set)):
        items = sorted({str(v).strip() for v in value if str(v).strip()})
        return tuple(items) if items else None
    s = str(value).strip()
    return s or None


def label_text(value):
    """Human string for a normalized or raw label; '' for empty. Pure."""
    n = normalize_label(value)
    if n is None:
        return ''
    if isinstance(n, tuple):
        return ', '.join(n)
    return n


def compare_labels(existing, suggested, fields):
    """Per-field comparison of two ``{field: label}`` dicts. Pure.

    Returns ``{field: {existing, suggested, applicable, agree}}`` where
    ``applicable`` is True only when the existing side has a value (there is
    a ground truth to compare against) and ``agree`` is set-equality for list
    fields, string equality otherwise (case-insensitive)."""
    out = {}
    for f in fields:
        e = normalize_label(existing.get(f))
        s = normalize_label(suggested.get(f))

        def _ci(v):
            if v is None:
                return None
            if isinstance(v, tuple):
                return tuple(x.lower() for x in v)
            return v.lower()

        out[f] = {
            'existing': label_text(e),
            'suggested': label_text(s),
            'applicable': e is not None,
            'agree': (e is not None and _ci(e) == _ci(s)),
        }
    return out


def question_labels(question, fields):
    """Current tag values of ``question`` as display labels keyed by field."""
    out = {}
    for f in fields:
        if f == 'q_type':
            out[f] = question.q_type
        elif f == 'level':
            out[f] = None if question.level is None else str(question.level)
        elif f == 'section':
            out[f] = question.section
        elif f == 'major_topic':
            out[f] = question.major_topic.name if question.major_topic else None
        elif f == 'major_subtopic':
            out[f] = question.major_subtopic.name if question.major_subtopic else None
        elif f == 'minor_topics':
            out[f] = [t.name for t in question.minor_topics]
        elif f == 'subtopics':
            out[f] = [s.name for s in question.subtopics]
        elif f == 'chapter':
            out[f] = question.chapter.name if question.chapter else None
        elif f == 'subchapter':
            out[f] = question.subchapter.name if question.subchapter else None
    return out


def labels_from_form_state(state, fields):
    """Resolve the edit-modal form state (``readCurrentTagFormState`` shape:
    ``*_id`` / ``*_ids`` plus scalars) to display labels keyed by field."""
    state = state or {}

    def _name(model, pk):
        if pk in (None, ''):
            return None
        try:
            row = model.query.get(int(pk))
        except (TypeError, ValueError):
            return None
        return row.name if row else None

    def _names(model, pks):
        out = []
        for pk in pks or []:
            n = _name(model, pk)
            if n:
                out.append(n)
        return out

    out = {}
    for f in fields:
        if f == 'q_type':
            out[f] = state.get('q_type')
        elif f == 'level':
            lv = state.get('level')
            out[f] = None if lv in (None, '') else str(lv)
        elif f == 'section':
            out[f] = state.get('section')
        elif f == 'major_topic':
            out[f] = _name(Topic, state.get('major_topic_id'))
        elif f == 'major_subtopic':
            out[f] = _name(Subtopic, state.get('major_subtopic_id'))
        elif f == 'minor_topics':
            out[f] = _names(Topic, state.get('minor_topic_ids'))
        elif f == 'subtopics':
            out[f] = _names(Subtopic, state.get('subtopic_ids'))
        elif f == 'chapter':
            out[f] = _name(Chapter, state.get('chapter_id'))
        elif f == 'subchapter':
            out[f] = _name(Subchapter, state.get('subchapter_id'))
    return out


def display_to_labels(display, fields):
    """``suggest_tags``' ``display`` dict (names keyed by field; level is an
    int) → labels keyed by field."""
    display = display or {}
    out = {}
    for f in fields:
        v = display.get(f)
        if f == 'level' and v is not None:
            v = str(v)
        out[f] = v
    return out


# ---------------------------------------------------------------------------
# Correction log
# ---------------------------------------------------------------------------

def log_corrections(question, suggested_labels, saved_labels, fields, *,
                    model=None, reasons=None, user_id=None):
    """Write one :class:`TagCorrection` per field in ``fields`` where at
    least one side has a value. Commits. Returns the number of rows."""
    reasons = reasons or {}
    cmp = compare_labels(saved_labels, suggested_labels, fields)
    rows = 0
    for f in fields:
        c = cmp[f]
        if not c['existing'] and not c['suggested']:
            continue
        db.session.add(TagCorrection(
            question_id=question.id,
            subject_id=question.subject,
            field=f,
            suggested=(c['suggested'] or None) and c['suggested'][:500],
            saved=(c['existing'] or None) and c['existing'][:500],
            agreed=bool(c['agree']),
            reason=(str(reasons.get(f)).strip()[:400] if reasons.get(f) else None),
            model=(model or None) and str(model)[:200],
            user_id=user_id,
        ))
        rows += 1
    if rows:
        db.session.commit()
    return rows


def _corrections_query(subject_id, days=None, fields=None):
    q = TagCorrection.query.filter_by(subject_id=subject_id)
    if days:
        q = q.filter(TagCorrection.created_at >= datetime.utcnow() - timedelta(days=int(days)))
    if fields:
        q = q.filter(TagCorrection.field.in_(list(fields)))
    return q


def corrections_report(subject_id, days=None, recent_limit=60):
    """Per-field agreement stats, top confusion pairs and recent rows."""
    from app.utils import utc_iso
    rows = (_corrections_query(subject_id, days)
            .order_by(TagCorrection.created_at.desc()).all())
    per_field = defaultdict(lambda: {'total': 0, 'agreed': 0})
    pairs = Counter()
    for r in rows:
        pf = per_field[r.field]
        pf['total'] += 1
        if r.agreed:
            pf['agreed'] += 1
        else:
            pairs[(r.field, r.suggested or '', r.saved or '')] += 1

    fields_out = []
    for f in ai_prompts.TAG_FIELDS:
        if f in per_field:
            pf = per_field[f]
            fields_out.append({'field': f, 'label': ai_prompts.TAG_FIELD_LABELS[f],
                               'total': pf['total'], 'agreed': pf['agreed'],
                               'rate': (pf['agreed'] / pf['total']) if pf['total'] else None})
    confusions = [{'field': f, 'label': ai_prompts.TAG_FIELD_LABELS.get(f, f),
                   'suggested': s, 'saved': v, 'count': n}
                  for (f, s, v), n in pairs.most_common(40)]
    recent = [{
        'id': r.id, 'question_id': r.question_id,
        'qid': r.question.qid if r.question else None,
        'field': r.field, 'label': ai_prompts.TAG_FIELD_LABELS.get(r.field, r.field),
        'suggested': r.suggested or '', 'saved': r.saved or '',
        'agreed': bool(r.agreed), 'reason': r.reason or '',
        'model': r.model or '', 'user': r.user.username if r.user else None,
        'created_at': utc_iso(r.created_at) if r.created_at else None,
    } for r in rows[:recent_limit]]
    total = len(rows)
    agreed = sum(1 for r in rows if r.agreed)
    return {'total': total, 'agreed': agreed,
            'rate': (agreed / total) if total else None,
            'fields': fields_out, 'confusions': confusions, 'recent': recent}


def clear_corrections(subject_id):
    """Delete every correction row for the subject. Returns the count."""
    n = _corrections_query(subject_id).delete(synchronize_session=False)
    db.session.commit()
    return n


def correction_patterns_text(subject_id, fields, limit=10):
    """Aggregate disagreements into a compact text block for the prompt:
    the most frequent (field, suggested → saved) pairs among ``fields``.
    '' when there is nothing to say. Never raises."""
    try:
        rows = (_corrections_query(subject_id, fields=fields)
                .filter(TagCorrection.agreed.is_(False)).all())
    except Exception:  # pragma: no cover - defensive
        logger.exception('correction pattern query failed for %s', subject_id)
        try:
            db.session.rollback()
        except Exception:
            pass
        return ''
    if not rows:
        return ''
    pairs = Counter((r.field, r.suggested or '', r.saved or '') for r in rows)
    lines = []
    for (f, s, v), n in pairs.most_common(max(1, int(limit))):
        label = ai_prompts.TAG_FIELD_LABELS.get(f, f)
        times = 'once' if n == 1 else f'{n} times'
        if s and v:
            lines.append(f'- {label}: the model suggested "{s}" but the teachers chose "{v}" ({times}).')
        elif v:
            lines.append(f'- {label}: the model left it empty but the teachers chose "{v}" ({times}).')
        else:
            lines.append(f'- {label}: the model suggested "{s}" but the teachers cleared it ({times}).')
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Evaluation (compare against existing tags, no writes)
# ---------------------------------------------------------------------------

def sample_tagged_questions(subject_id, fields, n):
    """Up to ``n`` random non-stem questions of the subject that already hold
    a value in at least one of ``fields``. Returns a list of Question."""
    from app.hierarchy import is_stem
    n = max(1, min(500, int(n or 20)))
    conds = []
    fset = set(fields)
    if 'q_type' in fset:
        conds.append(Question.q_type.isnot(None))
    if 'level' in fset:
        conds.append(Question.level.isnot(None))
    if 'section' in fset:
        conds.append(Question.section.isnot(None))
    if fset & {'major_topic', 'major_subtopic'}:
        conds.append(Question.major_topic_id.isnot(None))
    if fset & {'chapter', 'subchapter'}:
        conds.append(Question.chapter_id.isnot(None))
    if 'minor_topics' in fset:
        conds.append(Question.minor_topics.any())
    if 'subtopics' in fset:
        conds.append(Question.subtopics.any())
    q = Question.query.filter(Question.subject == subject_id)
    if conds:
        q = q.filter(db.or_(*conds))
    rows = q.order_by(func.rand()).limit(n * 2).all()
    out = [r for r in rows if not is_stem(r)]
    return out[:n]


def evaluate_one(question, versions, fields, config, image_max_dim, source_path):
    """Suggest tags for ONE already-tagged question and compare with what it
    holds. Pure per-item unit (no DB writes). Returns
    ``{status: compared|skip|error, message, fields: {f: {...}}, agreed, applicable}``."""
    from app import ai_tools, llm_client
    from app.hierarchy import is_stem
    label = question.qid
    if is_stem(question):
        return {'status': 'skip', 'message': f'{label} — stem (tags live on parts)',
                'fields': {}, 'agreed': 0, 'applicable': 0}
    existing = question_labels(question, fields)
    if all(normalize_label(v) is None for v in existing.values()):
        return {'status': 'skip', 'message': f'{label} — no existing tags in the selected fields',
                'fields': {}, 'agreed': 0, 'applicable': 0}
    try:
        res = ai_tools.suggest_tags(question, versions, fields, config,
                                    image_max_dim, source_path)
    except llm_client.LLMError as e:
        return {'status': 'error', 'message': f'{label} — LLM error: {e}',
                'fields': {}, 'agreed': 0, 'applicable': 0}
    except Exception as e:
        logger.exception('Evaluate suggest failed for %s', label)
        return {'status': 'error', 'message': f'{label} — {e}',
                'fields': {}, 'agreed': 0, 'applicable': 0}
    if not res.get('ok'):
        return {'status': 'skip', 'message': f'{label} — {res.get("error") or "no suggestion"}',
                'fields': {}, 'agreed': 0, 'applicable': 0}

    suggested = display_to_labels(res.get('display'), fields)
    cmp = compare_labels(existing, suggested, fields)
    reasons = res.get('reasons') or {}
    confidence = res.get('confidence') or {}
    for f in fields:
        cmp[f]['reason'] = reasons.get(f, '')
        cmp[f]['confidence'] = confidence.get(f)
    applicable = sum(1 for c in cmp.values() if c['applicable'])
    agreed = sum(1 for c in cmp.values() if c['agree'])
    diffs = [f'{ai_prompts.TAG_FIELD_LABELS.get(f, f)}: "{c["suggested"] or "—"}" vs "{c["existing"]}"'
             for f, c in cmp.items() if c['applicable'] and not c['agree']]
    unmatched = res.get('unmatched') or []
    msg = f'{label} — {agreed}/{applicable} agree'
    if diffs:
        msg += '; ' + '; '.join(diffs)
    if unmatched:
        msg += f' (unmatched: {", ".join(u["name"] for u in unmatched)})'
    return {'status': 'compared', 'message': msg, 'fields': cmp,
            'agreed': agreed, 'applicable': applicable,
            'question_id': question.id, 'qid': label}


def iter_tag_evaluate(qs, versions, fields, config, image_max_dim, source_path,
                      cancel, parallel=False, app=None, max_workers=1):
    """SSE generator mirroring ``ai_tools.iter_auto_tag`` but comparing
    instead of applying. Events: info / success (all agree) / skip / error /
    done; compared events carry ``detail`` (per-field comparison) and
    ``question_id`` / ``qid``. ``done`` carries ``stats`` with the overall
    and per-field agreement."""
    fields = [f for f in (fields or []) if f in ai_prompts.TAG_FIELDS]
    total = len(qs)
    yield {'type': 'info',
           'message': (f'Evaluating {total} tagged question(s) — fields: '
                       f'{", ".join(fields) or "(none)"}; versions: '
                       f'{", ".join(versions)}. Nothing is written.')}

    compared = skipped = errors = 0
    agreed_total = applicable_total = 0
    per_field = {f: {'applicable': 0, 'agreed': 0} for f in fields}
    current = 0

    def _absorb(res):
        nonlocal compared, skipped, errors, agreed_total, applicable_total
        st = res['status']
        if st == 'compared':
            compared += 1
            agreed_total += res['agreed']
            applicable_total += res['applicable']
            for f, c in res['fields'].items():
                if c['applicable']:
                    per_field[f]['applicable'] += 1
                    if c['agree']:
                        per_field[f]['agreed'] += 1
            all_ok = res['applicable'] > 0 and res['agreed'] == res['applicable']
            return 'success' if all_ok else 'issue'
        if st == 'skip':
            skipped += 1
            return 'skip'
        errors += 1
        return 'error'

    def _event(res, ev_type):
        ev = {'type': ev_type, 'message': res['message'],
              'current': current, 'total': total}
        if res['status'] == 'compared':
            ev['detail'] = res['fields']
            ev['question_id'] = res.get('question_id')
            ev['qid'] = res.get('qid')
            ev['agreed'] = res['agreed']
            ev['applicable'] = res['applicable']
        return ev

    def _worker(question):
        question = db.session.get(Question, question.id) or question
        return evaluate_one(question, versions, fields, config,
                            image_max_dim, source_path)

    use_parallel = bool(parallel and app is not None and max_workers and max_workers > 1)
    if use_parallel:
        from app.parallel import run_parallel, CANCELLED
        for r in run_parallel(app, cancel, qs, _worker, max_workers):
            if r['result'] is CANCELLED:
                continue
            current += 1
            if r['error'] is not None:
                errors += 1
                yield {'type': 'error', 'message': f'{r["item"].qid} — {r["error"]}',
                       'current': current, 'total': total}
                continue
            yield _event(r['result'], _absorb(r['result']))
    else:
        for question in qs:
            if cancel.is_set():
                yield {'type': 'info', 'message': 'Cancelled by user.',
                       'current': current, 'total': total}
                break
            current += 1
            res = _worker(question)
            yield _event(res, _absorb(res))

    rate = (agreed_total / applicable_total) if applicable_total else None
    stats = {'compared': compared, 'skipped': skipped, 'errors': errors,
             'agreed': agreed_total, 'applicable': applicable_total, 'rate': rate,
             'fields': {f: dict(v, rate=(v['agreed'] / v['applicable']) if v['applicable'] else None)
                        for f, v in per_field.items()}}
    pct = f'{rate * 100:.0f}%' if rate is not None else 'n/a'
    if not cancel.is_set():
        yield {'type': 'done',
               'message': (f'Done. Compared: {compared}, skipped: {skipped}, errors: {errors}. '
                           f'Agreement: {agreed_total}/{applicable_total} ({pct}).'),
               'current': total, 'total': total, 'stats': stats}
    else:
        yield {'type': 'done', 'message': f'Stopped. Agreement so far: {agreed_total}/{applicable_total} ({pct}).',
               'current': current, 'total': total, 'stats': stats}
