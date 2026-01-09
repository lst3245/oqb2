"""
Utility functions for the application
"""
import re
from functools import wraps, cmp_to_key
from flask import abort
from flask_login import current_user
from natsort import natsorted, natsort_keygen

def admin_required(f):
    """Decorator to require admin access"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated_function

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
    }
}

def apply_multi_sort(items, sort_config):
    """
    Apply multi-level sorting based on sort configuration.
    
    Args:
        items: List of Question objects to sort
        sort_config: List of dicts like [{"field": "qid", "direction": "asc"}, ...]
    
    Returns:
        Sorted list of items
    """
    if not items or not sort_config:
        return items
    
    # Create a natural sort key generator for qid-like fields
    nat_key = natsort_keygen()
    
    def make_sort_key(item):
        """Generate a tuple key for multi-level sorting"""
        keys = []
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
