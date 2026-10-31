"""Library writes shared by the admin routes and the agent endpoint
(docs/modules/agent-mcp.md "Write tools", ADR-022).

Each function takes explicit arguments (no ``current_user``), raises
:class:`OpError` (message + HTTP status) before writing anything when the
input is refused, and leaves the commit to the caller unless it says
otherwise. Routes keep their own authz and response shapes:

* :func:`rename_question` <- ``admin.rename_question``
* :func:`set_parent` <- ``admin.set_question_parent``
* :func:`upload_assets` / :func:`after_upload` <- ``admin.upload_question_asset``
* :func:`add_taxonomy_node` / :func:`update_taxonomy_node` <- the topic /
  subtopic / chapter / subchapter add and edit routes (restore point first)
* :func:`write_md` and :func:`replace_images` are the agent's replace path
  (the editor's MD save route keeps its own mtime check).

``app.admin`` helpers (QID format, asset paths) are imported inside the
functions: ``app.admin`` imports this module.
"""
from __future__ import annotations

import os
import shutil

from flask import current_app

from app import db, subject_snapshot
from app.hierarchy import (HierarchyError, collect_subtree_ids, parse_qid, parse_qno_token,
                           part_sort_value, relink_parent, rename_shape_ok, rewrite_descendant_token,
                           token_fits_under)
from app.i18n import t as _t
from app.models import Chapter, Question, QuestionAsset, Subchapter, Subject, Subtopic, Topic
from app.utils import VERSIONS


class OpError(Exception):
    def __init__(self, message, status=400):
        super().__init__(str(message))
        self.status = status


# ---------------------------------------------------------------- rename

def rename_question(question, new_qid, *, rename_files=False, allowed_subjects=None) -> dict:
    """Change ``question``'s QID (and its descendants' tokens). With
    ``rename_files`` the asset files move to the new names. ``allowed_subjects``
    (None = any) limits the target subject. Flushes; caller commits."""
    from app.admin import _build_asset_file_path, validate_qid_format
    new_qid = str(new_qid or '').strip()
    if not new_qid:
        raise OpError(_t('New QID is required'))
    old_qid = question.qid
    if new_qid == old_qid:
        return {'old_qid': old_qid, 'new_qid': new_qid, 'renamed_files': [], 'errors': [],
                'descendant_count': 0, 'message': _t('QID unchanged'), 'unchanged': True}

    parsed, err = validate_qid_format(new_qid)
    if err:
        raise OpError(err)
    new_subject = parsed['subject']
    if not db.session.get(Subject, new_subject):
        raise OpError(_t('Subject {subject} does not exist', subject=new_subject))
    if allowed_subjects is not None and new_subject not in allowed_subjects:
        raise OpError(_t('You do not have admin access to subject {subject}', subject=new_subject), 403)
    existing = Question.query.filter_by(qid=new_qid).first()
    if existing and existing.id != question.id:
        raise OpError(_t('A question with QID {qid} already exists', qid=new_qid), 409)

    old_parsed_qid = parse_qid(old_qid)
    source_path = current_app.config['SOURCE_PATH']
    subtree_ids = collect_subtree_ids(question)
    has_descendants = len(subtree_ids) > 1
    old_token = old_parsed_qid.token if old_parsed_qid else None
    new_token = parsed['qno_token']
    if old_token:
        shape_err = rename_shape_ok(old_token, new_token, has_descendants)
        if shape_err:
            raise OpError(shape_err)

    subtree = Question.query.filter(Question.id.in_(subtree_ids)).all()
    new_prefix = parse_qid(new_qid).prefix
    plans = []  # (question, new_qid, ParsedQno)
    new_qids = []
    for node in subtree:
        node_parsed = parse_qid(node.qid)
        if not node_parsed:
            raise OpError(_t('Cannot parse existing QID {qid}', qid=node.qid))
        try:
            if node.id == question.id:
                node_new_token = new_token
            else:
                node_new_token = rewrite_descendant_token(node_parsed.token, old_token, new_token)
        except HierarchyError as e:
            raise OpError(str(e)) from None
        node_new_qid = f'{new_prefix}_{node_new_token}'
        plans.append((node, node_new_qid, parse_qno_token(node_new_token)))
        new_qids.append(node_new_qid)

    if len(set(new_qids)) != len(new_qids):
        raise OpError(_t('Rename would produce duplicate QIDs in the subtree'), 409)
    collisions = (Question.query.filter(Question.qid.in_(new_qids))
                  .filter(~Question.id.in_(subtree_ids)).all())
    if collisions:
        raise OpError(_t('A question with QID {qid} already exists', qid=collisions[0].qid), 409)

    # QIDs and hierarchy columns first; parent_id is recomputed once every
    # new QID exists so ensure_question can find the new parents.
    for node, node_new_qid, node_tok in plans:
        node.qid = node_new_qid
        node.subject = parsed['subject']
        node.source = parsed['source']
        node.year = parsed.get('year')
        node.paper = parsed.get('paper')
        node.qno = node_tok.qno
        node.qno_end = node_tok.qno_end
        node.part = node_tok.own_part
        node.part_sort = part_sort_value(node_tok.own_part) if node_tok.own_part else None
    db.session.flush()
    for node, _nid, _tok in plans:
        relink_parent(node)

    renamed_files = []
    errors = []
    if rename_files:
        for node, _nid, _tok in plans:
            for asset in node.assets.all():
                old_full = os.path.join(source_path, asset.file_path)
                new_rel = _build_asset_file_path(node, asset)
                new_full = os.path.join(source_path, new_rel)
                if os.path.exists(old_full):
                    try:
                        os.makedirs(os.path.dirname(new_full), exist_ok=True)
                        shutil.move(old_full, new_full)
                        renamed_files.append({'old': asset.file_path, 'new': new_rel})
                        asset.file_path = new_rel
                    except Exception as e:
                        errors.append(_t('Error moving {path}: {e}', path=asset.file_path, e=str(e)))
                else:
                    asset.file_path = new_rel

    n_extra = len(plans) - 1
    if n_extra:
        msg = _t('Question renamed from {old_qid} to {new_qid} ({n_extra} descendant QID(s) updated)',
                 old_qid=old_qid, new_qid=new_qid, n_extra=n_extra)
    else:
        msg = _t('Question renamed from {old_qid} to {new_qid}', old_qid=old_qid, new_qid=new_qid)
    return {'old_qid': old_qid, 'new_qid': new_qid, 'renamed_files': renamed_files, 'errors': errors,
            'descendant_count': n_extra, 'message': msg}


