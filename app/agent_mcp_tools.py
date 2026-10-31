"""Tools of the agent endpoint (``POST /mcp``; docs/modules/agent-mcp.md).

Each tool is ``fn(args, ctx)`` registered with ``agent_mcp.tool``. Handlers
stay thin: they parse arguments, call a service, and return a JSON-able dict
(or a ``ToolResult`` with images). They raise ``ToolError`` for bad input;
the transport commits on success and rolls back otherwise. The token acts as
a super admin, so there are no per-subject checks here.

Sections: library (read), review, requests, library (write).
"""
from __future__ import annotations

import base64
import io
import os
import re

from flask import current_app

from app import db, storage
from app.agent_mcp import ToolError, ToolResult, read_playbook, tool
from app.models import Chapter, Question, QuestionAsset, Subchapter, Subject, Subtopic, Topic
from app.utils import VERSIONS, utc_iso

MAX_SEARCH_LIMIT = 500
MAX_IMAGES = 16
_DATA_URI = re.compile(r'data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=\s]+')

AGENT_NAME_PROP = {
    'agent_name': {'type': 'string', 'description': 'Optional display name for this agent (e.g. "Grok").'},
}


def _schema(properties=None, required=()):
    props = dict(AGENT_NAME_PROP)
    props.update(properties or {})
    out = {'type': 'object', 'properties': props, 'additionalProperties': False}
    if required:
        out['required'] = list(required)
    return out


# ---------------------------------------------------------------- helpers

def _int_arg(args, key, default=None, *, minimum=None, maximum=None):
    raw = args.get(key, default)
    if raw in (None, ''):
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ToolError(f'{key} must be an integer') from None
    if minimum is not None and value < minimum:
        value = minimum
    if maximum is not None and value > maximum:
        value = maximum
    return value


def _str_arg(args, key, *, required=False, limit=None):
    raw = args.get(key)
    text = '' if raw is None else str(raw).strip()
    if required and not text:
        raise ToolError(f'{key} is required')
    if limit and len(text) > limit:
        raise ToolError(f'{key} is longer than {limit} characters')
    return text


def find_question(args, *, required=True):
    """Question by ``qid`` (exact) or ``question_id``."""
    qid = _str_arg(args, 'qid')
    qpk = args.get('question_id')
    question = None
    if qid:
        question = Question.query.filter_by(qid=qid).first()
    elif qpk not in (None, ''):
        try:
            question = db.session.get(Question, int(qpk))
        except (TypeError, ValueError):
            raise ToolError('question_id must be an integer') from None
    elif required:
        raise ToolError('give qid or question_id')
    if question is None and required:
        raise ToolError(f'question not found: {qid or qpk}')
    return question


def _subject(args, key='subject'):
    sid = _str_arg(args, key, required=True).upper()
    subject = db.session.get(Subject, sid)
    if subject is None:
        raise ToolError(f'unknown subject: {sid}')
    return subject


def _node_json(node, kind):
    out = {'id': node.id, 'name': node.name, 'name_zh': node.name_zh, 'hidden': bool(node.hidden),
           'sort_order': node.sort_order, 'description': node.description}
    if kind == 'subtopics':
        out['topic_id'] = node.topic_id
    if kind == 'subchapters':
        out['chapter_id'] = node.chapter_id
    return out


def question_brief(q, taxonomy=None) -> dict:
    from app import question_edit_service as qes
    out = {'id': q.id, 'qid': q.qid, 'subject': q.subject, 'parent_id': q.parent_id,
           'level': q.level, 'q_type': q.q_type, 'marks': q.marks, 'verified': bool(q.verified)}
    if taxonomy is not None:
        out['major_topic'] = qes.label('major_topic', q.major_topic_id, taxonomy) or None
        out['major_subtopic'] = qes.label('major_subtopic', q.major_subtopic_id, taxonomy) or None
        out['chapter'] = qes.label('chapter', q.chapter_id, taxonomy) or None
    return out


def question_detail(q, taxonomy) -> dict:
    from app import hierarchy, question_edit_service as qes
    stem = hierarchy.is_stem(q)
    assets = (QuestionAsset.query.filter_by(question_id=q.id)
              .order_by(QuestionAsset.asset_type, QuestionAsset.version, QuestionAsset.part_number).all())
    children = sorted(getattr(q, 'children', None) or [], key=hierarchy.sort_key)
    return {
        'id': q.id, 'qid': q.qid, 'subject': q.subject, 'source': q.source, 'year': q.year,
        'paper': q.paper, 'qno': q.qno, 'part': q.part, 'is_stem': stem,
        'needs_prev_parts': bool(q.needs_prev_parts), 'verified': bool(q.verified),
        'parent': ({'id': q.parent.id, 'qid': q.parent.qid} if q.parent is not None else None),
        'ancestors': [a.qid for a in hierarchy.ancestors(q)],
        'children': [{'id': c.id, 'qid': c.qid} for c in children],
        'fields': qes.describe(q, taxonomy),
        'assets': [{'id': a.id, 'asset_type': a.asset_type, 'version': a.version,
                    'format': a.file_format, 'part_number': a.part_number,
                    'check_state': a.check_state} for a in assets],
        'created_at': utc_iso(q.created_at),
    }


def _pick_assets(question_id, asset_type, version=None):
    """Assets of one slot: ``version`` if given, else the first version in
    ``VERSIONS`` order that has any (IMG > MD > DOC inside a version)."""
    rows = QuestionAsset.query.filter_by(question_id=question_id, asset_type=asset_type).all()
    if version:
        rows = [a for a in rows if a.version == version]
    if not rows:
        return None, None, []
    fmt_rank = {'IMG': 0, 'MD': 1, 'DOC': 2}
    rank = {v: i for i, v in enumerate(VERSIONS)}
    rows.sort(key=lambda a: (rank.get(a.version, 99), fmt_rank.get(a.file_format, 9), a.part_number))
    best = rows[0]
    chosen = [a for a in rows if a.version == best.version and a.file_format == best.file_format]
    return best.version, best.file_format, sorted(chosen, key=lambda a: a.part_number)


def _source_file(rel):
    rel = (rel or '').replace('\\', '/')
    path = storage.safe_join(storage.source_path(), *[p for p in rel.split('/') if p])
    if not path or not os.path.isfile(path):
        return None
    return path


def encode_image(path, max_dim) -> tuple[str, str]:
    """``(base64, mime)`` of an image file, longest edge cut to ``max_dim``."""
    from PIL import Image
    with Image.open(path) as im:
        im.load()
        if im.mode not in ('RGB', 'RGBA', 'L', 'LA'):
            im = im.convert('RGBA' if 'A' in im.getbands() else 'RGB')
        if max_dim and max(im.size) > max_dim:
            im.thumbnail((max_dim, max_dim))
        buf = io.BytesIO()
        im.save(buf, format='PNG', optimize=True)
    return base64.b64encode(buf.getvalue()).decode('ascii'), 'image/png'


def parse_since(raw):
    """ISO 8601 (``...Z`` or an offset) -> naive UTC datetime, or None."""
    from datetime import datetime, timezone
    text = str(raw or '').strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        raise ToolError(f'not an ISO 8601 time: {text}') from None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def strip_data_uris(text) -> str:
    return _DATA_URI.sub('[embedded image]', text or '')


