"""
Reusable file-browser service.

Pure(ish) filesystem operations that take an absolute ``base_dir`` (a resolved
root) plus a ``rel_path`` and a ``can_write`` flag, so the same logic backs the
super-admin browser, the per-user browser, and the unified file selector. All
joins go through :func:`app.storage.safe_join` (hardened against the
sibling-prefix trap).

Also defines :class:`RootRegistry`, which maps a user to the set of roots they
may browse (with per-root write flags), and :class:`Root` descriptors.

Routes stay thin: they resolve a :class:`Root` via the registry, then call one
of the functions below and translate :class:`FileServiceError` into a JSON
error response.
"""
from __future__ import annotations

import os
import shutil

from flask import current_app
from werkzeug.utils import secure_filename

from app import storage


class FileServiceError(Exception):
    """Raised for hard failures; carries an HTTP ``status`` + ``message``."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# ---------------------------------------------------------------------------
# Root descriptors + registry
# ---------------------------------------------------------------------------

class Root:
    """One browsable root.

    ``id`` is a stable, opaque token the client echoes back (never a raw path
    for the per-user roots). ``path`` is the absolute base on disk. ``can_write``
    gates all mutating operations. ``removable`` marks admin-added extra roots.
    """

    __slots__ = ('id', 'label', 'path', 'can_write', 'removable', 'expose_path')

    def __init__(self, id, label, path, can_write=True, removable=False,
                 expose_path=False):
        self.id = id
        self.label = label
        self.path = os.path.abspath(path)
        self.can_write = can_write
        self.removable = removable
        # Whether to reveal the absolute server path to the client (super-admin
        # only). Per-user roots hide it to avoid leaking the server layout.
        self.expose_path = expose_path

    def to_dict(self):
        d = {
            'id': self.id,
            'label': self.label,
            'can_write': self.can_write,
            'removable': self.removable,
            'missing': not os.path.isdir(self.path),
        }
        if self.expose_path:
            d['path'] = self.path
        return d


# Built-in super-admin root ids.
ROOT_SOURCE = 'source'
ROOT_STORAGE = 'storage'
ROOT_USER = 'user'
_EXTRA_PREFIX = 'extra:'
_SHARED_PREFIX = 'shared:'

FILE_BROWSER_ROOTS_KEY = 'FILE_BROWSER_EXTRA_ROOTS'


def _load_extra_roots():
    """Extra super-admin root abs paths from the ``system_settings`` row.

    This key is intentionally NOT in the settings REGISTRY — it's a list managed
    by the File Browser's own routes. Malformed / missing rows yield ``[]``.
    """
    import json
    from app.models import SystemSetting
    try:
        row = SystemSetting.query.get(FILE_BROWSER_ROOTS_KEY)
    except Exception:
        return []
    if not row:
        return []
    try:
        data = json.loads(row.value)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    return [os.path.abspath(p.strip()) for p in data
            if isinstance(p, str) and p.strip()]


def _save_extra_roots(paths):
    """Persist the extra-roots list (abs paths) to the DB setting."""
    import json
    from flask_login import current_user
    from app import db
    from app.models import SystemSetting
    encoded = json.dumps([os.path.abspath(p) for p in paths])
    row = SystemSetting.query.get(FILE_BROWSER_ROOTS_KEY)
    if row is None:
        row = SystemSetting(key=FILE_BROWSER_ROOTS_KEY, value=encoded,
                            updated_by=current_user.id)
        db.session.add(row)
    else:
        row.value = encoded
        row.updated_by = current_user.id
    db.session.commit()


def allowed_drive():
    """The drive (e.g. ``Q:\\``) every browser root must live on, derived from
    SOURCE_PATH so only the question-bank volume is reachable."""
    drive, _ = os.path.splitdrive(storage.source_path())
    if drive:
        return os.path.normcase(drive + os.sep)
    return os.path.normcase(os.sep)


def path_on_allowed_drive(abs_path) -> bool:
    return os.path.normcase(os.path.abspath(abs_path)).startswith(allowed_drive())


class RootRegistry:
    """Resolves the roots a given user may browse, in one of two **scopes**.

    - **admin scope** (``/admin/files`` only, super-admin only): ``Source`` +
      ``Storage`` (covers Shared/System/User) + any admin-registered extra
      roots. All read-write; paths exposed.
    - **user scope** (default — user browser, file selector, PDF Import /
      Toolbox pickers): personal ``User/<name>`` (rw); for each subject the
      user can access, ``Shared/<subject>`` — read-write when they are a
      subject **admin**, read-only when they are a **user**. Pure **viewers**
      (and no-role subjects) are excluded.

    A **super-admin uses user scope by default** (so My Stuff → File Browser
    and every selector look identical to a regular user); the full admin root
    set is only produced when ``scope='admin'`` is explicitly requested *and*
    the user is a super-admin.
    """

    def __init__(self, user, scope=None):
        self.user = user
        # 'admin' scope is honoured only for super-admins; everyone else (and
        # super-admins by default) gets the user-scope root set.
        if scope == 'admin' and getattr(user, 'is_super_admin', False):
            self.scope = 'admin'
        else:
            self.scope = 'user'
        self._roots = self._build()

    # -- building ---------------------------------------------------------
    def _build(self):
        if self.scope == 'admin':
            return self._build_super()
        return self._build_user()

    def _build_super(self):
        roots = [
            Root(ROOT_SOURCE, 'Source', storage.source_path(),
                 can_write=True, removable=False, expose_path=True),
            Root(ROOT_STORAGE, 'Storage', storage.storage_path(),
                 can_write=True, removable=False, expose_path=True),
        ]
        seen = {os.path.normcase(r.path) for r in roots}
        for p in _load_extra_roots():
            key = os.path.normcase(p)
            if key in seen:
                continue
            seen.add(key)
            roots.append(Root(_EXTRA_PREFIX + key, p, p,
                              can_write=True, removable=True, expose_path=True))
        return roots

    def _build_user(self):
        from app.models import Subject
        roots = []
        # Personal home (always read-write; created on demand).
        home = storage.user_home(self.user)
        roots.append(Root(ROOT_USER, 'My Files', home,
                          can_write=True, removable=False))

        # Per-subject shared folders, gated by role.
        roles = {}
        try:
            roles = self.user.get_subject_roles()  # {subject_id: role}
        except Exception:
            roles = {}
        if roles:
            subj_names = {s.id: s.name for s in Subject.query.filter(
                Subject.id.in_(list(roles.keys()))).all()}
            for sid in sorted(roles.keys()):
                role = roles[sid]
                if role == 'admin':
                    can_write = True
                elif role == 'user':
                    can_write = False
                else:
                    continue  # viewer / unknown -> no access
                path = storage.shared_subject_dir(sid)
                if not path:
                    continue
                label = subj_names.get(sid, sid)
                roots.append(Root(_SHARED_PREFIX + sid,
                                  f'Shared · {label}', path,
                                  can_write=can_write, removable=False))
        return roots

    # -- access -----------------------------------------------------------
    def list(self):
        return list(self._roots)

    def list_dicts(self):
        return [r.to_dict() for r in self._roots]

    def resolve(self, root_id):
        """Return the :class:`Root` for ``root_id`` or ``None``. A blank id
        defaults to the first root (keeps legacy links working)."""
        if not root_id:
            return self._roots[0] if self._roots else None
        # Extra roots compare case-insensitively (they encode a path).
        rid = str(root_id)
        for r in self._roots:
            if r.id == rid:
                return r
        # Case-insensitive fallback for the path-encoded extra roots.
        low = rid.lower()
        for r in self._roots:
            if r.id.lower() == low:
                return r
        return None


def ensure_root_dir(root: Root):
    """Create a root's base dir if it doesn't exist (personal home / subject
    shared folder are created lazily on first browse)."""
    try:
        os.makedirs(root.path, exist_ok=True)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Path resolution within a root
# ---------------------------------------------------------------------------

def _resolve(base_dir: str, rel_path: str) -> str:
    """Resolve ``rel_path`` under ``base_dir`` or raise 400 on escape."""
    rel = (rel_path or '').strip('/').strip('\\')
    full = storage.safe_join(base_dir, rel) if rel else os.path.abspath(base_dir)
    if not full:
        raise FileServiceError(400, 'Invalid path')
    return full


def _require_write(can_write: bool):
    if not can_write:
        raise FileServiceError(403, 'This location is read-only.')


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------

def list_dir(base_dir: str, rel_path: str) -> dict:
    """Directory listing: ``{current_path, items:[{name,is_dir,size,modified}]}``."""
    base_dir = os.path.abspath(base_dir)
    full = _resolve(base_dir, rel_path)
    if not os.path.isdir(full):
        raise FileServiceError(404, 'Directory not found or access denied')

    rel = os.path.relpath(full, base_dir).replace('\\', '/')
    if rel == '.':
        rel = ''

    try:
        names = os.listdir(full)
    except PermissionError:
        names = []
    names.sort(key=lambda x: (not os.path.isdir(os.path.join(full, x)), x.lower()))

    items = []
    for name in names:
        entry_path = os.path.join(full, name)
        try:
            is_dir = os.path.isdir(entry_path)
            st = os.stat(entry_path)
        except OSError:
            continue
        items.append({
            'name': name,
            'is_dir': is_dir,
            'size': st.st_size if not is_dir else None,
            'modified': st.st_mtime,
        })
    return {'current_path': rel, 'items': items}


def get_download_path(base_dir: str, rel_path: str) -> str:
    """Validated absolute path of a file to download (raises if not a file)."""
    rel = (rel_path or '').strip('/').strip('\\')
    if not rel:
        raise FileServiceError(400, 'No file specified')
    full = _resolve(base_dir, rel)
    if not os.path.isfile(full):
        raise FileServiceError(404, 'File not found or access denied')
    return full


def save_uploads(base_dir: str, rel_path: str, files, can_write: bool,
                 rel_paths=None) -> dict:
    """Save uploaded ``FileStorage`` objects into ``base_dir/rel_path``.

    When ``rel_paths`` is given (parallel to ``files``), each entry may carry a
    relative path (e.g. a folder upload's ``webkitRelativePath``); intermediate
    folders are created and every path component is sanitised, so a whole folder
    tree can be uploaded at once while staying inside the root.
    """
    _require_write(can_write)
    target_dir = _resolve(base_dir, rel_path)
    if not os.path.isdir(target_dir):
        raise FileServiceError(404, 'Target directory not found or access denied')

    files = list(files or [])
    rel_paths = list(rel_paths or [])
    pairs = [(f, rel_paths[i] if i < len(rel_paths) else '')
             for i, f in enumerate(files) if getattr(f, 'filename', '')]
    if not pairs:
        raise FileServiceError(400, 'No files provided')

    uploaded, errors = [], []
    for f, sub in pairs:
        sub = (sub or '').replace('\\', '/').strip('/')
        if sub:
            parts = [secure_filename(p) for p in sub.split('/')
                     if p not in ('', '.', '..')]
            parts = [p for p in parts if p]
            if not parts:
                errors.append(f'Invalid path: {sub}')
                continue
            dest = storage.safe_join(target_dir, *parts)
            if not dest:
                errors.append(f'Invalid path: {sub}')
                continue
            label = '/'.join(parts)
            try:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
            except OSError as e:  # pragma: no cover - disk errors
                errors.append(f'{label}: {e}')
                continue
        else:
            filename = secure_filename(f.filename)
            if not filename:
                errors.append(f'Invalid filename: {f.filename}')
                continue
            dest = os.path.join(target_dir, filename)
            label = filename
        try:
            f.save(dest)
            uploaded.append(label)
        except Exception as e:  # pragma: no cover - disk errors
            errors.append(f'{label}: {e}')
    return {
        'success': True, 'uploaded': uploaded, 'errors': errors,
        'message': f'Uploaded {len(uploaded)} file(s)'
                   + (f', {len(errors)} error(s)' if errors else ''),
    }


def rename(base_dir: str, rel_path: str, new_name: str, can_write: bool) -> dict:
    _require_write(can_write)
    rel = (rel_path or '').strip('/').strip('\\')
    new_name = (new_name or '').strip()
    if not rel or not new_name:
        raise FileServiceError(400, 'Path and new name are required')
    if '/' in new_name or '\\' in new_name:
        raise FileServiceError(400, 'New name cannot contain path separators')

    full = _resolve(base_dir, rel)
    if not os.path.exists(full):
        raise FileServiceError(404, 'File or directory not found')

    parent = os.path.dirname(full)
    new_full = storage.safe_join(parent, new_name)
    if not new_full:
        raise FileServiceError(403, 'Access denied')
    if os.path.exists(new_full):
        raise FileServiceError(409, f'A file or directory named "{new_name}" already exists')
    try:
        os.rename(full, new_full)
    except OSError as e:
        raise FileServiceError(500, str(e))
    return {'success': True, 'new_name': new_name}


def delete(base_dir: str, rel_paths, can_write: bool) -> dict:
    _require_write(can_write)
    base_dir = os.path.abspath(base_dir)
    rel_paths = rel_paths or []
    if not rel_paths:
        raise FileServiceError(400, 'No paths specified')

    deleted, errors = [], []
    for rel_path in rel_paths:
        rel = (rel_path or '').strip('/').strip('\\')
        if not rel:
            errors.append('Cannot delete root directory')
            continue
        full = storage.safe_join(base_dir, rel)
        if not full or not os.path.exists(full):
            errors.append(f'{rel_path}: not found')
            continue
        if os.path.normcase(os.path.abspath(full)) == os.path.normcase(base_dir):
            errors.append(f'{rel_path}: cannot delete the root')
            continue
        try:
            if os.path.isdir(full):
                shutil.rmtree(full)
            else:
                os.remove(full)
            deleted.append(rel_path)
        except OSError as e:
            errors.append(f'{rel_path}: {e}')
    return {
        'success': True, 'deleted': deleted, 'errors': errors,
        'message': f'Deleted {len(deleted)} item(s)'
                   + (f', {len(errors)} error(s)' if errors else ''),
    }


def mkdir(base_dir: str, parent_path: str, name: str, can_write: bool) -> dict:
    _require_write(can_write)
    name = (name or '').strip()
    if not name:
        raise FileServiceError(400, 'Directory name is required')
    if '/' in name or '\\' in name:
        raise FileServiceError(400, 'Directory name cannot contain path separators')

    parent_dir = _resolve(base_dir, parent_path)
    if not os.path.isdir(parent_dir):
        raise FileServiceError(404, 'Parent directory not found')

    new_dir = storage.safe_join(parent_dir, name)
    if not new_dir:
        raise FileServiceError(403, 'Access denied')
    if os.path.exists(new_dir):
        raise FileServiceError(409, f'"{name}" already exists')
    try:
        os.makedirs(new_dir)
    except OSError as e:
        raise FileServiceError(500, str(e))
    rel = os.path.relpath(new_dir, os.path.abspath(base_dir)).replace('\\', '/')
    return {'success': True, 'name': name, 'rel_path': rel}


def _unique_copy_name(dest_dir: str, name: str) -> str:
    if not os.path.exists(os.path.join(dest_dir, name)):
        return name
    base, ext = os.path.splitext(name)
    candidate = f'{base}_copy{ext}'
    if not os.path.exists(os.path.join(dest_dir, candidate)):
        return candidate
    n = 2
    while True:
        candidate = f'{base}_copy{n}{ext}'
        if not os.path.exists(os.path.join(dest_dir, candidate)):
            return candidate
        n += 1


def copy(base_dir: str, sources, dest_dir: str, can_write: bool) -> dict:
    """Copy files/dirs (within the same root) into ``dest_dir``."""
    _require_write(can_write)
    sources = sources or []
    if not sources:
        raise FileServiceError(400, 'No sources specified')

    dest_full = _resolve(base_dir, dest_dir)
    if not os.path.isdir(dest_full):
        raise FileServiceError(404, 'Destination directory not found or access denied')

    copied, errors = [], []
    for rel_path in sources:
        rel = (rel_path or '').strip('/').strip('\\')
        if not rel:
            errors.append('Cannot copy root directory')
            continue
        src_full = storage.safe_join(base_dir, rel)
        if not src_full or not os.path.exists(src_full):
            errors.append(f'{rel_path}: not found')
            continue
        if os.path.isdir(src_full):
            src_abs = os.path.normcase(os.path.abspath(src_full))
            dest_abs = os.path.normcase(os.path.abspath(dest_full))
            if dest_abs == src_abs or dest_abs.startswith(src_abs + os.sep):
                errors.append(f'{rel_path}: cannot copy a folder into itself')
                continue
        dest_name = _unique_copy_name(dest_full, os.path.basename(src_full))
        dest_item = os.path.join(dest_full, dest_name)
        try:
            if os.path.isdir(src_full):
                shutil.copytree(src_full, dest_item)
            else:
                shutil.copy2(src_full, dest_item)
            copied.append({'original': rel_path, 'new_name': dest_name})
        except OSError as e:
            errors.append(f'{rel_path}: {e}')
    return {
        'success': True, 'copied': copied, 'errors': errors,
        'message': f'Copied {len(copied)} item(s)'
                   + (f', {len(errors)} error(s)' if errors else ''),
    }


def move(base_dir: str, sources, dest_dir: str, can_write: bool) -> dict:
    """Move files/dirs (within the same root) into ``dest_dir`` (cut/paste)."""
    _require_write(can_write)
    sources = sources or []
    if not sources:
        raise FileServiceError(400, 'No sources specified')

    dest_full = _resolve(base_dir, dest_dir)
    if not os.path.isdir(dest_full):
        raise FileServiceError(404, 'Destination directory not found or access denied')
    dest_abs = os.path.normcase(os.path.abspath(dest_full))

    moved, errors = [], []
    for rel_path in sources:
        rel = (rel_path or '').strip('/').strip('\\')
        if not rel:
            errors.append('Cannot move root directory')
            continue
        src_full = storage.safe_join(base_dir, rel)
        if not src_full or not os.path.exists(src_full):
            errors.append(f'{rel_path}: not found')
            continue
        src_abs = os.path.normcase(os.path.abspath(src_full))
        # Can't move a folder into itself or its own subtree.
        if os.path.isdir(src_full) and (
                dest_abs == src_abs or dest_abs.startswith(src_abs + os.sep)):
            errors.append(f'{rel_path}: cannot move a folder into itself')
            continue
        # Already in the destination folder — nothing to do.
        if os.path.normcase(os.path.dirname(src_abs)) == dest_abs:
            errors.append(f'{rel_path}: already in this folder')
            continue
        dest_name = _unique_copy_name(dest_full, os.path.basename(src_full))
        dest_item = os.path.join(dest_full, dest_name)
        try:
            shutil.move(src_full, dest_item)
            moved.append({'original': rel_path, 'new_name': dest_name})
        except OSError as e:
            errors.append(f'{rel_path}: {e}')
    return {
        'success': True, 'moved': moved, 'errors': errors,
        'message': f'Moved {len(moved)} item(s)'
                   + (f', {len(errors)} error(s)' if errors else ''),
    }
