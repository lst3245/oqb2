"""
Utility functions for the application
"""
import re
from functools import wraps
from flask import abort
from flask_login import current_user
from natsort import natsorted

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