def _read_text(path, limit=400_000):
    with open(path, encoding='utf-8', errors='replace') as fh:
        return fh.read(limit)


def _slot_content(q, asset_type, version, max_dim, label_prefix, content, counter, part=None):
    """Append text + image blocks of one slot to ``content``. Returns the
    slot description or None when the slot is empty. ``part`` keeps one
    page of an IMG slot."""
    from app import doc_thumbnails
    ver, fmt, assets = _pick_assets(q.id, asset_type, version)
    if part and fmt == 'IMG':
        assets = [a for a in assets if a.part_number == part]
    if not assets:
        return None
    desc = {'qid': q.qid, 'asset_type': asset_type, 'version': ver, 'format': fmt, 'pages': len(assets)}
    if fmt == 'IMG':
        for a in assets:
            if counter[0] >= MAX_IMAGES:
                content.append({'type': 'text', 'text': f'(image limit {MAX_IMAGES} reached)'})
                break
            path = _source_file(a.file_path)
            if not path:
                content.append({'type': 'text', 'text': f'{label_prefix}{q.qid} {asset_type} {ver} page {a.part_number}: file missing'})
                continue
            data, mime = encode_image(path, max_dim)
            content.append({'type': 'text', 'text': f'{label_prefix}{q.qid} {asset_type} {ver} page {a.part_number}'})
            content.append({'type': 'image', 'data': data, 'mimeType': mime})
            counter[0] += 1
    elif fmt == 'MD':
        path = _source_file(assets[0].file_path)
        text = strip_data_uris(_read_text(path)) if path else '(file missing)'
        content.append({'type': 'text', 'text': f'{label_prefix}{q.qid} {asset_type} {ver} (Markdown):\n{text}'})
    else:
        thumb = doc_thumbnails.thumbnail_path(assets[0].id)
        if thumb and os.path.isfile(thumb) and counter[0] < MAX_IMAGES:
            data, mime = encode_image(thumb, max_dim)
            content.append({'type': 'text', 'text': f'{label_prefix}{q.qid} {asset_type} {ver} (Word document, first page)'})
            content.append({'type': 'image', 'data': data, 'mimeType': mime})
            counter[0] += 1
        else:
            content.append({'type': 'text', 'text': f'{label_prefix}{q.qid} {asset_type} {ver}: Word document without a rendered page'})
    return desc


# ---------------------------------------------------------------- library (read)

@tool('get_instructions',
      'The OQB agent playbook: how to poll requests, reply, ask for approval, create review '
      'batches and change the library safely. Read it at the start of every session.',
      _schema(), title='Read the playbook')
def get_instructions(args, ctx):
    return {'playbook': read_playbook(), 'agent_name': ctx.agent_name}


@tool('list_subjects', 'Subjects with their question counts.', _schema(), title='List subjects')
def list_subjects(args, ctx):
    from sqlalchemy import func
    counts = dict(db.session.query(Question.subject, func.count(Question.id)).group_by(Question.subject).all())
    return {'subjects': [{'id': s.id, 'name': s.name, 'name_zh': s.name_zh,
                          'questions': int(counts.get(s.id, 0))}
                         for s in Subject.query.order_by(Subject.id).all()]}


@tool('get_taxonomy',
      'Topics (with subtopics) and chapters (with subchapters) of one subject: ids, English '
      'and Chinese names, hidden flag, order and tagging hints. Use the ids or names in changes.',
      _schema({'subject': {'type': 'string', 'description': 'Subject id, e.g. MATC.'}}, ('subject',)),
      title='Get a subject taxonomy')
def get_taxonomy(args, ctx):
    subject = _subject(args)
    topics = Topic.query.filter_by(subject_id=subject.id).order_by(Topic.sort_order, Topic.id).all()
    chapters = Chapter.query.filter_by(subject_id=subject.id).order_by(Chapter.sort_order, Chapter.id).all()
    out_topics = []
    for t in topics:
        row = _node_json(t, 'topics')
        row['subtopics'] = [_node_json(s, 'subtopics') for s in
                            Subtopic.query.filter_by(topic_id=t.id).order_by(Subtopic.sort_order, Subtopic.id)]
        out_topics.append(row)
    out_chapters = []
    for c in chapters:
        row = _node_json(c, 'chapters')
        row['subchapters'] = [_node_json(s, 'subchapters') for s in
                              Subchapter.query.filter_by(chapter_id=c.id).order_by(Subchapter.sort_order, Subchapter.id)]
        out_chapters.append(row)
    return {'subject': subject.id, 'topics': out_topics, 'chapters': out_chapters}


@tool('search_questions',
      'Find questions in one subject. Filters combine with AND. By default only leaves '
      '(taggable questions and parts) are returned, in paper order.',
      _schema({
          'subject': {'type': 'string'},
          'qids': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Exact QIDs.'},
          'qid_contains': {'type': 'string', 'description': 'Substring of the QID, e.g. DSE_2023_P1.'},
          'source': {'type': 'string', 'description': 'DSE, CE, AL, QB ...'},
          'year': {'type': 'integer'},
          'q_type': {'type': 'string', 'enum': ['MC', 'CQ']},
          'level': {'type': 'integer'},
          'major_topic': {'description': 'Topic id or name.'},
          'chapter': {'description': 'Chapter id or name.'},
          'untagged': {'type': 'boolean', 'description': 'Only questions without a major topic.'},
          'verified': {'type': 'boolean'},
          'leaves_only': {'type': 'boolean', 'default': True},
          'limit': {'type': 'integer', 'default': 50, 'maximum': MAX_SEARCH_LIMIT},
          'offset': {'type': 'integer', 'default': 0},
      }, ('subject',)),
      title='Search questions')
def search_questions(args, ctx):
    from app import hierarchy, question_edit_service as qes
    subject = _subject(args)
    tax = qes.Taxonomy.load(subject.id)
    q = Question.query.filter(Question.subject == subject.id)
    qids = args.get('qids')
    if qids:
        if not isinstance(qids, list):
            raise ToolError('qids must be a list')
        q = q.filter(Question.qid.in_([str(x).strip() for x in qids][:1000]))
    contains = _str_arg(args, 'qid_contains', limit=100)
    if contains:
        q = q.filter(Question.qid.contains(contains))
    source = _str_arg(args, 'source', limit=20)
    if source:
        q = q.filter(Question.source == source.upper())
    if args.get('year') not in (None, ''):
        q = q.filter(Question.year == _int_arg(args, 'year'))
    if args.get('q_type'):
        q = q.filter(Question.q_type == _str_arg(args, 'q_type').upper())
    if args.get('level') not in (None, ''):
        q = q.filter(Question.level == _int_arg(args, 'level'))
    try:
        if args.get('major_topic') not in (None, ''):
            q = q.filter(Question.major_topic_id == tax.resolve('topics', args.get('major_topic')))
        if args.get('chapter') not in (None, ''):
            q = q.filter(Question.chapter_id == tax.resolve('chapters', args.get('chapter')))
    except qes.EditError as exc:
        raise ToolError(str(exc)) from None
    if args.get('untagged'):
        q = q.filter(Question.major_topic_id.is_(None))
    if args.get('verified') is not None:
        q = q.filter(Question.verified.is_(bool(args.get('verified'))))
    if args.get('leaves_only', True):
        q = q.filter(hierarchy.leaf_clause())
    rows = sorted(q.all(), key=hierarchy.sort_key)
    offset = _int_arg(args, 'offset', 0, minimum=0)
    limit = _int_arg(args, 'limit', 50, minimum=1, maximum=MAX_SEARCH_LIMIT)
    page = rows[offset:offset + limit]
    return {'subject': subject.id, 'total': len(rows), 'offset': offset,
            'questions': [question_brief(r, tax) for r in page]}


