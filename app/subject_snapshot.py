"""
Subject restore points — per-subject snapshots of question tags and the
topic / chapter lists, with preview and restore.

A point is one subject's state **just before** a tag or taxonomy save. Every
write path that changes the fields below calls :func:`capture` (or
:func:`capture_state`) before it mutates anything; the point rides in the
caller's transaction, so it commits or rolls back together with the edit.
Auto Tag commits per question, so it uses :func:`capture_committed` (own
session, committed before the run starts).

Payload (JSON, ``PAYLOAD_VERSION``):

* ``topics`` / ``chapters``: ``id, name, sort_order, description``
* ``subtopics`` / ``subchapters``: ``id, <parent>_id, name, hidden,
  sort_order, description``
* ``questions``: one array per question, columns named by
  ``question_columns`` (``QUESTION_COLUMNS``)

Not stored: answers, comments, question descriptions, verified / check
flags, assets, files, hierarchy, other subjects.

Restore rewrites only the point's subject in one transaction, after saving
the live state as a ``before-restore`` point. Taxonomy rows keep their ids
(deleted rows are re-inserted with the old id); rows created since the point
are deleted only when nothing references them. Questions deleted since the
point are skipped; questions created since keep their tags.

Spec: ``docs/modules/subject-snapshots.md``; decision: ADR-013.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import and_, bindparam, select
from sqlalchemy.orm import Session

from app import db
from app.utils import utc_iso

PAYLOAD_VERSION = 1
KEEP_PER_SUBJECT = 100
# On top of the newest KEEP_PER_SUBJECT, the first point of each UTC day in
# this window survives pruning, so a busy day cannot push out "start of day".
DAILY_ANCHOR_DAYS = 30
MAX_NOTE_CHARS = 200
PREVIEW_SAMPLE_LIMIT = 40

ACTION_LABELS = {
    'baseline': 'Baseline (server start)',
    'manual': 'Manual snapshot',
    'topic-edit': 'Topic list edit',
    'chapter-edit': 'Chapter list edit',
    'taxonomy-hint': 'Tagging hint edit',
    'question-tags': 'Question tags save',
    'batch-update': 'Bulk edit',
    'tag-import': 'Question tags CSV import',
    'topic-import': 'Topics CSV import',
    'chapter-import': 'Chapters CSV import',
    'auto-tag': 'Auto Tag run',
    'before-restore': 'Before restore',
}

TAXONOMY_KINDS = ('topics', 'subtopics', 'chapters', 'subchapters')
TAXONOMY_FIELDS = {
    'topics': ('name', 'sort_order', 'description'),
    'subtopics': ('topic_id', 'name', 'hidden', 'sort_order', 'description'),
    'chapters': ('name', 'sort_order', 'description'),
    'subchapters': ('chapter_id', 'name', 'hidden', 'sort_order', 'description'),
}
PARENT = {'subtopics': ('topics', 'topic_id'), 'subchapters': ('chapters', 'chapter_id')}
KIND_LABELS = {'topics': 'Topic', 'subtopics': 'Subtopic',
               'chapters': 'Chapter', 'subchapters': 'Subchapter'}

QUESTION_FIELDS = ('major_topic_id', 'major_subtopic_id', 'chapter_id', 'subchapter_id',
                   'level', 'q_type', 'section', 'correct_percentage')
LINK_FIELDS = ('minor_topic_ids', 'subtopic_ids')
QUESTION_COLUMNS = ('id', 'qid') + QUESTION_FIELDS + LINK_FIELDS
REF_KIND = {
    'major_topic_id': 'topics', 'major_subtopic_id': 'subtopics',
    'chapter_id': 'chapters', 'subchapter_id': 'subchapters',
    'minor_topic_ids': 'topics', 'subtopic_ids': 'subtopics',
}
FIELD_LABELS = {
    'major_topic_id': 'Major topic', 'major_subtopic_id': 'Major subtopic',
    'chapter_id': 'Chapter', 'subchapter_id': 'Subchapter', 'level': 'Level',
    'q_type': 'Type', 'section': 'Section', 'correct_percentage': 'Correct %',
    'minor_topic_ids': 'Minor topics', 'subtopic_ids': 'Subtopics',
}


class SnapshotError(Exception):
    """A restore point cannot be read or applied."""


def action_label(action):
    return ACTION_LABELS.get(action, action or '')


# ---------------------------------------------------------------------------
# State: plain dicts (pure)
# ---------------------------------------------------------------------------

def empty_state(subject_id):
    state = {'subject_id': subject_id, 'fields': QUESTION_FIELDS + LINK_FIELDS,
             'questions': {}}
    for kind in TAXONOMY_KINDS:
        state[kind] = {}
    return state


def encode_state(state):
    """State dict -> compact JSON payload string. Pure."""
    out = {'v': PAYLOAD_VERSION, 'subject_id': state['subject_id']}
    for kind in TAXONOMY_KINDS:
        fields = TAXONOMY_FIELDS[kind]
        out[kind] = [dict({'id': nid}, **{f: row.get(f) for f in fields})
                     for nid, row in sorted(state[kind].items())]
    out['question_columns'] = list(QUESTION_COLUMNS)
    out['questions'] = [
        [pk, row.get('qid')]
        + [row.get(f) for f in QUESTION_FIELDS]
        + [sorted(row.get(f) or ()) for f in LINK_FIELDS]
        for pk, row in sorted(state['questions'].items())
    ]
    return json.dumps(out, ensure_ascii=False, separators=(',', ':'))


def decode_state(payload):
    """JSON payload string -> state dict. Columns missing from an older
    payload are left out of ``state['fields']`` so restore never blanks a
    field the point did not record. Pure."""
    try:
        data = json.loads(payload)
    except (TypeError, ValueError) as e:
        raise SnapshotError(f'Restore point payload is not valid JSON: {e}') from None
    if not isinstance(data, dict) or data.get('v') != PAYLOAD_VERSION:
        raise SnapshotError('Unsupported restore point format.')

    state = empty_state(data.get('subject_id'))
    for kind in TAXONOMY_KINDS:
        fields = TAXONOMY_FIELDS[kind]
        rows = {}
        for r in data.get(kind) or []:
            row = {f: r.get(f) for f in fields}
            if 'hidden' in row:
                row['hidden'] = bool(row['hidden'])
            rows[int(r['id'])] = row
        state[kind] = rows

    cols = list(data.get('question_columns') or QUESTION_COLUMNS)
    state['fields'] = tuple(f for f in QUESTION_FIELDS + LINK_FIELDS if f in cols)
    questions = {}
    for raw in data.get('questions') or []:
        rec = dict(zip(cols, raw))
        row = {'qid': rec.get('qid')}
        for f in QUESTION_FIELDS:
            row[f] = rec.get(f)
        for f in LINK_FIELDS:
            row[f] = tuple(sorted({int(x) for x in rec.get(f) or ()}))
        questions[int(rec['id'])] = row
    state['questions'] = questions
    return state


def orm_question_row(question):
    """Current in-memory tag values of a ``Question`` (or any object with the
    same attributes) in state-row shape. Pure."""
    row = {f: getattr(question, f) for f in QUESTION_FIELDS}
    row['minor_topic_ids'] = tuple(sorted({t.id for t in question.minor_topics}))
    row['subtopic_ids'] = tuple(sorted({s.id for s in question.subtopics}))
    return row


def question_tags_differ(state, question):
    """True when ``question``'s in-memory tags differ from its row in
    ``state`` (read before the edit). Compares values rather than ORM
    attribute history, which autoflush resets mid-request. Pure."""
    before = state['questions'].get(question.id)
    if before is None:
        return True
    now = orm_question_row(question)
    return any(before.get(f) != now.get(f) for f in QUESTION_FIELDS + LINK_FIELDS)


# ---------------------------------------------------------------------------
# Restore plan (pure)
# ---------------------------------------------------------------------------

def _ids(mapping, kind):
    return set((mapping or {}).get(kind) or ())


def plan_restore(live, point, *, foreign=None, external_refs=None, external_existing=None):
    """Work out what restoring ``point`` over ``live`` changes. Pure.

    ``foreign``: ``{kind: {id: subject_or_None}}`` — point ids that now
    belong to another subject (or are orphaned); each is a conflict.
    ``external_refs``: ``{kind: set(ids)}`` — this subject's ids referenced by
    other subjects' questions (never deleted).
    ``external_existing``: ``{kind: set(ids)}`` — ids outside this subject's
    taxonomy that still exist (a point question may legitimately point there).
    """
    foreign = foreign or {}
    plan = {'subject_id': point['subject_id'], 'conflicts': [], 'taxonomy': {}}

    for kind in TAXONOMY_KINDS:
        for nid, other in sorted((foreign.get(kind) or {}).items()):
            name = point[kind].get(nid, {}).get('name')
            where = f'subject {other}' if other else 'no subject (orphaned row)'
            plan['conflicts'].append(
                f'{KIND_LABELS[kind]} id {nid} ("{name}") now belongs to {where}.')
    for kind, (pkind, pfield) in PARENT.items():
        for nid, row in sorted(point[kind].items()):
            if row.get(pfield) not in point[pkind] and row.get(pfield) not in live[pkind]:
                plan['conflicts'].append(
                    f'{KIND_LABELS[kind]} "{row.get("name")}" points at missing '
                    f'{KIND_LABELS[pkind].lower()} id {row.get(pfield)}.')

    for kind in TAXONOMY_KINDS:
        fields = TAXONOMY_FIELDS[kind]
        skip = _ids(foreign, kind)
        t = {'insert': [], 'update': [], 'delete': [], 'keep': []}
        for nid, row in sorted(point[kind].items()):
            if nid in skip:
                continue
            cur = live[kind].get(nid)
            if cur is None:
                t['insert'].append(dict({'id': nid}, **{f: row.get(f) for f in fields}))
                continue
            changed = {f: row.get(f) for f in fields if row.get(f) != cur.get(f)}
            if changed:
                t['update'].append({'id': nid, 'set': changed,
                                    'old': {f: cur.get(f) for f in changed}})
        plan['taxonomy'][kind] = t

    valid = {kind: (set(point[kind]) - _ids(foreign, kind)) | set(live[kind])
             | _ids(external_existing, kind) for kind in TAXONOMY_KINDS}

    fields = tuple(point.get('fields') or (QUESTION_FIELDS + LINK_FIELDS))
    scalar_fields = [f for f in QUESTION_FIELDS if f in fields]
    link_fields = [f for f in LINK_FIELDS if f in fields]
    links = {f: {'add': [], 'remove': []} for f in LINK_FIELDS}
    changed_rows, scalar_updates, dropped, targets = [], [], [], {}

    for pk, prow in sorted(point['questions'].items()):
        cur = live['questions'].get(pk)
        if cur is None:
            continue
        target = {}
        for f in scalar_fields:
            v = prow.get(f)
            kind = REF_KIND.get(f)
            if kind and v is not None and v not in valid[kind]:
                dropped.append((pk, f, v))
                v = None
            target[f] = v
        for f in link_fields:
            kind = REF_KIND[f]
            kept = []
            for v in prow.get(f) or ():
                if v in valid[kind]:
                    kept.append(v)
                else:
                    dropped.append((pk, f, v))
            target[f] = tuple(sorted(set(kept)))
        targets[pk] = target

        changed = [f for f in scalar_fields if target[f] != cur.get(f)]
        if changed:
            scalar_updates.append(dict({'id': pk}, **{f: target[f] for f in scalar_fields}))
        for f in link_fields:
            want, have = set(target[f]), set(cur.get(f) or ())
            links[f]['add'].extend((pk, v) for v in sorted(want - have))
            links[f]['remove'].extend((pk, v) for v in sorted(have - want))
            if want != have:
                changed.append(f)
        if changed:
            changed_rows.append({'id': pk, 'qid': cur.get('qid'), 'fields': changed,
                                 'target': target})

    # References that survive the restore decide which newer taxonomy rows stay.
    refs = {kind: _ids(external_refs, kind) for kind in TAXONOMY_KINDS}
    for pk, cur in live['questions'].items():
        src = targets.get(pk, {})
        for f, kind in REF_KIND.items():
            v = src[f] if f in src else cur.get(f)
            if f in LINK_FIELDS:
                refs[kind].update(v or ())
            elif v is not None:
                refs[kind].add(v)

    for kind in ('subtopics', 'subchapters'):
        t = plan['taxonomy'][kind]
        for nid in sorted(set(live[kind]) - set(point[kind])):
            (t['keep'] if nid in refs[kind] else t['delete']).append(nid)
    for kind, child_kind in (('topics', 'subtopics'), ('chapters', 'subchapters')):
        pfield = PARENT[child_kind][1]
        skip = _ids(foreign, child_kind)
        parents_in_use = {row.get(pfield) for nid, row in point[child_kind].items()
                          if nid not in skip}
        parents_in_use |= {live[child_kind][nid].get(pfield)
                           for nid in plan['taxonomy'][child_kind]['keep']}
        t = plan['taxonomy'][kind]
        for nid in sorted(set(live[kind]) - set(point[kind])):
            in_use = nid in refs[kind] or nid in parents_in_use
            (t['keep'] if in_use else t['delete']).append(nid)

    plan['questions'] = {
        'scalar_fields': scalar_fields,
        'scalar_updates': scalar_updates,
        'links': links,
        'changed': changed_rows,
        'skipped': sorted(set(point['questions']) - set(live['questions'])),
        'added_since': sorted(set(live['questions']) - set(point['questions'])),
        'dropped_refs': dropped,
    }
    return plan


def plan_has_changes(plan):
    if plan['questions']['changed']:
        return True
    return any(t['insert'] or t['update'] or t['delete'] for t in plan['taxonomy'].values())


def _display(field, value, *states):
    kind = REF_KIND.get(field)
    if kind is None:
        return '—' if value is None or value == '' else str(value)

    def name(nid):
        for st in states:
            row = st[kind].get(nid)
            if row:
                return row.get('name')
        return f'#{nid}'

    if field in LINK_FIELDS:
        return ', '.join(name(v) for v in value) if value else '—'
    return '—' if value is None else name(value)


def summarize(plan, live, point, sample_limit=PREVIEW_SAMPLE_LIMIT):
    """Human-readable preview of ``plan`` (names, counts, sample rows). Pure."""
    taxonomy = {}
    for kind in TAXONOMY_KINDS:
        t = plan['taxonomy'][kind]
        renamed = [{'from': u['old']['name'], 'to': u['set']['name']}
                   for u in t['update'] if 'name' in u['set']]
        taxonomy[kind] = {
            'label': KIND_LABELS[kind],
            'recreate': [r['name'] for r in t['insert']],
            'rename': renamed,
            'other_updates': sum(1 for u in t['update'] if 'name' not in u['set']),
            'delete': [live[kind][nid]['name'] for nid in t['delete']],
            'keep_newer': [live[kind][nid]['name'] for nid in t['keep']],
        }

    q = plan['questions']
    field_counts = Counter(f for c in q['changed'] for f in c['fields'])
    samples = []
    for c in q['changed'][:sample_limit]:
        cur = live['questions'][c['id']]
        samples.append({
            'qid': c['qid'],
            'changes': [{'field': FIELD_LABELS[f],
                         'from': _display(f, cur.get(f), live, point),
                         'to': _display(f, c['target'].get(f), point, live)}
                        for f in c['fields']],
        })
    return {
        'subject_id': plan['subject_id'],
        'has_changes': plan_has_changes(plan),
        'conflicts': list(plan['conflicts']),
        'questions_in_point': len(point['questions']),
        'questions_now': len(live['questions']),
        'questions_changed': len(q['changed']),
        'questions_deleted_since': len(q['skipped']),
        'questions_added_since': len(q['added_since']),
        'field_counts': [{'field': FIELD_LABELS[f], 'count': field_counts[f]}
                         for f in QUESTION_FIELDS + LINK_FIELDS if field_counts.get(f)],
        'dropped_refs': len(q['dropped_refs']),
        'taxonomy': taxonomy,
        'samples': samples,
        'samples_truncated': max(len(q['changed']) - sample_limit, 0),
    }


# ---------------------------------------------------------------------------
# DB: read, capture, prune
# ---------------------------------------------------------------------------

def read_state(subject_id, session=None):
    """One subject's tags + taxonomy as stored in the DB. Column selects under
    ``no_autoflush``, so pending ORM edits in ``session`` are not included."""
    from app.models import (Topic, Subtopic, Chapter, Subchapter, Question,
                            question_minor_topics, question_subtopics)
    s = session if session is not None else db.session
    state = empty_state(subject_id)
    qt = Question.__table__
    with s.no_autoflush:
        for r in s.execute(select(Topic.id, Topic.name, Topic.sort_order, Topic.description)
                           .where(Topic.subject_id == subject_id)):
            state['topics'][r.id] = {'name': r.name, 'sort_order': r.sort_order,
                                     'description': r.description}
        for r in s.execute(select(Subtopic.id, Subtopic.topic_id, Subtopic.name, Subtopic.hidden,
                                  Subtopic.sort_order, Subtopic.description)
                           .join(Topic, Topic.id == Subtopic.topic_id)
                           .where(Topic.subject_id == subject_id)):
            state['subtopics'][r.id] = {'topic_id': r.topic_id, 'name': r.name,
                                        'hidden': bool(r.hidden), 'sort_order': r.sort_order,
                                        'description': r.description}
        for r in s.execute(select(Chapter.id, Chapter.name, Chapter.sort_order, Chapter.description)
                           .where(Chapter.subject_id == subject_id)):
            state['chapters'][r.id] = {'name': r.name, 'sort_order': r.sort_order,
                                       'description': r.description}
        for r in s.execute(select(Subchapter.id, Subchapter.chapter_id, Subchapter.name,
                                  Subchapter.hidden, Subchapter.sort_order, Subchapter.description)
                           .join(Chapter, Chapter.id == Subchapter.chapter_id)
                           .where(Chapter.subject_id == subject_id)):
            state['subchapters'][r.id] = {'chapter_id': r.chapter_id, 'name': r.name,
                                          'hidden': bool(r.hidden), 'sort_order': r.sort_order,
                                          'description': r.description}

        cols = [qt.c.id, qt.c.qid] + [qt.c[f] for f in QUESTION_FIELDS]
        for r in s.execute(select(*cols).where(qt.c.subject == subject_id)):
            m = r._mapping
            row = {'qid': m['qid']}
            for f in QUESTION_FIELDS:
                row[f] = m[f]
            row['minor_topic_ids'] = []
            row['subtopic_ids'] = []
            state['questions'][m['id']] = row
        for field, tbl, col in (('minor_topic_ids', question_minor_topics, 'topic_id'),
                                ('subtopic_ids', question_subtopics, 'subtopic_id')):
            stmt = (select(tbl.c.question_id, tbl.c[col])
                    .select_from(tbl.join(qt, qt.c.id == tbl.c.question_id))
                    .where(qt.c.subject == subject_id))
            for pk, ref in s.execute(stmt):
                row = state['questions'].get(pk)
                if row is not None:
                    row[field].append(ref)
    for row in state['questions'].values():
        for f in LINK_FIELDS:
            row[f] = tuple(sorted(set(row[f])))
    return state


def _clean_note(note):
    note = ' '.join(str(note or '').split())
    return note[:MAX_NOTE_CHARS] or None


def _current_user_id():
    try:
        from flask import has_request_context
        from flask_login import current_user
        if has_request_context() and current_user.is_authenticated:
            return current_user.id
    except Exception:
        pass
    return None


def select_prunable(points, now, *, pending=0, protect=(),
                    keep=KEEP_PER_SUBJECT, anchor_days=DAILY_ANCHOR_DAYS):
    """Ids to delete from ``points`` (``[(id, created_at), ...]``): everything
    except the newest ``keep - pending``, the first point of each UTC day
    within ``anchor_days`` of ``now``, and ``protect``. Pure."""
    ordered = sorted(points, key=lambda p: p[0], reverse=True)
    survivors = {pid for pid, _ in ordered[:max(keep - pending, 0)]}
    cutoff = now - timedelta(days=anchor_days)
    first_of_day = {}
    for pid, ts in ordered:
        if ts is not None and ts >= cutoff:
            day = ts.date()
            if day not in first_of_day or pid < first_of_day[day]:
                first_of_day[day] = pid
    survivors |= set(first_of_day.values())
    survivors |= set(protect or ())
    return sorted(pid for pid, _ in ordered if pid not in survivors)


def prune(subject_id, *, session=None, pending=0, keep_ids=()):
    """Delete the subject's points that :func:`select_prunable` releases
    (``pending`` = points added to the session but not yet flushed)."""
    from app.models import SubjectRestorePoint
    s = session if session is not None else db.session
    t = SubjectRestorePoint.__table__
    with s.no_autoflush:
        rows = s.execute(select(t.c.id, t.c.created_at)
                         .where(t.c.subject_id == subject_id)).all()
    excess = select_prunable([(r.id, r.created_at) for r in rows], datetime.utcnow(),
                             pending=pending, protect=keep_ids)
    if excess:
        s.execute(t.delete().where(t.c.id.in_(excess)))
    return len(excess)


def capture_state(state, action, *, note=None, session=None, user_id=None, keep_ids=()):
    """Add a point holding an already-read ``state`` to ``session`` (default
    ``db.session``) without committing, and prune old points. Use when the
    state had to be read before the edit but keeping it depends on the edit
    (``update_question``)."""
    from app.models import SubjectRestorePoint
    s = session if session is not None else db.session
    point = SubjectRestorePoint(
        subject_id=state['subject_id'],
        user_id=user_id if user_id is not None else _current_user_id(),
        action=action,
        note=_clean_note(note),
        payload=encode_state(state),
        created_at=datetime.utcnow(),
    )
    s.add(point)
    prune(state['subject_id'], session=s, pending=1, keep_ids=keep_ids)
    return point


def capture(subject_id, action, *, note=None, session=None, user_id=None, keep_ids=()):
    """Add a restore point of ``subject_id``'s current DB state to ``session``
    (default ``db.session``) **without committing**. Call before mutating; the
    caller's commit persists the point and the edit together."""
    state = read_state(subject_id, session)
    return capture_state(state, action, note=note, session=session,
                         user_id=user_id, keep_ids=keep_ids)


