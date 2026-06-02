"""
Utility functions for the application
"""
import re
from functools import wraps, cmp_to_key
from flask import abort, request
from flask_login import current_user
from natsort import natsorted, natsort_keygen


# ==================== Asset Versions ====================
#
# `QuestionAsset.version` replaces the old "language" concept. The canonical
# list below is ALSO the default priority order (highest priority first). The
# ENO / CHO versions are official public-exam scans and therefore sit last by
# default. This is the single source of truth — import it from here rather
# than re-hardcoding the codes anywhere else.

VERSIONS = ['EN', 'CH', 'BI', 'ENO', 'CHO']

VERSION_LABELS = {
    'EN': 'English',
    'CH': 'Chinese',
    'BI': 'Bilingual',
    'ENO': 'English (Official)',
    'CHO': 'Chinese (Official)',
}

# "Typed" versions are the ones we author and proofread; their per-asset
# check_state drives the per-question asset-status rollup in the admin list.
# ENO/CHO are official public-exam scans used only as the proofreading
# REFERENCE, so they are excluded from the status indicator/rollup.
TYPED_VERSIONS = ['EN', 'CH', 'BI']
OFFICIAL_VERSIONS = ['ENO', 'CHO']

# Default priority order = canonical list order.
DEFAULT_VERSION_PRIORITY = list(VERSIONS)


def utc_iso(dt):
    """Serialize a naive UTC ``datetime`` for JSON/API (ISO 8601 with ``Z``)."""
    if dt is None:
        return None
    return dt.strftime('%Y-%m-%dT%H:%M:%SZ')


def parse_version_priority(raw, legacy_preferred=None):
    """Parse a comma-separated `version_priority` value into a complete ordered
    list of every known version (highest priority first).

    - Keeps only known version codes, de-duplicated, in the supplied order.
    - Appends any versions still missing in `DEFAULT_VERSION_PRIORITY` order so
      the result always contains all of `VERSIONS`.
    - When `raw` is empty/None but `legacy_preferred` is supplied (an old
      `preferred_language` / `preview_language` value such as 'EN' or 'CH'),
      seeds the list with the legacy "preferred -> BI -> other" semantics:
      `[preferred, 'BI']` followed by the remaining defaults.
    """
    valid = set(VERSIONS)
    parts = [p.strip().upper() for p in (raw or '').split(',') if p.strip()]
    out = []
    for p in parts:
        if p in valid and p not in out:
            out.append(p)
    if not out and legacy_preferred:
        pref = str(legacy_preferred).strip().upper()
        for p in (pref, 'BI'):
            if p in valid and p not in out:
                out.append(p)
    for p in DEFAULT_VERSION_PRIORITY:
        if p not in out:
            out.append(p)
    return out


# ==================== Permission Decorators ====================

def admin_required(f):
    """Decorator to require admin access to at least one subject (or super admin)"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            abort(403)
        if not current_user.is_super_admin and not current_user.has_any_admin_access():
            abort(403)
        return f(*args, **kwargs)
    return decorated_function


def super_admin_required(f):
    """Decorator to require super admin access"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_super_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated_function


def subject_access_required(f):
    """
    Decorator to require access to a subject.
    Expects subject_id in URL params, form data, or JSON body.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            abort(403)
        
        # Super admin has access to everything
        if current_user.is_super_admin:
            return f(*args, **kwargs)
        
        # Get subject_id from various sources
        subject_id = kwargs.get('subject_id') or \
                     request.args.get('subject_id') or \
                     request.form.get('subject_id')
        
        if not subject_id and request.is_json:
            subject_id = request.get_json().get('subject_id')
        
        if subject_id and not current_user.has_subject_access(subject_id):
            abort(403)
        
        return f(*args, **kwargs)
    return decorated_function


def subject_admin_required(f):
    """
    Decorator to require admin access to a subject.
    Expects subject_id in URL params, form data, or JSON body.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            abort(403)
        
        # Super admin has access to everything
        if current_user.is_super_admin:
            return f(*args, **kwargs)
        
        # Get subject_id from various sources
        subject_id = kwargs.get('subject_id') or \
                     request.args.get('subject_id') or \
                     request.form.get('subject_id')
        
        if not subject_id and request.is_json:
            subject_id = request.get_json().get('subject_id')
        
        if subject_id and not current_user.is_subject_admin(subject_id):
            abort(403)
        
        return f(*args, **kwargs)
    return decorated_function


# ==================== Permission Helper Functions ====================

def get_user_accessible_subjects():
    """Get subjects the current user can access"""
    from app.models import Subject
    
    if not current_user.is_authenticated:
        return []
    
    if current_user.is_super_admin:
        return Subject.query.all()
    
    accessible_ids = current_user.get_accessible_subjects()
    if not accessible_ids:
        return []
    
    return Subject.query.filter(Subject.id.in_(accessible_ids)).all()


def get_user_admin_subjects():
    """Get subjects the current user has admin access to"""
    from app.models import Subject
    
    if not current_user.is_authenticated:
        return []
    
    if current_user.is_super_admin:
        return Subject.query.all()
    
    admin_ids = current_user.get_admin_subjects()
    if not admin_ids:
        return []
    
    return Subject.query.filter(Subject.id.in_(admin_ids)).all()