@tool('get_question',
      'Everything about one question: tags (ids and English names), marks, answer text, '
      'tree (parent, ancestors, children) and its asset files.',
      _schema({'qid': {'type': 'string'}, 'question_id': {'type': 'integer'}}),
      title='Get a question')
def get_question(args, ctx):
    from app import question_edit_service as qes
    q = find_question(args)
    return question_detail(q, qes.Taxonomy.load(q.subject))


@tool('get_question_image',
      'The question (or answer / solution) as images, for looking at it. For a part, the '
      'shared stem pages of its ancestors come first unless include_context is false. '
      'Markdown slots come back as text, Word slots as a rendered first page when one exists.',
      _schema({
          'qid': {'type': 'string'}, 'question_id': {'type': 'integer'},
          'asset_type': {'type': 'string', 'enum': ['QUE', 'ANS', 'SOL', 'WHOLE'], 'default': 'QUE'},
          'version': {'type': 'string', 'enum': list(VERSIONS),
                      'description': 'Default: the first version that exists (EN, CH, BI, ENO, CHO).'},
          'part': {'type': 'integer', 'description': 'Only this page of an image slot (1, 2, ...).'},
          'include_context': {'type': 'boolean', 'default': True},
          'max_dim': {'type': 'integer', 'description': 'Longest image edge in px (default LLM_IMAGE_MAX_DIM).'},
      }),
      title='See a question')
def get_question_image(args, ctx):
    from app import hierarchy
    q = find_question(args)
    asset_type = (_str_arg(args, 'asset_type') or 'QUE').upper()
    if asset_type not in ('QUE', 'ANS', 'SOL', 'WHOLE'):
        raise ToolError('asset_type must be QUE, ANS, SOL or WHOLE')
    version = _str_arg(args, 'version').upper() or None
    if version and version not in VERSIONS:
        raise ToolError(f'version must be one of {", ".join(VERSIONS)}')
    default_dim = int(current_app.config.get('LLM_IMAGE_MAX_DIM') or 1600)
    max_dim = _int_arg(args, 'max_dim', default_dim, minimum=200, maximum=4000)
    content, counter, slots = [], [0], []
    if args.get('include_context', True) and asset_type == 'QUE':
        for anc in hierarchy.ancestors(q):
            desc = _slot_content(anc, 'QUE', version, max_dim, 'Context (stem) ', content, counter)
            if desc:
                slots.append(dict(desc, context=True))
    part = _int_arg(args, 'part', None, minimum=1)
    desc = _slot_content(q, asset_type, version, max_dim, '', content, counter, part=part)
    if desc:
        slots.append(desc)
    else:
        content.append({'type': 'text', 'text': f'{q.qid} has no {asset_type} asset'
                                                + (f' in {version}' if version else '') + '.'})
    return ToolResult(content=content, structured={'qid': q.qid, 'slots': slots})


@tool('get_md_asset',
      'Raw Markdown of a question slot (QUE / ANS / SOL). Embedded base64 images are replaced '
      'by "[embedded image]" unless keep_images is true.',
      _schema({
          'qid': {'type': 'string'}, 'question_id': {'type': 'integer'},
          'asset_type': {'type': 'string', 'enum': ['QUE', 'ANS', 'SOL'], 'default': 'QUE'},
          'version': {'type': 'string', 'enum': list(VERSIONS)},
          'keep_images': {'type': 'boolean', 'default': False},
      }),
      title='Read a Markdown asset')
def get_md_asset(args, ctx):
    q = find_question(args)
    asset_type = (_str_arg(args, 'asset_type') or 'QUE').upper()
    version = _str_arg(args, 'version').upper() or None
    rows = QuestionAsset.query.filter_by(question_id=q.id, asset_type=asset_type, file_format='MD').all()
    if version:
        rows = [a for a in rows if a.version == version]
    if not rows:
        raise ToolError(f'{q.qid} has no Markdown {asset_type}' + (f' in {version}' if version else ''))
    rank = {v: i for i, v in enumerate(VERSIONS)}
    asset = sorted(rows, key=lambda a: rank.get(a.version, 99))[0]
    path = _source_file(asset.file_path)
    if not path:
        raise ToolError('the Markdown file is missing on disk')
    text = _read_text(path, limit=5_000_000)
    if not args.get('keep_images'):
        text = strip_data_uris(text)
    return {'qid': q.qid, 'asset_id': asset.id, 'asset_type': asset_type, 'version': asset.version,
            'markdown': text}


# ---------------------------------------------------------------- review

_CHANGE_SCHEMA = {
    'description': 'A list of {field, old, new} or an object {field: new}. Fields: major_topic, '
                   'major_subtopic, minor_topics, subtopics, chapter, subchapter, level, q_type, '
                   'section, marks, correct_percentage, answer, description, comment.',
}
_ITEM_SCHEMA = {
    'type': 'object',
    'properties': {
        'qid': {'type': 'string'}, 'question_id': {'type': 'integer'},
        'kind': {'type': 'string', 'enum': ['fields', 'freeform']},
        'changes': _CHANGE_SCHEMA,
        'title': {'type': 'string', 'description': 'Required for a freeform item.'},
        'rationale': {'type': 'string'},
        'confidence': {'type': 'number', 'minimum': 0, 'maximum': 1},
    },
}


def _review_url(batch_id):
    from flask import url_for
    try:
        return url_for('review.batch_page', batch_id=batch_id)
    except Exception:
        return None


def _request_id_arg(args, *, required=False):
    from app.models import SupportRequest
    rid = _int_arg(args, 'request_id')
    if rid is None:
        if required:
            raise ToolError('request_id is required')
        return None
    if db.session.get(SupportRequest, rid) is None:
        raise ToolError(f'request {rid} not found')
    return rid


def _batch_arg(args):
    from app.models import ReviewBatch
    bid = _int_arg(args, 'batch_id')
    batch = db.session.get(ReviewBatch, bid) if bid else None
    if batch is None:
        raise ToolError(f'review batch not found: {args.get("batch_id")}')
    return batch


def _problems_text(problems):
    return '; '.join(f"#{p['index']} {p['qid']}: {p['message']}".replace(' :', ':') for p in problems[:50])


@tool('create_review_batch',
      'Propose tag / field changes for admins to check one question at a time (Admin -> Review). '
      'One subject per batch. Items with errors are not saved and come back in "problems". '
      'See get_instructions for the item format.',
      _schema({
          'subject': {'type': 'string'},
          'title': {'type': 'string'},
          'summary': {'type': 'string'},
          'request_id': {'type': 'integer', 'description': 'The request this batch answers (a note is posted there).'},
          'items': {'type': 'array', 'items': _ITEM_SCHEMA},
          'reason': {'type': 'string'},
      }, ('subject', 'title', 'items')),
      title='Create a review batch', writes=True)