# ---------------------------------------------------------------- parent

def set_parent(child, parent_id) -> dict:
    """Attach ``child`` under ``parent_id`` or detach it (``None`` / ``''``).
    The child's QID must already sit under the parent token; parts at a level
    with a shared block go under the block that owns them, and a request that
    disagrees is refused with 409 (the session is rolled back). Caller commits."""
    from app.admin import _block_owner_conflict
    if parent_id in ('', None):
        if child.part:
            raise OpError(_t('Cannot detach a labelled part; rename it to a standalone Qn first.'))
        child.parent_id = None
        return {'parent_id': None}
    try:
        parent_id = int(parent_id)
    except (TypeError, ValueError):
        raise OpError(_t('Invalid parent_id')) from None
    parent = db.session.get(Question, parent_id)
    if parent is None:
        raise OpError(_t('Question not found'), 404)
    if parent.subject != child.subject:
        raise OpError(_t('Parent must be in the same subject'))
    if parent.id == child.id or parent.id in collect_subtree_ids(child):
        raise OpError(_t('Cannot set a descendant as parent'))
    if not token_fits_under(child.qid, parent.qid):
        raise OpError(_t('Child QID is not under that parent token'))
    child.parent_id = parent.id
    db.session.flush()
    if child.part:
        relink_parent(child)
        if child.parent_id != parent.id:
            owner = db.session.get(Question, child.parent_id) if child.parent_id else None
            message = _block_owner_conflict(child, parent, owner)
            db.session.rollback()
            raise OpError(message, 409)
    return {'parent_id': child.parent_id}


# ---------------------------------------------------------------- assets

class BytesUpload:
    """In-memory stand-in for a werkzeug ``FileStorage`` (``filename``,
    ``read()``, ``save(path)``) so agent uploads share :func:`upload_assets`."""

    def __init__(self, filename, data: bytes):
        self.filename = filename
        self._data = data

    def read(self):
        return self._data

    def save(self, path):
        with open(path, 'wb') as fh:
            fh.write(self._data)


def asset_folder(question) -> str:
    from app.admin import _extract_qb_detail
    if question.source in ('DSE', 'CE', 'AL'):
        return '/'.join([question.subject, 'PP', question.source, str(question.year), question.paper])
    return '/'.join([question.subject, 'QB', _extract_qb_detail(question.qid)])


