"""scripts/sms_contract_mint.py: tokens it mints verify; it refuses real issuers."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

from app import sms_verify
import smskit

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, 'scripts'))
import sms_contract_mint as mint  # noqa: E402

NOW = smskit.REFERENCE_NOW


class MintTests(unittest.TestCase):
    def claims_file(self, data):
        fd, name = tempfile.mkstemp(suffix='.json')
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            json.dump(data, fh)
        self.addCleanup(os.remove, name)
        return name

    def person_claims(self):
        c = dict(smskit.claims('teacher_viewer_user'))
        for k in ('iss', 'aud', 'purpose', 'ver', 'iat', 'nbf', 'exp', 'jti'):
            c.pop(k, None)
        return c

    def run_main(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = mint.main(list(args))
        return code, out.getvalue().strip()

    def test_handoff_round_trip(self):
        code, token = self.run_main('--key', smskit.path('keys', 'sms-contract-test.pem'),
                                    '--kid', 'sms-contract-test', '--iss', smskit.PRIMARY,
                                    '--claims', self.claims_file(self.person_claims()), '--now', str(NOW))
        self.assertEqual(code, 0)
        result = sms_verify.verify_handoff(token, smskit.trust(NOW), NOW)
        self.assertEqual(result.exp - result.iat, 60)

    def test_snapshot_round_trip(self):
        c = dict(smskit.claims('snapshot_baseline'))
        for k in ('iss', 'aud', 'purpose', 'ver', 'iat', 'nbf', 'exp', 'jti', 'snapshot_id'):
            c.pop(k, None)
        code, token = self.run_main('--key', smskit.path('keys', 'sms-contract-test.pem'),
                                    '--kid', 'sms-contract-test', '--iss', smskit.PRIMARY,
                                    '--purpose', 'snapshot', '--claims', self.claims_file(c), '--now', str(NOW))
        self.assertEqual(code, 0)
        snap = sms_verify.verify_snapshot(token, smskit.trust(NOW), NOW)
        self.assertEqual(snap.nbf, NOW)

    def test_refuses_real_issuer_without_flag(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            mint.main(['--key', smskit.path('keys', 'sms-contract-test.pem'), '--kid', 'x',
                       '--iss', 'https://ms.tic.edu.hk', '--claims', self.claims_file({})])


if __name__ == '__main__':
    unittest.main()