def create_review_batch(args, ctx):
    from app import review_service as rs, support_requests_service as srs
    try:
        payload = rs.parse_payload({'subject': args.get('subject'), 'title': args.get('title'),
                                    'summary': args.get('summary'), 'items': args.get('items')})
    except rs.ReviewError as exc:
        raise ToolError(str(exc)) from None
    subject = _subject({'subject': payload['subject']})
    if not payload['title']:
        raise ToolError('title is required')
    rid = _request_id_arg(args)
    valid, problems = rs.prepare(subject.id, payload['items'])
    if not valid:
        raise ToolError('no item could be saved: ' + _problems_text(problems))
    try:
        batch = rs.create_batch(subject.id, payload['title'], summary=payload['summary'], source='agent',
                                agent_name=ctx.agent_name, linked_request_id=rid)
    except rs.ReviewError as exc:
        raise ToolError(str(exc)) from None
    rs.add_items(batch, valid)
    ctx.touch(subject_id=subject.id, batch_id=batch.id, request_id=rid)
    if rid:
        srs.add_system_note(rid, f'{ctx.agent_name} created review batch #{batch.id} '
                                 f'"{batch.title}" with {len(valid)} items for an admin to check.',
                            meta={'review_batch_id': batch.id})
    return {'batch_id': batch.id, 'saved': len(valid), 'problems': problems, 'url': _review_url(batch.id)}


@tool('add_review_items',
      'Add more items to one of your review batches (same format as create_review_batch).',
      _schema({
          'batch_id': {'type': 'integer'},
          'items': {'type': 'array', 'items': _ITEM_SCHEMA},
          'reason': {'type': 'string'},
      }, ('batch_id', 'items')),
      title='Add review items', writes=True)
def add_review_items(args, ctx):
    from app import review_service as rs
    batch = _batch_arg(args)
    if batch.status == 'archived':
        raise ToolError('this batch is archived; create a new one')
    items = args.get('items')
    if not isinstance(items, list) or not items:
        raise ToolError('items must be a non-empty list')
    valid, problems = rs.prepare(batch.subject_id, items)
    if not valid:
        raise ToolError('no item could be saved: ' + _problems_text(problems))
    rs.add_items(batch, valid)
    ctx.touch(subject_id=batch.subject_id, batch_id=batch.id, request_id=batch.linked_request_id)
    return {'batch_id': batch.id, 'saved': len(valid), 'problems': problems}


@tool('list_review_batches',
      'Review batches with their progress (pending / applied / rejected counts).',
      _schema({
          'subject': {'type': 'string'},
          'status': {'type': 'string', 'enum': ['open', 'done', 'archived']},
          'source': {'type': 'string', 'enum': ['agent', 'import']},
          'updated_since': {'type': 'string', 'description': 'ISO 8601 UTC, e.g. 2026-11-01T09:00:00Z'},
          'limit': {'type': 'integer', 'default': 50},
      }),
      title='List review batches')
def list_review_batches(args, ctx):
    from app import review_service as rs
    from app.models import ReviewBatch
    query = ReviewBatch.query
    if args.get('subject'):
        query = query.filter(ReviewBatch.subject_id == _str_arg(args, 'subject').upper())
    if args.get('status'):
        query = query.filter(ReviewBatch.status == _str_arg(args, 'status'))
    if args.get('source'):
        query = query.filter(ReviewBatch.source == _str_arg(args, 'source'))
    since = parse_since(args.get('updated_since'))
    if since:
        query = query.filter(ReviewBatch.updated_at >= since)
    limit = _int_arg(args, 'limit', 50, minimum=1, maximum=500)
    batches = query.order_by(ReviewBatch.updated_at.desc()).limit(limit).all()
    counts = rs.item_counts([b.id for b in batches])
    return {'batches': [rs.batch_payload(b, counts[b.id]) for b in batches]}


@tool('get_review_batch',
      'One review batch with every item: proposed changes, current values, status, '
      'what was applied, and the admin\'s note. Read rejections to learn.',
      _schema({
          'batch_id': {'type': 'integer'},
          'status': {'type': 'string', 'enum': ['pending', 'skipped', 'applied', 'done', 'rejected']},
      }, ('batch_id',)),
      title='Get a review batch')
def get_review_batch(args, ctx):
    from app import question_edit_service as qes, review_service as rs
    from app.models import ReviewItem
    batch = _batch_arg(args)
    query = ReviewItem.query.filter_by(batch_id=batch.id)
    if args.get('status'):
        query = query.filter(ReviewItem.status == _str_arg(args, 'status'))
    items = query.order_by(ReviewItem.position, ReviewItem.id).all()
    taxonomy = qes.Taxonomy.load(batch.subject_id)
    ids = [i.question_id for i in items if i.question_id]
    questions = {q.id: q for q in Question.query.filter(Question.id.in_(ids)).all()} if ids else {}
    out = []
    for item in items:
        q = questions.get(item.question_id)
        if q is not None and q.subject != batch.subject_id:
            q = None
        out.append(rs.item_payload(item, question=q, taxonomy=taxonomy, mode='en'))
    return {'batch': rs.batch_payload(batch), 'items': out}


# ---------------------------------------------------------------- requests

MAX_TEXT_ATTACHMENT = 200_000
MAX_BLOB_ATTACHMENT = 10 * 1024 * 1024
_TEXT_TYPES = ('text/', 'application/json', 'application/xml', 'application/x-yaml')

_ATTACHMENT_SCHEMA = {
    'type': 'array',
    'items': {
        'type': 'object',
        'properties': {
            'name': {'type': 'string'},
            'mime': {'type': 'string'},
            'data_base64': {'type': 'string'},
        },
        'required': ['name', 'data_base64'],
    },
}


def _request(args):
    from app.models import SupportRequest
    return db.session.get(SupportRequest, _request_id_arg(args, required=True))


def _agent_files(args):
    raw = args.get('attachments') or []
    if not isinstance(raw, list):
        raise ToolError('attachments must be a list')
    max_count = int(current_app.config.get('REQUESTS_MAX_ATTACHMENTS_PER_MESSAGE', 10) or 10)
    max_bytes = int(current_app.config.get('REQUESTS_MAX_ATTACHMENT_MB', 20) or 20) * 1024 * 1024
    files = []
    for n, item in enumerate(raw):
        if not isinstance(item, dict) or not item.get('name'):
            raise ToolError(f'attachment #{n} needs a name and data_base64')
        try:
            data = base64.b64decode(str(item.get('data_base64') or ''), validate=True)
        except ValueError:
            raise ToolError(f'attachment #{n} ({item["name"]}): data_base64 is not valid base64') from None
        files.append({'name': str(item['name']), 'data': data, 'mime': item.get('mime')})
    from app import support_requests_service as srs
    try:
        srs.check_files(files, max_count=max_count, max_bytes=max_bytes)
    except srs.RequestError as exc:
        raise ToolError(str(exc)) from None
    return files


