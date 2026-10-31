"""Question field writes shared by the Edit modal (``POST /admin/questions/
<id>/update``), Review apply and the agent endpoint (docs/modules/review.md).

* ``FIELDS`` is the supported field list. Taxonomy fields hold ids;
  ``minor_topics`` / ``subtopics`` hold sorted id lists.
* :func:`write_values` is the one ORM writer and keeps the Edit modal's rules:
  stems never take tag fields, a new major topic clears the major subtopic
  (and a new chapter the subchapter), a subtopic / subchapter outside its
  parent is dropped.
* :class:`Taxonomy` resolves ids or names (English or Chinese, through
  ``i18n.build_name_index``) inside one subject.
* :func:`normalize_changes`, :func:`plan_after` and :func:`stale_fields` are
  pure: they build and preview a change list without touching the DB.
* :func:`apply_fields` (Review, agent) = stale check + restore point +
  :func:`write_values`.

Callers authorise. The Edit modal captures its own restore point before
:func:`write_values`; nothing here commits.
"""
from __future__ import annotations

from collections import OrderedDict

from app import db
from app.i18n import t as _t

LEVELS = (1, 2, 3)
Q_TYPES = ('MC', 'CQ')
SECTION_MAX = 50

# field -> spec. ``attr`` is the Question column (or relationship), ``kind``
# the value shape, ``leaf`` = never written on a stem, ``snapshot`` = a
# restore-point field (subject_snapshot.QUESTION_FIELDS / LINK_FIELDS).
FIELDS = OrderedDict([
    ('level', {'kind': 'level', 'attr': 'level', 'leaf': True, 'snapshot': True, 'label': 'Level'}),
    ('q_type', {'kind': 'q_type', 'attr': 'q_type', 'leaf': True, 'snapshot': True, 'label': 'Type'}),
    ('section', {'kind': 'text', 'attr': 'section', 'leaf': False, 'snapshot': True, 'label': 'Section'}),
    ('description', {'kind': 'text', 'attr': 'description', 'leaf': False, 'snapshot': False,
                     'label': 'Description'}),
    ('answer', {'kind': 'text', 'attr': 'answer', 'leaf': False, 'snapshot': False, 'label': 'Answer'}),
    ('comment', {'kind': 'text', 'attr': 'comment', 'leaf': False, 'snapshot': False, 'label': 'Comment'}),
    ('correct_percentage', {'kind': 'pct', 'attr': 'correct_percentage', 'leaf': False, 'snapshot': True,
                            'label': 'Correct %'}),
    ('marks', {'kind': 'marks', 'attr': 'marks', 'leaf': True, 'snapshot': True, 'label': 'Marks'}),
    ('major_topic', {'kind': 'ref', 'tax': 'topics', 'attr': 'major_topic_id', 'leaf': True,
                     'snapshot': True, 'label': 'Major topic'}),
    ('major_subtopic', {'kind': 'ref', 'tax': 'subtopics', 'attr': 'major_subtopic_id', 'leaf': True,
                        'snapshot': True, 'parent': 'major_topic', 'label': 'Major subtopic'}),
    ('minor_topics', {'kind': 'refs', 'tax': 'topics', 'attr': 'minor_topics', 'leaf': True,
                      'snapshot': True, 'label': 'Minor topics'}),
    ('subtopics', {'kind': 'refs', 'tax': 'subtopics', 'attr': 'subtopics', 'leaf': True,
                   'snapshot': True, 'label': 'Subtopics'}),
    ('chapter', {'kind': 'ref', 'tax': 'chapters', 'attr': 'chapter_id', 'leaf': True,
                 'snapshot': True, 'label': 'Chapter'}),
    ('subchapter', {'kind': 'ref', 'tax': 'subchapters', 'attr': 'subchapter_id', 'leaf': True,
                    'snapshot': True, 'parent': 'chapter', 'label': 'Subchapter'}),
])

# Fields the Auto Tag correction log (tag_corrections, subject_ai_service)
# understands, under the same names.
CORRECTION_FIELDS = ('major_topic', 'major_subtopic', 'minor_topics', 'subtopics', 'chapter',
                     'subchapter', 'q_type', 'level', 'section', 'marks')