def natural_sort_key(qid):
    """
    Generate a sort key for natural sorting of question IDs
    Handles Q1, Q2, Q10 correctly (not Q1, Q10, Q2)
    """
    def convert(text):
        return int(text) if text.isdigit() else text.lower()
    
    return [convert(c) for c in re.split('([0-9]+)', qid)]

def natural_sort(items, key_func=None):
    """
    Natural sort a list of items
    """
    if key_func:
        return natsorted(items, key=key_func)
    return natsorted(items)

# Sort field definitions with their key functions
SORT_FIELDS = {
    'qid': {
        'label': 'Question ID',
        'key': lambda q: q.qid,
        'natural': True  # Use natural sort for this field
    },
    'year': {
        'label': 'Year',
        'key': lambda q: q.year if q.year else 0,
        'natural': False
    },
    'level': {
        'label': 'Level',
        'key': lambda q: q.level,
        'natural': False
    },
    'topic': {
        'label': 'Topic',
        'key': lambda q: q.major_topic.name if q.major_topic else 'ZZZ',
        'natural': True
    },
    'subtopic': {
        'label': 'Subtopic',
        'key': lambda q: q.major_subtopic.name if q.major_subtopic else 'ZZZ',
        'natural': True
    },
    'created_time': {
        'label': 'Created Time',
        'key': lambda q: q.created_at,
        'natural': False
    },
    'source': {
        'label': 'Source',
        'key': lambda q: q.source,
        'natural': False
    },
    'section': {
        'label': 'Section',
        'key': lambda q: q.section if q.section else '',
        'natural': False
    },
    'q_type': {
        'label': 'Question Type',
        'key': lambda q: q.q_type,
        'natural': False
    },
    'correct_percentage': {
        'label': 'Correct %',
        'key': lambda q: (0, q.correct_percentage) if q.correct_percentage is not None else (1, 0),
        'natural': False
    },
    'chapter': {
        'label': 'Chapter',
        'key': lambda q: q.chapter.name if q.chapter else 'ZZZ',
        'natural': True
    },
    'subchapter': {
        'label': 'Subchapter',
        'key': lambda q: q.subchapter.name if q.subchapter else 'ZZZ',
        'natural': True
    }
}

# ==================== Manual Block Reordering ====================
#
# A subset of the sort fields can be "grouped" — when one or more of these
# appear in a sort_config, the distinct combinations of their values form
# blocks that the user can drag into a custom flat order (see
# `enumerate_sort_groups` + `apply_multi_sort(..., group_order=...)`).
#
# Block keys are tuples of integer IDs (0 = NULL / untagged) so a rename of a
# topic/subtopic/chapter/subchapter never invalidates a saved manual order.

GROUPING_FIELDS = ['topic', 'subtopic', 'chapter', 'subchapter']

# Integer-id accessors for each grouping field (0 when untagged).
GROUP_ID_KEYS = {
    'topic': lambda q: q.major_topic_id or 0,
    'subtopic': lambda q: q.major_subtopic_id or 0,
    'chapter': lambda q: q.chapter_id or 0,
    'subchapter': lambda q: q.subchapter_id or 0,
}

# Human-readable name accessors (None when untagged).
GROUP_NAME_KEYS = {
    'topic': lambda q: q.major_topic.name if q.major_topic else None,
    'subtopic': lambda q: q.major_subtopic.name if q.major_subtopic else None,
    'chapter': lambda q: q.chapter.name if q.chapter else None,
    'subchapter': lambda q: q.subchapter.name if q.subchapter else None,
}

GROUP_NONE_LABELS = {
    'topic': '(No topic)',
    'subtopic': '(No subtopic)',
    'chapter': '(No chapter)',
    'subchapter': '(No subchapter)',
}


def grouping_fields_in_config(sort_config):
    """Return the grouping fields present in a sort_config, in priority order."""
    if not sort_config:
        return []
    return [c.get('field') for c in sort_config if c.get('field') in GROUPING_FIELDS]


def enumerate_sort_groups(questions, group_fields):
    """
    Enumerate the distinct grouping-blocks present in `questions`.

    Args:
        questions: list of Question objects
        group_fields: ordered list of grouping field names (subset of GROUPING_FIELDS)

    Returns:
        Ordered list of dicts (default natural-name order):
        [{"key": [int, ...], "labels": {field: name}, "count": int}, ...]
    """
    group_fields = [f for f in (group_fields or []) if f in GROUPING_FIELDS]
    if not group_fields or not questions:
        return []

    nat_key = natsort_keygen()
    blocks = {}

    for q in questions:
        key_tuple = tuple(int(GROUP_ID_KEYS[f](q)) for f in group_fields)
        block = blocks.get(key_tuple)
        if block is None:
            labels = {}
            sort_components = []
            for f in group_fields:
                name = GROUP_NAME_KEYS[f](q)
                labels[f] = name if name is not None else GROUP_NONE_LABELS[f]
                # None sorts last; non-None sorts by natural key.
                sort_components.append((1, ()) if name is None else (0, nat_key(name)))
            block = {
                'key': list(key_tuple),
                'labels': labels,
                'count': 0,
                '_sort': sort_components,
            }
            blocks[key_tuple] = block
        block['count'] += 1

    ordered = sorted(blocks.values(), key=lambda b: b['_sort'])
    for b in ordered:
        b.pop('_sort', None)
    return ordered