def _reporter_info(req, subjects):
    from app import authz
    from app.models import User
    user = db.session.get(User, req.reporter_id) if req.reporter_id else None
    if user is None:
        return None
    levels = {sid: authz.effective_level(user, sid) for sid in sorted(subjects)}
    return {
        'id': user.id,
        'name': user.display_name or user.username,
        'is_super_admin': authz.is_super_admin(user),
        'is_subject_admin': bool(subjects) and all(authz.has_level(user, sid, 'admin') for sid in subjects),
        'levels': levels,
    }


@tool('list_requests',
      'Issue threads. needs_agent=true lists the ones waiting for you, oldest first: an admin '
      'approved your plan, or a person wrote last on an open / in-progress thread. Poll this first.',
      _schema({
          'needs_agent': {'type': 'boolean'},
          'status': {'type': 'string', 'enum': ['active', 'all', 'open', 'in_progress', 'needs_info',
                                                'awaiting_approval', 'approved', 'resolved', 'closed']},
          'category': {'type': 'string', 'enum': ['question', 'general', 'app_bug', 'feature']},
          'subject': {'type': 'string'},
          'updated_since': {'type': 'string', 'description': 'ISO 8601 UTC'},
          'limit': {'type': 'integer', 'default': 50},
      }),
      title='List requests')
def list_requests(args, ctx):
    from sqlalchemy import and_, or_
    from app import support_requests_service as srs
    from app.models import SupportRequest as R, SupportRequestQuestion
    query = R.query
    needs = bool(args.get('needs_agent'))
    status = _str_arg(args, 'status') or ('all' if needs else 'active')
    if status == 'active':
        query = query.filter(R.status.in_(srs.ACTIVE_STATUSES))
    elif status in srs.STATUSES:
        query = query.filter(R.status == status)
    if needs:
        query = query.filter(or_(R.status == 'approved',
                                 and_(R.status.in_(('open', 'in_progress')),
                                      R.last_actor_kind.in_(('user', 'admin')))))
    if args.get('category'):
        query = query.filter(R.category == _str_arg(args, 'category'))
    if args.get('subject'):
        sid = _str_arg(args, 'subject').upper()
        query = query.filter(or_(R.subject_id == sid, R.id.in_(
            db.session.query(SupportRequestQuestion.request_id).filter(SupportRequestQuestion.subject_id == sid))))
    since = parse_since(args.get('updated_since'))
    if since:
        query = query.filter(R.last_message_at >= since)
    limit = _int_arg(args, 'limit', 50, minimum=1, maximum=500)
    order = R.last_message_at.asc() if needs else R.last_message_at.desc()
    rows = query.order_by(order, R.id).limit(limit).all()
    ids = [r.id for r in rows]
    links = srs.linked_questions(ids)
    names = srs.display_names(r.reporter_id for r in rows)
    return {'requests': [srs.request_payload(r, links=links[r.id], names=names) for r in rows]}


@tool('get_request',
      'One request with every message (oldest first), attachment names, linked questions, the '
      'reporter\'s access level, and the approval state. Use since_message_id to read only new messages.',
      _schema({
          'request_id': {'type': 'integer'},
          'since_message_id': {'type': 'integer'},
      }, ('request_id',)),
      title='Get a request')
def get_request(args, ctx):
    from app import support_requests_service as srs
    from app.models import ReviewBatch
    req = _request(args)
    messages, attachments = srs.thread(req)
    since = _int_arg(args, 'since_message_id', 0, minimum=0)
    names = srs.display_names({m.author_user_id for m in messages} | {req.reporter_id})
    links = srs.linked_questions([req.id])[req.id]
    pending = srs.pending_approval(req)
    decisions = [m for m in messages if m.kind in ('approval', 'rejection')]
    last = decisions[-1] if decisions else None
    batches = ReviewBatch.query.filter_by(linked_request_id=req.id).order_by(ReviewBatch.id).all()
    return {
        'request': srs.request_payload(req, links=links, names=names),
        'reporter': _reporter_info(req, srs.request_subjects(req)),
        'approval': {
            'pending_request_message_id': pending.id if pending else None,
            'last_decision': ({'kind': last.kind, 'by': names.get(last.author_user_id), 'note': last.body or '',
                               'message_id': last.id, 'answers': srs.load_meta(last.meta).get('approval_request_id')}
                              if last else None),
        },
        'review_batches': [{'id': b.id, 'title': b.title, 'subject': b.subject_id, 'status': b.status}
                           for b in batches],
        'messages': [srs.message_payload(m, attachments.get(m.id, []), names)
                     for m in messages if m.id > since],
    }


@tool('get_attachment',
      'A file from a request message. Images come back as image content (longest edge cut to '
      'max_dim); text files as text; anything else as a base64 blob (10 MB at most).',
      _schema({
          'attachment_id': {'type': 'integer'},
          'max_dim': {'type': 'integer', 'description': 'Image size limit in pixels (default LLM_IMAGE_MAX_DIM).'},
      }, ('attachment_id',)),
      title='Get an attachment')
def get_attachment(args, ctx):
    from app import support_requests_service as srs
    from app.models import SupportRequestAttachment
    att = db.session.get(SupportRequestAttachment, _int_arg(args, 'attachment_id'))
    if att is None:
        raise ToolError(f'attachment not found: {args.get("attachment_id")}')
    path = srs.attachment_path(att)
    if not path or not os.path.isfile(path):
        raise ToolError('the attachment file is missing on disk')
    info = {'attachment_id': att.id, 'request_id': att.request_id, 'name': att.original_name,
            'mime': att.mime, 'size': att.size}
    mime = (att.mime or '').lower()
    if mime.startswith('image/'):
        max_dim = _int_arg(args, 'max_dim', int(current_app.config.get('LLM_IMAGE_MAX_DIM', 1600) or 1600),
                           minimum=64, maximum=4096)
        try:
            data, out_mime = encode_image(path, max_dim)
        except Exception as exc:
            raise ToolError(f'could not read the image: {exc}') from None
        return ToolResult([{'type': 'text', 'text': f'{att.original_name} ({att.size} bytes)'},
                           {'type': 'image', 'data': data, 'mimeType': out_mime}], info)
    if mime.startswith(_TEXT_TYPES) or mime in _TEXT_TYPES:
        with open(path, 'rb') as fh:
            raw = fh.read(MAX_TEXT_ATTACHMENT + 1)
        text = raw[:MAX_TEXT_ATTACHMENT].decode('utf-8', errors='replace')
        if len(raw) > MAX_TEXT_ATTACHMENT:
            text += '\n[... cut]'
        return ToolResult([{'type': 'text', 'text': text}], info)
    if att.size > MAX_BLOB_ATTACHMENT:
        raise ToolError(f'{att.original_name} is {att.size} bytes; only files up to 10 MB are sent')
    with open(path, 'rb') as fh:
        blob = base64.b64encode(fh.read()).decode('ascii')
    return ToolResult([{'type': 'resource', 'resource': {
        'uri': f'oqb://requests/{att.request_id}/attachments/{att.id}',
        'mimeType': att.mime or 'application/octet-stream', 'blob': blob}}], info)