ALIASES = {
    'major_topic_id': 'major_topic', 'major_subtopic_id': 'major_subtopic',
    'minor_topic_ids': 'minor_topics', 'subtopic_ids': 'subtopics',
    'chapter_id': 'chapter', 'subchapter_id': 'subchapter', 'type': 'q_type',
    'question_type': 'q_type', 'topic': 'major_topic', 'subtopic': 'major_subtopic',
}

PARENT_OF = {'subtopics': 'topics', 'subchapters': 'chapters'}


class EditError(ValueError):
    """A change cannot be applied as given (unknown field, bad value)."""


class StaleError(EditError):
    """The question no longer holds a change's ``old`` value."""

    def __init__(self, fields):
        self.fields = list(fields)
        super().__init__(_t('Changed since the proposal: {fields}', fields=', '.join(self.fields)))


def field_name(raw) -> str | None:
    name = str(raw or '').strip().lower()
    name = ALIASES.get(name, name)
    return name if name in FIELDS else None


def is_snapshot_field(field) -> bool:
    return bool(FIELDS.get(field, {}).get('snapshot'))


# ---------------------------------------------------------------- taxonomy

class Taxonomy:
    """One subject's topics / subtopics / chapters / subchapters, for id
    checks, name lookup and display. Nodes are ORM rows or any objects with
    ``id``, ``name``, ``name_zh`` (+ ``topic_id`` / ``chapter_id``)."""

    def __init__(self, topics=(), subtopics=(), chapters=(), subchapters=()):
        self.nodes = {
            'topics': {n.id: n for n in topics},
            'subtopics': {n.id: n for n in subtopics},
            'chapters': {n.id: n for n in chapters},
            'subchapters': {n.id: n for n in subchapters},
        }
        self.parent = {
            'subtopics': {n.id: getattr(n, 'topic_id', None) for n in subtopics},
            'subchapters': {n.id: getattr(n, 'chapter_id', None) for n in subchapters},
        }
        self._indexes = {}

    @classmethod
    def load(cls, subject_id):
        from app.models import Chapter, Subchapter, Subtopic, Topic
        topics = Topic.query.filter_by(subject_id=subject_id).all()
        chapters = Chapter.query.filter_by(subject_id=subject_id).all()
        topic_ids = [t.id for t in topics]
        chapter_ids = [c.id for c in chapters]
        subtopics = (Subtopic.query.filter(Subtopic.topic_id.in_(topic_ids)).all()
                     if topic_ids else [])
        subchapters = (Subchapter.query.filter(Subchapter.chapter_id.in_(chapter_ids)).all()
                       if chapter_ids else [])
        return cls(topics, subtopics, chapters, subchapters)

    def _index(self, kind, parent_id=None):
        from app.i18n import build_name_index
        key = (kind, parent_id)
        if key not in self._indexes:
            nodes = self.nodes[kind].values()
            if parent_id is not None:
                nodes = [n for n in nodes if self.parent[kind].get(n.id) == parent_id]
            self._indexes[key] = build_name_index(nodes)
        return self._indexes[key]

    def resolve(self, kind, raw, parent_id=None):
        """Id for ``raw`` (an id, a numeric string, a name in either language,
        or ``{"id"|"name": ...}``) inside this subject; ``None`` for blank.
        A name is looked up under ``parent_id`` first. Raises EditError."""
        from app.i18n import lookup_name
        if isinstance(raw, dict):
            raw = raw.get('id') if raw.get('id') not in (None, '') else raw.get('name')
        if raw is None or isinstance(raw, bool):
            return None
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            if int(raw) == 0:
                return None
            if int(raw) in self.nodes[kind]:
                return int(raw)
            raise EditError(_t('id {id} is not in this subject', id=int(raw)))
        text = str(raw).strip()
        if not text:
            return None
        if text.isdigit():
            if int(text) in self.nodes[kind]:
                return int(text)
            raise EditError(_t('id {id} is not in this subject', id=text))
        node = None
        if parent_id is not None and kind in PARENT_OF:
            node = lookup_name(self._index(kind, parent_id), text)
        if node is None:
            node = lookup_name(self._index(kind), text)
        if node is None:
            raise EditError(_t('nothing named "{name}" in this subject', name=text))
        return node.id

    def name(self, kind, node_id):
        node = self.nodes.get(kind, {}).get(node_id)
        return node.name if node is not None else None

    def label(self, kind, node_id, mode='en'):
        from app.i18n import display_name
        node = self.nodes.get(kind, {}).get(node_id)
        if node is None:
            return None if node_id is None else f'#{node_id}'
        return display_name(node, mode)


