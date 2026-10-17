#!/usr/bin/env python3
"""Mint an SMS handoff token or directory snapshot for dev / staging tests.

No app import, no database. By default it only signs for contract-test
issuers (host ending in ``.invalid``) so it cannot mint something a
production OQB would trust; ``--allow-issuer`` lifts that for a deliberate
workshop test. The private key never leaves the file you point at.

    python scripts/sms_contract_mint.py --key tests/fixtures/sms/keys/sms-contract-test.pem \
        --kid sms-contract-test --iss https://sms-contract-test.invalid \
        --purpose handoff --claims claims.json [--now 1792000000] [--out token.jwt]

``claims.json`` holds the person / level claims (``sub``, ``email``, ``name``,
``default_level``, ``subjects`` ... or ``users`` / ``subjects`` for a
snapshot). ``aud``, ``ver``, ``purpose``, ``iss``, ``iat``, ``nbf``, ``exp``
and ``jti`` (and ``snapshot_id``) are filled in unless present.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from urllib.parse import urlparse

PURPOSES = {'handoff': ('app_handoff', 60), 'snapshot': ('directory_snapshot', 600)}


def is_contract_test_issuer(issuer: str) -> bool:
    host = (urlparse(issuer).hostname or '').lower()
    return host == 'invalid' or host.endswith('.invalid')


def build_claims(claims: dict, *, iss: str, purpose: str, now: int, lifetime: int | None) -> dict:
    name, default_lifetime = PURPOSES[purpose]
    out = dict(claims)
    out.setdefault('iss', iss)
    out.setdefault('aud', 'oqb')
    out.setdefault('purpose', name)
    out.setdefault('ver', 1)
    out.setdefault('iat', now)
    out.setdefault('nbf', out['iat'])
    out.setdefault('exp', out['iat'] + (lifetime if lifetime is not None else default_lifetime))
    out.setdefault('jti', str(uuid.uuid4()))
    if purpose == 'snapshot':
        out.setdefault('snapshot_id', str(uuid.uuid4()))
    return out


def mint(private_pem: bytes, kid: str, claims: dict) -> str:
    import jwt
    return jwt.encode(claims, private_pem, algorithm='RS256', headers={'kid': kid, 'typ': 'JWT'})


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--key', required=True, help='private key PEM')
    p.add_argument('--kid', required=True)
    p.add_argument('--iss', required=True)
    p.add_argument('--purpose', choices=sorted(PURPOSES), default='handoff')
    p.add_argument('--claims', required=True, help='JSON file with the claims')
    p.add_argument('--now', type=int, default=None, help='iat (default: the real clock)')
    p.add_argument('--lifetime', type=int, default=None, help='exp - iat in seconds')
    p.add_argument('--allow-issuer', action='store_true',
                   help='sign for an issuer that is not a contract-test (.invalid) issuer')
    p.add_argument('--out', default=None, help='write the token here (default: stdout)')
    args = p.parse_args(argv)

    if args.iss != (args.iss or '').strip() or not args.iss.startswith('https://'):
        p.error('--iss must be an https:// URL')
    if not is_contract_test_issuer(args.iss) and not args.allow_issuer:
        p.error('refusing to sign for a non-.invalid issuer without --allow-issuer')
    with open(args.claims, 'r', encoding='utf-8') as fh:
        claims = json.load(fh)
    if not isinstance(claims, dict):
        p.error('--claims must hold a JSON object')
    with open(args.key, 'rb') as fh:
        private_pem = fh.read()
    now = args.now if args.now is not None else int(time.time())
    token = mint(private_pem, args.kid, build_claims(claims, iss=args.iss, purpose=args.purpose,
                                                     now=now, lifetime=args.lifetime))
    if args.out:
        with open(args.out, 'w', encoding='ascii') as fh:
            fh.write(token)
    else:
        sys.stdout.write(token + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