@tool('post_message',
      'Reply in a request thread as the agent. Optionally set the status in the same call '
      '(needs_info when you ask the reporter something, resolved when done) and attach files.',
      _schema({
          'request_id': {'type': 'integer'},
          'body': {'type': 'string', 'description': 'Plain text; **bold**, `code`, ```blocks``` and links are shown.'},
          'status': {'type': 'string', 'enum': ['open', 'in_progress', 'needs_info', 'resolved', 'closed']},
          'attachments': _ATTACHMENT_SCHEMA,
          'reason': {'type': 'string'},
      }, ('request_id', 'body')),
      title='Post a message', writes=True)
def post_message(args, ctx):
    from app import support_requests_service as srs
    req = _request(args)
    status = _str_arg(args, 'status')
    if status and not srs.can_set_status('agent', req.status, status) and status != req.status:
        raise ToolError(f'the agent cannot set {status} from {req.status}')
    files = _agent_files(args)
    try:
        msg = srs.add_message(req, author_kind='agent', agent_name=ctx.agent_name,
                              body=_str_arg(args, 'body'), files=files, actor_kind='agent')
        if status and status != req.status:
            srs.set_status(req, status, role='agent', agent_name=ctx.agent_name)
    except srs.RequestError as exc:
        raise ToolError(str(exc)) from None
    ctx.touch(request_id=req.id, subject_id=req.subject_id)
    return {'message_id': msg.id, 'status': req.status}


@tool('set_request_status',
      'Change a request\'s status as the agent (not awaiting_approval: use request_approval; '
      'not approved: only an admin approves).',
      _schema({
          'request_id': {'type': 'integer'},
          'status': {'type': 'string', 'enum': ['open', 'in_progress', 'needs_info', 'resolved', 'closed']},
          'note': {'type': 'string'},
          'reason': {'type': 'string'},
      }, ('request_id', 'status')),
      title='Set request status', writes=True)
def set_request_status(args, ctx):
    from app import support_requests_service as srs
    req = _request(args)
    try:
        msg = srs.set_status(req, _str_arg(args, 'status', required=True), role='agent',
                             agent_name=ctx.agent_name, note=_str_arg(args, 'note', limit=srs.MAX_BODY) or '')
    except srs.RequestError as exc:
        raise ToolError(str(exc)) from None
    ctx.touch(request_id=req.id, subject_id=req.subject_id)
    return {'message_id': msg.id, 'status': req.status}


@tool('request_approval',
      'Ask the subject admins to approve a plan before you change the library (status becomes '
      'awaiting_approval). Then stop; on a later run, get_request shows approval.last_decision. '
      'Approved: carry out exactly that plan. Rejected: read the note and reply or propose again.',
      _schema({
          'request_id': {'type': 'integer'},
          'plan': {'type': 'string', 'description': 'What you will change, on which QIDs, and why.'},
          'actions': {'description': 'Optional machine-readable form of the plan (kept in the message meta).'},
          'reason': {'type': 'string'},
      }, ('request_id', 'plan')),
      title='Request approval', writes=True)
def request_approval(args, ctx):
    from app import support_requests_service as srs
    req = _request(args)
    actions = args.get('actions')
    try:
        msg = srs.request_approval(req, agent_name=ctx.agent_name,
                                   plan=_str_arg(args, 'plan', required=True, limit=srs.MAX_BODY),
                                   meta={'actions': actions} if actions is not None else None)
    except srs.RequestError as exc:
        raise ToolError(str(exc)) from None
    ctx.touch(request_id=req.id, subject_id=req.subject_id)
    return {'message_id': msg.id, 'status': req.status}


@tool('link_review_batch',
      'Link a review batch to a request (shown on both pages, with a note in the thread).',
      _schema({
          'request_id': {'type': 'integer'},
          'batch_id': {'type': 'integer'},
          'reason': {'type': 'string'},
      }, ('request_id', 'batch_id')),
      title='Link a review batch', writes=True)
def link_review_batch(args, ctx):
    from app import support_requests_service as srs
    req = _request(args)
    batch = _batch_arg(args)
    batch.linked_request_id = req.id
    srs.add_system_note(req.id, f'Review batch #{batch.id} "{batch.title}" ({batch.subject_id}) is linked '
                                f'to this request.', meta={'review_batch_id': batch.id})
    ctx.touch(request_id=req.id, batch_id=batch.id, subject_id=batch.subject_id)
    return {'request_id': req.id, 'batch_id': batch.id}


# ---------------------------------------------------------------- library (write)

MAX_AGENT_FILE = 25 * 1024 * 1024
_AGENT_UPLOAD_EXT = ('png', 'jpg', 'jpeg', 'md')
_WRITE_COMMON = {
    'reason': {'type': 'string', 'description': 'Why (required; shown in the action log and the thread).'},
    'request_id': {'type': 'integer', 'description': 'The request this work answers (a note is posted there).'},
    'batch_id': {'type': 'integer', 'description': 'The review batch this work belongs to, if any.'},
}
_QUESTION_REF = {
    'qid': {'type': 'string'},
    'question_id': {'type': 'integer'},
}


def _write_schema(properties, required=()):
    """Schema of a write tool on one question (``qid`` or ``question_id``)."""
    return _schema(dict(_QUESTION_REF, **properties, **_WRITE_COMMON), tuple(required) + ('reason',))


def _reason(args) -> str:
    reason = _str_arg(args, 'reason', limit=500)
    if not reason:
        raise ToolError('reason is required for every write')
    return reason


def _note(ctx, tool_name, reason, target=''):
    text = f'{ctx.agent_name} {tool_name}' + (f' {target}' if target else '') + f': {reason}'
    return text[:300]


def _decode(item, n, *, what='file'):
    try:
        data = base64.b64decode(str(item.get('data_base64') or ''), validate=True)
    except ValueError:
        raise ToolError(f'{what} #{n}: data_base64 is not valid base64') from None
    if not data:
        raise ToolError(f'{what} #{n} is empty')
    if len(data) > MAX_AGENT_FILE:
        raise ToolError(f'{what} #{n} is larger than 25 MB')
    return data


def _backup_to_thread(request_id, ctx, question, label, files):
    """Attach the files about to be overwritten to a system line in the
    request thread, so an admin can put them back."""
    from app import support_requests_service as srs
    from app.models import SupportRequest
    req = db.session.get(SupportRequest, request_id)
    if req is None or not files:
        return None
    return srs.add_message(req, author_kind='system', kind='message',
                           body=f'{ctx.agent_name} is replacing {question.qid} {label}. '
                                f'The previous files are attached.',
                           meta={'backup_of': question.qid, 'slot': label}, files=files)


@tool('update_question_fields',
      'Change tag and detail fields of one question (same fields and value rules as review items). '
      'Send old values: a field that no longer holds its old value is refused unless confirm_stale=true. '
      'Takes a restore point for tag fields.',
      _write_schema({
          'changes': _CHANGE_SCHEMA,
          'confirm_stale': {'type': 'boolean'},
      }, ('changes',)),
      title='Update question fields', writes=True, thread_note=True)
