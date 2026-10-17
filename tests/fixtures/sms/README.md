# SMS → OQB contract-test fixtures (contract v1.2 §15)

Sample handoff tokens and directory snapshots for OQB's dev and CI, signed
with dedicated **contract-test keys**. The private keys in `keys/` are public
on purpose. **Never pin them, or trust these issuers, in live OQB.**

Contract: [`../sms-oqb-handoff.md`](../sms-oqb-handoff.md).

## Trust entries to load in OQB dev / test

| Entry | Issuer | Pinned kids | Settings |
|---|---|---|---|
| primary | `https://sms-contract-test.invalid` | `sms-contract-test`, `sms-contract-test-2` | `primary`, `allow_jit`, `accepts_feed` |
| secondary | `https://sms-contract-test-workshop.invalid` | `sms-contract-test-workshop` | not primary, `level_cap`, `allowed_subs = [8d1f7c2e-…-000000000005]` (the test account), no JIT, no feed |

`sms-contract-test-unknown` is deliberately **not** pinned (unknown-`kid`
case). `cases.json` → `trust` has the same entries with the PEM paths and
SHA-256 fingerprints (DER SPKI). `jwks/primary.json` and
`jwks/secondary.json` are what each issuer's JWKS endpoint would return.

## Files

| Path | Contents |
|---|---|
| `tokens/<case>.jwt` | Handoff token (compact JWS) |
| `tokens/<case>.json` | Its header and claims, for reading |
| `snapshots/<case>.jwt` / `.json` | Directory snapshot body (`application/jwt`) and its decoded form |
| `cases.json` | `reference_now`, trust entries, people, subjects, and for every case: `verify_at`, `expect` (outcome / reason code / HTTP status), `acceptance_tests` (§15 numbers), `note` |
| `sign.py` | Rebuilds everything (`build`), re-signs one case with the real clock (`mint <case>`), or signs any claims file (`sign`) |
| `verify.py` | Reference verifier for contract §6 steps 1–8 and the snapshot checks; runs every case against `cases.json` |

Everything is signed at `reference_now = 1792000000`. **Freeze OQB's clock
to each case's `verify_at`** (time cases sit a few seconds after it; the
snapshot sequence steps 10 minutes per snapshot). Apply snapshots in the
order listed in `cases.json`; the brake and stale cases depend on the
earlier ones.

## People and subjects

Fixed UUIDs (see `cases.json` → `people` / `subjects`): a teacher (CTM), a
Maths panel admin (LSY), a whole-school admin (WKM), an unlinked teacher
(HPL), the workshop test account, a leaver (YSF), an access-only person (TKW)
and brake-test staff. Subjects: MATH (meant to map to MATC / MAT1 / MAT2),
M2, PHY, CHEM and an inactive one, so the mapping and "unmapped" cases have
something to work with.

## Running

Needs Python 3.10+ and `cryptography` (and nothing from SMS):

```bash
python verify.py                       # every case: expected vs actual
python verify.py --secondary-disabled  # secondary issuer off → issuer_disabled
python sign.py mint teacher_viewer_user  # fresh token for a manual sign-in test
python sign.py build                   # regenerate (deterministic jti / uuids)
```

`verify.py` is a reference, not OQB's implementation: it shows the order of
checks and the reason codes the cases expect. Steps OQB owns (single-use
`jti` storage, linking, sessions, the brake) are described in each case's
`note` and acceptance-test numbers.
