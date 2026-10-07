"""Dashboard Bulk Edit: one plan for the preview and the write.

The preview route and ``batch_update_questions`` both go through
``parse_batch_form`` / ``resolve_batch`` / ``apply_resolved`` so the
before/after a teacher confirms is what the save stores. Parent checks
match the previous writer: a major subtopic is kept only when it belongs
to the new major topic, and a subchapter only when it belongs to the new
chapter; otherwise that child is cleared.
"""

_NONE = 'None'

_GROUP_LABELS = (
    ('level', 'Level'),
    ('q_type', 'Question type'),
    ('section', 'Section'),
    ('correct_percentage', 'Correct percentage'),
    ('topics', 'Topics & subtopics'),
    ('chapters', 'Chapter & subchapter'),
)

# Display order matches the Bulk Edit form.
_FIELDS = (
    ('level', 'Level', 'scalar'),
    ('q_type', 'Question type', 'scalar'),
    ('section', 'Section', 'scalar'),
    ('correct_percentage', 'Correct percentage', 'percent'),
    ('major_topic', 'Major topic', 'named'),
    ('major_subtopic', 'Major subtopic', 'named'),
    ('minor_topics', 'Minor topics', 'list'),
    ('subtopics', 'Subtopics', 'list'),
    ('chapter', 'Chapter', 'named'),
    ('subchapter', 'Subchapter', 'named'),
)