def update_question_fields(args, ctx):
    from app import hierarchy, question_edit_service as qes
    reason = _reason(args)
    q = find_question(args)
    taxonomy = qes.Taxonomy.load(q.subject)
    current = qes.current_values(q)
    try:
        changes, warnings = qes.normalize_changes(args.get('changes'), taxonomy, current,
                                                  stem=hierarchy.is_stem(q))
        if not changes:
            raise ToolError('no changes given')
        result = qes.apply_fields(q, changes, taxonomy, action='agent-edit',
                                  note=_note(ctx, 'update_question_fields', reason, q.qid),
                                  confirm_stale=bool(args.get('confirm_stale')))
    except qes.StaleError as exc:
        raise ToolError(f'{exc}. Read the question again (get_question) or pass confirm_stale=true.') from None
    except qes.EditError as exc:
        raise ToolError(str(exc)) from None
    ctx.touch(subject_id=q.subject, question_id=q.id, qid=q.qid)
    return {
        'qid': q.qid,
        'changed': result['changed'],
        'before': {f: qes.label(f, result['before'][f], taxonomy) for f in result['changed']},
        'after': {f: qes.label(f, result['after'][f], taxonomy) for f in result['changed']},
        'warnings': warnings,
    }


@tool('rename_question',
      'Change a QID (its parts are renamed with it). rename_files=true (default) also renames the '
      'asset files. Ask for approval first.',
      _write_schema({
          'new_qid': {'type': 'string'},
          'rename_files': {'type': 'boolean', 'default': True},
      }, ('new_qid',)),
      title='Rename a question', writes=True, thread_note=True)
def rename_question(args, ctx):
    from app import library_ops_service as ops
    _reason(args)
    q = find_question(args)
    old_subject = q.subject
    try:
        result = ops.rename_question(q, _str_arg(args, 'new_qid', required=True),
                                     rename_files=args.get('rename_files', True) is not False)
    except ops.OpError as exc:
        raise ToolError(str(exc)) from None
    ctx.touch(subject_id=old_subject, question_id=q.id, qid=f'{result["old_qid"]} -> {result["new_qid"]}')
    result.pop('message', None)
    return result


@tool('set_parent',
      'Attach a question under a stem (its QID must already sit under the stem\'s token), or '
      'detach it (parent_qid null). Parts in a shared block follow the block. Ask for approval first.',
      _write_schema({
          'parent_qid': {'type': ['string', 'null']},
          'parent_id': {'type': ['integer', 'null']},
      }),
      title='Set a question\'s parent', writes=True, thread_note=True)
def set_parent(args, ctx):
    from app import library_ops_service as ops
    _reason(args)
    q = find_question(args)
    parent_id = None
    if args.get('parent_qid'):
        parent = Question.query.filter_by(qid=_str_arg(args, 'parent_qid')).first()
        if parent is None:
            raise ToolError(f'question not found: {args.get("parent_qid")}')
        parent_id = parent.id
    elif args.get('parent_id') is not None:
        parent_id = _int_arg(args, 'parent_id')
    try:
        result = ops.set_parent(q, parent_id)
    except ops.OpError as exc:
        raise ToolError(str(exc)) from None
    parent = db.session.get(Question, result['parent_id']) if result['parent_id'] else None
    ctx.touch(subject_id=q.subject, question_id=q.id, qid=q.qid)
    return {'qid': q.qid, 'parent_id': result['parent_id'], 'parent_qid': parent.qid if parent else None}


@tool('upload_asset',
      'Add files to a question slot: PNG / JPEG images become the next image parts; one Markdown '
      'file (.md) fills an empty Markdown slot. Ask for approval first.',
      _write_schema({
          'asset_type': {'type': 'string', 'enum': ['QUE', 'ANS', 'SOL']},
          'version': {'type': 'string', 'enum': list(VERSIONS)},
          'files': {'type': 'array', 'items': {'type': 'object', 'properties': {
              'name': {'type': 'string', 'description': 'File name with extension (.png, .jpg, .md).'},
              'data_base64': {'type': 'string'}}, 'required': ['name', 'data_base64']}},
      }, ('asset_type', 'version', 'files')),
      title='Upload question files', writes=True, thread_note=True)
def upload_asset(args, ctx):
    from app import library_ops_service as ops
    _reason(args)
    q = find_question(args)
    raw = args.get('files')
    if not isinstance(raw, list) or not raw:
        raise ToolError('files must be a non-empty list')
    uploads = []
    for n, item in enumerate(raw):
        name = str((item or {}).get('name') or '').strip()
        ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
        if ext not in _AGENT_UPLOAD_EXT:
            raise ToolError(f'file #{n}: only .png, .jpg, .jpeg and .md files can be uploaded')
        uploads.append(ops.BytesUpload(name, _decode(item, n)))
    try:
        result = ops.upload_assets(q, _str_arg(args, 'version', required=True).upper(),
                                   _str_arg(args, 'asset_type', required=True).upper(), uploads)
    except ops.OpError as exc:
        raise ToolError(str(exc)) from None
    if not result['uploaded']:
        raise ToolError('nothing was uploaded: ' + '; '.join(result['errors']))
    db.session.commit()
    ops.after_upload(result['uploaded'])
    ctx.touch(subject_id=q.subject, question_id=q.id, qid=q.qid)
    return {'qid': q.qid, 'errors': result['errors'],
            'uploaded': [{'asset_id': u['asset'].id, 'file_path': u['file_path'], 'format': u['file_format'],
                          'part_number': u['part_number']} for u in result['uploaded']]}


@tool('replace_asset',
      'Replace a slot\'s content: format IMG replaces every image part with the images given (in '
      'order); format MD overwrites (or creates) the Markdown file. Needs request_id: the old files '
      'are attached to that thread first. Ask for approval first.',
      _write_schema({
          'asset_type': {'type': 'string', 'enum': ['QUE', 'ANS', 'SOL']},
          'version': {'type': 'string', 'enum': list(VERSIONS)},
          'format': {'type': 'string', 'enum': ['IMG', 'MD']},
          'images': {'type': 'array', 'items': {'type': 'object', 'properties': {
              'data_base64': {'type': 'string'}}, 'required': ['data_base64']}},
          'markdown': {'type': 'string'},
      }, ('asset_type', 'version', 'format', 'request_id')),
      title='Replace question files', writes=True, thread_note=True)
