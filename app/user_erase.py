"""Full erase of a user account (Admin -> Users -> Delete).

Disable (``account_admin.set_active``) is the action for staff who leave: it
keeps every row and file and blocks sign-in. Delete is a deliberate erase of
the account and everything it owns, in one transaction:

* deleted: generated files (+ PDF siblings on disk), My Files sections,
  search profiles, generation presets and question sets (shared ones too),
  file shares on either side, subject permissions, SMS links and levels;
* set to NULL: "who did it" columns elsewhere (``NULL_REFS``);
* kept: every ``auth_audit_log`` row, plus a new ``user_deleted`` row.

Files are removed only after the commit succeeds, so a rollback leaves the disk
untouched. Every path is resolved and confined to ``USER_PATH`` (the user's own
folder) or ``OUTPUT_PATH`` (legacy files). Spec: docs/modules/admin-panel.md.

Service code: English reasons only; the route translates.
"""
from __future__ import annotations

import os

from flask import current_app
from sqlalchemy import delete, func, inspect, or_, select, update

from app import auth_audit, db, storage
from app.models import (
    ExternalIdentity, FileSection, FileShare, GeneratedFile, PromptOverride, PromptVariant,
    Question, SavedFilter, SavedGenerationProfile, SavedQuestionSet, SmsSnapshot, SmsSubjectMap,
    SmsTrustIssuer, SmsTrustKey, SubjectPromptNote, SubjectRestorePoint, SystemSetting,
    TagCorrection, User, UserSmsLevel, UserSubjectPermission,
)

IN_PROGRESS = ('pending', 'generating')

# (table, column): references that outlive the user and become NULL.
NULL_REFS = (
    (TagCorrection.__table__, 'user_id'),
    (SystemSetting.__table__, 'updated_by'),
    (PromptOverride.__table__, 'updated_by'),
    (SubjectPromptNote.__table__, 'updated_by'),
    (Question.__table__, 'verified_by'),
    (SubjectRestorePoint.__table__, 'user_id'),
    (ExternalIdentity.__table__, 'linked_by_user_id'),
    (SmsSnapshot.__table__, 'decided_by_user_id'),
    (SmsSubjectMap.__table__, 'confirmed_by_user_id'),
    (SmsTrustIssuer.__table__, 'updated_by_user_id'),
    (SmsTrustKey.__table__, 'pinned_by_user_id'),
)
# MyISAM on the live server (not transactional): written last, right before
# the user row, so a failure earlier in the erase leaves them untouched.
LATE_NULL_REFS = ((PromptVariant.__table__, 'updated_by'),)

OWNED = (
    ('saved_filters', SavedFilter.__table__),
    ('saved_generation_profiles', SavedGenerationProfile.__table__),
    ('saved_question_sets', SavedQuestionSet.__table__),
)
LINKS = (
    ('subject_permissions', UserSubjectPermission.__table__),
    ('sms_levels', UserSmsLevel.__table__),
    ('sms_identities', ExternalIdentity.__table__),
)


class EraseRefused(Exception):
    """A guard refused the erase; nothing was written."""

    def __init__(self, reason: str, status: int = 409, **params):
        super().__init__(reason)
        self.reason, self.status, self.params = reason, status, params


def _log():
    return current_app.logger


def _exec(stmt) -> int:
    return db.session.execute(stmt).rowcount


def _tables() -> set:
    return set(inspect(db.session.connection()).get_table_names())


def _count(table, *where) -> int:
    return db.session.execute(select(func.count()).select_from(table).where(*where)).scalar() or 0


# ------------------------------------------------------------------ plan

def _plan(user, tables, lock=False) -> dict:
    """Rows the erase touches. ``lock`` reads current data under share locks
    (inside the erase transaction) instead of the snapshot."""
    uid = user.id
    gf, fs, sh = GeneratedFile.__table__, FileSection.__table__, FileShare.__table__
    files_q = select(gf.c.id, gf.c.filename, gf.c.status).where(gf.c.user_id == uid)
    sections_q = select(fs.c.id).where(fs.c.user_id == uid)
    if lock:
        files_q, sections_q = files_q.with_for_update(read=True), sections_q.with_for_update(read=True)
    files = db.session.execute(files_q).all()
    section_ids = [r.id for r in db.session.execute(sections_q)]
    file_ids = [r.id for r in files]

    counts = {'generated_files': len(files), 'file_sections': len(section_ids)}
    for key, table in OWNED:
        counts[key] = _count(table, table.c.user_id == uid)
        counts[key + '_shared'] = _count(table, table.c.user_id == uid, table.c.is_shared.is_(True))
    counts['file_shares'] = _count(sh, or_(
        sh.c.shared_by_user_id == uid, sh.c.shared_with_user_id == uid,
        sh.c.file_id.in_(file_ids), sh.c.section_id.in_(section_ids)))
    for key, table in LINKS:
        counts[key] = _count(table, table.c.user_id == uid) if table.name in tables else 0
    return {
        'files': files, 'file_ids': file_ids, 'section_ids': section_ids, 'counts': counts,
        'in_progress': sum(1 for r in files if r.status in IN_PROGRESS),
    }