def capture_many(subject_ids, action, *, note=None, session=None):
    """:func:`capture` once per distinct subject id (sorted). Returns points."""
    return [capture(sid, action, note=note, session=session)
            for sid in sorted({sid for sid in subject_ids if sid})]


def capture_committed(subject_ids, action, *, note=None):
    """Capture and commit in a private session, independent of
    ``db.session``. For writers that commit per row (Auto Tag), so the point
    exists before the first row changes. Returns the new point ids."""
    uid = _current_user_id()
    with Session(db.engine, expire_on_commit=False) as s:
        points = [capture(sid, action, note=note, session=s, user_id=uid)
                  for sid in sorted({sid for sid in subject_ids if sid})]
        s.commit()
        return [p.id for p in points]


def ensure_baselines():
    """Boot: one ``baseline`` point for every subject that has none.
    Idempotent; commits ``db.session``. Returns the subject ids captured."""
    from app.models import Subject, SubjectRestorePoint
    have = set(db.session.execute(select(SubjectRestorePoint.subject_id).distinct()).scalars())
    missing = [sid for sid in db.session.execute(select(Subject.id).order_by(Subject.id)).scalars()
               if sid not in have]
    for sid in missing:
        capture(sid, 'baseline', note='First restore point, taken at server start')
    if missing:
        db.session.commit()
    return missing