def upload_assets(question, version, asset_type, files) -> dict:
    """Add asset files to a slot: IMG files become the next parts, MD and DOC
    are single-slot (refused when the slot has one). ``files``: objects with
    ``filename``, ``read()`` and ``save(path)``. Writes the files and adds
    rows; the caller commits, then calls :func:`after_upload`.
    Returns ``{'uploaded': [...], 'errors': [...]}`` (per-file problems)."""
    if version not in VERSIONS:
        raise OpError(_t('Invalid version'))
    if asset_type == 'WHOLE':
        if question.parent_id:
            raise OpError(_t('A whole-question copy can only be stored on the root question, not on a part.'))
    elif asset_type not in ('QUE', 'ANS', 'SOL'):
        raise OpError(_t('Invalid asset type'))
    if not files:
        raise OpError(_t('No files provided'))

    source_path = current_app.config['SOURCE_PATH']
    md_max = current_app.config.get('MD_MAX_SIZE_BYTES', 5 * 1024 * 1024)
    # Only IMG assets are multi-part; MD and DOC are always part 1 and must
    # not consume an image part index.
    last_img_part = QuestionAsset.query.filter_by(
        question_id=question.id, version=version, asset_type=asset_type, file_format='IMG'
    ).order_by(QuestionAsset.part_number.desc()).first()
    next_img_part = (last_img_part.part_number + 1) if last_img_part else 1

    uploaded = []
    errors = []
    folder = None
    for f in files:
        if not f.filename:
            continue
        ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else 'png'
        if ext in ('png', 'jpg', 'jpeg', 'gif', 'bmp'):
            file_format = 'IMG'
        elif ext in ('doc', 'docx'):
            file_format = 'DOC'
        elif ext in ('md', 'markdown'):
            file_format = 'MD'
        else:
            errors.append(_t('{filename}: unsupported extension', filename=f.filename))
            continue
        if asset_type == 'WHOLE' and file_format != 'IMG':
            errors.append(_t('{filename}: a whole-question copy must be an image', filename=f.filename))
            continue

        data = None
        if file_format == 'MD':
            existing_md = QuestionAsset.query.filter_by(
                question_id=question.id, version=version, asset_type=asset_type, file_format='MD').first()
            if existing_md:
                errors.append(_t(
                    '{filename}: a Markdown asset already exists for {asset_type}/{version}. Edit or delete it first.',
                    filename=f.filename, asset_type=asset_type, version=version))
                continue
            data = f.read()
            if len(data) > md_max:
                errors.append(_t('{filename}: exceeds MD_MAX_SIZE_BYTES ({md_max} bytes)',
                                 filename=f.filename, md_max=md_max))
                continue
            filename = f"{question.qid}_{version}_{asset_type}.md"
            part_to_use = 1
        elif file_format == 'DOC':
            existing_doc = QuestionAsset.query.filter_by(
                question_id=question.id, version=version, asset_type=asset_type, file_format='DOC').first()
            if existing_doc:
                errors.append(_t(
                    '{filename}: a Word document already exists for {asset_type}/{version}. Delete it first to replace.',
                    filename=f.filename, asset_type=asset_type, version=version))
                continue
            filename = f"{question.qid}_{version}_{asset_type}.{ext}"
            part_to_use = 1
        else:
            part_suffix = f'_{next_img_part}' if next_img_part > 1 else ''
            filename = f"{question.qid}_{version}_{asset_type}{part_suffix}.{ext}"
            part_to_use = next_img_part

        folder = folder or asset_folder(question)
        rel_path = f"{folder}/{filename}"
        full_path = os.path.join(source_path, *rel_path.split('/'))
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        if file_format == 'MD':
            with open(full_path, 'wb') as out:
                out.write(data)
        else:
            f.save(full_path)

        asset = QuestionAsset(question_id=question.id, asset_type=asset_type, file_format=file_format,
                              version=version, file_path=rel_path, part_number=part_to_use)
        db.session.add(asset)
        uploaded.append({'asset': asset, 'part_number': part_to_use, 'file_path': rel_path,
                         'file_format': file_format})
        if file_format == 'IMG':
            next_img_part += 1
    return {'uploaded': uploaded, 'errors': errors}


def after_upload(uploaded) -> None:
    """DOC thumbnail lifecycle after the commit: a new IMG drops a stale DOC
    thumbnail in its slot; a new DOC schedules a render."""
    from app import doc_thumbnails
    for u in uploaded:
        a = u['asset']
        if a.file_format == 'IMG':
            doc_thumbnails.on_img_asset_created(a)
        elif a.file_format == 'DOC':
            doc_thumbnails.on_doc_asset_created(a)


def slot_assets(question, asset_type, version, file_format):
    return (QuestionAsset.query.filter_by(question_id=question.id, asset_type=asset_type,
                                          version=version, file_format=file_format)
            .order_by(QuestionAsset.part_number).all())


def asset_bytes(asset) -> bytes | None:
    from app import storage
    path = storage.safe_join(storage.source_path(), *str(asset.file_path).split('/'))
    if not path or not os.path.isfile(path):
        return None
    with open(path, 'rb') as fh:
        return fh.read()