# ---------------------------------------------------------------- values (pure)

def current_values(question) -> dict:
    """Every ``FIELDS`` value of ``question`` (ids for taxonomy)."""
    out = {}
    for f, spec in FIELDS.items():
        if spec['kind'] == 'refs':
            out[f] = sorted({n.id for n in getattr(question, spec['attr'])})
        else:
            out[f] = getattr(question, spec['attr'])
    return out


def _norm_text(raw, limit=None):
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if limit and len(text) > limit:
        raise EditError(_t('longer than {n} characters', n=limit))
    return text


def normalize_value(field, raw, taxonomy: Taxonomy | None = None, parent_id=None):
    """``raw`` (agent / JSON / form value) -> the stored value of ``field``.
    Raises EditError. Pure apart from ``taxonomy``."""
    spec = FIELDS[field]
    kind = spec['kind']
    try:
        if kind == 'level':
            if raw in (None, ''):
                return None
            value = int(str(raw).strip())
            if value not in LEVELS:
                raise EditError(_t('level must be 1, 2 or 3'))
            return value
        if kind == 'q_type':
            text = _norm_text(raw)
            if text is None:
                return None
            text = text.upper()
            if text not in Q_TYPES:
                raise EditError(_t('type must be MC or CQ'))
            return text
        if kind == 'text':
            return _norm_text(raw, SECTION_MAX if field == 'section' else None)
        if kind == 'pct':
            if raw in (None, ''):
                return None
            value = int(str(raw).strip())
            if not 0 <= value <= 100:
                raise EditError(_t('correct % must be 0 to 100'))
            return value
        if kind == 'marks':
            from app.utils import parse_marks
            return parse_marks(raw)
    except EditError:
        raise
    except (TypeError, ValueError) as exc:
        raise EditError(str(exc) or f'bad value for {field}') from None
    if taxonomy is None:
        raise EditError('taxonomy not loaded')
    if kind == 'ref':
        return taxonomy.resolve(spec['tax'], raw, parent_id)
    if kind == 'refs':
        if raw in (None, ''):
            return []
        items = raw if isinstance(raw, (list, tuple, set)) else [p for p in str(raw).split(',')]
        ids = set()
        for item in items:
            node_id = taxonomy.resolve(spec['tax'], item)
            if node_id is not None:
                ids.add(node_id)
        return sorted(ids)
    raise EditError(f'unsupported field {field}')


def values_equal(field, a, b) -> bool:
    if FIELDS[field]['kind'] == 'refs':
        return sorted(set(a or ())) == sorted(set(b or ()))
    if isinstance(a, str) and isinstance(b, str):
        return a.strip() == b.strip()
    return a == b


def _raw_changes(raw):
    """Accept ``[{field, old?, new}]``, ``{field: new}`` or
    ``{field: {old?, new}}``; yield ``(field_raw, entry)``."""
    if isinstance(raw, dict):
        for key, value in raw.items():
            if isinstance(value, dict) and 'new' in value:
                yield key, dict(value, field=key)
            else:
                yield key, {'field': key, 'new': value}
    elif isinstance(raw, (list, tuple)):
        for entry in raw:
            if not isinstance(entry, dict):
                raise EditError(_t('each change must be an object with "field" and "new"'))
            yield entry.get('field'), entry
    else:
        raise EditError(_t('changes must be a list or an object'))