def delete_for_subject(subject_id, session=None):
    """Drop every point of a subject (subject delete). Caller commits."""
    from app.models import SubjectRestorePoint
    s = session if session is not None else db.session
    t = SubjectRestorePoint.__table__
    s.execute(t.delete().where(t.c.subject_id == subject_id))


# ---------------------------------------------------------------------------
# DB: list, preview, restore
# ---------------------------------------------------------------------------

def point_meta(row, username=None):
    return {
        'id': row.id,
        'subject_id': row.subject_id,
        'created_at': utc_iso(row.created_at),
        'action': row.action,
        'action_label': action_label(row.action),
        'note': row.note,
        'username': username,
    }


def list_points(subject_id):
    """Newest first, without payloads."""
    from app.models import SubjectRestorePoint as P, User
    rows = db.session.execute(
        select(P.id, P.subject_id, P.created_at, P.action, P.note, User.username)
        .outerjoin(User, User.id == P.user_id)
        .where(P.subject_id == subject_id)
        .order_by(P.id.desc())
    ).all()
    return [point_meta(r, r.username) for r in rows]


def gather_context(session, subject_id, live, point):
    """DB lookups :func:`plan_restore` needs beyond the two states."""
    from app.models import (Topic, Subtopic, Chapter, Subchapter, Question,
                            question_minor_topics, question_subtopics)
    s = session
    qt = Question.__table__
    models = {'topics': Topic, 'subtopics': Subtopic, 'chapters': Chapter,
              'subchapters': Subchapter}
    foreign, external_refs, external_existing = {}, {}, {}

    with s.no_autoflush:
        for kind in TAXONOMY_KINDS:
            missing = set(point[kind]) - set(live[kind])
            if not missing:
                continue
            if kind == 'topics':
                stmt = select(Topic.id, Topic.subject_id).where(Topic.id.in_(missing))
            elif kind == 'chapters':
                stmt = select(Chapter.id, Chapter.subject_id).where(Chapter.id.in_(missing))
            elif kind == 'subtopics':
                stmt = (select(Subtopic.id, Topic.subject_id)
                        .outerjoin(Topic, Topic.id == Subtopic.topic_id)
                        .where(Subtopic.id.in_(missing)))
            else:
                stmt = (select(Subchapter.id, Chapter.subject_id)
                        .outerjoin(Chapter, Chapter.id == Subchapter.chapter_id)
                        .where(Subchapter.id.in_(missing)))
            found = {nid: subj for nid, subj in s.execute(stmt) if subj != subject_id}
            if found:
                foreign[kind] = found

        scalar_cols = {'topics': [qt.c.major_topic_id], 'subtopics': [qt.c.major_subtopic_id],
                       'chapters': [qt.c.chapter_id], 'subchapters': [qt.c.subchapter_id]}
        link_tables = {'topics': (question_minor_topics, 'topic_id'),
                       'subtopics': (question_subtopics, 'subtopic_id')}
        for kind in TAXONOMY_KINDS:
            candidates = set(live[kind]) - set(point[kind])
            if not candidates:
                continue
            hits = set()
            for col in scalar_cols[kind]:
                hits.update(s.execute(select(col).where(qt.c.subject != subject_id,
                                                        col.in_(candidates))).scalars())
            if kind in link_tables:
                tbl, col_name = link_tables[kind]
                hits.update(s.execute(
                    select(tbl.c[col_name])
                    .select_from(tbl.join(qt, qt.c.id == tbl.c.question_id))
                    .where(qt.c.subject != subject_id, tbl.c[col_name].in_(candidates))
                ).scalars())
            if hits:
                external_refs[kind] = hits

        wanted = {kind: set() for kind in TAXONOMY_KINDS}
        for prow in point['questions'].values():
            for f, kind in REF_KIND.items():
                vals = prow.get(f) if f in LINK_FIELDS else [prow.get(f)]
                for v in vals or ():
                    if v is not None and v not in point[kind] and v not in live[kind]:
                        wanted[kind].add(v)
        for kind, ids in wanted.items():
            if ids:
                model = models[kind]
                external_existing[kind] = set(
                    s.execute(select(model.id).where(model.id.in_(ids))).scalars())

    return {'foreign': foreign, 'external_refs': external_refs,
            'external_existing': external_existing}