def replace_asset(args, ctx):
    from PIL import Image
    from app import library_ops_service as ops
    _reason(args)
    q = find_question(args)
    rid = _request_id_arg(args, required=True)
    version = _str_arg(args, 'version', required=True).upper()
    asset_type = _str_arg(args, 'asset_type', required=True).upper()
    fmt = _str_arg(args, 'format', required=True).upper()
    if version not in VERSIONS or asset_type not in ('QUE', 'ANS', 'SOL'):
        raise ToolError('bad version or asset_type')
    label = f'{asset_type} {version} {fmt}'
    old = ops.slot_assets(q, asset_type, version, fmt)
    backups = []
    for a in old:
        data = ops.asset_bytes(a)
        if data:
            backups.append({'name': os.path.basename(a.file_path), 'data': data, 'mime': None})

    if fmt == 'IMG':
        raw = args.get('images')
        if not isinstance(raw, list) or not raw:
            raise ToolError('images must be a non-empty list')
        images = []
        for n, item in enumerate(raw):
            try:
                im = Image.open(io.BytesIO(_decode(item or {}, n, what='image')))
                im.load()
            except ToolError:
                raise
            except Exception:
                raise ToolError(f'image #{n} is not a readable image') from None
            images.append(im if im.mode in ('RGB', 'RGBA') else im.convert('RGBA' if 'A' in im.getbands() else 'RGB'))
        _backup_to_thread(rid, ctx, q, label, backups)
        try:
            result = ops.replace_images(q, asset_type, version, images)
        except ops.OpError as exc:
            raise ToolError(str(exc)) from None
        out = {'qid': q.qid, 'format': 'IMG', 'wrote': result['wrote'], 'deleted': result['deleted'],
               'file_paths': result['file_paths']}
    elif fmt == 'MD':
        markdown = args.get('markdown')
        if not isinstance(markdown, str) or not markdown.strip():
            raise ToolError('markdown text is required')
        try:
            if old:
                _backup_to_thread(rid, ctx, q, label, backups)
                size = ops.write_md(old[0], markdown)
                out = {'qid': q.qid, 'format': 'MD', 'asset_id': old[0].id, 'size_bytes': size, 'created': False}
            else:
                result = ops.upload_assets(q, version, asset_type,
                                           [ops.BytesUpload(f'{q.qid}.md', markdown.encode('utf-8'))])
                if not result['uploaded']:
                    raise ToolError('; '.join(result['errors']) or 'nothing was written')
                db.session.flush()
                u = result['uploaded'][0]
                out = {'qid': q.qid, 'format': 'MD', 'asset_id': u['asset'].id, 'created': True,
                       'size_bytes': len(markdown.encode('utf-8'))}
        except ops.OpError as exc:
            raise ToolError(str(exc)) from None
        db.session.commit()
    else:
        raise ToolError('format must be IMG or MD')
    ctx.touch(subject_id=q.subject, question_id=q.id, qid=q.qid, request_id=rid)
    return out


@tool('add_taxonomy_node',
      'Add a topic or chapter to a subject, or a subtopic / subchapter under parent_id. English '
      'name required, Chinese name optional. Takes a restore point. Ask for approval first.',
      _schema(dict({
          'kind': {'type': 'string', 'enum': ['topic', 'subtopic', 'chapter', 'subchapter']},
          'subject': {'type': 'string', 'description': 'For topic / chapter.'},
          'parent_id': {'type': 'integer', 'description': 'Topic id (subtopic) or chapter id (subchapter).'},
          'name': {'type': 'string'},
          'name_zh': {'type': 'string'},
          'hidden': {'type': 'boolean'},
      }, **_WRITE_COMMON), ('kind', 'name', 'reason')),
      title='Add a taxonomy node', writes=True, thread_note=True)
def add_taxonomy_node(args, ctx):
    from app import library_ops_service as ops
    reason = _reason(args)
    kind = _str_arg(args, 'kind', required=True)
    if kind in ('topic', 'chapter'):
        parent = _subject(args).id
    else:
        parent = _int_arg(args, 'parent_id')
        if parent is None:
            raise ToolError('parent_id is required for a subtopic or subchapter')
    name = _str_arg(args, 'name', required=True, limit=200)
    try:
        row, subject_id = ops.add_taxonomy_node(
            kind, parent, name, name_zh=_str_arg(args, 'name_zh', limit=200) or None,
            hidden=bool(args.get('hidden')), action='agent-edit',
            note=_note(ctx, 'add_taxonomy_node', reason, f'{kind} "{name}"'))
    except ops.OpError as exc:
        raise ToolError(str(exc)) from None
    ctx.touch(subject_id=subject_id, qid=f'{kind} "{row.name}"')
    return {'kind': kind, 'id': row.id, 'name': row.name, 'name_zh': row.name_zh, 'subject': subject_id}


@tool('rename_taxonomy_node',
      'Rename a topic, subtopic, chapter or subchapter (English and / or Chinese name; an empty '
      'name_zh clears it). Takes a restore point. Ask for approval first.',
      _schema(dict({
          'kind': {'type': 'string', 'enum': ['topic', 'subtopic', 'chapter', 'subchapter']},
          'id': {'type': 'integer'},
          'name': {'type': 'string'},
          'name_zh': {'type': 'string'},
      }, **_WRITE_COMMON), ('kind', 'id', 'reason')),
      title='Rename a taxonomy node', writes=True, thread_note=True)
def rename_taxonomy_node(args, ctx):
    from app import library_ops_service as ops
    reason = _reason(args)
    kind = _str_arg(args, 'kind', required=True)
    spec = ops.TAXONOMY_KINDS.get(kind)
    if spec is None:
        raise ToolError(f'unknown kind: {kind}')
    row = db.session.get(spec['model'], _int_arg(args, 'id'))
    if row is None:
        raise ToolError(f'{kind} not found: {args.get("id")}')
    updates = {}
    if 'name' in args:
        updates['name'] = _str_arg(args, 'name', limit=200)
    if 'name_zh' in args:
        updates['name_zh'] = _str_arg(args, 'name_zh', limit=200) or None
    if not updates:
        raise ToolError('give name and / or name_zh')
    old = {'name': row.name, 'name_zh': row.name_zh}
    try:
        subject_id = ops.update_taxonomy_node(kind, row, updates, action='agent-edit',
                                              note=_note(ctx, 'rename_taxonomy_node', reason, f'{kind} {row.id}'))
    except ops.OpError as exc:
        raise ToolError(str(exc)) from None
    ctx.touch(subject_id=subject_id, qid=f'{kind} "{old["name"]}"')
    return {'kind': kind, 'id': row.id, 'before': old, 'after': {'name': row.name, 'name_zh': row.name_zh},
            'subject': subject_id}


@tool('list_agent_actions',
      'Your past writes from the action log (newest first): tool, outcome, what it touched, reason.',
      _schema({
          'tool': {'type': 'string'},
          'request_id': {'type': 'integer'},
          'subject': {'type': 'string'},
          'since': {'type': 'string', 'description': 'ISO 8601 UTC'},
          'limit': {'type': 'integer', 'default': 50},
      }),
      title='List agent actions')
def list_agent_actions(args, ctx):
    from app.models import AgentActionLog as L
    query = L.query
    if args.get('tool'):
        query = query.filter(L.tool == _str_arg(args, 'tool'))
    if args.get('request_id') is not None:
        query = query.filter(L.request_id == _int_arg(args, 'request_id'))
    if args.get('subject'):
        query = query.filter(L.subject_id == _str_arg(args, 'subject').upper())
    since = parse_since(args.get('since'))
    if since:
        query = query.filter(L.created_at >= since)
    limit = _int_arg(args, 'limit', 50, minimum=1, maximum=500)
    rows = query.order_by(L.id.desc()).limit(limit).all()
    return {'actions': [{
        'id': r.id, 'at': utc_iso(r.created_at), 'agent': r.agent_name, 'tool': r.tool, 'outcome': r.outcome,
        'subject': r.subject_id, 'question_id': r.question_id, 'request_id': r.request_id,
        'batch_id': r.batch_id, 'reason': r.reason, 'summary': r.summary, 'error': r.error,
    } for r in rows]}
