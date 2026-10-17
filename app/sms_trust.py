"""SMS trust list: which issuers OQB accepts, with which pinned keys
(contract v1.2 sections 2-3; docs/modules/sms-sso.md).

Source of truth is the DB (``sms_trust_issuers`` + ``sms_trust_keys``). In
test / staging / dev only, ``SMS_TRUST_BOOTSTRAP_FILE`` replaces it. Guards
that always apply:

* contract-test issuers (host ending in ``.invalid``) are dropped unless the
  environment is test / staging / dev;
* a non-primary entry never allows JIT or the feed, is always level-capped,
  and counts as enabled only with a future ``enabled_until`` and a non-empty
  ``allowed_subs``;
* SSO and the feed need exactly one enabled primary (``TrustList.primary``).
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field, replace
from datetime import datetime
from urllib.parse import urlparse

from app import sms_clock

log = logging.getLogger(__name__)

PRODUCTION = 'production'
NON_PRODUCTION = ('staging', 'test', 'dev')

CACHE_SECONDS = 60
NON_PRIMARY_MAX_DAYS = 7
JWKS_TIMEOUT_SECONDS = 5
JWKS_CACHE_SECONDS = 3600
JWKS_REFETCH_SECONDS = 300
MIN_RSA_BITS = 2048

# Recorded issuers (contract section 2 table). Nothing here is ever pinned
# automatically: a super admin pins a key after checking its fingerprint.
LIVE_ISSUER = 'https://ms.tic.edu.hk'
LIVE_JWKS_URL = 'https://ms.tic.edu.hk/api/v1/apps/jwks.json'
LIVE_KID = 'sms-live-202610'  # public PEM: deploy/sms/sms-live-202610.pub.pem
LIVE_FINGERPRINT = 'fed812e5e0ebcc47e44ec3942ccc74db8310ac9ea873cd56bfe628e9ca5ea1f2'
WORKSHOP_ISSUER = 'https://ms-test.tic.edu.hk'
# Every hostname of the workshop serves it (also tic-test.mathedu.info).
WORKSHOP_JWKS_URL = 'https://ms-test.tic.edu.hk/api/v1/apps/jwks.json'
WORKSHOP_KID = 'sms-test-202610'
WORKSHOP_FINGERPRINT = '450e8b5093650aa7c1feafd9458582c3c66c27687d19e79866d055ee8f239d2b'


# ---------------------------------------------------------------- data

@dataclass(frozen=True)
class TrustKey:
    kid: str
    public_key: object
    fingerprint: str


@dataclass(frozen=True)
class TrustEntry:
    issuer: str
    enabled: bool = False
    enabled_until: int | None = None  # unix seconds
    primary: bool = False
    allow_jit: bool = False
    accepts_feed: bool = False
    allowed_subs: frozenset = frozenset()
    level_cap: bool = False
    jwks_url: str | None = None
    jwks_auto: bool = False
    keys: dict = field(default_factory=dict, hash=False, compare=False)

    @property
    def capped(self) -> bool:
        return self.level_cap or not self.primary

    def active(self, now: int) -> bool:
        if not self.enabled:
            return False
        if self.enabled_until is not None and now >= self.enabled_until:
            return False
        if not self.primary:
            return self.enabled_until is not None and bool(self.allowed_subs)
        return True


class TrustList:
    def __init__(self, entries=(), environment_name: str = PRODUCTION, source: str = 'db'):
        self.entries = {e.issuer: e for e in entries}
        self.environment = environment_name
        self.source = source

    def get(self, issuer):
        if not isinstance(issuer, str):
            return None
        return self.entries.get(issuer)

    def primary(self, now: int):
        """The one enabled primary entry; ``None`` (fail closed) unless exactly one."""
        found = [e for e in self.entries.values() if e.primary and e.active(now)]
        return found[0] if len(found) == 1 else None

    def issuer_caps(self, now: int) -> dict:
        """``{issuer: capped}`` for entries whose stored SMS levels count now."""
        primary = self.primary(now)
        return {e.issuer: (e is not primary) or e.level_cap
                for e in self.entries.values() if e.active(now)}


# ---------------------------------------------------------------- environment

def environment() -> str:
    """``OQB_ENVIRONMENT``; anything unknown or empty means production."""
    value = None
    try:
        from flask import current_app, has_app_context
        if has_app_context():
            value = current_app.config.get('OQB_ENVIRONMENT')
    except Exception:
        value = None
    if value is None:
        value = os.environ.get('OQB_ENVIRONMENT')
    value = (value or '').strip().lower()
    return value if value in NON_PRODUCTION else PRODUCTION


def is_contract_test_issuer(issuer) -> bool:
    try:
        host = (urlparse(str(issuer)).hostname or '').lower()
    except ValueError:
        return True
    return host == 'invalid' or host.endswith('.invalid')


def normalise(entries, env: str) -> list:
    """Apply the guards that never depend on the clock. Pure."""
    out = []
    for e in entries:
        if env == PRODUCTION and is_contract_test_issuer(e.issuer):
            continue
        if not e.primary:
            e = replace(e, allow_jit=False, accepts_feed=False, level_cap=True)
        out.append(e)
    return out


# ---------------------------------------------------------------- keys

def fingerprint(public_key) -> str:
    from cryptography.hazmat.primitives import serialization
    der = public_key.public_bytes(serialization.Encoding.DER,
                                  serialization.PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(der).hexdigest()


def parse_public_pem(pem) -> tuple:
    """``(public_key, fingerprint)``; RSA of at least 2048 bits only."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
    data = pem.encode('ascii') if isinstance(pem, str) else bytes(pem)
    key = serialization.load_pem_public_key(data)
    if not isinstance(key, RSAPublicKey):
        raise ValueError('not an RSA public key')
    if key.key_size < MIN_RSA_BITS:
        raise ValueError(f'RSA key smaller than {MIN_RSA_BITS} bits')
    return key, fingerprint(key)


