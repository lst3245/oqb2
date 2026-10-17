#!/usr/bin/env python3
"""SMS → OQB contract-test fixtures (contract v1.2 §15).

Needs only ``cryptography``. The keys in ``keys/`` are **contract-test keys**:
public on purpose, for OQB dev and CI only. Never pin them in live OQB.

    python sign.py build                     # regenerate keys/ (if missing), jwks/, tokens/, snapshots/, cases.json
    python sign.py mint teacher_viewer_user  # one case re-signed with the real clock (manual testing)
    python sign.py sign claims.json [--issuer secondary] [--kid ...]   # sign any claims file

Fixtures are signed at a fixed reference time so they are reproducible; each
case in ``cases.json`` has ``verify_at`` — freeze OQB's clock there.
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import hmac
import json
import os
import sys
import time
import uuid
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

HERE = Path(__file__).resolve().parent
KEYS = HERE / "keys"
REFERENCE_NOW = 1792000000  # 2026-10-14T06:13:20Z
NS = uuid.UUID("6f0c3d9e-5b1a-4c55-9a51-0c7f3e2d1a00")

PRIMARY_ISS = "https://sms-contract-test.invalid"
SECONDARY_ISS = "https://sms-contract-test-workshop.invalid"  # stands in for the workshop issuer

SIGNERS = {
    "primary": (PRIMARY_ISS, "sms-contract-test"),
    "rotated": (PRIMARY_ISS, "sms-contract-test-2"),
    "secondary": (SECONDARY_ISS, "sms-contract-test-workshop"),
    # Never pinned anywhere: tokens signed with it must fail with unknown_kid.
    "unknown": (PRIMARY_ISS, "sms-contract-test-unknown"),
}


# ---- people and subjects -----------------------------------------------------

def _sub(n: int) -> str:
    return f"8d1f7c2e-0c1a-4b7e-9000-{n:012d}"


SUBJECTS = {
    "MATH": {"uuid": "5a0e0001-3c2b-4d6e-8f10-000000000001", "code": "MATH",
             "name_en": "Mathematics", "name_zh": "數學", "is_active": True},
    "M2": {"uuid": "5a0e0001-3c2b-4d6e-8f10-000000000002", "code": "M2",
           "name_en": "Mathematics Extended Part Module 2", "name_zh": "數學延伸部分單元二",
           "is_active": True},
    "PHY": {"uuid": "5a0e0001-3c2b-4d6e-8f10-000000000003", "code": "PHY",
            "name_en": "Physics", "name_zh": "物理", "is_active": True},
    "CHEM": {"uuid": "5a0e0001-3c2b-4d6e-8f10-000000000004", "code": "CHEM",
             "name_en": "Chemistry", "name_zh": "化學", "is_active": True},
    "LAT": {"uuid": "5a0e0001-3c2b-4d6e-8f10-000000000005", "code": "LAT",
            "name_en": "Latin", "name_zh": None, "is_active": False},
}

PEOPLE = {
    "teacher": {"sub": _sub(1), "email": "ctm@school.test", "name": "CHAN Tai Man",
                "name_zh": "陳大文", "staff_code": "CTM"},
    "panel_admin": {"sub": _sub(2), "email": "lsy@school.test", "name": "LEE Siu Yan",
                    "name_zh": "李小欣", "staff_code": "LSY"},
    "school_admin": {"sub": _sub(3), "email": "wkm@school.test", "name": "WONG Ka Ming",
                     "name_zh": None, "staff_code": "WKM"},
    "unlinked": {"sub": _sub(4), "email": "hpl@school.test", "name": "HO Pui Ling",
                 "name_zh": "何佩玲", "staff_code": "HPL"},
    "test_account": {"sub": _sub(5), "email": "oqb-test1@school.test", "name": "OQB Test 1",
                     "name_zh": None, "staff_code": None},
    "leaver": {"sub": _sub(6), "email": "ysf@school.test", "name": "YIP Sau Fong",
               "name_zh": "葉秀芳", "staff_code": "YSF"},
    "access_only": {"sub": _sub(7), "email": "tkw@school.test", "name": "TSE Kwok Wai",
                    "name_zh": None, "staff_code": "TKW"},
}
BRAKE_PEOPLE = {
    f"brake_{i:02d}": {"sub": _sub(100 + i), "email": f"brake{i:02d}@school.test",
                       "name": f"BRAKE Test {i:02d}", "name_zh": None, "staff_code": f"BR{i:02d}"}
    for i in range(1, 11)
}


def _level(code: str, level: str, *, names: bool) -> dict:
    s = SUBJECTS[code]
    item = {"uuid": s["uuid"], "code": s["code"]}
    if names:
        item |= {"name_en": s["name_en"], "name_zh": s["name_zh"]}
    item["level"] = level
    return item


# ---- signing -------------------------------------------------------------------

def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _json(value: dict) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, sort_keys=True).encode()


def key_path(kid: str, public: bool = False) -> Path:
    return KEYS / (f"{kid}.pub.pem" if public else f"{kid}.pem")


def ensure_keys() -> None:
    KEYS.mkdir(exist_ok=True)
    for _iss, kid in SIGNERS.values():
        path = key_path(kid)
        if path.exists():
            continue
        key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        path.write_bytes(key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()))
        key_path(kid, public=True).write_bytes(key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))


def load_key(kid: str) -> rsa.RSAPrivateKey:
    return serialization.load_pem_private_key(key_path(kid).read_bytes(), password=None)


def sign(claims: dict, signer: str = "primary", *, header: dict | None = None) -> str:
    _iss, kid = SIGNERS[signer]
    head = {"alg": "RS256", "typ": "JWT", "kid": kid, **(header or {})}
    signing_input = f"{b64(_json(head))}.{b64(_json(claims))}".encode()
    signature = load_key(kid).sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input.decode()}.{b64(signature)}"


def sign_alg_none(claims: dict) -> str:
    head = {"alg": "none", "typ": "JWT", "kid": SIGNERS["primary"][1]}
    return f"{b64(_json(head))}.{b64(_json(claims))}."


def sign_hs256_with_public_key(claims: dict) -> str:
    """The classic key-confusion attack: HMAC keyed with the pinned public PEM."""
    kid = SIGNERS["primary"][1]
    head = {"alg": "HS256", "typ": "JWT", "kid": kid}
    signing_input = f"{b64(_json(head))}.{b64(_json(claims))}".encode()
    mac = hmac.new(key_path(kid, public=True).read_bytes(), signing_input, hashlib.sha256)
    return f"{signing_input.decode()}.{b64(mac.digest())}"


def tamper(token: str, **changes) -> str:
    head, body, sig = token.split(".")
    claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    claims.update(changes)
    return f"{head}.{b64(_json(claims))}.{sig}"


def jwk(kid: str) -> dict:
    numbers = load_key(kid).public_key().public_numbers()

    def uint(v: int) -> str:
        return b64(v.to_bytes((v.bit_length() + 7) // 8, "big"))

    return {"kty": "RSA", "use": "sig", "alg": "RS256", "kid": kid,
            "n": uint(numbers.n), "e": uint(numbers.e)}


def spki_sha256(kid: str) -> str:
    der = load_key(kid).public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(der).hexdigest()


# ---- claims builders -------------------------------------------------------------

def handoff(case: str, person: str, default_level: str, subjects: list, *, now: int,
            iss: str = PRIMARY_ISS, ttl: int = 60, **overrides) -> dict:
    p = (PEOPLE | BRAKE_PEOPLE)[person]
    claims = {
        "iss": iss, "aud": "oqb", "purpose": "app_handoff", "ver": 1,
        "sub": p["sub"], "email": p["email"], "name": p["name"], "name_zh": p["name_zh"],
        "staff_code": p["staff_code"], "academic_year": "2026-2027",
        "default_level": default_level,
        "subjects": [_level(c, lvl, names=True) for c, lvl in subjects],
        "jti": str(uuid.uuid5(NS, f"handoff:{case}")),
        "iat": now, "nbf": now, "exp": now + ttl,
    }
    claims.update(overrides)
    return claims


def roster_entry(person: str, *, active: bool, default_level: str = "viewer",
                 subjects: list | None = None) -> dict:
    p = (PEOPLE | BRAKE_PEOPLE)[person]
    return {
        "sub": p["sub"], "active": active, "email": p["email"], "name": p["name"],
        "name_zh": p["name_zh"], "staff_code": p["staff_code"],
        "default_level": default_level if active else "none",
        "subjects": [_level(c, lvl, names=False) for c, lvl in (subjects or [])] if active else [],
    }


def snapshot(case: str, users: list, *, iat: int, iss: str = PRIMARY_ISS) -> dict:
    return {
        "iss": iss, "aud": "oqb", "purpose": "directory_snapshot", "ver": 1,
        "jti": str(uuid.uuid5(NS, f"snapshot:{case}")),
        "snapshot_id": str(uuid.uuid5(NS, f"snapshot-id:{case}")),
        "iat": iat, "nbf": iat, "exp": iat + 600,
        "subjects": list(SUBJECTS.values()),
        "users": users,
    }


def baseline_roster() -> list:
    return [
        roster_entry("teacher", active=True, subjects=[("MATH", "user"), ("PHY", "user")]),
        roster_entry("panel_admin", active=True, subjects=[("M2", "admin"), ("MATH", "admin")]),
        roster_entry("school_admin", active=True, default_level="admin"),
        roster_entry("unlinked", active=True, subjects=[("CHEM", "user")]),
        roster_entry("access_only", active=True, default_level="none"),
        roster_entry("test_account", active=True, subjects=[("MATH", "user")]),
        roster_entry("leaver", active=False),
    ]


# ---- the cases -----------------------------------------------------------------

def token_cases(now: int) -> list[dict]:
    """Each: name, token, verify_at, expect ('accept' | reason code), acceptance test, note."""
    T = now
    teacher_subjects = [("MATH", "user"), ("PHY", "user")]
    cases = []

    def add(name, token, expect, tests, note, verify_at=T + 5):
        cases.append({"name": name, "token": token, "verify_at": verify_at, "expect": expect,
                      "acceptance_tests": tests, "note": note})

    add("teacher_viewer_user",
        sign(handoff("teacher_viewer_user", "teacher", "viewer", teacher_subjects, now=T)),
        "accept", [1, 3, 10, 14],
        "Linked teacher: viewer everywhere, user on MATH and PHY. Submit twice for the replay test.")
    add("teacher_math_only",
        sign(handoff("teacher_math_only", "teacher", "viewer", [("MATH", "user")], now=T + 10)),
        "accept", [5], "Later token for the same teacher: PHY is gone (local grants survive).",
        verify_at=T + 15)
    add("teacher_empty_subjects",
        sign(handoff("teacher_empty_subjects", "teacher", "viewer", [], now=T)),
        "accept", [2], "Viewer on everything, nothing editable.")
    add("access_only_none",
        sign(handoff("access_only_none", "access_only", "none", [], now=T)),
        "accept", [2], "May sign in, but default_level none and no subjects: no SMS-derived access.")
    add("panel_admin",
        sign(handoff("panel_admin", "panel_admin", "viewer", [("M2", "admin"), ("MATH", "admin")],
                     now=T)),
        "accept", [4], "Panel-scoped Question Bank Admin: admin on the panel's subjects only.")
    add("school_admin",
        sign(handoff("school_admin", "school_admin", "admin", [], now=T)),
        "accept", [4], "Whole-school admin: admin on every subject, never OQB super admin.")
    add("unlinked_teacher",
        sign(handoff("unlinked_teacher", "unlinked", "viewer", [("CHEM", "user")], now=T)),
        "not_linked", [6, 9], "Unlinked sub: link prompt with JIT off; creates a user with JIT on.")
    add("teacher_rotated_kid",
        sign(handoff("teacher_rotated_kid", "teacher", "viewer", teacher_subjects, now=T),
             "rotated"),
        "accept", [14], "Signed with the second primary key (rotation); reject once unpinned.")
    add("expired_31s",
        sign(handoff("expired_31s", "teacher", "viewer", teacher_subjects, now=T - 91)),
        "expired", [11], "exp is 31 s before verify_at.")
    add("expired_20s",
        sign(handoff("expired_20s", "teacher", "viewer", teacher_subjects, now=T - 80)),
        "accept", [11], "exp is 20 s before verify_at: inside the 30 s skew.")
    add("nbf_60s_ahead",
        sign(handoff("nbf_60s_ahead", "teacher", "viewer", teacher_subjects, now=T + 60)),
        "not_yet_valid", [11], "iat = nbf = verify_at + 60.", verify_at=T)
    add("lifetime_300s",
        sign(handoff("lifetime_300s", "teacher", "viewer", teacher_subjects, now=T, ttl=300)),
        "bad_lifetime", [11], "exp - iat = 300 (> 120).")
    base = handoff("attack", "teacher", "viewer", teacher_subjects, now=T)
    add("alg_none", sign_alg_none({**base, "jti": str(uuid.uuid5(NS, "alg_none"))}),
        "bad_alg", [12], "Unsigned token.")
    add("hs256_with_public_key",
        sign_hs256_with_public_key({**base, "jti": str(uuid.uuid5(NS, "hs256"))}),
        "bad_alg", [12], "HMAC keyed with the pinned public PEM.")
    add("tampered_payload",
        tamper(sign({**base, "jti": str(uuid.uuid5(NS, "tampered"))}), default_level="admin"),
        "bad_signature", [12], "Valid signature over different claims (default_level changed).")
    add("unknown_kid",
        sign({**base, "jti": str(uuid.uuid5(NS, "unknown_kid"))}, "unknown"),
        "unknown_kid", [12], "kid sms-contract-test-unknown is in no trust entry and no JWKS.")
    add("wrong_aud_jupas",
        sign(handoff("wrong_aud_jupas", "teacher", "viewer", teacher_subjects, now=T, aud="jupas")),
        "bad_aud", [13], "aud is jupas.")
    missing = handoff("missing_purpose", "teacher", "viewer", teacher_subjects, now=T)
    missing.pop("purpose")
    add("missing_purpose", sign(missing), "bad_purpose_or_ver", [13], "No purpose claim.")
    add("ver_2",
        sign(handoff("ver_2", "teacher", "viewer", teacher_subjects, now=T, ver=2)),
        "bad_purpose_or_ver", [13], "ver is 2.")
    add("unknown_issuer",
        sign(handoff("unknown_issuer", "teacher", "viewer", teacher_subjects, now=T,
                     iss="https://sms-unknown.invalid")),
        "unknown_issuer", [13], "iss has no trust entry (signed with a pinned key, still refused).")
    add("admin_with_subjects",
        sign(handoff("admin_with_subjects", "school_admin", "admin", [("MATH", "admin")], now=T)),
        "malformed", [], "default_level admin requires an empty subjects list.")
    add("secondary_test_account_admin",
        sign(handoff("secondary_test_account_admin", "test_account", "admin", [], now=T,
                     iss=SECONDARY_ISS), "secondary"),
        "accept_capped", [13],
        "Non-primary issuer, allowed test sub: accept with level cap (default viewer, max user). "
        "With the secondary entry disabled: issuer_disabled.")
    add("secondary_teacher_not_allowed",
        sign(handoff("secondary_teacher_not_allowed", "teacher", "viewer", teacher_subjects, now=T,
                     iss=SECONDARY_ISS), "secondary"),
        "sub_not_allowed", [13], "Non-primary issuer, real teacher's sub not in allowed_subs.")
    return cases


def snapshot_cases(now: int) -> list[dict]:
    T = now
    cases = []

    def add(name, token, expect, tests, note, verify_at):
        cases.append({"name": name, "token": token, "verify_at": verify_at, "expect": expect,
                      "acceptance_tests": tests, "note": note})

    add("snapshot_baseline", sign(snapshot("baseline", baseline_roster(), iat=T)),
        "200 applied", [17, 8],
        "Roster + catalogue (LAT inactive). Link teacher / panel_admin / school_admin first.",
        T + 5)
    add("snapshot_older_iat",
        sign(snapshot("older", baseline_roster(), iat=T - 600)),
        "409 stale", [19], "Apply after snapshot_baseline: older iat.", T + 5)
    roster = baseline_roster()
    roster[0] = roster_entry("teacher", active=False)
    add("snapshot_deprovision_teacher", sign(snapshot("deprovision", roster, iat=T + 600)),
        "200 applied", [18],
        "Teacher active=false: disable (sms_deprovisioned), drop SMS levels, bump session version.",
        T + 605)
    add("snapshot_reactivate_teacher", sign(snapshot("reactivate", baseline_roster(), iat=T + 1200)),
        "200 applied", [18], "Teacher active again: re-enable only if disabled as sms_deprovisioned.",
        T + 1205)
    missing = [u for u in baseline_roster() if u["sub"] != PEOPLE["panel_admin"]["sub"]]
    add("snapshot_missing_panel_admin", sign(snapshot("missing", missing, iat=T + 1800)),
        "200 applied", [20], "Linked panel_admin absent: unchanged and flagged for review.",
        T + 1805)
    brake_up = baseline_roster() + [roster_entry(k, active=True) for k in BRAKE_PEOPLE]
    add("snapshot_brake_setup", sign(snapshot("brake_setup", brake_up, iat=T + 2400)),
        "200 applied", [19], "Adds brake_01..10 (link them all before applying).", T + 2405)
    brake_down = baseline_roster() + [roster_entry(k, active=False) for k in BRAKE_PEOPLE]
    add("snapshot_brake_trip", sign(snapshot("brake_trip", brake_down, iat=T + 3000)),
        "202 held", [19], "Deactivates 10 linked users (> max(5, 10%)): hold, deactivate nothing.",
        T + 3005)
    add("snapshot_secondary_issuer",
        sign(snapshot("secondary", baseline_roster(), iat=T, iss=SECONDARY_ISS), "secondary"),
        "403 rejected", [19], "Feed from a non-primary issuer is refused.", T + 5)
    return cases


def decode(token: str) -> dict:
    def part(s: str) -> dict:
        return json.loads(base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)))

    head, body, _ = token.split(".")
    return {"header": part(head), "claims": part(body)}


def build(now: int) -> None:
    ensure_keys()
    (HERE / "jwks").mkdir(exist_ok=True)
    (HERE / "jwks" / "primary.json").write_text(json.dumps(
        {"keys": [jwk(SIGNERS["primary"][1]), jwk(SIGNERS["rotated"][1])]}, indent=2) + "\n")
    (HERE / "jwks" / "secondary.json").write_text(json.dumps(
        {"keys": [jwk(SIGNERS["secondary"][1])]}, indent=2) + "\n")
    index = {
        "reference_now": now,
        "trust": {
            "primary": {"issuer": PRIMARY_ISS, "primary": True, "allow_jit": True,
                        "accepts_feed": True,
                        "keys": {k: f"keys/{k}.pub.pem" for k in
                                 (SIGNERS["primary"][1], SIGNERS["rotated"][1])},
                        "sha256": {k: spki_sha256(k) for k in
                                   (SIGNERS["primary"][1], SIGNERS["rotated"][1])}},
            "secondary": {"issuer": SECONDARY_ISS, "primary": False, "allow_jit": False,
                          "accepts_feed": False, "level_cap": True,
                          "allowed_subs": [PEOPLE["test_account"]["sub"]],
                          "keys": {SIGNERS["secondary"][1]:
                                   f"keys/{SIGNERS['secondary'][1]}.pub.pem"},
                          "sha256": {SIGNERS["secondary"][1]: spki_sha256(SIGNERS["secondary"][1])}},
        },
        "people": PEOPLE | BRAKE_PEOPLE,
        "subjects": SUBJECTS,
        "tokens": [],
        "snapshots": [],
    }
    for folder, cases, key in (("tokens", token_cases(now), "tokens"),
                               ("snapshots", snapshot_cases(now), "snapshots")):
        out = HERE / folder
        out.mkdir(exist_ok=True)
        for case in cases:
            (out / f"{case['name']}.jwt").write_text(case["token"] + "\n")
            (out / f"{case['name']}.json").write_text(
                json.dumps(decode(case["token"]), indent=2, ensure_ascii=False) + "\n")
            index[key].append({k: v for k, v in case.items() if k != "token"}
                              | {"file": f"{folder}/{case['name']}.jwt"})
    (HERE / "cases.json").write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {len(index['tokens'])} tokens and {len(index['snapshots'])} snapshots.")


def mint(name: str) -> None:
    ensure_keys()
    now = int(time.time())
    for case in token_cases(now) + snapshot_cases(now):
        if case["name"] == name:
            print(case["token"])
            return
    sys.exit(f"unknown case {name!r}")


def sign_file(path: str, issuer: str) -> None:
    ensure_keys()
    claims = json.loads(Path(path).read_text())
    claims.setdefault("iss", SIGNERS[issuer][0])
    print(sign(claims, issuer))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--now", type=int, default=REFERENCE_NOW)
    m = sub.add_parser("mint")
    m.add_argument("case")
    s = sub.add_parser("sign")
    s.add_argument("claims")
    s.add_argument("--issuer", choices=sorted(SIGNERS), default="primary")
    args = parser.parse_args()
    if args.cmd == "build":
        build(args.now)
    elif args.cmd == "mint":
        mint(args.case)
    else:
        sign_file(args.claims, args.issuer)


if __name__ == "__main__":
    os.umask(0o022)
    main()
