"""Pure tests of app/sms_trust.py: guards, bootstrap format, JWKS cache."""
import json
import os
import tempfile
import unittest
from dataclasses import replace
from unittest import mock

from app import sms_trust
import smskit

NOW = smskit.REFERENCE_NOW


class GuardTests(unittest.TestCase):
    def test_environment_defaults_to_production(self):
        for value in ('', 'prod', 'PRODUCTION', 'live', None):
            with mock.patch.dict(os.environ, {'OQB_ENVIRONMENT': value or ''}):
                self.assertEqual(sms_trust.environment(), sms_trust.PRODUCTION)
        with mock.patch.dict(os.environ, {'OQB_ENVIRONMENT': 'Staging'}):
            self.assertEqual(sms_trust.environment(), 'staging')

    def test_contract_test_issuer_detection(self):
        self.assertTrue(sms_trust.is_contract_test_issuer(smskit.PRIMARY))
        self.assertTrue(sms_trust.is_contract_test_issuer('https://invalid'))
        self.assertFalse(sms_trust.is_contract_test_issuer(sms_trust.LIVE_ISSUER))
        self.assertFalse(sms_trust.is_contract_test_issuer('https://invalid.example.com'))

    def test_normalise_forces_non_primary_limits(self):
        loose = replace(smskit.entry('secondary', until=NOW + 60), allow_jit=True, accepts_feed=True,
                        level_cap=False, issuer='https://ms-test.example.org')
        [out] = sms_trust.normalise([loose], sms_trust.PRODUCTION)
        self.assertFalse(out.allow_jit)
        self.assertFalse(out.accepts_feed)
        self.assertTrue(out.level_cap)

    def test_active_rules(self):
        primary = smskit.entry('primary')
        self.assertTrue(primary.active(NOW))
        self.assertFalse(replace(primary, enabled=False).active(NOW))
        self.assertFalse(replace(primary, enabled_until=NOW).active(NOW))
        sec = smskit.entry('secondary', until=NOW + 1)
        self.assertTrue(sec.active(NOW))
        self.assertFalse(replace(sec, enabled_until=None).active(NOW))
        self.assertFalse(replace(sec, allowed_subs=frozenset()).active(NOW))

    def test_issuer_caps(self):
        caps = smskit.trust(NOW).issuer_caps(NOW)
        self.assertEqual(caps, {smskit.PRIMARY: False, smskit.SECONDARY: True})
        self.assertEqual(smskit.trust(NOW, secondary_enabled=False).issuer_caps(NOW), {smskit.PRIMARY: False})

    def test_small_rsa_key_refused(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec, rsa
        small = rsa.generate_private_key(public_exponent=65537, key_size=1024).public_key()
        pem = small.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        with self.assertRaises(ValueError):
            sms_trust.parse_public_pem(pem)
        ecpem = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        with self.assertRaises(ValueError):
            sms_trust.parse_public_pem(ecpem)

    def test_recorded_live_key_matches_its_fingerprint(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(repo, 'deploy', 'sms', 'sms-live-202610.pub.pem'), 'rb') as fh:
            key, fp = sms_trust.parse_public_pem(fh.read())
        self.assertEqual(fp, sms_trust.LIVE_FINGERPRINT)
        self.assertEqual(key.key_size, 3072)

    def test_sub_items_and_fingerprint_normalising(self):
        items = sms_trust._sub_items(['  ABC ', {'sub': 'def', 'label': 'oqb-test-viewer'}, '', {'label': 'x'}])
        self.assertEqual(items, [{'sub': 'abc', 'label': None}, {'sub': 'def', 'label': 'oqb-test-viewer'}])
        self.assertEqual(sms_trust.normalise_fingerprint(' AB:cd ef '), 'abcdef')


class BootstrapTests(unittest.TestCase):
    def write(self, data):
        fd, name = tempfile.mkstemp(suffix='.json')
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            json.dump(data, fh)
        self.addCleanup(os.remove, name)
        return name

    def sample(self):
        spec = smskit.cases()['trust']
        return {'issuers': [
            {'issuer': spec['primary']['issuer'], 'primary': True, 'allow_jit': True, 'accepts_feed': True,
             'keys': [{'kid': k, 'pem_file': smskit.path(rel)} for k, rel in spec['primary']['keys'].items()]},
            {'issuer': spec['secondary']['issuer'], 'enabled_until': NOW + 3600,
             'allowed_subs': spec['secondary']['allowed_subs'],
             'keys': [{'kid': k, 'pem': smskit.pem(rel).decode()} for k, rel in spec['secondary']['keys'].items()]},
        ]}

    def test_entries_from_bootstrap(self):
        entries = sms_trust.entries_from_bootstrap(self.sample(), '/')
        by = {e.issuer: e for e in entries}
        self.assertEqual(set(by[smskit.PRIMARY].keys), {'sms-contract-test', 'sms-contract-test-2'})
        self.assertTrue(by[smskit.SECONDARY].level_cap)
        self.assertEqual(by[smskit.SECONDARY].enabled_until, NOW + 3600)

    def test_bootstrap_used_only_outside_production(self):
        name = self.write(self.sample())
        sms_trust.invalidate()
        with mock.patch.dict(os.environ, {'OQB_ENVIRONMENT': 'test', 'SMS_TRUST_BOOTSTRAP_FILE': name}):
            tl = sms_trust._load()
        self.assertEqual(tl.source, 'bootstrap')
        self.assertIsNotNone(tl.primary(NOW))
        with mock.patch.dict(os.environ, {'OQB_ENVIRONMENT': 'production', 'SMS_TRUST_BOOTSTRAP_FILE': name}), \
                mock.patch.object(sms_trust, 'entries_from_db', return_value=[]), \
                mock.patch.object(sms_trust, '_alert_bootstrap_ignored') as alert:
            tl = sms_trust._load()
        self.assertEqual(tl.source, 'db')
        self.assertEqual(tl.entries, {})
        alert.assert_called_once()

    def test_broken_bootstrap_trusts_nobody(self):
        name = self.write({'issuers': [{'issuer': smskit.PRIMARY, 'primary': True,
                                        'keys': [{'kid': 'k', 'pem': 'not a pem'}]}]})
        with mock.patch.dict(os.environ, {'OQB_ENVIRONMENT': 'dev', 'SMS_TRUST_BOOTSTRAP_FILE': name}):
            tl = sms_trust._load()
        self.assertEqual(tl.entries, {})

    def test_example_file_in_docs_parses(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        example = os.path.join(repo, 'docs', 'reference', 'sms-trust-bootstrap.example.json')
        with open(example, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        entries = sms_trust.entries_from_bootstrap(data, os.path.dirname(example))
        self.assertEqual({e.issuer for e in entries}, {smskit.PRIMARY, smskit.SECONDARY})


class JwksCacheTests(unittest.TestCase):
    def setUp(self):
        self.t = [1000.0]
        self.fetches = []
        self.alerts = []
        with open(smskit.path('jwks', 'primary.json'), 'r', encoding='utf-8') as fh:
            self.doc = json.load(fh)

    def cache(self, fail=False):
        def fetch(url, timeout):
            self.fetches.append(url)
            if fail:
                raise OSError('offline')
            return self.doc
        return sms_trust.JwksCache(fetcher=fetch, clock=lambda: self.t[0],
                                   on_new_kid=lambda e, k: self.alerts.append(k.kid))

    def entry(self, auto):
        return replace(smskit.entry('primary', kids=set()), jwks_url='https://jwks.invalid/j.json', jwks_auto=auto)

    def test_keys_from_jwks_matches_pinned_fingerprints(self):
        keys = sms_trust.keys_from_jwks(self.doc)
        spec = smskit.cases()['trust']['primary']['sha256']
        for kid, fp in spec.items():
            self.assertEqual(keys[kid].fingerprint, fp)

    def test_auto_off_alerts_once_and_never_trusts(self):
        cache, entry = self.cache(), self.entry(False)
        self.assertIsNone(cache.resolve(entry, 'sms-contract-test-2'))
        self.assertIsNone(cache.resolve(entry, 'sms-contract-test-2'))
        self.assertEqual(self.alerts, ['sms-contract-test-2'])
        self.assertEqual(len(self.fetches), 1)  # throttled for 5 minutes

    def test_auto_on_trusts_with_cache_and_refetch_throttle(self):
        cache, entry = self.cache(), self.entry(True)
        self.assertIsNotNone(cache.resolve(entry, 'sms-contract-test'))
        self.assertIsNone(cache.resolve(entry, 'sms-contract-test-unknown'))
        self.assertEqual(len(self.fetches), 1)
        self.t[0] += sms_trust.JWKS_REFETCH_SECONDS
        cache.resolve(entry, 'sms-contract-test-unknown')
        self.assertEqual(len(self.fetches), 2)
        self.t[0] += sms_trust.JWKS_CACHE_SECONDS
        self.assertIsNotNone(cache.resolve(entry, 'sms-contract-test'))
        self.assertEqual(len(self.fetches), 3)

    def test_fetch_failure_is_no_key(self):
        cache = self.cache(fail=True)
        self.assertIsNone(cache.resolve(self.entry(True), 'sms-contract-test'))

    def test_no_url_no_fetch(self):
        cache = self.cache()
        self.assertIsNone(cache.resolve(smskit.entry('primary'), 'x'))
        self.assertEqual(self.fetches, [])


if __name__ == '__main__':
    unittest.main()
