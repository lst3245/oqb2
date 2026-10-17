"""
One read / download policy for generated files, plus scrubbing of question
ids out of saved JSON blobs (file filter data, generation options, shared
profiles) before they reach a user who cannot read those subjects.

Owner and super admin always pass. A share recipient (direct or section
share) may download only when they can read every subject of the file's
questions; a file whose question ids cannot be recovered is owner / super
admin only.
"""
from __future__ import annotations

import json

from app import authz

_ID_KEYS = ('question_ids', 'ids')
_QID_KEYS = ('qids',)


def _is_owner_or_super(gen_file, user) -> bool:
    return gen_file.user_id == user.id or authz.is_super_admin(user)


def is_shared_with(gen_file, user) -> bool:
    from app.models import FileShare
    if FileShare.query.filter_by(file_id=gen_file.id, shared_with_user_id=user.id).first():
        return True
    if gen_file.section_id is not None:
        return FileShare.query.filter_by(
            section_id=gen_file.section_id, shared_with_user_id=user.id).first() is not None
    return False


def can_view(gen_file, user) -> bool:
    """See the row / its metadata: owner, super admin, or a share recipient."""
    return _is_owner_or_super(gen_file, user) or is_shared_with(gen_file, user)


def _load_json(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _as_ints(values) -> list[int]:
    if isinstance(values, str):
        values = [v for v in values.split(',') if v.strip()]
    out = []
    for v in values or ():
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            continue
    return out


def file_question_ids(gen_file) -> list[int]:
    return _as_ints(_load_json(gen_file.generation_options).get('question_ids'))


def file_subjects(gen_file) -> set[str]:
    from app import db
    from app.models import Question
    ids = file_question_ids(gen_file)
    if not ids:
        return set()
    rows = db.session.query(Question.subject).filter(Question.id.in_(ids)).distinct()
    return {r[0] for r in rows}


def can_download(gen_file, user) -> bool:
    if _is_owner_or_super(gen_file, user):
        return True
    if not is_shared_with(gen_file, user):
        return False
    subjects = file_subjects(gen_file)
    if not subjects:
        return False
    return all(authz.has_level(user, s, 'viewer') for s in subjects)


def strip_out_of_scope(payload: dict, readable, id_subject: dict, qid_subject: dict | None = None) -> dict:
    """Copy of ``payload`` with ``question_ids`` / ``ids`` / ``qids`` reduced to
    questions whose subject is in ``readable``. Unknown ids are dropped. The
    original value shape (list or comma string) is kept. Pure."""
    if not isinstance(payload, dict):
        return payload
    readable = set(readable or ())
    qid_subject = qid_subject or {}
    out = dict(payload)

    def _keep(values, lookup, convert):
        was_str = isinstance(values, str)
        items = [v for v in values.split(',') if v.strip()] if was_str else list(values or [])
        kept = []
        for v in items:
            try:
                key = convert(v)
            except (TypeError, ValueError):
                continue
            if lookup.get(key) in readable:
                kept.append(v)
        return ','.join(str(v) for v in kept) if was_str else kept

    for key in _ID_KEYS:
        if key in out and out[key]:
            out[key] = _keep(out[key], id_subject, int)
    for key in _QID_KEYS:
        if key in out and out[key]:
            out[key] = _keep(out[key], qid_subject, lambda v: str(v).strip())
    return out


def scrub(payload, user=None):
    """``strip_out_of_scope`` against the DB for ``user`` (super admin: no-op)."""
    if not isinstance(payload, dict) or authz.is_super_admin(user):
        return payload
    from app import db
    from app.models import Question
    ids = []
    for key in _ID_KEYS:
        ids.extend(_as_ints(payload.get(key)))
    qids = []
    for key in _QID_KEYS:
        raw = payload.get(key) or []
        if isinstance(raw, str):
            raw = raw.split(',')
        qids.extend(str(q).strip() for q in raw if str(q).strip())
    if not ids and not qids:
        return payload
    id_subject = {}
    if ids:
        id_subject = dict(db.session.query(Question.id, Question.subject)
                          .filter(Question.id.in_(set(ids))).all())
    qid_subject = {}
    if qids:
        qid_subject = dict(db.session.query(Question.qid, Question.subject)
                           .filter(Question.qid.in_(set(qids))).all())
    readable = authz.subjects_with_level(user, 'viewer')
    return strip_out_of_scope(payload, readable, id_subject, qid_subject)