class BatchEditError(Exception):
    """Bad Bulk Edit input. ``status`` is the HTTP code for the route."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def parse_batch_question_ids(form):
    """Question ids from ``question_ids``, in submitted order, de-duplicated."""
    raw = form.getlist('question_ids')
    if not raw:
        raise BatchEditError('No questions selected')
    ids = []
    seen = set()
    for item in raw:
        text = str(item).strip()
        if not text.isdigit():
            continue
        number = int(text)
        if number in seen:
            continue
        seen.add(number)
        ids.append(number)
    if not ids:
        raise BatchEditError('Invalid question IDs')
    return ids


def _blank(value):
    return value is None or (isinstance(value, str) and value.strip() == '')


def _optional_int(raw, label):
    if _blank(raw):
        return None
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        raise BatchEditError(f'{label} must be a whole number') from None


def _id_list(values, label):
    """Non-blank ids, form order, duplicates dropped."""
    ids = []
    seen = set()
    for raw in values or []:
        if _blank(raw):
            continue
        number = _optional_int(raw, label)
        if number in seen:
            continue
        seen.add(number)
        ids.append(number)
    return ids


def _kept_text(raw):
    """Empty clears the field. A non-empty string is stored as sent (no strip)."""
    if raw is None or raw == '':
        return None
    return raw


def parse_batch_form(form):
    """Flags plus raw targets. No database access.

    ``correct_percentage_warning`` is set when a number outside 0–100 will
    be stored as empty, matching the writer.
    """
    flags = {
        'level': form.get('update_level') == '1',
        'q_type': form.get('update_q_type') == '1',
        'section': form.get('update_section') == '1',
        'correct_percentage': form.get('update_correct_pct') == '1',
        'topics': form.get('update_topics') == '1',
        'chapters': form.get('update_chapters') == '1',
    }
    level = _optional_int(form.get('level'), 'Level') if flags['level'] else None
    q_type = _kept_text(form.get('q_type')) if flags['q_type'] else None
    section = _kept_text(form.get('section')) if flags['section'] else None

    correct_percentage = None
    correct_percentage_warning = None
    if flags['correct_percentage']:
        raw = form.get('correct_percentage')
        if not _blank(raw):
            correct_percentage = _optional_int(raw, 'Correct percentage')
            if correct_percentage is None or not 0 <= correct_percentage <= 100:
                correct_percentage = None
                correct_percentage_warning = (
                    'Correct percentage must be between 0 and 100, so it will be cleared.'
                )

    return {
        'flags': flags,
        'level': level,
        'q_type': q_type,
        'section': section,
        'correct_percentage': correct_percentage,
        'correct_percentage_warning': correct_percentage_warning,
        'major_topic_id': _optional_int(form.get('major_topic_id'), 'Major topic') if flags['topics'] else None,
        'major_subtopic_id': _optional_int(form.get('major_subtopic_id'), 'Major subtopic') if flags['topics'] else None,
        'minor_topic_ids': _id_list(form.getlist('minor_topic_ids'), 'Minor topic') if flags['topics'] else [],
        'subtopic_ids': _id_list(form.getlist('subtopic_ids'), 'Subtopic') if flags['topics'] else [],
        'chapter_id': _optional_int(form.get('chapter_id'), 'Chapter') if flags['chapters'] else None,
        'subchapter_id': _optional_int(form.get('subchapter_id'), 'Subchapter') if flags['chapters'] else None,
    }


def updating_labels(flags):
    return [label for key, label in _GROUP_LABELS if flags.get(key)]


def _named(id_, name):
    if id_ is None:
        return None
    return (id_, name)


def resolve_batch(parsed, topics, subtopics, chapters, subchapters):
    """Turn raw ids into the values the writer will store.

    ``topics`` / ``chapters`` map id → name.
    ``subtopics`` / ``subchapters`` map id → ``{'name', 'topic_id'}`` or
    ``{'name', 'chapter_id'}``. Missing ids are skipped (minor topics and
    M2M subtopics) or warned (a missing major topic / chapter is still
    written, and the database will reject it).
    """
    warnings = []
    if parsed.get('correct_percentage_warning'):
        warnings.append(parsed['correct_percentage_warning'])

    flags = parsed['flags']
    major = None
    major_sub = None
    minors = []
    subs = []
    if flags['topics']:
        topic_id = parsed['major_topic_id']
        if topic_id is not None:
            major = _named(topic_id, topics.get(topic_id))
            if topic_id not in topics:
                warnings.append('The selected major topic no longer exists, so saving may fail.')
        sub_id = parsed['major_subtopic_id']
        if sub_id is not None:
            row = subtopics.get(sub_id)
            if row and topic_id is not None and row['topic_id'] == topic_id:
                major_sub = _named(sub_id, row['name'])
            else:
                warnings.append(
                    'The major subtopic does not belong to the selected major topic, so it will be cleared.'
                )
        missing_minors = 0
        for mid in parsed['minor_topic_ids']:
            if mid in topics:
                minors.append(_named(mid, topics[mid]))
            else:
                missing_minors += 1
        if missing_minors:
            warnings.append(
                f'{missing_minors} minor topic(s) no longer exist and will be skipped.'
            )
        missing_subs = 0
        for sid in parsed['subtopic_ids']:
            row = subtopics.get(sid)
            if row:
                subs.append(_named(sid, row['name']))
            else:
                missing_subs += 1
        if missing_subs:
            warnings.append(
                f'{missing_subs} subtopic(s) no longer exist and will be skipped.'
            )

    chapter = None
    subchapter = None
    if flags['chapters']:
        chapter_id = parsed['chapter_id']
        if chapter_id is not None:
            chapter = _named(chapter_id, chapters.get(chapter_id))
            if chapter_id not in chapters:
                warnings.append('The selected chapter no longer exists, so saving may fail.')
        sub_id = parsed['subchapter_id']
        if sub_id is not None:
            row = subchapters.get(sub_id)
            if row and chapter_id is not None and row['chapter_id'] == chapter_id:
                subchapter = _named(sub_id, row['name'])
            else:
                warnings.append(
                    'The subchapter does not belong to the selected chapter, so it will be cleared.'
                )

    return {
        'flags': flags,
        'level': parsed['level'],
        'q_type': parsed['q_type'],
        'section': parsed['section'],
        'correct_percentage': parsed['correct_percentage'],
        'major_topic_id': major[0] if major else None,
        'major_subtopic_id': major_sub[0] if major_sub else None,
        'minor_topic_ids': [item[0] for item in minors],
        'subtopic_ids': [item[0] for item in subs],
        'chapter_id': chapter[0] if chapter else None,
        'subchapter_id': subchapter[0] if subchapter else None,
        'major_topic': major,
        'major_subtopic': major_sub,
        'minor_topics': minors,
        'subtopics': subs,
        'chapter': chapter,
        'subchapter': subchapter,
        'warnings': warnings,
    }


def view_from_question(question):
    """Current tag fields as the preview compares them."""

    def named(obj, id_):
        if id_ is None:
            return None
        name = getattr(obj, 'name', None) if obj is not None else None
        return (id_, name)

    return {
        'level': question.level,
        'q_type': question.q_type or None,
        'section': question.section if question.section else None,
        'correct_percentage': question.correct_percentage,
        'major_topic': named(getattr(question, 'major_topic', None), question.major_topic_id),
        'major_subtopic': named(getattr(question, 'major_subtopic', None), question.major_subtopic_id),
        'minor_topics': [(row.id, row.name) for row in (question.minor_topics or [])],
        'subtopics': [(row.id, row.name) for row in (question.subtopics or [])],
        'chapter': named(getattr(question, 'chapter', None), question.chapter_id),
        'subchapter': named(getattr(question, 'subchapter', None), question.subchapter_id),
    }


def after_view(before, resolved):
    """``before`` with only the ticked bundles replaced by ``resolved``."""
    after = {
        'level': before['level'],
        'q_type': before['q_type'],
        'section': before['section'],
        'correct_percentage': before['correct_percentage'],
        'major_topic': before['major_topic'],
        'major_subtopic': before['major_subtopic'],
        'minor_topics': list(before['minor_topics']),
        'subtopics': list(before['subtopics']),
        'chapter': before['chapter'],
        'subchapter': before['subchapter'],
    }
    flags = resolved['flags']
    if flags.get('level'):
        after['level'] = resolved['level']
    if flags.get('q_type'):
        after['q_type'] = resolved['q_type']
    if flags.get('section'):
        after['section'] = resolved['section']
    if flags.get('correct_percentage'):
        after['correct_percentage'] = resolved['correct_percentage']
    if flags.get('topics'):
        after['major_topic'] = resolved['major_topic']
        after['major_subtopic'] = resolved['major_subtopic']
        after['minor_topics'] = list(resolved['minor_topics'])
        after['subtopics'] = list(resolved['subtopics'])
    if flags.get('chapters'):
        after['chapter'] = resolved['chapter']
        after['subchapter'] = resolved['subchapter']
    return after


def _display_scalar(kind, value):
    if value is None or value == '':
        return _NONE, True
    if kind == 'percent':
        return f'{value}%', False
    return str(value), False


def _display_named(pair):
    if not pair or pair[0] is None:
        return _NONE, True
    _id, name = pair
    return (name if name else f'#{_id}'), False


def _display_list(pairs):
    if not pairs:
        return _NONE, True
    parts = []
    for id_, name in pairs:
        parts.append(name if name else f'#{id_}')
    return ', '.join(parts), False


def _ids(pairs):
    return [pair[0] for pair in pairs]


def changes_between(before, after):
    """Fields whose stored value would change. Same set of ids is unchanged
    even when the order differs (minor topics and M2M subtopics)."""
    changes = []
    for key, label, kind in _FIELDS:
        old = before[key]
        new = after[key]
        if kind == 'list':
            if set(_ids(old)) == set(_ids(new)):
                continue
            old_ids = set(_ids(old))
            new_ids = set(_ids(new))
            before_text, before_empty = _display_list(old)
            after_text, after_empty = _display_list(new)
            removed = [name if name else f'#{id_}' for id_, name in old if id_ not in new_ids]
            added = [name if name else f'#{id_}' for id_, name in new if id_ not in old_ids]
            changes.append({
                'field': key,
                'label': label,
                'before': before_text,
                'after': after_text,
                'before_empty': before_empty,
                'after_empty': after_empty,
                'removed': removed,
                'added': added,
            })
            continue
        if kind == 'named':
            old_id = old[0] if old else None
            new_id = new[0] if new else None
            if old_id == new_id:
                continue
            before_text, before_empty = _display_named(old)
            after_text, after_empty = _display_named(new)
        else:
            # 0 is a real correct-percentage; only None and '' mean empty.
            if (None if old in (None, '') else old) == (None if new in (None, '') else new):
                continue
            before_text, before_empty = _display_scalar(kind, old)
            after_text, after_empty = _display_scalar(kind, new)
        changes.append({
            'field': key,
            'label': label,
            'before': before_text,
            'after': after_text,
            'before_empty': before_empty,
            'after_empty': after_empty,
        })
    return changes


def preview_for_questions(rows, resolved):
    """``rows`` is ``(id, qid, before_view)`` in selection order.

    Questions with no field change are listed under ``unchanged`` only.
    """
    changed = []
    unchanged = []
    for question_id, qid, before in rows:
        diffs = changes_between(before, after_view(before, resolved))
        if diffs:
            changed.append({'id': question_id, 'qid': qid, 'changes': diffs})
        else:
            unchanged.append({'id': question_id, 'qid': qid})
    return {
        'questions': changed,
        'unchanged': unchanged,
        'changed_count': len(changed),
        'unchanged_count': len(unchanged),
        'warnings': list(resolved.get('warnings') or []),
        'updating': updating_labels(resolved['flags']),
    }


def apply_resolved(question, resolved, minor_topics, subtopics):
    """Write ``resolved`` onto ``question``. Caller commits.

    ``minor_topics`` and ``subtopics`` are model instances in write order.
    """
    flags = resolved['flags']
    if flags.get('level'):
        question.level = resolved['level']
    if flags.get('q_type'):
        question.q_type = resolved['q_type']
    if flags.get('section'):
        question.section = resolved['section']
    if flags.get('correct_percentage'):
        question.correct_percentage = resolved['correct_percentage']
    if flags.get('topics'):
        question.major_topic_id = resolved['major_topic_id']
        question.major_subtopic_id = resolved['major_subtopic_id']
        question.minor_topics.clear()
        for topic in minor_topics:
            question.minor_topics.append(topic)
        question.subtopics.clear()
        for subtopic in subtopics:
            question.subtopics.append(subtopic)
    if flags.get('chapters'):
        question.chapter_id = resolved['chapter_id']
        question.subchapter_id = resolved['subchapter_id']
