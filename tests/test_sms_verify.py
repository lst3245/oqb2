"""Edge cases of app/sms_verify.py beyond the shipped fixtures (pure; no DB)."""
import base64
import json
import unittest

from app import sms_verify
import smskit

NOW = smskit.REFERENCE_NOW


def _b64(obj) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b'=').decode()


class HandoffEdgeTests(unittest.TestCase):
    def verify(self, token, now=NOW, trust=None):
        return sms_verify.verify_handoff(token, trust or smskit.trust(now), now)

    def reason(self, token, now=NOW, trust=None):
        with self.assertRaises(sms_verify.VerifyError) as cm:
            self.verify(token, now, trust)
        return cm.exception.reason

    def test_minted_baseline_accepts(self):
        result = self.verify(smskit.mint(iat=NOW, nbf=NOW, exp=NOW + 60))
        self.assertTrue(result.primary)

    def test_garbage_and_oversize_are_malformed(self):
        for token in ('', 'abc', 'a.b', 'a.b.c.d', '!!.??.xx', None, 12, b'\xff\xfe.a.b',
                      'x' * (sms_verify.MAX_TOKEN_BYTES + 1)):
            self.assertEqual(self.reason(token), 'malformed', repr(token)[:40])

    def test_hs256_is_bad_alg(self):
        head = _b64({'alg': 'HS256', 'kid': 'sms-contract-test'})
        body = _b64(smskit.claims('teacher_viewer_user'))
        self.assertEqual(self.reason(f'{head}.{body}.c2ln', now=smskit.case('teacher_viewer_user')['verify_at']),
                         'bad_alg')

    def test_missing_kid_is_unknown_kid(self):
        token = smskit.mint(iat=NOW, nbf=NOW, exp=NOW + 60)
        head, body, sig = token.split('.')
        bare = _b64({'alg': 'RS256', 'typ': 'JWT'})
        self.assertEqual(self.reason(f'{bare}.{body}.{sig}'), 'unknown_kid')

    def test_tampered_payload_is_bad_signature(self):
        token = smskit.mint(iat=NOW, nbf=NOW, exp=NOW + 60)
        head, body, sig = token.split('.')
        claims = json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)))
        claims['default_level'] = 'admin'
        claims['subjects'] = []
        self.assertEqual(self.reason(f'{head}.{_b64(claims)}.{sig}'), 'bad_signature')

    def test_aud_list_containing_oqb_accepted(self):
        self.verify(smskit.mint(iat=NOW, nbf=NOW, exp=NOW + 60, aud=['other', 'oqb']))
        self.assertEqual(self.reason(smskit.mint(iat=NOW, nbf=NOW, exp=NOW + 60, aud=['other'])), 'bad_aud')

    def test_ver_must_be_int_one(self):
        for ver in (2, '1', True, 1.0):
            self.assertEqual(self.reason(smskit.mint(iat=NOW, nbf=NOW, exp=NOW + 60, ver=ver)),
                             'bad_purpose_or_ver', repr(ver))

    def test_clock_skew_window(self):
        token = smskit.mint(iat=NOW, nbf=NOW, exp=NOW + 60)
        self.verify(token, now=NOW - 30)
        self.assertEqual(self.reason(token, now=NOW - 31), 'not_yet_valid')
        self.verify(token, now=NOW + 89)
        self.assertEqual(self.reason(token, now=NOW + 90), 'expired')

    def test_lifetime_limit_120(self):
        self.verify(smskit.mint(iat=NOW, nbf=NOW, exp=NOW + 120))
        self.assertEqual(self.reason(smskit.mint(iat=NOW, nbf=NOW, exp=NOW + 121)), 'bad_lifetime')

    def test_float_times_are_malformed(self):
        self.assertEqual(self.reason(smskit.mint(iat=float(NOW), nbf=NOW, exp=NOW + 60)), 'malformed')

    def test_type_and_enum_checks(self):
        cases = {
            'bad sub': dict(sub='not-a-uuid'),
            'bad default': dict(default_level='superuser'),
            'admin with subjects': dict(default_level='admin'),
            'subject not above default': dict(subjects=[dict(smskit.claims('teacher_viewer_user')['subjects'][0],
                                                             level='viewer')]),
            'email missing': dict(email=None),
            'subjects not a list': dict(subjects={}),
        }
        for label, override in cases.items():
            token = smskit.mint(iat=NOW, nbf=NOW, exp=NOW + 60, **override)
            self.assertEqual(self.reason(token), 'malformed', label)

    def test_upper_case_sub_is_stored_canonical(self):
        sub = smskit.person('teacher')['sub']
        result = self.verify(smskit.mint(iat=NOW, nbf=NOW, exp=NOW + 60, sub=sub.upper()))
        self.assertEqual(result.sub, sub)

    def test_duplicate_subject_uuid_is_malformed(self):
        subj = smskit.claims('teacher_viewer_user')['subjects'][0]
        token = smskit.mint(iat=NOW, nbf=NOW, exp=NOW + 60, subjects=[subj, subj])
        self.assertEqual(self.reason(token), 'malformed')

    def test_nullable_academic_year(self):
        result = self.verify(smskit.mint(iat=NOW, nbf=NOW, exp=NOW + 60, academic_year=None))
        self.assertIsNone(result.academic_year)

    def test_non_primary_needs_window_and_subs(self):
        c = smskit.case('secondary_test_account_admin')
        now = c['verify_at']
        token = smskit.read(c['file'])
        from app import sms_trust
        expired = sms_trust.TrustList([smskit.entry('primary'), smskit.entry('secondary', until=now)], 'test')
        self.assertEqual(self.reason(token, now, expired), 'issuer_disabled')
        no_subs = sms_trust.TrustList([smskit.entry('primary'),
                                       smskit.entry('secondary', until=now + 60, allowed_subs=())], 'test')
        self.assertEqual(self.reason(token, now, no_subs), 'issuer_disabled')

    def test_capped_subject_entries(self):
        """Non-primary: subject admin -> user; a subject equal to the capped default is dropped."""
        sec = smskit.claims('secondary_test_account_admin')
        subj = dict(smskit.claims('teacher_viewer_user')['subjects'][0], level='admin')
        token = smskit.mint('secondary_test_account_admin', kid='sms-contract-test-workshop',
                            iat=NOW, nbf=NOW, exp=NOW + 60, default_level='viewer', subjects=[subj])
        result = self.verify(token)
        self.assertEqual(result.sub, sec['sub'])
        self.assertEqual([(s.code, s.level) for s in result.subjects], [(subj['code'], 'user')])

    def test_two_enabled_primaries_fail_closed(self):
        from app import sms_trust
        from dataclasses import replace
        other = replace(smskit.entry('secondary', until=NOW + 60), primary=True, issuer='https://x.invalid')
        trust = sms_trust.TrustList([smskit.entry('primary'), other], 'test')
        self.assertIsNone(trust.primary(NOW))
        self.assertEqual(self.reason(smskit.mint(iat=NOW, nbf=NOW, exp=NOW + 60), trust=trust), 'issuer_disabled')

    def test_resolver_is_consulted_for_unpinned_kid(self):
        calls = []

        def resolver(entry, kid):
            calls.append(kid)
            return None
        c = smskit.case('teacher_rotated_kid')
        with self.assertRaises(sms_verify.VerifyError):
            sms_verify.verify_handoff(smskit.read(c['file']),
                                      smskit.trust(c['verify_at'], primary_kids={'sms-contract-test'}),
                                      c['verify_at'], key_resolver=resolver)
        self.assertEqual(calls, ['sms-contract-test-2'])