def _build_manual_block_index(group_order, group_fields):
    """
    Build a {block_key_tuple: position} index from a stored group_order, but
    only when it was built for exactly the grouping fields currently in effect.

    Returns (index_dict, sentinel_pos) or (None, 0) when no valid manual order.
    """
    if not group_order or not isinstance(group_order, dict):
        return None, 0
    go_fields = [f for f in (group_order.get('fields') or []) if f in GROUPING_FIELDS]
    go_order = group_order.get('order') or []
    # Stale guard: the manual order only applies if it was built for the exact
    # set + order of grouping fields currently active.
    if go_fields != group_fields or not go_order:
        return None, 0

    index = {}
    for pos, block_key in enumerate(go_order):
        try:
            t = tuple(int(x) for x in block_key)
        except (TypeError, ValueError):
            continue
        if t not in index:
            index[t] = pos
    if not index:
        return None, 0
    # Unlisted blocks sort after every listed one.
    return index, len(go_order)


def apply_multi_sort(items, sort_config, group_order=None):
    """
    Apply multi-level sorting based on sort configuration.
    
    Args:
        items: List of Question objects to sort
        sort_config: List of dicts like [{"field": "qid", "direction": "asc"}, ...]
        group_order: Optional manual block ordering, shaped as
            {"fields": ["topic", "subtopic"], "order": [[12, 45], [13, 0], ...]}.
            When provided AND its `fields` match the grouping fields present in
            `sort_config`, questions are first ordered by their block's manual
            position; within a block they keep sorting by the remaining fields.
            Unlisted blocks fall back to natural-name order at the end.
    
    Returns:
        Sorted list of items
    """
    if not items or not sort_config:
        return items
    
    # Create a natural sort key generator for qid-like fields
    nat_key = natsort_keygen()

    # Resolve optional manual block ordering.
    group_fields = grouping_fields_in_config(sort_config)
    manual_index, manual_sentinel = _build_manual_block_index(group_order, group_fields)
    
    def make_sort_key(item):
        """Generate a tuple key for multi-level sorting"""
        keys = []
        # Manual block position takes top priority when active. The grouping
        # fields remain in the per-config loop below as a stable natural-name
        # fallback (constant within a listed block; orders unlisted blocks).
        if manual_index is not None:
            block_tuple = tuple(int(GROUP_ID_KEYS[f](item)) for f in group_fields)
            pos = manual_index.get(block_tuple, manual_sentinel)
            keys.append((pos, False))
        for config in sort_config:
            field = config.get('field', 'qid')
            direction = config.get('direction', 'asc')
            
            field_info = SORT_FIELDS.get(field, SORT_FIELDS['qid'])
            raw_value = field_info['key'](item)
            
            # For natural sort fields, use natsort key
            if field_info.get('natural', False) and isinstance(raw_value, str):
                key_value = nat_key(raw_value)
            else:
                key_value = raw_value
            
            keys.append((key_value, direction == 'desc'))
        
        return keys
    
    def compare_items(a, b):
        """Compare two items based on multi-level sort"""
        keys_a = make_sort_key(a)
        keys_b = make_sort_key(b)
        
        for (val_a, desc_a), (val_b, desc_b) in zip(keys_a, keys_b):
            # Handle None values
            if val_a is None and val_b is None:
                continue
            if val_a is None:
                return 1 if not desc_a else -1
            if val_b is None:
                return -1 if not desc_a else 1
            
            # Compare values
            try:
                if val_a < val_b:
                    result = -1
                elif val_a > val_b:
                    result = 1
                else:
                    continue  # Equal, move to next sort level
            except TypeError:
                # Handle comparison errors by converting to strings
                str_a, str_b = str(val_a), str(val_b)
                if str_a < str_b:
                    result = -1
                elif str_a > str_b:
                    result = 1
                else:
                    continue
            
            # Apply direction
            return -result if desc_a else result
        
        return 0  # All sort levels are equal
    
    return sorted(items, key=cmp_to_key(compare_items))

def parse_qno(qno_str):
    """
    Parse question number string like 'Q5' to integer 5
    """
    if qno_str.startswith('Q'):
        return int(qno_str[1:])
    return int(qno_str)

def get_file_extension(filename):
    """Get file extension from filename"""
    return filename.rsplit('.', 1)[1].lower() if '.' in filename else ''

def is_image_file(filename):
    """Check if file is an image"""
    image_extensions = {'png', 'jpg', 'jpeg', 'gif', 'bmp', 'tiff'}
    return get_file_extension(filename) in image_extensions

def is_doc_file(filename):
    """Check if file is a Word document"""
    doc_extensions = {'doc', 'docx'}
    return get_file_extension(filename) in doc_extensions