def _trust_key(kid, pem) -> TrustKey:
    key, fp = parse_public_pem(pem)
    return TrustKey(kid=kid, public_key=key, fingerprint=fp)


def _sub_items(values) -> list:
    """``[{'sub', 'label'}]`` from strings or ``{'sub', 'label'}`` objects."""
    out = []
    for v in values or ():
        if isinstance(v, dict):
            sub, label = str(v.get('sub') or '').strip().lower(), (v.get('label') or None)
        else:
            sub, label = str(v).strip().lower(), None
        if sub:
            out.append({'sub': sub, 'label': str(label)[:80] if label else None})
    return out


def _subs(values) -> frozenset:
    return frozenset(item['sub'] for item in _sub_items(values))


def allowed_sub_items(row) -> list:
    """The allowlist of a trust row with optional account labels."""
    try:
        return _sub_items(json.loads(row.allowed_subs_json or '[]'))
    except ValueError:
        return []


def normalise_fingerprint(value) -> str:
    return str(value or '').strip().lower().replace(':', '').replace(' ', '')


def _ts(value) -> int | None:
    if value is None or value == '':
        return None
    if isinstance(value, datetime):
        return sms_clock.to_ts(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    return sms_clock.to_ts(datetime.fromisoformat(str(value).replace('Z', '')))


# ---------------------------------------------------------------- loading

_cache_lock = threading.Lock()
_cache = {'at': 0.0, 'list': None}
_bootstrap_alerted = False


def invalidate() -> None:
    with _cache_lock:
        _cache['list'] = None
        _cache['at'] = 0.0


def _bootstrap_path() -> str:
    try:
        from flask import current_app, has_app_context
        if has_app_context():
            return (current_app.config.get('SMS_TRUST_BOOTSTRAP_FILE') or '').strip()
    except Exception:
        pass
    return (os.environ.get('SMS_TRUST_BOOTSTRAP_FILE') or '').strip()


def entries_from_bootstrap(data: dict, base_dir: str) -> list:
    """Entries from the bootstrap JSON (format in docs/modules/sms-sso.md)."""
    out = []
    for item in data.get('issuers') or ():
        keys = {}
        for k in item.get('keys') or ():
            kid = k.get('kid')
            pem = k.get('pem')
            if pem is None and k.get('pem_file'):
                path = os.path.join(base_dir, k['pem_file'])
                with open(path, 'rb') as fh:
                    pem = fh.read()
            if kid and pem:
                keys[kid] = _trust_key(kid, pem)
        out.append(TrustEntry(
            issuer=item['issuer'],
            enabled=bool(item.get('enabled', True)),
            enabled_until=_ts(item.get('enabled_until')),
            primary=bool(item.get('primary')),
            allow_jit=bool(item.get('allow_jit')),
            accepts_feed=bool(item.get('accepts_feed')),
            allowed_subs=_subs(item.get('allowed_subs')),
            level_cap=bool(item.get('level_cap', not item.get('primary'))),
            jwks_url=item.get('jwks_url') or None,
            jwks_auto=bool(item.get('jwks_auto')),
            keys=keys,
        ))
    return out


def entries_from_db() -> list:
    from app.models import SmsTrustIssuer
    out = []
    for row in SmsTrustIssuer.query.order_by(SmsTrustIssuer.id).all():
        keys = {}
        for k in row.keys:
            try:
                keys[k.kid] = _trust_key(k.kid, k.public_pem)
            except Exception as exc:
                log.error('sms_trust: pinned key %s of %s unusable (%s)', k.kid, row.issuer,
                          exc.__class__.__name__)
        try:
            subs = json.loads(row.allowed_subs_json or '[]')
        except ValueError:
            subs = []
        out.append(TrustEntry(
            issuer=row.issuer, enabled=bool(row.enabled),
            enabled_until=_ts(row.enabled_until), primary=bool(row.is_primary),
            allow_jit=bool(row.allow_jit), accepts_feed=bool(row.accepts_feed),
            allowed_subs=_subs(subs), level_cap=bool(row.level_cap),
            jwks_url=row.jwks_url or None, jwks_auto=bool(row.jwks_auto), keys=keys,
        ))
    return out


def _alert_bootstrap_ignored() -> None:
    global _bootstrap_alerted
    if _bootstrap_alerted:
        return
    _bootstrap_alerted = True
    log.error('sms_trust: SMS_TRUST_BOOTSTRAP_FILE is set in production; ignored')
    try:
        from app import auth_audit
        auth_audit.record_now(auth_audit.TRUST_BOOTSTRAP_IGNORED, 'fail', alert=True,
                              detail={'environment': PRODUCTION})
    except Exception:
        log.exception('sms_trust: could not audit the ignored bootstrap file')


def _load() -> TrustList:
    env = environment()
    path = _bootstrap_path()
    if path and env != PRODUCTION:
        try:
            with open(path, 'r', encoding='utf-8') as fh:
                data = json.load(fh)
            entries = entries_from_bootstrap(data, os.path.dirname(os.path.abspath(path)))
            return TrustList(normalise(entries, env), env, source='bootstrap')
        except Exception as exc:
            log.error('sms_trust: bootstrap file unusable (%s); trusting nobody',
                      exc.__class__.__name__)
            return TrustList((), env, source='bootstrap')
    if path:
        _alert_bootstrap_ignored()
    try:
        entries = entries_from_db()
    except Exception as exc:
        log.error('sms_trust: trust list unreadable (%s); trusting nobody', exc.__class__.__name__)
        entries = []
    return TrustList(normalise(entries, env), env, source='db')


def load_trust_list() -> TrustList:
    """Cached for ``CACHE_SECONDS``; ``invalidate()`` after any edit."""
    with _cache_lock:
        cached = _cache['list']
        if cached is not None and time.monotonic() - _cache['at'] < CACHE_SECONDS:
            return cached
    fresh = _load()
    with _cache_lock:
        _cache['list'] = fresh
        _cache['at'] = time.monotonic()
    return fresh


def bootstrap_in_use() -> bool:
    return bool(_bootstrap_path()) and environment() != PRODUCTION


# ---------------------------------------------------------------- DB edits

class TrustEditError(ValueError):
    pass


def upsert_issuer(issuer: str, *, actor_id=None, **fields):
    """Create or update a trust row (caller commits). Refuses contract-test
    issuers in production and non-primary windows over 7 days."""
    from app import auth_audit, db
    from app.models import SmsTrustIssuer
    if not isinstance(issuer, str) or not issuer.startswith('https://'):
        raise TrustEditError('issuer must be an https:// URL')
    if is_contract_test_issuer(issuer) and environment() == PRODUCTION:
        raise TrustEditError('contract-test issuers cannot be trusted in production')
    row = SmsTrustIssuer.query.filter_by(issuer=issuer).first()
    if row is None:
        row = SmsTrustIssuer(issuer=issuer)
        db.session.add(row)
    for name in ('enabled', 'is_primary', 'allow_jit', 'accepts_feed', 'level_cap', 'jwks_auto'):
        if name in fields:
            setattr(row, name, bool(fields[name]))
    if 'jwks_url' in fields:
        row.jwks_url = fields['jwks_url'] or None
    if 'allowed_subs' in fields:
        from app.sms_verify import canonical_uuid
        items = _sub_items(fields['allowed_subs'])
        if any(canonical_uuid(i['sub']) is None for i in items):
            raise TrustEditError('every allowed sub must be a UUID')
        row.allowed_subs_json = json.dumps(sorted(items, key=lambda i: i['sub']))
    if 'enabled_until' in fields:
        until = fields['enabled_until']
        row.enabled_until = sms_clock.to_datetime(_ts(until)) if until else None
    if not row.is_primary and row.enabled:
        until = _ts(row.enabled_until)
        if until is None or until > sms_clock.now() + NON_PRIMARY_MAX_DAYS * 86400:
            raise TrustEditError('a non-primary issuer needs enabled_until within 7 days')
    if row.is_primary and SmsTrustIssuer.query.filter(SmsTrustIssuer.is_primary.is_(True),
                                                       SmsTrustIssuer.issuer != issuer).first():
        raise TrustEditError('another issuer is already primary')
    row.updated_at = sms_clock.utcnow()
    row.updated_by_user_id = actor_id
    db.session.flush()
    auth_audit.record(auth_audit.TRUST_EDIT, 'ok', issuer=issuer, actor_id=actor_id,
                      detail={k: (list(v) if isinstance(v, (set, frozenset, tuple)) else v)
                              for k, v in fields.items()})
    invalidate()
    return row


def pin_key(issuer: str, kid: str, pem, *, expected_fingerprint: str, actor_id=None):
    """Pin one public key (caller commits). ``expected_fingerprint`` is the
    SHA-256 of the DER SPKI the admin confirmed over a second channel; a
    mismatch refuses the pin."""
    from app import auth_audit, db
    from app.models import SmsTrustIssuer, SmsTrustKey
    row = SmsTrustIssuer.query.filter_by(issuer=issuer).first()
    if row is None:
        raise TrustEditError('unknown issuer')
    if not isinstance(kid, str) or not kid.strip() or len(kid) > 64:
        raise TrustEditError('kid must be 1-64 characters')
    text = pem.decode('ascii') if isinstance(pem, bytes) else str(pem)
    try:
        _key, fp = parse_public_pem(text)
    except ValueError as exc:
        raise TrustEditError(f'unusable public key: {exc}') from None
    if normalise_fingerprint(expected_fingerprint) != fp:
        raise TrustEditError('the fingerprint does not match this key')
    existing = SmsTrustKey.query.filter_by(issuer_id=row.id, kid=kid).first()
    if existing is not None:
        raise TrustEditError('that kid is already pinned')
    key = SmsTrustKey(issuer_id=row.id, kid=kid, public_pem=text, fingerprint=fp,
                      pinned_at=sms_clock.utcnow(), pinned_by_user_id=actor_id)
    db.session.add(key)
    db.session.flush()
    auth_audit.record(auth_audit.TRUST_PIN, 'ok', issuer=issuer, actor_id=actor_id,
                      detail={'kid': kid, 'fingerprint': fp})
    invalidate()
    return key


def unpin_key(issuer: str, kid: str, *, actor_id=None) -> bool:
    from app import auth_audit, db
    from app.models import SmsTrustIssuer, SmsTrustKey
    row = SmsTrustIssuer.query.filter_by(issuer=issuer).first()
    if row is None:
        return False
    key = SmsTrustKey.query.filter_by(issuer_id=row.id, kid=kid).first()
    if key is None:
        return False
    db.session.delete(key)
    db.session.flush()
    auth_audit.record(auth_audit.TRUST_UNPIN, 'ok', issuer=issuer, actor_id=actor_id,
                      detail={'kid': kid})
    invalidate()
    return True


def open_test_window(issuer: str, *, kid: str, pem, expected_fingerprint: str,
                     allowed_subs, enabled_until, actor_id=None):
    """Contract section 2a step 3 on a non-primary entry (caller commits):
    pin the window's key after the fingerprint check, then enable it capped,
    without JIT / feed / JWKS auto-trust, for at most 7 days, for exactly
    ``allowed_subs`` (strings or ``{'sub', 'label'}``)."""
    from app import auth_audit
    from app.models import SmsTrustIssuer, SmsTrustKey
    row = SmsTrustIssuer.query.filter_by(issuer=issuer).first()
    if row is None or row.is_primary:
        raise TrustEditError('test windows are for a non-primary issuer only')
    if not _sub_items(allowed_subs):
        raise TrustEditError('list the test accounts\' subs')
    until = _ts(enabled_until)
    if until is None or until <= sms_clock.now():
        raise TrustEditError('the window needs an end date in the future')
    existing = SmsTrustKey.query.filter_by(issuer_id=row.id, kid=kid).first()
    if existing is None:
        pin_key(issuer, kid, pem, expected_fingerprint=expected_fingerprint, actor_id=actor_id)
    elif existing.fingerprint != normalise_fingerprint(expected_fingerprint):
        raise TrustEditError('that kid is pinned with a different key')
    upsert_issuer(issuer, actor_id=actor_id, enabled=True, enabled_until=until, is_primary=False,
                  allow_jit=False, accepts_feed=False, level_cap=True, jwks_auto=False,
                  allowed_subs=allowed_subs)
    auth_audit.record(auth_audit.TEST_WINDOW, 'ok', reason='open', issuer=issuer, actor_id=actor_id,
                      detail={'kid': kid, 'until': until, 'subs': len(_sub_items(allowed_subs))})
    return row


def close_test_window(issuer: str, *, actor_id=None) -> list:
    """Section 2a closing step 1 (caller commits): disable the entry and unpin
    every key. Returns the unpinned kids. Linked test accounts are listed by
    ``sms_sso_service.linked_users(issuer)`` for unlinking / disabling."""
    from app import auth_audit
    from app.models import SmsTrustIssuer
    row = SmsTrustIssuer.query.filter_by(issuer=issuer).first()
    if row is None or row.is_primary:
        raise TrustEditError('test windows are for a non-primary issuer only')
    upsert_issuer(issuer, actor_id=actor_id, enabled=False)
    kids = [k.kid for k in list(row.keys)]
    for kid in kids:
        unpin_key(issuer, kid, actor_id=actor_id)
    auth_audit.record(auth_audit.TEST_WINDOW, 'ok', reason='close', issuer=issuer,
                      actor_id=actor_id, detail={'unpinned': kids})
    return kids


def seed_defaults() -> int:
    """Insert the live and workshop entries if absent; never updates a row.
    Live: enabled + primary, no key (SSO fails closed until a super admin
    pins SMS's key). Workshop: disabled. Contract-test issuers: never."""
    from app import db
    from app.models import SmsTrustIssuer
    added = 0
    defaults = (
        dict(issuer=LIVE_ISSUER, enabled=True, is_primary=True, allow_jit=True,
             accepts_feed=True, level_cap=False, jwks_url=LIVE_JWKS_URL),
        dict(issuer=WORKSHOP_ISSUER, enabled=False, is_primary=False, allow_jit=False,
             accepts_feed=False, level_cap=True, jwks_url=WORKSHOP_JWKS_URL,
             allowed_subs_json='[]'),
    )
    for values in defaults:
        if SmsTrustIssuer.query.filter_by(issuer=values['issuer']).first() is None:
            db.session.add(SmsTrustIssuer(**values))
            added += 1
    if added:
        db.session.commit()
        invalidate()
    return added


# ---------------------------------------------------------------- JWKS

def _b64int(value: str) -> int:
    raw = base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))
    return int.from_bytes(raw, 'big')


