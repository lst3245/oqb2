"""Helpers for the SMS sign-in tests: the official contract-test fixtures
(tests/fixtures/sms, byte copies of what SMS delivered) and minting extra
edge-case tokens with the fixture keys (public on purpose, dev / CI only)."""
import json
import os
import uuid

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'sms')
_CASES = None


def path(*parts) -> str:
    return os.path.join(FIXTURES, *parts)


def cases() -> dict:
    global _CASES
    if _CASES is None:
        with open(path('cases.json'), 'r', encoding='utf-8') as fh:
            _CASES = json.load(fh)
    return _CASES


def read(rel: str) -> str:
    with open(path(rel), 'r', encoding='ascii') as fh:
        return fh.read().strip()


def pem(rel: str) -> bytes:
    with open(path(rel), 'rb') as fh:
        return fh.read()


def case(name: str) -> dict:
    for group in ('tokens', 'snapshots'):
        for c in cases()[group]:
            if c['name'] == name:
                return c
    raise KeyError(name)


def claims(name: str) -> dict:
    """Decoded claims of a fixture (``tokens/<name>.json`` or ``snapshots/``)."""
    folder = 'snapshots' if name.startswith('snapshot_') else 'tokens'
    with open(path(folder, f'{name}.json'), 'r', encoding='utf-8') as fh:
        return json.load(fh)['claims']


def person(name: str) -> dict:
    return cases()['people'][name]


def subject_uuid(code: str) -> str:
    return cases()['subjects'][code]['uuid']


PRIMARY = cases()['trust']['primary']['issuer']
SECONDARY = cases()['trust']['secondary']['issuer']
REFERENCE_NOW = cases()['reference_now']


def private_key(kid: str) -> bytes:
    return pem(f'keys/{kid}.pem')


def entry(which: str, *, enabled=True, until=None, kids=None, allowed_subs=None):
    from app import sms_trust
    spec = cases()['trust'][which]
    keys = {}
    for kid, rel in spec['keys'].items():
        if kids is None or kid in kids:
            keys[kid] = sms_trust._trust_key(kid, pem(rel))
    return sms_trust.TrustEntry(
        issuer=spec['issuer'], enabled=enabled, enabled_until=until,
        primary=spec['primary'], allow_jit=spec.get('allow_jit', False),
        accepts_feed=spec.get('accepts_feed', False),
        allowed_subs=frozenset(allowed_subs if allowed_subs is not None else spec.get('allowed_subs', ())),
        level_cap=spec.get('level_cap', False), keys=keys,
    )


def trust(now: int, *, secondary_enabled=True, primary_kids=None, env='test'):
    """The fixture trust list: primary with both kids pinned (or
    ``primary_kids``), secondary enabled until ``now + 1 day``; the
    ``sms-contract-test-unknown`` kid is never pinned."""
    from app import sms_trust
    return sms_trust.TrustList(
        [entry('primary', kids=primary_kids),
         entry('secondary', enabled=secondary_enabled, until=now + 86400)],
        env)


def mint(base: str = 'teacher_viewer_user', *, kid: str = 'sms-contract-test', drop=(),
         header=None, fresh_jti=True, **overrides) -> str:
    """Re-sign a fixture's claims with changes, for cases SMS did not ship."""
    import jwt
    data = dict(claims(base))
    if fresh_jti:
        data['jti'] = str(uuid.uuid4())
    data.update(overrides)
    for key in drop:
        data.pop(key, None)
    head = {'kid': kid, 'typ': 'JWT'}
    head.update(header or {})
    return jwt.encode(data, private_key(kid), algorithm='RS256', headers=head)


def install_db_trust(*, secondary_enabled=True, primary_kids=None, until=None, actor_id=None):
    """Write the fixture trust entries to the test DB (pins with the
    fingerprints from cases.json). Caller commits."""
    from app import sms_clock, sms_trust
    spec = cases()['trust']
    sms_trust.upsert_issuer(PRIMARY, enabled=True, is_primary=True, allow_jit=True,
                            accepts_feed=True, level_cap=False, actor_id=actor_id)
    for kid, rel in spec['primary']['keys'].items():
        if primary_kids is None or kid in primary_kids:
            sms_trust.pin_key(PRIMARY, kid, pem(rel), expected_fingerprint=spec['primary']['sha256'][kid],
                              actor_id=actor_id)
    sms_trust.upsert_issuer(SECONDARY, enabled=False, is_primary=False, allow_jit=False,
                            accepts_feed=False, level_cap=True, allowed_subs=spec['secondary']['allowed_subs'],
                            actor_id=actor_id)
    for kid, rel in spec['secondary']['keys'].items():
        sms_trust.pin_key(SECONDARY, kid, pem(rel), expected_fingerprint=spec['secondary']['sha256'][kid],
                          actor_id=actor_id)
    if secondary_enabled:
        sms_trust.upsert_issuer(SECONDARY, enabled=True,
                                enabled_until=until or sms_clock.now() + 86400, actor_id=actor_id)
    sms_trust.invalidate()
