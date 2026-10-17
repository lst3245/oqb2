"""
Saved-list number in front of teacher-facing topic / subtopic names.

``Subject.topic_number_prefix`` / ``subtopic_number_prefix`` turn on
``01 Basic Algebra`` / ``1.1 Law of Indices`` wherever the UI shows a topic
name: dashboard filter and question tags, Reorder blocks, the tag editor,
Batch Edit. Display only: never match, post or store these labels (use ids /
``name_en``). Generated documents ignore this and follow the Generate page
numbering option.
"""
from flask import g, has_app_context

from app.i18n import display_name

_CACHE_ATTR = '_oqb_tax_numbers'


def prefixed(number, name):
    """``"01 Basic Algebra"``, or ``name`` when there is no number. Pure."""
    if name is None:
        return None
    return f'{number} {name}' if number else name


def subject_numbers(subject_id):
    """``({topic_id: '01'}, {subtopic_id: '1.1'})`` for the prefixes the
    subject turns on (an empty map for each one that is off). Cached for the
    rest of the request/app context."""
    cache = g.setdefault(_CACHE_ATTR, {}) if has_app_context() else None
    if cache is not None and subject_id in cache:
        return cache[subject_id]

    from app import db
    from app.models import Subject, Topic, Subtopic
    from app.utils import number_taxonomy

    result = ({}, {})
    subject = db.session.get(Subject, subject_id) if subject_id else None
    if subject is not None and (subject.topic_number_prefix or subject.subtopic_number_prefix):
        topics = Topic.query.filter_by(subject_id=subject_id).all()
        subs = (Subtopic.query.join(Topic, Subtopic.topic_id == Topic.id)
                .filter(Topic.subject_id == subject_id).all())
        topic_nums, sub_nums = number_taxonomy(topics, subs)
        result = (topic_nums if subject.topic_number_prefix else {},
                  sub_nums if subject.subtopic_number_prefix else {})
    if cache is not None:
        cache[subject_id] = result
    return result


def number_of(node):
    """Prefix number of a Topic / Subtopic row, or None (off, hidden, or any
    other kind of node)."""
    kind = type(node).__name__
    if kind == 'Topic':
        return subject_numbers(node.subject_id)[0].get(node.id)
    if kind == 'Subtopic':
        topic = node.topic
        if topic is None:
            return None
        return subject_numbers(topic.subject_id)[1].get(node.id)
    return None


def numbered_name(node, mode=None):
    """``display_name`` with the subject's number prefix (if any)."""
    if node is None:
        return None
    return prefixed(number_of(node), display_name(node, mode))