def normalize_changes(raw, taxonomy: Taxonomy, current: dict, *, stem=False):
    """Raw change list -> ``([{field, old, new}], warnings)``. ``old`` falls
    back to ``current`` when missing or unresolvable. Parent checks use the
    new parent when the same list changes it. Raises EditError. Pure."""
    entries = OrderedDict()
    for field_raw, entry in _raw_changes(raw):
        f = field_name(field_raw)
        if f is None:
            raise EditError(_t('unknown field "{field}"', field=field_raw))
        if f in entries:
            raise EditError(_t('field "{field}" listed twice', field=f))
        if 'new' not in entry:
            raise EditError(_t('{field}: missing "new"', field=f))
        if stem and FIELDS[f]['leaf']:
            raise EditError(_t('{field}: a question with parts cannot hold this (set it on the parts)', field=f))
        entries[f] = entry
    if not entries:
        raise EditError(_t('no changes'))
    warnings = []
    out = []
    new_parent = {}
    for f in FIELDS:  # parents before children
        if f not in entries:
            continue
        entry = entries[f]
        spec = FIELDS[f]
        parent_field = spec.get('parent')
        parent_id = None
        if parent_field:
            parent_id = new_parent.get(parent_field, current.get(parent_field))
        try:
            new = normalize_value(f, entry.get('new'), taxonomy, parent_id)
        except EditError as exc:
            raise EditError(f'{f}: {exc}') from None
        if parent_field and new is not None:
            owner = taxonomy.parent[spec['tax']].get(new)
            if parent_id is None or owner != parent_id:
                raise EditError(_t('{field}: not under {parent} "{name}"', field=f, parent=parent_field,
                                   name=taxonomy.name(PARENT_OF[spec['tax']], parent_id) or '-'))
        if spec['kind'] == 'ref' and f in ('major_topic', 'chapter'):
            new_parent[f] = new
        old = current.get(f)
        if 'old' in entry:
            try:
                old = normalize_value(f, entry.get('old'), taxonomy,
                                      current.get(parent_field) if parent_field else None)
            except EditError:
                warnings.append(_t('{field}: old value "{old}" not recognised; the current value is used',
                                   field=f, old=entry.get('old')))
                old = current.get(f)
        out.append({'field': f, 'old': old, 'new': new})
    return out, warnings


def plan_after(current: dict, values: dict, taxonomy: Taxonomy | None = None, *, stem=False) -> dict:
    """Values after :func:`write_values` would run on a question holding
    ``current``. Mirrors its rules (stems, parent clearing). Pure."""
    after = {f: (list(v) if isinstance(v, (list, tuple)) else v) for f, v in current.items()}
    for f in ('level', 'q_type', 'marks'):
        if f in values and not stem:
            after[f] = values[f]
    for f in ('section', 'description', 'answer', 'comment', 'correct_percentage'):
        if f in values:
            after[f] = values[f]
    if stem:
        return after
    for parent, child, kind in (('major_topic', 'major_subtopic', 'subtopics'),
                                ('chapter', 'subchapter', 'subchapters')):
        if parent in values:
            if values[parent] != after.get(parent):
                after[child] = None
            after[parent] = values[parent]
        if child in values:
            cid = values[child]
            owner = taxonomy.parent[kind].get(cid) if (taxonomy and cid) else None
            after[child] = cid if (cid and after.get(parent) and owner == after.get(parent)) else None
    for f in ('minor_topics', 'subtopics'):
        if f in values:
            after[f] = sorted(set(values[f] or ()))
    return after


def stale_fields(changes, current: dict) -> list:
    """Fields whose current value is no longer the change's ``old``. Pure."""
    return [c['field'] for c in changes
            if c['field'] in FIELDS and not values_equal(c['field'], current.get(c['field']), c.get('old'))]


def changed_fields(before: dict, after: dict) -> list:
    """``FIELDS`` names whose value differs between two value dicts. Pure."""
    return [f for f in FIELDS if f in before and f in after and not values_equal(f, before[f], after[f])]


def label(field, value, taxonomy: Taxonomy | None = None, mode='en'):
    """Display text of a stored value ('' for empty)."""
    spec = FIELDS.get(field)
    if spec is None or value is None or value == []:
        return ''
    if spec['kind'] == 'ref':
        return (taxonomy.label(spec['tax'], value, mode) if taxonomy else str(value)) or ''
    if spec['kind'] == 'refs':
        names = [(taxonomy.label(spec['tax'], v, mode) if taxonomy else str(v)) or '' for v in value]
        return ', '.join(n for n in names if n)
    return str(value)


