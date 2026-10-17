#!/usr/bin/env python3
"""Reference verifier for contract v1.2 §6 steps 1–8 (and §10 for snapshots).

Not OQB's implementation — a readable check that every fixture in
``cases.json`` produces its documented outcome. Needs only ``cryptography``.

    python verify.py              # check all fixtures
    python verify.py --secondary-disabled   # the workshop-like issuer switched off
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

HERE = Path(__file__).resolve().parent
SKEW = 30
MAX_LIFETIME = 120
LEVELS = ("none", "viewer", "user", "admin")


class Refused(Exception):
    pass


def _part(segment: str) -> dict:
    try:
        return json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))
    except ValueError as exc:
        raise Refused("malformed") from exc


def verify(token: str, now: int, trust: dict, *, purpose: str, secondary_enabled: bool) -> dict:
    # 1. size and shape
    if len(token) > 8192 * (1 if purpose == "app_handoff" else 640):
        raise Refused("malformed")
    segments = token.split(".")
    if len(segments) != 3:
        raise Refused("malformed")
    header, claims = _part(segments[0]), _part(segments[1])
    # 2. issuer from the unverified payload -> trust entry
    entry = next((e for e in trust.values() if e["issuer"] == claims.get("iss")), None)
    if entry is None:
        raise Refused("unknown_issuer")
    if not entry["primary"] and not secondary_enabled:
        raise Refused("issuer_disabled")
    # 3. alg and pinned kid
    if header.get("alg") != "RS256":
        raise Refused("bad_alg")
    if header.get("kid") not in entry["keys"]:
        raise Refused("unknown_kid")
    # 4. signature with that key only
    public = serialization.load_pem_public_key((HERE / entry["keys"][header["kid"]]).read_bytes())
    signature = base64.urlsafe_b64decode(segments[2] + "=" * (-len(segments[2]) % 4))
    try:
        public.verify(signature, f"{segments[0]}.{segments[1]}".encode(),
                      padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature as exc:
        raise Refused("bad_signature") from exc
    # 5. audience, purpose, version
    aud = claims.get("aud")
    if not (aud == "oqb" or (isinstance(aud, list) and "oqb" in aud)):
        raise Refused("bad_aud")
    if claims.get("purpose") != purpose or claims.get("ver") != 1:
        raise Refused("bad_purpose_or_ver")
    # 6. times
    iat, nbf, exp = claims.get("iat"), claims.get("nbf"), claims.get("exp")
    if not all(isinstance(v, int) for v in (iat, nbf, exp)):
        raise Refused("malformed")
    if now < nbf - SKEW or iat > now + SKEW:
        raise Refused("not_yet_valid")
    if now >= exp + SKEW:
        raise Refused("expired")
    limit = MAX_LIFETIME if purpose == "app_handoff" else 600
    if exp - iat > limit:
        raise Refused("bad_lifetime")
    # 7. types and enums
    if purpose == "app_handoff":
        if claims.get("default_level") not in LEVELS[:2] + ("admin",):
            raise Refused("malformed")
        for s in claims.get("subjects", []):
            if s.get("level") not in ("viewer", "user", "admin"):
                raise Refused("malformed")
        if claims["default_level"] == "admin" and claims["subjects"]:
            raise Refused("malformed")
    # 8. non-primary issuer: allowlist and level cap
    if not entry["primary"]:
        if purpose != "app_handoff":
            raise Refused("rejected_not_primary")
        if claims.get("sub") not in entry["allowed_subs"]:
            raise Refused("sub_not_allowed")
        claims = dict(claims)
        claims["default_level"] = "viewer" if claims["default_level"] != "none" else "none"
        claims["subjects"] = [{**s, "level": "user" if s["level"] == "admin" else s["level"]}
                              for s in claims["subjects"]]
        claims["_capped"] = True
    return claims


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--secondary-disabled", action="store_true")
    args = parser.parse_args()
    index = json.loads((HERE / "cases.json").read_text())
    trust = index["trust"]
    failures = 0
    for group, purpose in (("tokens", "app_handoff"), ("snapshots", "directory_snapshot")):
        for case in index[group]:
            token = (HERE / case["file"]).read_text().strip()
            try:
                claims = verify(token, case["verify_at"], trust, purpose=purpose,
                                secondary_enabled=not args.secondary_disabled)
                got = "accept_capped" if claims.get("_capped") else "accept"
            except Refused as exc:
                got = str(exc)
            expected = case["expect"]
            if group == "snapshots":
                # Steps 1-8 only; the status code depends on OQB's stored state.
                expected = "rejected_not_primary" if "403" in expected else "accept"
            if expected == "not_linked":
                expected = "accept"  # token is valid; linking decides the outcome
            if args.secondary_disabled and "secondary" in case["name"]:
                expected = "issuer_disabled"
            ok = got == expected
            failures += not ok
            print(f"{'ok  ' if ok else 'FAIL'} {case['name']:<34} expected {expected:<22} got {got}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