def load_point_state(row):
    state = decode_state(row.payload)
    if state['subject_id'] != row.subject_id:
        raise SnapshotError('Restore point payload does not match its subject.')
    return state


def preview(row):
    """Read-only: what restoring point ``row`` would change right now."""
    target = load_point_state(row)
    live = read_state(row.subject_id)
    plan = plan_restore(live, target, **gather_context(db.session, row.subject_id, live, target))
    return summarize(plan, live, target)


def apply_plan(session, subject_id, plan):
    """Execute a conflict-free plan with Core statements, every statement
    scoped to ``subject_id``. Does not commit."""
    from app.models import (Topic, Subtopic, Chapter, Subchapter, Question,
                            question_minor_topics, question_subtopics)
    s = session
    tables = {'topics': Topic.__table__, 'subtopics': Subtopic.__table__,
              'chapters': Chapter.__table__, 'subchapters': Subchapter.__table__}
    topic_ids = select(tables['topics'].c.id).where(tables['topics'].c.subject_id == subject_id)
    chapter_ids = select(tables['chapters'].c.id).where(tables['chapters'].c.subject_id == subject_id)

    def in_subject(kind):
        t = tables[kind]
        if kind in ('topics', 'chapters'):
            return t.c.subject_id == subject_id
        if kind == 'subtopics':
            return t.c.topic_id.in_(topic_ids)
        return t.c.chapter_id.in_(chapter_ids)

    tax = plan['taxonomy']
    for kind in ('topics', 'chapters', 'subtopics', 'subchapters'):
        rows = tax[kind]['insert']
        if rows:
            extra = {'subject_id': subject_id} if kind in ('topics', 'chapters') else {}
            s.execute(tables[kind].insert(), [dict(r, **extra) for r in rows])
    for kind in ('topics', 'chapters', 'subtopics', 'subchapters'):
        t = tables[kind]
        for u in tax[kind]['update']:
            s.execute(t.update().where(and_(t.c.id == u['id'], in_subject(kind))).values(**u['set']))

    q = plan['questions']
    qt = Question.__table__
    if q['scalar_updates'] and q['scalar_fields']:
        stmt = (qt.update()
                .where(and_(qt.c.id == bindparam('b_id'), qt.c.subject == subject_id))
                .values({f: bindparam('b_' + f) for f in q['scalar_fields']}))
        s.execute(stmt, [dict({'b_id': u['id']}, **{'b_' + f: u[f] for f in q['scalar_fields']})
                         for u in q['scalar_updates']])
    for field, tbl, col in (('minor_topic_ids', question_minor_topics, 'topic_id'),
                            ('subtopic_ids', question_subtopics, 'subtopic_id')):
        ops = q['links'][field]
        if ops['remove']:
            s.execute(tbl.delete().where(and_(tbl.c.question_id == bindparam('b_q'),
                                              tbl.c[col] == bindparam('b_r'))),
                      [{'b_q': pk, 'b_r': ref} for pk, ref in ops['remove']])
        if ops['add']:
            s.execute(tbl.insert(), [{'question_id': pk, col: ref} for pk, ref in ops['add']])

    for kind in ('subtopics', 'subchapters', 'topics', 'chapters'):
        ids = tax[kind]['delete']
        if ids:
            t = tables[kind]
            s.execute(t.delete().where(and_(t.c.id.in_(ids), in_subject(kind))))


def restore(row):
    """Restore point ``row`` over its subject and commit. Saves the live
    state as a ``before-restore`` point first (same transaction). Raises
    :class:`SnapshotError` on conflicts; nothing is written then."""
    s = db.session
    subject_id = row.subject_id
    target = load_point_state(row)
    live = read_state(subject_id, s)
    plan = plan_restore(live, target, **gather_context(s, subject_id, live, target))
    if plan['conflicts']:
        raise SnapshotError('Cannot restore: ' + ' '.join(plan['conflicts'][:5]))
    summary = summarize(plan, live, target)
    before = capture_state(live, 'before-restore', note=f'Before restoring point #{row.id}',
                           session=s, keep_ids={row.id})
    apply_plan(s, subject_id, plan)
    s.commit()
    return {'before_point_id': before.id, 'summary': summary}