def correction_labels(values: dict, taxonomy: Taxonomy, fields) -> dict:
    """``{field: label}`` for the tag-correction log: English names for
    taxonomy, strings for level / marks (``subject_ai_service`` shape)."""
    out = {}
    for f in fields:
        if f not in FIELDS:
            continue
        spec = FIELDS[f]
        v = values.get(f)
        if spec['kind'] == 'ref':
            out[f] = taxonomy.name(spec['tax'], v) if v else None
        elif spec['kind'] == 'refs':
            out[f] = [n for n in (taxonomy.name(spec['tax'], i) for i in (v or ())) if n]
        elif v is None:
            out[f] = None
        else:
            out[f] = str(v)
    return out


def describe(question, taxonomy: Taxonomy, mode='en') -> dict:
    """``{field: {value, label}}`` of every field, for the agent and the UI."""
    values = current_values(question)
    return {f: {'value': values[f], 'label': label(f, values[f], taxonomy, mode)} for f in FIELDS}


# ---------------------------------------------------------------- ORM writer

def apply_fields(question, changes, taxonomy: Taxonomy, *, action, note=None,
                 confirm_stale=False, capture=True) -> dict:
    """Write a normalised change list (:func:`normalize_changes`) onto
    ``question``. Raises :class:`StaleError` when a field no longer holds its
    ``old`` value and ``confirm_stale`` is off. Takes a ``subject_snapshot``
    point labelled ``action`` first when a restore-point field changes (skip
    with ``capture=False`` when the caller took one for a whole run). Does
    not commit. Returns ``{changed, before, after, stale}`` (stored values)."""
    from app import hierarchy, subject_snapshot
    current = current_values(question)
    stale = stale_fields(changes, current)
    if stale and not confirm_stale:
        raise StaleError(stale)
    stem = hierarchy.is_stem(question)
    values = {c['field']: c['new'] for c in changes}
    after = plan_after(current, values, taxonomy, stem=stem)
    changed = changed_fields(current, after)
    if capture and any(is_snapshot_field(f) for f in changed):
        subject_snapshot.capture(question.subject, action, note=note)
    if changed:
        write_values(question, values, stem_row=stem)
    return {'changed': changed, 'before': {f: current[f] for f in changed},
            'after': {f: after[f] for f in changed}, 'stale': stale}


def write_values(question, values: dict, *, stem_row: bool) -> None:
    """Write ``values`` (``FIELDS`` names, stored shapes) onto ``question``
    with the Edit modal's rules. Does not commit or capture."""
    from app.models import Subchapter, Subtopic, Topic

    if not stem_row and 'level' in values:
        question.level = values['level']
    if not stem_row and 'q_type' in values:
        question.q_type = values['q_type']
    for f in ('section', 'description', 'answer', 'comment', 'correct_percentage'):
        if f in values:
            setattr(question, f, values[f])
    # Marks live on leaves too; a stem's total is derived.
    if not stem_row and 'marks' in values:
        question.marks = values['marks']
    # Tags live on leaves. Ignore topic/chapter writes on stems.
    if stem_row:
        return

    if 'major_topic' in values:
        new_topic = values['major_topic']
        # A new major topic clears the major subtopic (it may no longer fit).
        if new_topic != question.major_topic_id:
            question.major_subtopic_id = None
        question.major_topic_id = new_topic
    if 'major_subtopic' in values:
        sid = values['major_subtopic']
        sub = db.session.get(Subtopic, int(sid)) if sid else None
        if sub and question.major_topic_id and sub.topic_id == question.major_topic_id:
            question.major_subtopic_id = sub.id
        else:
            question.major_subtopic_id = None
    if 'minor_topics' in values:
        question.minor_topics.clear()
        for tid in values['minor_topics'] or ():
            topic = db.session.get(Topic, int(tid))
            if topic:
                question.minor_topics.append(topic)
    if 'subtopics' in values:
        question.subtopics.clear()
        for sid in values['subtopics'] or ():
            sub = db.session.get(Subtopic, int(sid))
            if sub:
                question.subtopics.append(sub)
    if 'chapter' in values:
        new_chapter = values['chapter']
        if new_chapter != question.chapter_id:
            question.subchapter_id = None
        question.chapter_id = new_chapter
    if 'subchapter' in values:
        scid = values['subchapter']
        sc = db.session.get(Subchapter, int(scid)) if scid else None
        if sc and question.chapter_id and sc.chapter_id == question.chapter_id:
            question.subchapter_id = sc.id
        else:
            question.subchapter_id = None