def keys_from_jwks(doc) -> dict:
    """``{kid: TrustKey}`` for RSA keys of at least 2048 bits."""
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers
    out = {}
    for item in (doc or {}).get('keys') or ():
        try:
            if item.get('kty') != 'RSA' or not isinstance(item.get('kid'), str):
                continue
            if item.get('alg') not in (None, 'RS256'):
                continue
            key = RSAPublicNumbers(_b64int(item['e']), _b64int(item['n'])).public_key()
            if key.key_size < MIN_RSA_BITS:
                continue
            out[item['kid']] = TrustKey(kid=item['kid'], public_key=key, fingerprint=fingerprint(key))
        except Exception:
            continue
    return out


def _default_fetcher(url: str, timeout: int):
    import requests
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


class JwksCache:
    """Per-issuer JWKS cache. ``jwks_auto`` off: a fetch only detects a new kid
    (``on_new_kid``) and the key is never trusted. On: keys are trusted for
    ``JWKS_CACHE_SECONDS``; an unknown kid refetches at most every
    ``JWKS_REFETCH_SECONDS``."""

    def __init__(self, fetcher=None, clock=time.monotonic, on_new_kid=None):
        self.fetcher = fetcher or _default_fetcher
        self.clock = clock
        self.on_new_kid = on_new_kid
        self._state = {}
        self._alerted = set()
        self._lock = threading.Lock()

    def clear(self):
        with self._lock:
            self._state.clear()
            self._alerted.clear()

    def _refresh(self, entry, now) -> dict:
        st = self._state.setdefault(entry.issuer, {'keys': {}, 'fetched_at': None, 'tried_at': None})
        st['tried_at'] = now
        try:
            st['keys'] = keys_from_jwks(self.fetcher(entry.jwks_url, JWKS_TIMEOUT_SECONDS))
            st['fetched_at'] = now
        except Exception as exc:
            log.warning('sms_trust: JWKS fetch for %s failed (%s)', entry.issuer, exc.__class__.__name__)
        return st

    def resolve(self, entry, kid):
        if not entry.jwks_url:
            return None
        now = self.clock()
        with self._lock:
            st = self._state.get(entry.issuer)
            fresh = st is not None and st['fetched_at'] is not None and now - st['fetched_at'] < JWKS_CACHE_SECONDS
            can_try = st is None or st['tried_at'] is None or now - st['tried_at'] >= JWKS_REFETCH_SECONDS
            if (not fresh or kid not in st['keys']) and can_try:
                st = self._refresh(entry, now)
            keys = st['keys'] if st else {}
            found = keys.get(kid)
            if entry.jwks_auto:
                return found if (st and st['fetched_at'] is not None
                                 and now - st['fetched_at'] < JWKS_CACHE_SECONDS) else None
            new = found is not None and (entry.issuer, kid) not in self._alerted
            if new:
                self._alerted.add((entry.issuer, kid))
        if new and self.on_new_kid:
            try:
                self.on_new_kid(entry, found)
            except Exception:
                log.exception('sms_trust: new-kid alert failed')
        return None


def _alert_new_kid(entry, key) -> None:
    from app import auth_audit
    auth_audit.record_now(auth_audit.JWKS_NEW_KID, 'info', issuer=entry.issuer, alert=True,
                          detail={'kid': key.kid, 'fingerprint': key.fingerprint})


JWKS = JwksCache(on_new_kid=_alert_new_kid)


def key_resolver(entry, kid):
    """Verifier hook for kids that are not pinned (JWKS)."""
    return JWKS.resolve(entry, kid)