class SnapshotEdgeTests(unittest.TestCase):
    def mint_snapshot(self, **overrides):
        base = dict(iat=NOW, nbf=NOW, exp=NOW + 300)
        base.update(overrides)
        return smskit.mint('snapshot_baseline', **base)

    def test_snapshot_needs_nbf(self):
        with self.assertRaises(sms_verify.VerifyError) as cm:
            sms_verify.verify_snapshot(self.mint_snapshot(drop=('nbf',)), smskit.trust(NOW), NOW)
        self.assertEqual(cm.exception.reason, 'malformed')

    def test_snapshot_lifetime_600(self):
        sms_verify.verify_snapshot(self.mint_snapshot(exp=NOW + 600), smskit.trust(NOW), NOW)
        with self.assertRaises(sms_verify.VerifyError) as cm:
            sms_verify.verify_snapshot(self.mint_snapshot(exp=NOW + 601), smskit.trust(NOW), NOW)
        self.assertEqual(cm.exception.reason, 'bad_lifetime')

    def test_handoff_token_is_not_a_snapshot(self):
        token = smskit.mint(iat=NOW, nbf=NOW, exp=NOW + 60)
        with self.assertRaises(sms_verify.VerifyError) as cm:
            sms_verify.verify_snapshot(token, smskit.trust(NOW), NOW)
        self.assertEqual(cm.exception.reason, 'bad_purpose_or_ver')

    def test_invalid_user_entries_are_listed_not_fatal(self):
        users = list(smskit.claims('snapshot_baseline')['users'])
        users.append({'sub': 'nope', 'active': True})
        users.append(dict(users[0]))
        users.append(dict(users[1], active=False))
        snap = sms_verify.verify_snapshot(self.mint_snapshot(users=users), smskit.trust(NOW), NOW)
        whys = [why for _i, _s, why in snap.invalid_entries]
        self.assertIn('bad_sub', whys)
        self.assertIn('duplicate_sub', whys)
        self.assertEqual(len(snap.users), len(users) - len(whys))

    def test_check_fresh_and_status_map(self):
        snap = sms_verify.verify_snapshot(self.mint_snapshot(), smskit.trust(NOW), NOW)
        sms_verify.check_fresh(snap, NOW - 1, False)
        for last, seen in ((NOW, False), (NOW - 1, True)):
            with self.assertRaises(sms_verify.VerifyError) as cm:
                sms_verify.check_fresh(snap, last, seen)
            self.assertEqual(cm.exception.reason, 'stale_snapshot')
        self.assertEqual(sms_verify.feed_status('stale_snapshot'), 409)
        self.assertEqual(sms_verify.feed_status('issuer_disabled'), 403)
        self.assertEqual(sms_verify.feed_status('malformed'), 400)
        self.assertEqual(sms_verify.feed_status('bad_signature'), 401)


if __name__ == '__main__':
    unittest.main()
