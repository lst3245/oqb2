"""User groups: a named set of per-subject roles, inherited by members.

Effective access is computed in ``app/authz.py`` (the highest of the personal
grant, every group, and SMS). This module only creates groups and replaces
their grants and memberships. It never writes ``user_subject_permissions``
or ``user_sms_levels``, and it never grants super admin.

The route translates ``GroupError.code``. Callers commit.
"""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import Subject, User, UserGroup, UserGroupMember, UserGroupPermission

ROLES = ('viewer', 'user', 'admin')
NAME_MAX = 80
DESC_MAX = 200


class GroupError(Exception):
    """A rejected edit. ``code`` is mapped to a translated message by the route."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def clean_name(raw) -> str:
    name = (raw or '').strip()
    if not name:
        raise GroupError('name_required')
    if len(name) > NAME_MAX:
        raise GroupError('name_too_long')
    return name


def clean_description(raw) -> str | None:
    text = (raw or '').strip()
    if len(text) > DESC_MAX:
        raise GroupError('description_too_long')
    return text or None


def _name_taken(name: str, except_id: int | None = None) -> bool:
    query = UserGroup.query.filter(func.lower(UserGroup.name) == name.lower())
    if except_id is not None:
        query = query.filter(UserGroup.id != except_id)
    return query.first() is not None


def create_group(name, description=None) -> UserGroup:
    name = clean_name(name)
    description = clean_description(description)
    if _name_taken(name):
        raise GroupError('name_taken')
    row = UserGroup(name=name, description=description)
    db.session.add(row)
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        raise GroupError('name_taken')
    return row


def rename_group(group: UserGroup, name, description) -> None:
    name = clean_name(name)
    description = clean_description(description)
    if _name_taken(name, except_id=group.id):
        raise GroupError('name_taken')
    group.name = name
    group.description = description
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        raise GroupError('name_taken')


def delete_group(group: UserGroup) -> None:
    """Remove the group, its grants and its memberships. Personal grants
    and SMS levels are not touched."""
    UserGroupPermission.query.filter_by(group_id=group.id).delete(synchronize_session=False)
    UserGroupMember.query.filter_by(group_id=group.id).delete(synchronize_session=False)
    db.session.delete(group)


def apply_permissions(group: UserGroup, permissions) -> None:
    """Merge ``{subject_id: role|''|None}``. Only the submitted keys change.
    A blank role removes that subject's grant."""
    if permissions is None:
        permissions = {}
    if not isinstance(permissions, dict):
        raise GroupError('bad_permissions')
    known = {sid for (sid,) in db.session.query(Subject.id)}
    for subject_id, role in permissions.items():
        sid = str(subject_id)
        if sid not in known:
            raise GroupError('unknown_subject')
        existing = UserGroupPermission.query.filter_by(
            group_id=group.id, subject_id=sid).first()
        if role is None or role == '':
            if existing:
                db.session.delete(existing)
            continue
        if role not in ROLES:
            raise GroupError('bad_role')
        if existing:
            existing.role = role
        else:
            db.session.add(UserGroupPermission(group_id=group.id, subject_id=sid, role=role))


def _id_list(raw, code: str) -> list[int]:
    if not isinstance(raw, list):
        raise GroupError(code)
    out = []
    seen = set()
    for item in raw:
        try:
            number = int(item)
        except (TypeError, ValueError):
            raise GroupError(code)
        if number < 1 or number in seen:
            if number < 1:
                raise GroupError(code)
            continue
        seen.add(number)
        out.append(number)
    return out


def set_members(group: UserGroup, user_ids) -> None:
    """Replace the group's members with ``user_ids`` (an empty list clears it)."""
    ids = _id_list(user_ids, 'bad_user_ids')
    if ids:
        found = {uid for (uid,) in db.session.query(User.id).filter(User.id.in_(ids))}
        if found != set(ids):
            raise GroupError('unknown_user')
    UserGroupMember.query.filter_by(group_id=group.id).delete(synchronize_session=False)
    for uid in ids:
        db.session.add(UserGroupMember(user_id=uid, group_id=group.id))


def set_user_groups(user: User, group_ids) -> None:
    """Replace the user's memberships. Does not change personal grants or SMS."""
    ids = _id_list(group_ids, 'bad_group_ids')
    if ids:
        found = {gid for (gid,) in db.session.query(UserGroup.id).filter(UserGroup.id.in_(ids))}
        if found != set(ids):
            raise GroupError('unknown_group')
    UserGroupMember.query.filter_by(user_id=user.id).delete(synchronize_session=False)
    for gid in ids:
        db.session.add(UserGroupMember(user_id=user.id, group_id=gid))


def list_rows() -> list[dict]:
    """One dict per group: the model, how many members, and an access summary."""
    from app.users_directory import summarize
    groups = UserGroup.query.order_by(UserGroup.name, UserGroup.id).all()
    subject_ids = [sid for (sid,) in db.session.query(Subject.id).order_by(Subject.id)]
    grants = defaultdict(dict)
    counts = defaultdict(int)
    if groups:
        ids = [g.id for g in groups]
        for gid, sid, role in (db.session.query(
                UserGroupPermission.group_id, UserGroupPermission.subject_id,
                UserGroupPermission.role)
                .filter(UserGroupPermission.group_id.in_(ids))):
            grants[gid][sid] = role
        for gid, n in (db.session.query(UserGroupMember.group_id, func.count())
                       .filter(UserGroupMember.group_id.in_(ids))
                       .group_by(UserGroupMember.group_id)):
            counts[gid] = n
    return [{
        'group': group,
        'member_count': counts[group.id],
        'summary': summarize(grants[group.id], subject_ids,
                             super_admin=False, active=True),
    } for group in groups]


def access_summary(group: UserGroup) -> str:
    from app.users_directory import summarize
    subject_ids = [sid for (sid,) in db.session.query(Subject.id).order_by(Subject.id)]
    grants = {row.subject_id: row.role for row in
              UserGroupPermission.query.filter_by(group_id=group.id)}
    return summarize(grants, subject_ids, super_admin=False, active=True)


def member_count(group: UserGroup) -> int:
    return UserGroupMember.query.filter_by(group_id=group.id).count()


def panel_context(group: UserGroup) -> dict:
    subjects = Subject.query.order_by(Subject.id).all()
    grants = {row.subject_id: row.role for row in
              UserGroupPermission.query.filter_by(group_id=group.id)}
    member_ids = {uid for (uid,) in db.session.query(UserGroupMember.user_id)
                  .filter_by(group_id=group.id)}
    users = User.query.order_by(User.username, User.id).all()
    return {
        'group': group,
        'subjects': subjects,
        'grants': grants,
        'users': users,
        'member_ids': member_ids,
    }