def write_md(asset, content) -> int:
    """Overwrite an MD asset's file and drop its render cache. Returns the
    byte size. No mtime check: the agent read it through the API just before."""
    from app import md_render, storage
    if asset.file_format != 'MD':
        raise OpError(_t('Asset is not Markdown'))
    if not isinstance(content, str):
        raise OpError(_t('content must be a string'))
    payload = content.encode('utf-8')
    md_max = current_app.config.get('MD_MAX_SIZE_BYTES', 5 * 1024 * 1024)
    if len(payload) > md_max:
        raise OpError(_t('Content exceeds MD_MAX_SIZE_BYTES ({md_max} bytes); got {n}', md_max=md_max,
                         n=len(payload)), 413)
    path = storage.safe_join(storage.source_path(), *str(asset.file_path).split('/'))
    if not path:
        raise OpError(_t('File not found'), 404)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as fh:
        fh.write(payload)
    md_render.invalidate(asset.id)
    return len(payload)


def replace_images(question, asset_type, version, images) -> dict:
    """Replace every IMG part of one slot with ``images`` (PIL images, one
    part each). Commits (``batch_image_gen.replace_img_assets``)."""
    from app.batch_image_gen import replace_img_assets
    if version not in VERSIONS:
        raise OpError(_t('Invalid version'))
    if asset_type not in ('QUE', 'ANS', 'SOL', 'WHOLE'):
        raise OpError(_t('Invalid asset type'))
    if not images:
        raise OpError(_t('No files provided'))
    return replace_img_assets(question, asset_type, version, list(images), stitch=False,
                              source_path=current_app.config['SOURCE_PATH'])


# ---------------------------------------------------------------- taxonomy

TAXONOMY_KINDS = {
    'topic': {'model': Topic, 'parent': None, 'key': 'subject_id', 'action': 'topic-edit'},
    'subtopic': {'model': Subtopic, 'parent': Topic, 'key': 'topic_id', 'action': 'topic-edit'},
    'chapter': {'model': Chapter, 'parent': None, 'key': 'subject_id', 'action': 'chapter-edit'},
    'subchapter': {'model': Subchapter, 'parent': Chapter, 'key': 'chapter_id', 'action': 'chapter-edit'},
}


def node_subject(kind, row) -> str:
    if kind == 'subtopic':
        return row.topic.subject_id
    if kind == 'subchapter':
        return row.chapter.subject_id
    return row.subject_id


def add_taxonomy_node(kind, parent, name, *, name_zh=None, hidden=False, action=None, note=None):
    """New topic / chapter under subject ``parent``, or subtopic / subchapter
    under node id ``parent``, last in sort order. Takes the subject's restore
    point first. Returns ``(row, subject_id)``; caller commits."""
    spec = TAXONOMY_KINDS.get(kind)
    if spec is None:
        raise OpError(f'unknown taxonomy kind: {kind}')
    name = str(name or '').strip()
    if not name:
        raise OpError(_t('Name is required'))
    if spec['parent'] is None:
        subject_id = str(parent or '').strip()
        if not subject_id or db.session.get(Subject, subject_id) is None:
            raise OpError(_t('Subject {subject} does not exist', subject=subject_id))
        key_value = subject_id
    else:
        try:
            parent_row = db.session.get(spec['parent'], int(parent))
        except (TypeError, ValueError):
            parent_row = None
        if parent_row is None:
            raise OpError(_t('Missing required fields'), 404)
        subject_id = parent_row.subject_id
        key_value = parent_row.id
    subject_snapshot.capture(subject_id, action or spec['action'], note=note)
    model = spec['model']
    max_order = (db.session.query(db.func.max(model.sort_order))
                 .filter(getattr(model, spec['key']) == key_value).scalar() or 0)
    row = model(name=name, name_zh=(str(name_zh).strip() or None) if name_zh else None,
                hidden=bool(hidden), sort_order=max_order + 1)
    setattr(row, spec['key'], key_value)
    db.session.add(row)
    db.session.flush()
    return row, subject_id


def update_taxonomy_node(kind, row, updates, *, hidden=None, action=None, note=None) -> str:
    """Apply ``updates`` (``name`` / ``name_zh``) and an optional ``hidden``
    flag after the subject's restore point. Returns the subject id."""
    spec = TAXONOMY_KINDS.get(kind)
    if spec is None:
        raise OpError(f'unknown taxonomy kind: {kind}')
    if 'name' in updates and not str(updates['name'] or '').strip():
        raise OpError(_t('Name is required'))
    subject_id = node_subject(kind, row)
    subject_snapshot.capture(subject_id, action or spec['action'], note=note)
    for field, value in updates.items():
        setattr(row, field, value)
    if hidden is not None:
        row.hidden = bool(hidden)
    return subject_id
