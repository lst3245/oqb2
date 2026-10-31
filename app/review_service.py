"""Review: batches of proposed question changes that subject admins work
through one question at a time (docs/modules/review.md, ADR-022).

A batch belongs to one subject and comes from the agent endpoint
(``create_review_batch``) or from an admin's JSON import. An item is either
``fields`` (a ``[{field, old, new}]`` list over ``question_edit_service.FIELDS``,
taxonomy values stored as ids) or ``freeform`` (a described change the admin
marks done or rejects; nothing is written).

* :func:`validate_items` is pure (resolvers passed in); :func:`prepare` loads
  the questions and taxonomy and calls it.
* :func:`apply_item` re-checks the item against the live question: fields that
  already hold the new value are dropped, the rest must still hold ``old``
  unless the admin confirms. It writes through ``question_edit_service.apply_fields``
  (restore point ``review-apply``). Callers commit, then call
  :func:`log_corrections` so tag decisions feed ``tag_corrections``.

Statuses: items ``pending`` / ``skipped`` (still open) -> ``applied`` /
``done`` / ``rejected``; batches ``open`` -> ``done`` (nothing open left) ->
``archived`` (hidden from the default list).
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime

from app import db
from app import question_edit_service as qes
from app.i18n import t as _t

log = logging.getLogger(__name__)

ITEM_KINDS = ('fields', 'freeform')
ITEM_STATUSES = ('pending', 'skipped', 'applied', 'done', 'rejected')
OPEN_ITEM_STATUSES = ('pending', 'skipped')
BATCH_STATUSES = ('open', 'done', 'archived')
SOURCES = ('agent', 'import')
MAX_ITEMS = 2000
MAX_TITLE = 200
MAX_ITEM_TITLE = 300
MAX_RATIONALE = 8000
MAX_NOTE = 1000


class ReviewError(ValueError):
    """A review action or payload is refused; the message is for the user."""


# ---------------------------------------------------------------- pure

def clip(text, limit):
    text = str(text or '').strip()
    return text[:limit] if text else ''


def parse_confidence(raw):
    """0..1, or a percentage 1..100 (scaled); ``None`` for blank. Raises
    ReviewError. Pure."""
    if raw is None or raw == '' or isinstance(raw, bool):
        return None
    try:
        value = float(str(raw).strip().rstrip('%'))
    except ValueError:
        raise ReviewError(_t('confidence must be a number from 0 to 1')) from None
    if value > 1:
        value = value / 100.0
    if not 0 <= value <= 1:
        raise ReviewError(_t('confidence must be a number from 0 to 1'))
    return round(value, 3)


def parse_payload(data):
    """JSON text or an already-parsed object -> ``{subject, title, summary,
    request_id, items}``. A bare list is the item list. Raises ReviewError. Pure."""
    if isinstance(data, (str, bytes)):
        try:
            data = json.loads(data)
        except ValueError as exc:
            raise ReviewError(_t('Not valid JSON: {error}', error=exc)) from None
    if isinstance(data, list):
        data = {'items': data}
    if not isinstance(data, dict):
        raise ReviewError(_t('Expected a JSON object with "items"'))
    items = data.get('items')
    if not isinstance(items, list) or not items:
        raise ReviewError(_t('"items" must be a non-empty list'))
    if len(items) > MAX_ITEMS:
        raise ReviewError(_t('At most {n} items per batch', n=MAX_ITEMS))
    return {
        'subject': str(data.get('subject') or data.get('subject_id') or '').strip().upper(),
        'title': clip(data.get('title'), MAX_TITLE),
        'summary': clip(data.get('summary'), MAX_RATIONALE),
        'request_id': data.get('request_id'),
        'items': items,
    }


def item_kind(raw: dict) -> str:
    kind = str(raw.get('kind') or '').strip().lower()
    if kind in ITEM_KINDS:
        return kind
    return 'fields' if 'changes' in raw else 'freeform'


def validate_items(raw_items, subject_id, *, find, taxonomy, current, stem):
    """Check raw items for one subject. ``find(qid, question_id)`` returns a
    question or None; ``current(q)`` its ``question_edit_service`` values;
    ``stem(q)`` whether it has parts. Returns ``(valid, problems)``: valid
    items are dicts ready for :func:`add_items`; problems are
    ``{index, qid, level: 'error'|'warning', message}`` (index from 1).
    Pure apart from the callables."""
    valid, problems = [], []
    seen = Counter()

    def problem(i, qid, level, message):
        problems.append({'index': i, 'qid': qid or '', 'level': level, 'message': str(message)})

    for i, raw in enumerate(raw_items or [], start=1):
        if not isinstance(raw, dict):
            problem(i, '', 'error', _t('each item must be an object'))
            continue
        qid = str(raw.get('qid') or '').strip()
        qpk = raw.get('question_id')
        kind = item_kind(raw)
        question = find(qid, qpk) if (qid or qpk not in (None, '')) else None
        if question is not None:
            qid = question.qid
        if question is not None and question.subject != subject_id:
            problem(i, qid, 'error', _t('{qid} is in {subject}, not {batch}',
                                        qid=qid, subject=question.subject, batch=subject_id))
            continue
        rationale = clip(raw.get('rationale') or raw.get('reason'), MAX_RATIONALE)
        try:
            confidence = parse_confidence(raw.get('confidence'))
        except ReviewError as exc:
            problem(i, qid, 'warning', exc)
            confidence = None
        item = {'question_id': question.id if question is not None else None, 'qid': qid,
                'kind': kind, 'changes': None, 'title': clip(raw.get('title'), MAX_ITEM_TITLE),
                'rationale': rationale, 'confidence': confidence}
        if kind == 'freeform':
            if not item['title'] and not rationale:
                problem(i, qid, 'error', _t('a free-text item needs a title or a rationale'))
                continue
            if (qid or qpk not in (None, '')) and question is None:
                problem(i, qid or str(qpk), 'warning', _t('question not found; kept as text'))
                item['qid'] = qid or str(qpk)
            valid.append(item)
            continue
        if question is None:
            problem(i, qid or (str(qpk) if qpk not in (None, '') else ''), 'error',
                    _t('question not found') if (qid or qpk not in (None, '')) else _t('qid is required'))
            continue
        cur = current(question)
        try:
            changes, warns = qes.normalize_changes(raw.get('changes'), taxonomy, cur, stem=stem(question))
        except qes.EditError as exc:
            problem(i, qid, 'error', exc)
            continue
        for w in warns:
            problem(i, qid, 'warning', w)
        kept = [c for c in changes if not (qes.values_equal(c['field'], c['new'], cur.get(c['field']))
                                           and qes.values_equal(c['field'], c['old'], cur.get(c['field'])))]
        if not kept:
            problem(i, qid, 'error', _t('nothing to change: every field already holds the new value'))
            continue
        if len(kept) < len(changes):
            problem(i, qid, 'warning', _t('fields that already hold the new value were left out'))
        stale = qes.stale_fields(kept, cur)
        if stale:
            problem(i, qid, 'warning', _t('"old" differs from the current value: {fields}',
                                          fields=', '.join(stale)))
        seen[question.id] += 1
        if seen[question.id] == 2:
            problem(i, qid, 'warning', _t('this question appears more than once'))
        item['changes'] = kept
        valid.append(item)
    return valid, problems


def change_rows(changes, cur, taxonomy, mode='en', applied=None):
    """Display rows for an item: ``{field, label, old, new, current,
    *_label, stale, noop}``. ``noop`` = the question already holds ``new``. Pure."""
    rows = []
    for c in changes or []:
        f = c.get('field')
        if f not in qes.FIELDS:
            continue
        now = cur.get(f) if cur is not None else None
        rows.append({
            'field': f, 'label': qes.FIELDS[f]['label'], 'kind': qes.FIELDS[f]['kind'],
            'tax': qes.FIELDS[f].get('tax'),
            'old': c.get('old'), 'new': c.get('new'), 'current': now,
            'old_label': qes.label(f, c.get('old'), taxonomy, mode),
            'new_label': qes.label(f, c.get('new'), taxonomy, mode),
            'current_label': qes.label(f, now, taxonomy, mode),
            'noop': cur is not None and qes.values_equal(f, now, c.get('new')),
            'stale': cur is not None and not qes.values_equal(f, now, c.get('old'))
                     and not qes.values_equal(f, now, c.get('new')),
        })
    return rows


def next_batch_status(status, counts) -> str:
    """Batch status after an item decision: ``open`` <-> ``done`` follows
    whether anything is still pending or skipped; ``archived`` stays. Pure."""
    if status == 'archived':
        return status
    open_left = sum(counts.get(s, 0) for s in OPEN_ITEM_STATUSES)
    return 'open' if open_left else 'done'


# ---------------------------------------------------------------- loading

def load_json(text, default=None):
    try:
        return json.loads(text) if text else default
    except ValueError:
        return default


def prepare(subject_id, raw_items):
    """DB-backed :func:`validate_items` for one subject."""
    from app import hierarchy
    from app.models import Question
    raw_items = list(raw_items or [])
    qids = {str(r.get('qid')).strip() for r in raw_items if isinstance(r, dict) and r.get('qid')}
    pks = set()
    for r in raw_items:
        if isinstance(r, dict) and r.get('question_id') not in (None, ''):
            try:
                pks.add(int(r.get('question_id')))
            except (TypeError, ValueError):
                pass
    by_qid, by_id = {}, {}
    if qids:
        for q in Question.query.filter(Question.qid.in_(list(qids))).all():
            by_qid[q.qid] = q
            by_id[q.id] = q
    if pks - set(by_id):
        for q in Question.query.filter(Question.id.in_(list(pks - set(by_id)))).all():
            by_id[q.id] = q

    def find(qid, qpk):
        if qid:
            return by_qid.get(qid)
        try:
            return by_id.get(int(qpk))
        except (TypeError, ValueError):
            return None

    taxonomy = qes.Taxonomy.load(subject_id)
    return validate_items(raw_items, subject_id, find=find, taxonomy=taxonomy,
                          current=qes.current_values, stem=hierarchy.is_stem)


def create_batch(subject_id, title, *, summary=None, source='agent', agent_name=None,
                 created_by=None, linked_request_id=None):
    from app.models import ReviewBatch, Subject
    if db.session.get(Subject, subject_id) is None:
        raise ReviewError(_t('Unknown subject: {subject}', subject=subject_id))
    title = clip(title, MAX_TITLE)
    if not title:
        raise ReviewError(_t('A title is required'))
    if source not in SOURCES:
        source = 'agent'
    batch = ReviewBatch(subject_id=subject_id, title=title, summary=clip(summary, MAX_RATIONALE) or None,
                        source=source, agent_name=(agent_name or None), created_by=created_by,
                        linked_request_id=linked_request_id, status='open')
    db.session.add(batch)
    db.session.flush()
    return batch


def add_items(batch, items):
    """Append validated items (from :func:`validate_items`) to ``batch``."""
    from sqlalchemy import func
    from app.models import ReviewItem
    start = db.session.query(func.max(ReviewItem.position)).filter(ReviewItem.batch_id == batch.id).scalar() or 0
    rows = []
    for n, it in enumerate(items, start=1):
        rows.append(ReviewItem(
            batch_id=batch.id, question_id=it.get('question_id'), qid=(it.get('qid') or '')[:100],
            position=start + n, kind=it['kind'],
            changes=json.dumps(it['changes'], ensure_ascii=False) if it.get('changes') else None,
            title=it.get('title') or None, rationale=it.get('rationale') or None,
            confidence=it.get('confidence'), status='pending'))
    db.session.add_all(rows)
    if rows and batch.status == 'done':
        batch.status = 'open'
    batch.updated_at = datetime.utcnow()
    db.session.flush()
    return rows


def item_counts(batch_ids) -> dict:
    """``{batch_id: Counter(status -> n)}``."""
    from sqlalchemy import func
    from app.models import ReviewItem
    out = {bid: Counter() for bid in batch_ids}
    if not batch_ids:
        return out
    rows = (db.session.query(ReviewItem.batch_id, ReviewItem.status, func.count(ReviewItem.id))
            .filter(ReviewItem.batch_id.in_(list(batch_ids)))
            .group_by(ReviewItem.batch_id, ReviewItem.status).all())
    for bid, status, n in rows:
        out[bid][status] = int(n)
    return out


def refresh_batch_status(batch):
    batch.status = next_batch_status(batch.status, item_counts([batch.id])[batch.id])
    batch.updated_at = datetime.utcnow()


def batch_payload(batch, counts=None) -> dict:
    from app.utils import utc_iso
    counts = counts if counts is not None else item_counts([batch.id])[batch.id]
    return {
        'id': batch.id, 'subject': batch.subject_id, 'title': batch.title, 'summary': batch.summary,
        'source': batch.source, 'agent_name': batch.agent_name, 'status': batch.status,
        'linked_request_id': batch.linked_request_id,
        'counts': {s: int(counts.get(s, 0)) for s in ITEM_STATUSES},
        'total': int(sum(counts.values())),
        'created_at': utc_iso(batch.created_at), 'updated_at': utc_iso(batch.updated_at),
    }


def item_payload(item, *, question=None, taxonomy=None, mode='en', usernames=None) -> dict:
    """Item for the review page and the agent: stored changes with labels,
    the live values, and stale / no-op flags (``question`` None = deleted)."""
    from app import hierarchy
    from app.utils import utc_iso
    cur = qes.current_values(question) if question is not None else None
    changes = load_json(item.changes, []) or []
    applied = load_json(item.applied_changes, None)
    rows = change_rows(changes, cur, taxonomy, mode)
    return {
        'id': item.id, 'batch_id': item.batch_id, 'position': item.position, 'qid': item.qid,
        'question_id': item.question_id, 'question_exists': question is not None,
        'subject': question.subject if question is not None else None,
        'is_stem': bool(question is not None and hierarchy.is_stem(question)),
        'kind': item.kind, 'title': item.title, 'rationale': item.rationale,
        'confidence': item.confidence, 'status': item.status, 'admin_note': item.admin_note,
        'decided_by': item.decided_by,
        'decided_by_name': (usernames or {}).get(item.decided_by) if item.decided_by else None,
        'decided_at': utc_iso(item.decided_at),
        'changes': rows,
        'stale': [r['field'] for r in rows if r['stale']],
        'all_noop': bool(rows) and all(r['noop'] for r in rows),
        'applied': [dict(a, label=qes.FIELDS.get(a.get('field'), {}).get('label', a.get('field')),
                         old_label=qes.label(a.get('field'), a.get('old'), taxonomy, mode),
                         new_label=qes.label(a.get('field'), a.get('new'), taxonomy, mode))
                    for a in (applied or []) if a.get('field') in qes.FIELDS],
    }


# ---------------------------------------------------------------- decisions

def _require_open(item):
    if item.status not in OPEN_ITEM_STATUSES:
        raise ReviewError(_t('This item is already decided ({status})', status=item.status))


def _question_of(item, batch):
    from app.models import Question
    question = db.session.get(Question, item.question_id) if item.question_id else None
    if question is None:
        raise ReviewError(_t('The question no longer exists'))
    if question.subject != batch.subject_id:
        raise ReviewError(_t('The question is no longer in {subject}', subject=batch.subject_id))
    return question


def _decide(item, status, user_id, note):
    item.status = status
    item.decided_by = user_id
    item.decided_at = datetime.utcnow()
    if note is not None:
        item.admin_note = clip(note, MAX_NOTE) or None


def apply_item(item, batch, *, user_id, fields=None, overrides=None, confirm_stale=False,
               note=None, capture=True, taxonomy=None):
    """Apply a pending item. ``fields`` limits it to some of its fields;
    ``overrides`` (``{field: new}``) replaces proposed values. Raises
    ReviewError, ``question_edit_service.StaleError`` (nothing written) or
    EditError. Does not commit. Returns ``{question, taxonomy, result,
    changes}`` (``question`` None for a free-text item)."""
    from app import hierarchy
    _require_open(item)
    if item.kind == 'freeform':
        _decide(item, 'done', user_id, note)
        return {'question': None, 'taxonomy': None, 'result': None, 'changes': []}
    question = _question_of(item, batch)
    taxonomy = taxonomy or qes.Taxonomy.load(batch.subject_id)
    stored = load_json(item.changes, []) or []
    wanted = set(fields) if fields else None
    overrides = overrides or {}
    raw = []
    for c in stored:
        f = c.get('field')
        if wanted is not None and f not in wanted:
            continue
        raw.append({'field': f, 'old': c.get('old'), 'new': overrides.get(f, c.get('new'))})
    if not raw:
        raise ReviewError(_t('Choose at least one field to apply'))
    cur = qes.current_values(question)
    changes, _warn = qes.normalize_changes(raw, taxonomy, cur, stem=hierarchy.is_stem(question))
    todo = [c for c in changes if not qes.values_equal(c['field'], cur.get(c['field']), c['new'])]
    result = qes.apply_fields(question, todo, taxonomy, action='review-apply',
                              note=f'Review #{batch.id}: {item.qid}', confirm_stale=confirm_stale,
                              capture=capture) if todo else {'changed': [], 'before': {}, 'after': {},
                                                             'stale': []}
    applied = [{'field': f, 'old': result['before'][f], 'new': result['after'][f]} for f in result['changed']]
    item.applied_changes = json.dumps(applied, ensure_ascii=False)
    _decide(item, 'applied', user_id, note)
    return {'question': question, 'taxonomy': taxonomy, 'result': result, 'changes': changes}


def reject_item(item, *, user_id, note=None):
    _require_open(item)
    _decide(item, 'rejected', user_id, note)


def skip_item(item, *, user_id=None):
    if item.status != 'pending':
        raise ReviewError(_t('Only a pending item can be skipped'))
    item.status = 'skipped'


def reopen_item(item):
    if item.status == 'applied':
        raise ReviewError(_t('An applied change is undone from Restore Points, not reopened'))
    if item.status == 'pending':
        return
    item.status = 'pending'
    item.decided_by = None
    item.decided_at = None


def apply_all(batch, *, user_id, min_confidence=None):
    """Apply every pending ``fields`` item (at or above ``min_confidence``
    when given) under one restore point. Stale or invalid items stay pending.
    Does not commit. Returns ``(summary, applied)`` where ``applied`` is
    ``[(item, question)]`` for :func:`log_corrections` after the commit."""
    from app import subject_snapshot
    from app.models import ReviewItem
    items = (ReviewItem.query.filter_by(batch_id=batch.id, status='pending', kind='fields')
             .order_by(ReviewItem.position).all())
    if min_confidence is not None:
        low = [i for i in items if i.confidence is None or i.confidence < min_confidence]
        items = [i for i in items if i.confidence is not None and i.confidence >= min_confidence]
    else:
        low = []
    summary = {'applied': 0, 'stale': [], 'errors': [], 'below_threshold': len(low)}
    applied = []
    if not items:
        return summary, applied
    taxonomy = qes.Taxonomy.load(batch.subject_id)
    subject_snapshot.capture(batch.subject_id, 'review-apply', note=f'Review #{batch.id}: apply all')
    for item in items:
        try:
            out = apply_item(item, batch, user_id=user_id, capture=False, taxonomy=taxonomy)
        except qes.StaleError:
            summary['stale'].append(item.qid)
            continue
        except (ReviewError, qes.EditError) as exc:
            summary['errors'].append({'qid': item.qid, 'message': str(exc)})
            continue
        summary['applied'] += 1
        applied.append((item, out['question']))
    refresh_batch_status(batch)
    return summary, applied


def log_corrections(item, batch, question, taxonomy=None, *, user_id=None):
    """Feed an applied or rejected tag decision into ``tag_corrections``
    (Auto Tag learns from it). Commits on its own; never raises."""
    if question is None or item.kind != 'fields' or item.status not in ('applied', 'rejected'):
        return 0
    try:
        from app import subject_ai_service
        taxonomy = taxonomy or qes.Taxonomy.load(batch.subject_id)
        proposed = {c['field']: c.get('new') for c in (load_json(item.changes, []) or [])
                    if c.get('field') in qes.CORRECTION_FIELDS}
        fields = list(proposed)
        if not fields:
            return 0
        saved_values = qes.current_values(question)
        suggested = qes.correction_labels(proposed, taxonomy, fields)
        saved = qes.correction_labels(saved_values, taxonomy, fields)
        reason = clip(item.rationale, 400) or None
        model = (f'agent:{batch.agent_name}' if batch.source == 'agent' and batch.agent_name
                 else 'review-' + batch.source)
        return subject_ai_service.log_corrections(
            question, suggested, saved, fields, model=model,
            reasons={f: reason for f in fields} if reason else None, user_id=user_id)
    except Exception:
        db.session.rollback()
        log.exception('review corrections not logged for item %s', item.id)
        return 0


# ---------------------------------------------------------------- scope

def admin_subject_ids(user):
    """Subjects whose batches ``user`` may work on (``None`` = all)."""
    from app import authz
    if authz.is_super_admin(user):
        return None
    return authz.subjects_with_level(user, 'admin')


def scoped_batches(user):
    from app.models import ReviewBatch
    query = ReviewBatch.query
    subjects = admin_subject_ids(user)
    if subjects is not None:
        query = query.filter(ReviewBatch.subject_id.in_(subjects or ['']))
    return query


def pending_count(user) -> int:
    """Pending items in open batches ``user`` administers (nav badge)."""
    from app.models import ReviewBatch, ReviewItem
    subjects = admin_subject_ids(user)
    if subjects is not None and not subjects:
        return 0
    query = (db.session.query(db.func.count(ReviewItem.id))
             .join(ReviewBatch, ReviewBatch.id == ReviewItem.batch_id)
             .filter(ReviewBatch.status == 'open', ReviewItem.status == 'pending'))
    if subjects is not None:
        query = query.filter(ReviewBatch.subject_id.in_(subjects))
    return int(query.scalar() or 0)


def delete_for_subject(subject_id):
    """Subject delete: drop its batches and their items. Caller commits."""
    from app.models import ReviewBatch, ReviewItem
    ids = [r[0] for r in db.session.query(ReviewBatch.id).filter(ReviewBatch.subject_id == subject_id)]
    if ids:
        ReviewItem.query.filter(ReviewItem.batch_id.in_(ids)).delete(synchronize_session=False)
        ReviewBatch.query.filter(ReviewBatch.id.in_(ids)).delete(synchronize_session=False)