def _refusal(user, actor, plan):
    if actor is not None and user.id == actor.id:
        return EraseRefused('self', 400)
    if user.is_super_admin and user.is_active:
        others = _count(User.__table__, User.is_super_admin.is_(True), User.active.is_(True),
                        User.id != user.id)
        if not others:
            return EraseRefused('last_super_admin')
    if plan['in_progress']:
        return EraseRefused('job_running', n=plan['in_progress'])
    if _toolbox_save_running(user.id):
        return EraseRefused('toolbox_export')
    return None


def _toolbox_save_running(user_id) -> bool:
    """True while this user has a toolbox PDF save-to-server job in the
    in-memory registry (``app.toolbox.pdf._EXPORT_JOBS``). Download exports
    are not a guard. No registry (import failure) means nothing to refuse."""
    try:
        from app.toolbox.pdf import user_save_export_running
    except Exception:
        _log().exception('user erase: toolbox save-export registry unavailable')
        return False
    return bool(user_save_export_running(user_id))


# ------------------------------------------------------------------ paths

def _real(path) -> str:
    return os.path.normcase(os.path.realpath(path))


def _inside(path, root) -> bool:
    """``path`` (links resolved) is strictly below ``root``."""
    root_n, path_n = _real(root), _real(path)
    try:
        return path_n != root_n and os.path.commonpath([root_n, path_n]) == root_n
    except ValueError:
        return False


def _home_dir(user):
    """The user's own ``User/<name>`` folder, or None when it is unsafe to
    remove: escapes the root, is a link / junction, or another account maps to
    the same folder name (sanitised legacy names)."""
    name = storage.safe_username(user)
    root = storage.user_path()
    home = storage.safe_join(root, name)
    if not home or not _inside(home, root) or _real(os.path.dirname(home)) != _real(root):
        return None
    if _real(home) != os.path.normcase(os.path.join(os.path.realpath(root), name)):
        return None
    for (other,) in db.session.execute(select(User.username).where(User.id != user.id)):
        if storage.safe_username(other) == name:
            return None
    return home


def _file_targets(user, plan):
    """``(paths, home, refused)``: files to remove after the commit (main file
    + PDF sibling, located like ``generator.generated_file_dir``), the user's
    folder (or None) and how many targets were refused."""
    from app.generator import _pdf_sibling_filename  # avoid module cycle
    home = _home_dir(user)
    refused = 0
    if home is None and os.path.lexists(storage.user_home(user)):
        _log().warning('user erase: not removing folder %r (unsafe or shared name)', storage.user_home(user))
        refused += 1
    gen_dir = os.path.join(home, 'generated') if home else None
    legacy = storage.output_path()
    gf = GeneratedFile.__table__
    paths = []
    for row in plan['files']:
        name = row.filename or ''
        if not name or os.path.basename(name) != name or name in ('.', '..'):
            _log().warning('user erase: refusing generated file name %r (file %s)', name, row.id)
            refused += 1
            continue
        names = [n for n in (name, _pdf_sibling_filename(name)) if n]
        if gen_dir and os.path.lexists(os.path.join(gen_dir, name)):
            base, root = gen_dir, home
        elif os.path.lexists(os.path.join(legacy, name)):
            if _count(gf, gf.c.filename == name, gf.c.user_id != user.id):
                _log().warning('user erase: legacy file %r is also named by another user; kept', name)
                refused += 1
                continue
            base, root = legacy, legacy
        else:
            continue
        for n in names:
            path = storage.safe_join(base, n)
            if not path or not os.path.lexists(path):
                continue
            if not _inside(path, root):
                _log().warning('user erase: refusing path outside its root: %r', path)
                refused += 1
                continue
            paths.append(path)
    return paths, home, refused


def _remove_link(path):
    if os.name == 'nt':
        os.rmdir(path)
    else:
        os.unlink(path)


