"""Verify SMS handoff tokens and directory snapshots (contract v1.2 sections
6, 10 and 12). Pure: no Flask, no DB, the clock is a parameter.

The checks run in the order of SMS's reference verifier
(tests/fixtures/sms/verify.py); the first failure wins and raises
``VerifyError(reason)`` with a section 12 reason code. Exception text never
contains the token. Levels in the result are OQB level names
(``sms_levels.to_oqb_level``), already capped for non-primary issuers.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
import uuid as uuidlib
from dataclasses import asdict, dataclass

from app import sms_levels
from app.sms_trust import is_contract_test_issuer, PRODUCTION

SKEW_SECONDS = 30
HANDOFF_MAX_LIFETIME = 120
SNAPSHOT_MAX_LIFETIME = 600
MAX_TOKEN_BYTES = 8 * 1024
MAX_SNAPSHOT_BYTES = 5 * 1024 * 1024
MAX_SUBJECTS = 200
MAX_CATALOGUE = 5000
MAX_USERS = 50000

PURPOSE_HANDOFF = 'app_handoff'
PURPOSE_SNAPSHOT = 'directory_snapshot'
AUDIENCE = 'oqb'

# Section 12 codes, plus OQB-local ones (audit only; the user sees the
# generic page).
REASONS = (
    'malformed', 'unknown_issuer', 'issuer_disabled', 'bad_alg', 'unknown_kid',
    'bad_signature', 'bad_aud', 'bad_purpose_or_ver', 'not_yet_valid', 'expired',
    'bad_lifetime', 'replay', 'sub_not_allowed', 'not_linked', 'privileged_account',
    'disabled', 'stale_snapshot',
)
LOCAL_REASONS = ('server_error', 'bad_origin', 'rate_limited')

_SEGMENT = re.compile(r'^[A-Za-z0-9_-]+$')
_LIMITS = {'email': 254, 'name': 200, 'name_zh': 200, 'staff_code': 32,
           'academic_year': 32, 'code': 32, 'name_en': 200}


class VerifyError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class VerifiedSubject:
    uuid: str
    code: str
    name_en: str | None
    name_zh: str | None
    level: str  # OQB level name


@dataclass(frozen=True)
class VerifiedHandoff:
    issuer: str
    sub: str
    jti: str
    kid: str
    iat: int
    nbf: int
    exp: int
    email: str
    name: str
    name_zh: str | None
    staff_code: str | None
    academic_year: str | None
    default_level: str | None  # OQB level name; None = no access from SMS
    subjects: tuple
    primary: bool
    capped: bool

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> 'VerifiedHandoff':
        data = json.loads(text)
        data['subjects'] = tuple(VerifiedSubject(**s) for s in data.get('subjects') or ())
        return cls(**data)


@dataclass(frozen=True)
class CatalogueSubject:
    uuid: str
    code: str
    name_en: str
    name_zh: str | None
    is_active: bool


@dataclass(frozen=True)
class SnapshotUser:
    sub: str
    active: bool
    email: str | None
    name: str | None
    name_zh: str | None
    staff_code: str | None
    sms_default_level: str  # the SMS label, for the roster display
    default_level: str | None  # OQB level name
    subjects: tuple  # VerifiedSubject (name fields None)
    sms_subjects: tuple  # raw ``{uuid, code, level}`` dicts for the roster


@dataclass(frozen=True)
class VerifiedSnapshot:
    issuer: str
    jti: str
    snapshot_id: str
    kid: str
    iat: int
    nbf: int
    exp: int
    catalogue: tuple
    users: tuple
    invalid_entries: tuple  # ``(index, sub or None, why)``


# ---------------------------------------------------------------- helpers

def _fail(reason):
    raise VerifyError(reason)


def _b64json(segment: str) -> dict:
    try:
        raw = base64.urlsafe_b64decode(segment + '=' * (-len(segment) % 4))
        value = json.loads(raw.decode('utf-8'))
    except (ValueError, binascii.Error, UnicodeDecodeError):
        _fail('malformed')
    if not isinstance(value, dict):
        _fail('malformed')
    return value


def _shape(token, limit: int):
    if isinstance(token, (bytes, bytearray)):
        if len(token) > limit:
            _fail('malformed')
        try:
            token = bytes(token).decode('ascii')
        except UnicodeDecodeError:
            _fail('malformed')
    if not isinstance(token, str):
        _fail('malformed')
    token = token.strip()
    if not token or len(token.encode('utf-8')) > limit:
        _fail('malformed')
    parts = token.split('.')
    # An empty signature (alg "none") is left to the algorithm check: bad_alg.
    if len(parts) != 3 or not all(p and _SEGMENT.match(p) for p in parts[:2]):
        _fail('malformed')
    if parts[2] and not _SEGMENT.match(parts[2]):
        _fail('malformed')
    return token, _b64json(parts[0]), _b64json(parts[1])


def _entry(trust, claims, now):
    issuer = claims.get('iss')
    entry = trust.get(issuer) if isinstance(issuer, str) else None
    if entry is None:
        _fail('unknown_issuer')
    if getattr(trust, 'environment', PRODUCTION) == PRODUCTION and is_contract_test_issuer(issuer):
        _fail('unknown_issuer')
    if not entry.active(now):
        _fail('issuer_disabled')
    if entry.primary and trust.primary(now) is not entry:
        _fail('issuer_disabled')
    return entry


def _key(entry, header, resolver):
    if header.get('alg') != 'RS256':
        _fail('bad_alg')
    kid = header.get('kid')
    if not isinstance(kid, str) or not kid:
        _fail('unknown_kid')
    key = entry.keys.get(kid)
    if key is None and resolver is not None:
        key = resolver(entry, kid)
    if key is None:
        _fail('unknown_kid')
    return kid, key


def _signature(token, key) -> dict:
    import jwt
    try:
        decoded = jwt.PyJWS().decode_complete(token, key=key.public_key, algorithms=['RS256'])
    except jwt.InvalidAlgorithmError:
        _fail('bad_alg')
    except jwt.PyJWTError:
        _fail('bad_signature')
    try:
        claims = json.loads(decoded['payload'].decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        _fail('malformed')
    if not isinstance(claims, dict):
        _fail('malformed')
    return claims


def _audience_purpose(claims, purpose):
    aud = claims.get('aud')
    if not (aud == AUDIENCE or (isinstance(aud, list) and AUDIENCE in aud)):
        _fail('bad_aud')
    ver = claims.get('ver')
    if claims.get('purpose') != purpose or type(ver) is not int or ver != 1:
        _fail('bad_purpose_or_ver')


def _int(value):
    return type(value) is int


def _times(claims, now, max_lifetime):
    iat, nbf, exp = claims.get('iat'), claims.get('nbf'), claims.get('exp')
    if not (_int(iat) and _int(nbf) and _int(exp)):
        _fail('malformed')
    if now < nbf - SKEW_SECONDS or iat > now + SKEW_SECONDS:
        _fail('not_yet_valid')
    if now >= exp + SKEW_SECONDS:
        _fail('expired')
    if exp - iat > max_lifetime or exp < iat:
        _fail('bad_lifetime')
    return iat, nbf, exp


def canonical_uuid(value):
    """Lower-case canonical 8-4-4-4-12 UUID string, or ``None``."""
    if not isinstance(value, str) or len(value) != 36:
        return None
    try:
        parsed = uuidlib.UUID(value)
    except ValueError:
        return None
    text = str(parsed)
    return text if text == value.lower() else None


_uuid = canonical_uuid


def _text(value, name, *, nullable=False):
    """``(ok, value)``: a string within its length cap, or ``None`` when allowed."""
    if value is None:
        return nullable, None
    if not isinstance(value, str) or len(value) > _LIMITS.get(name, 200):
        return False, None
    return True, value


# ---------------------------------------------------------------- handoff

def _handoff_claims(claims):
    sub = _uuid(claims.get('sub'))
    jti = _uuid(claims.get('jti'))
    if not sub or not jti:
        _fail('malformed')
    fields = {}
    for name, nullable in (('email', False), ('name', False), ('name_zh', True),
                           ('staff_code', True), ('academic_year', True)):
        ok, value = _text(claims.get(name), name, nullable=nullable)
        if not ok:
            _fail('malformed')
        fields[name] = value
    default = claims.get('default_level')
    if not sms_levels.is_default_level(default):
        _fail('malformed')
    subjects = claims.get('subjects')
    if not isinstance(subjects, list) or len(subjects) > MAX_SUBJECTS:
        _fail('malformed')
    if default == sms_levels.ADMIN and subjects:
        _fail('malformed')
    parsed, seen = [], set()
    for item in subjects:
        if not isinstance(item, dict):
            _fail('malformed')
        uid = _uuid(item.get('uuid'))
        level = item.get('level')
        ok_code, code = _text(item.get('code'), 'code')
        ok_en, name_en = _text(item.get('name_en'), 'name_en')
        ok_zh, name_zh = _text(item.get('name_zh'), 'name_zh', nullable=True)
        if not (uid and ok_code and code and ok_en and ok_zh):
            _fail('malformed')
        if uid in seen or not sms_levels.is_subject_level(level):
            _fail('malformed')
        if not sms_levels.above(level, default):
            _fail('malformed')
        seen.add(uid)
        parsed.append((uid, code, name_en, name_zh, level))
    return sub, jti, fields, default, parsed


def _apply_cap(default, subjects, capped):
    """SMS labels -> OQB levels; the cap drops entries no longer above the default."""
    if capped:
        default = sms_levels.cap(default, 'default')
    out = []
    for uid, code, name_en, name_zh, level in subjects:
        if capped:
            level = sms_levels.cap(level, 'subject')
            if not sms_levels.above(level, default):
                continue
        out.append(VerifiedSubject(uuid=uid, code=code, name_en=name_en, name_zh=name_zh,
                                   level=sms_levels.to_oqb_level(level)))
    return sms_levels.to_oqb_level(default), tuple(out)


def verify_handoff(token, trust, now: int, key_resolver=None) -> VerifiedHandoff:
    token, header, unverified = _shape(token, MAX_TOKEN_BYTES)          # 1
    entry = _entry(trust, unverified, now)                              # 2
    kid, key = _key(entry, header, key_resolver)                        # 3
    claims = _signature(token, key)                                     # 4
    if claims.get('iss') != entry.issuer:
        _fail('bad_signature')
    _audience_purpose(claims, PURPOSE_HANDOFF)                          # 5
    iat, nbf, exp = _times(claims, now, HANDOFF_MAX_LIFETIME)           # 6
    sub, jti, fields, default, subjects = _handoff_claims(claims)       # 7
    if not entry.primary and sub not in entry.allowed_subs:             # 8
        _fail('sub_not_allowed')
    default_oqb, subjects_oqb = _apply_cap(default, subjects, entry.capped)
    return VerifiedHandoff(
        issuer=entry.issuer, sub=sub, jti=jti, kid=kid, iat=iat, nbf=nbf, exp=exp,
        email=fields['email'], name=fields['name'], name_zh=fields['name_zh'],
        staff_code=fields['staff_code'], academic_year=fields['academic_year'],
        default_level=default_oqb, subjects=subjects_oqb,
        primary=entry.primary, capped=entry.capped,
    )


# ---------------------------------------------------------------- snapshot

def _catalogue(items):
    if not isinstance(items, list) or len(items) > MAX_CATALOGUE:
        _fail('malformed')
    out, seen = [], set()
    for item in items:
        if not isinstance(item, dict):
            _fail('malformed')
        uid = _uuid(item.get('uuid'))
        ok_code, code = _text(item.get('code'), 'code')
        ok_en, name_en = _text(item.get('name_en'), 'name_en')
        ok_zh, name_zh = _text(item.get('name_zh'), 'name_zh', nullable=True)
        active = item.get('is_active')
        if not (uid and ok_code and code and ok_en and ok_zh and isinstance(active, bool)):
            _fail('malformed')
        if uid in seen:
            _fail('malformed')
        seen.add(uid)
        out.append(CatalogueSubject(uuid=uid, code=code, name_en=name_en or '',
                                    name_zh=name_zh, is_active=active))
    return tuple(out)


def _snapshot_user(item):
    """``(SnapshotUser, None)`` or ``(None, why)``."""
    if not isinstance(item, dict):
        return None, 'not_an_object'
    sub = _uuid(item.get('sub'))
    if not sub:
        return None, 'bad_sub'
    active = item.get('active')
    if not isinstance(active, bool):
        return None, 'bad_active'
    fields = {}
    for name in ('email', 'name', 'name_zh', 'staff_code'):
        ok, value = _text(item.get(name), name, nullable=True)
        if not ok:
            return None, f'bad_{name}'
        fields[name] = value
    default = item.get('default_level')
    if not sms_levels.is_default_level(default):
        return None, 'bad_default_level'
    subjects = item.get('subjects')
    if not isinstance(subjects, list) or len(subjects) > MAX_SUBJECTS:
        return None, 'bad_subjects'
    if default == sms_levels.ADMIN and subjects:
        return None, 'admin_with_subjects'
    if not active and (default != sms_levels.NONE or subjects):
        return None, 'inactive_with_levels'
    parsed, raw, seen = [], [], set()
    for s in subjects:
        if not isinstance(s, dict):
            return None, 'bad_subject'
        uid = _uuid(s.get('uuid'))
        ok_code, code = _text(s.get('code'), 'code')
        level = s.get('level')
        if not (uid and ok_code and code) or uid in seen:
            return None, 'bad_subject'
        if not sms_levels.is_subject_level(level) or not sms_levels.above(level, default):
            return None, 'bad_subject_level'
        seen.add(uid)
        parsed.append(VerifiedSubject(uuid=uid, code=code, name_en=None, name_zh=None,
                                      level=sms_levels.to_oqb_level(level)))
        raw.append({'uuid': uid, 'code': code, 'level': level})
    return SnapshotUser(
        sub=sub, active=active, email=fields['email'], name=fields['name'],
        name_zh=fields['name_zh'], staff_code=fields['staff_code'],
        sms_default_level=default, default_level=sms_levels.to_oqb_level(default),
        subjects=tuple(parsed), sms_subjects=tuple(raw),
    ), None


def verify_snapshot(body, trust, now: int, key_resolver=None) -> VerifiedSnapshot:
    token, header, unverified = _shape(body, MAX_SNAPSHOT_BYTES)        # 1
    entry = _entry(trust, unverified, now)                              # 2
    kid, key = _key(entry, header, key_resolver)                        # 3
    claims = _signature(token, key)                                     # 4
    if claims.get('iss') != entry.issuer:
        _fail('bad_signature')
    _audience_purpose(claims, PURPOSE_SNAPSHOT)                         # 5
    iat, nbf, exp = _times(claims, now, SNAPSHOT_MAX_LIFETIME)          # 6
    if not entry.primary or not entry.accepts_feed or entry is not trust.primary(now):
        _fail('issuer_disabled')
    jti = _uuid(claims.get('jti'))                                      # 7
    snapshot_id = _uuid(claims.get('snapshot_id'))
    if not jti or not snapshot_id:
        _fail('malformed')
    catalogue = _catalogue(claims.get('subjects'))
    users_raw = claims.get('users')
    if not isinstance(users_raw, list) or len(users_raw) > MAX_USERS:
        _fail('malformed')
    users, invalid, seen = [], [], set()
    for index, item in enumerate(users_raw):
        user, why = _snapshot_user(item)
        if user is not None and user.sub in seen:
            user, why = None, 'duplicate_sub'
        if user is None:
            raw_sub = item.get('sub') if isinstance(item, dict) else None
            invalid.append((index, raw_sub if isinstance(raw_sub, str) else None, why))
            continue
        seen.add(user.sub)
        users.append(user)
    return VerifiedSnapshot(
        issuer=entry.issuer, jti=jti, snapshot_id=snapshot_id, kid=kid,
        iat=iat, nbf=nbf, exp=exp, catalogue=catalogue, users=tuple(users),
        invalid_entries=tuple(invalid),
    )


def check_fresh(snapshot, last_iat: int, jti_seen: bool) -> None:
    """``iat`` must be newer than the last applied one and the jti unseen."""
    if snapshot.iat <= int(last_iat or 0) or jti_seen:
        _fail('stale_snapshot')


_FEED_STATUS = {
    'malformed': 400, 'bad_aud': 400, 'bad_purpose_or_ver': 400,
    'issuer_disabled': 403, 'stale_snapshot': 409,
}


def feed_status(reason: str) -> int:
    """HTTP status of a refused snapshot (contract section 10 table)."""
    return _FEED_STATUS.get(reason, 401)
