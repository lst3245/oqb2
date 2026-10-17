"""All 31 official SMS fixture cases (tests/fixtures/sms/cases.json) against
OQB's own verifier, with the clock frozen to each case's ``verify_at``.

One method per case, ``test_case_<name>``, generated from cases.json so the
names are stable and ``pytest -v`` reports every case. Each docstring names
the contract section 15 acceptance tests the case covers. Route-level runs
of the same cases (``POST /sso/sms``, ``POST /api/sms/directory``) live in
test_sms_routes_fixtures.py and test_sms_feed.py.
"""
import unittest

from app import sms_trust, sms_verify
import smskit

ACCEPT = ('accept', 'accept_capped', 'not_linked')


def _snapshot_status(name: str) -> int:
    """Walk the snapshots in cases.json order up to ``name`` (the stale and
    brake cases depend on earlier ones) and return the verifier-level HTTP
    status of ``name``: 200 for a verified, fresh snapshot."""
    last_iat, seen = 0, set()
    for c in smskit.cases()['snapshots']:
        now = c['verify_at']
        try:
            snap = sms_verify.verify_snapshot(smskit.read(c['file']), smskit.trust(now), now)
            sms_verify.check_fresh(snap, last_iat, snap.jti in seen)
            status = 200
        except sms_verify.VerifyError as exc:
            status, snap = sms_verify.feed_status(exc.reason), None
        if c['name'] == name:
            return status
        if snap is not None:
            last_iat = max(last_iat, snap.iat)
            seen.add(snap.jti)
    raise KeyError(name)


class FixtureVerifierCases(unittest.TestCase):
    """Generated: ``test_case_<name>`` for every token and snapshot case."""


def _token_test(c):
    def test(self):
        now = c['verify_at']
        token = smskit.read(c['file'])
        if c['expect'] in ACCEPT:
            result = sms_verify.verify_handoff(token, smskit.trust(now), now)
            self.assertEqual(result.capped and not result.primary, c['expect'] == 'accept_capped')
            self.assertEqual(result.sub, smskit.claims(c['name'])['sub'])
        else:
            with self.assertRaises(sms_verify.VerifyError) as cm:
                sms_verify.verify_handoff(token, smskit.trust(now), now)
            self.assertEqual(cm.exception.reason, c['expect'])
            self.assertNotIn(token, str(cm.exception))
    test.__doc__ = f"{c['name']}: expect {c['expect']} (AT {c['acceptance_tests']}). {c['note']}"
    return test


def _snapshot_test(c):
    def test(self):
        expected = int(c['expect'].split()[0])
        got = _snapshot_status(c['name'])
        # 202 (held) depends on how many subs OQB has linked; the verifier
        # only decides "accepted". The feed handler test checks 200 vs 202.
        self.assertEqual(got, 200 if expected == 202 else expected)
    test.__doc__ = f"{c['name']}: expect {c['expect']} (AT {c['acceptance_tests']}). {c['note']}"
    return test


for _c in smskit.cases()['tokens']:
    setattr(FixtureVerifierCases, f"test_case_{_c['name']}", _token_test(_c))
for _c in smskit.cases()['snapshots']:
    setattr(FixtureVerifierCases, f"test_case_{_c['name']}", _snapshot_test(_c))


class FixtureVariantTests(unittest.TestCase):
    def test_all_31_cases_generated(self):
        names = [n for n in dir(FixtureVerifierCases) if n.startswith('test_case_')]
        self.assertEqual(len(names), 31)

    def test_secondary_disabled_gives_issuer_disabled(self):
        """README --secondary-disabled variant (AT 13)."""
        for name in ('secondary_test_account_admin', 'secondary_teacher_not_allowed'):
            c = smskit.case(name)
            now = c['verify_at']
            with self.assertRaises(sms_verify.VerifyError) as cm:
                sms_verify.verify_handoff(smskit.read(c['file']),
                                          smskit.trust(now, secondary_enabled=False), now)
            self.assertEqual(cm.exception.reason, 'issuer_disabled', name)

    def test_rotation_unpinned_kid_is_unknown(self):
        """AT 14: both kids verify while pinned; the old one fails once unpinned."""
        c = smskit.case('teacher_rotated_kid')
        now = c['verify_at']
        token = smskit.read(c['file'])
        sms_verify.verify_handoff(token, smskit.trust(now), now)
        with self.assertRaises(sms_verify.VerifyError) as cm:
            sms_verify.verify_handoff(token, smskit.trust(now, primary_kids={'sms-contract-test'}), now)
        self.assertEqual(cm.exception.reason, 'unknown_kid')
        first = smskit.case('teacher_viewer_user')
        sms_verify.verify_handoff(smskit.read(first['file']),
                                  smskit.trust(first['verify_at'], primary_kids={'sms-contract-test'}),
                                  first['verify_at'])

    def test_contract_test_issuers_refused_in_production(self):
        c = smskit.case('teacher_viewer_user')
        now = c['verify_at']
        prod = sms_trust.TrustList(sms_trust.normalise(
            [smskit.entry('primary'), smskit.entry('secondary', until=now + 60)], sms_trust.PRODUCTION),
            sms_trust.PRODUCTION)
        self.assertEqual(prod.entries, {})
        unguarded = smskit.trust(now, env=sms_trust.PRODUCTION)
        with self.assertRaises(sms_verify.VerifyError) as cm:
            sms_verify.verify_handoff(smskit.read(c['file']), unguarded, now)
        self.assertEqual(cm.exception.reason, 'unknown_issuer')

    def test_fingerprints_match_cases_json(self):
        for which in ('primary', 'secondary'):
            spec = smskit.cases()['trust'][which]
            for kid, rel in spec['keys'].items():
                _key, fp = sms_trust.parse_public_pem(smskit.pem(rel))
                self.assertEqual(fp, spec['sha256'][kid], kid)

    def test_levels_of_accepted_cases(self):
        """AT 1 / 2 / 4 / 13 at the verifier: OQB level names, cap applied."""
        def verify(name):
            c = smskit.case(name)
            return sms_verify.verify_handoff(smskit.read(c['file']), smskit.trust(c['verify_at']),
                                             c['verify_at'])
        teacher = verify('teacher_viewer_user')
        self.assertEqual(teacher.default_level, 'viewer')
        self.assertEqual({s.code: s.level for s in teacher.subjects}, {'MATH': 'user', 'PHY': 'user'})
        self.assertIsNone(verify('access_only_none').default_level)
        self.assertEqual(verify('school_admin').default_level, 'admin')
        panel = verify('panel_admin')
        self.assertEqual({s.code: s.level for s in panel.subjects}, {'MATH': 'admin', 'M2': 'admin'})
        capped = verify('secondary_test_account_admin')
        self.assertTrue(capped.capped)
        self.assertEqual(capped.default_level, 'viewer')
        self.assertEqual(capped.subjects, ())

    def test_snapshot_contents(self):
        c = smskit.case('snapshot_baseline')
        snap = sms_verify.verify_snapshot(smskit.read(c['file']), smskit.trust(c['verify_at']), c['verify_at'])
        self.assertEqual(len(snap.catalogue), 5)
        self.assertFalse({s.code: s.is_active for s in snap.catalogue}['LAT'])
        leaver = {u.sub: u for u in snap.users}[smskit.person('leaver')['sub']]
        self.assertFalse(leaver.active)
        self.assertIsNone(leaver.default_level)
        self.assertEqual(snap.invalid_entries, ())


if __name__ == '__main__':
    unittest.main()