def _remove_files(paths, home) -> dict:
    """After commit. Never raises; every failure is logged and counted."""
    deleted = failed = 0
    for path in paths:
        try:
            os.remove(path)
            deleted += 1
        except FileNotFoundError:
            pass
        except OSError as exc:
            failed += 1
            _log().warning('user erase: could not delete %r (%s)', path, exc)
    if home and os.path.isdir(home):
        for dirpath, dirnames, filenames in os.walk(home, topdown=False, followlinks=False):
            for f in filenames:
                path = os.path.join(dirpath, f)
                try:
                    os.remove(path)
                    deleted += 1
                except OSError as exc:
                    failed += 1
                    _log().warning('user erase: could not delete %r (%s)', path, exc)
            for d in dirnames:
                path = os.path.join(dirpath, d)
                try:
                    if os.path.islink(path) or os.path.isjunction(path):
                        _remove_link(path)
                    else:
                        os.rmdir(path)
                except OSError as exc:
                    failed += 1
                    _log().warning('user erase: could not remove folder %r (%s)', path, exc)
        try:
            os.rmdir(home)
        except OSError as exc:
            failed += 1
            _log().warning('user erase: could not remove folder %r (%s)', home, exc)
    return {'files_deleted': deleted, 'files_failed': failed}


def _home_file_count(home) -> int:
    if not home or not os.path.isdir(home):
        return 0
    return sum(len(files) for _, _, files in os.walk(home, followlinks=False))


# ------------------------------------------------------------------ public

def preview(user, actor) -> dict:
    """Counts for the confirm dialog (read only). ``refusal`` is an
    ``EraseRefused`` or None."""
    tables = _tables()
    plan = _plan(user, tables)
    home = _home_dir(user)
    return {
        'user': {'id': user.id, 'username': user.username, 'active': user.is_active,
                 'is_super_admin': bool(user.is_super_admin)},
        'counts': plan['counts'],
        'sms_linked': plan['counts']['sms_identities'] > 0,
        'home_files': _home_file_count(home),
        'in_progress': plan['in_progress'],
        'refusal': _refusal(user, actor, plan),
    }


def _null(table, column, uid) -> int:
    col = table.c[column]
    values = {column: None}
    for c in table.c:
        if c.onupdate is not None:
            values[c.name] = c  # keep e.g. updated_at as it was
    return _exec(update(table).where(col == uid).values(values))


def erase_user(user, actor, ip=None) -> dict:
    """Erase ``user`` (see module doc). Raises ``EraseRefused`` (nothing
    written) or re-raises any other error after a rollback (no file touched)."""
    uid, username = user.id, user.username
    try:
        if actor is not None and uid == actor.id:
            raise EraseRefused('self', 400)
        actor_id = actor.id if actor is not None else None
        db.session.execute(select(User.id).where(User.id == uid).with_for_update())
        tables = _tables()
        plan = _plan(user, tables, lock=True)
        refusal = _refusal(user, actor, plan)
        if refusal:
            raise refusal
        paths, home, refused = _file_targets(user, plan)
        file_ids, section_ids = plan['file_ids'], plan['section_ids']

        nulled = {}
        for table, column in NULL_REFS:
            if table.name in tables:
                nulled[f'{table.name}.{column}'] = _null(table, column, uid)
        for _key, table in OWNED:
            _exec(delete(table).where(table.c.user_id == uid))
        gf, fs, sh = GeneratedFile.__table__, FileSection.__table__, FileShare.__table__
        if section_ids:
            _exec(update(gf).where(gf.c.section_id.in_(section_ids), gf.c.user_id != uid)
                  .values(section_id=None))
        _exec(delete(gf).where(gf.c.user_id == uid))
        _exec(delete(fs).where(fs.c.user_id == uid))
        for _key, table in LINKS:
            if table.name in tables:
                _exec(delete(table).where(table.c.user_id == uid))
        for table, column in LATE_NULL_REFS:
            if table.name in tables:
                nulled[f'{table.name}.{column}'] = _null(table, column, uid)
        _exec(delete(sh).where(or_(
            sh.c.shared_by_user_id == uid, sh.c.shared_with_user_id == uid,
            sh.c.file_id.in_(file_ids), sh.c.section_id.in_(section_ids))))
        if _exec(delete(User.__table__).where(User.id == uid)) != 1:
            raise RuntimeError(f'user {uid} row not deleted')

        counts = plan['counts']
        if 'auth_audit_log' in tables:
            auth_audit.record(auth_audit.USER_DELETED, 'ok', user_id=uid, actor_id=actor_id, ip=ip,
                              detail={'username': username, 'deleted': counts,
                                      'nulled': {k: v for k, v in nulled.items() if v}})
        else:
            _log().warning('user erase: auth_audit_log missing; no audit row for user %s', uid)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    if user in db.session:
        db.session.expunge(user)
    _log().info('user erase: user %s (%s) erased by %s', uid, username, actor_id)
    result = _remove_files(paths, home)
    result['files_refused'] = refused
    return {'counts': counts, 'nulled': nulled, **result}
