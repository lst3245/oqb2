"""app/sms_levels.py and the one-module rule for level names."""
import os
import re
import unittest

from app import sms_levels

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Modules that must take level names from app/sms_levels.py.
SCANNED = ('app/sms_verify.py', 'app/sms_trust.py', 'app/auth_audit.py', 'app/sso.py',
           'app/sms_sso_service.py', 'app/sms_linking.py', 'app/sms_feed_service.py')
LITERAL = re.compile(r"""['"](none|viewer|user|admin)['"]""")
CSP_KEYWORD = re.compile(r"""(-src|-ancestors|-action) 'none'""")
NOT_A_LEVEL = '# not a level'


class LevelTests(unittest.TestCase):
    def test_enums(self):
        self.assertEqual(sms_levels.DEFAULT_LEVELS, ('none', 'viewer', 'admin'))
        self.assertEqual(sms_levels.SUBJECT_LEVELS, ('viewer', 'user', 'admin'))
        self.assertFalse(sms_levels.is_default_level('user'))
        self.assertFalse(sms_levels.is_subject_level('none'))
        self.assertFalse(sms_levels.is_subject_level(None))

    def test_cap(self):
        self.assertEqual(sms_levels.cap('admin', 'default'), 'viewer')
        self.assertEqual(sms_levels.cap('none', 'default'), 'none')
        self.assertEqual(sms_levels.cap('admin', 'subject'), 'user')
        self.assertEqual(sms_levels.cap('viewer', 'subject'), 'viewer')
        self.assertEqual(sms_levels.oqb_cap('default'), 'viewer')
        self.assertEqual(sms_levels.oqb_cap('subject'), 'user')

    def test_above_and_mapping(self):
        self.assertTrue(sms_levels.above('user', 'viewer'))
        self.assertFalse(sms_levels.above('viewer', 'viewer'))
        self.assertTrue(sms_levels.above('viewer', 'none'))
        self.assertIsNone(sms_levels.to_oqb_level('none'))
        self.assertEqual(sms_levels.to_oqb_level('admin'), 'admin')

    def test_display_names_are_final(self):
        self.assertEqual([sms_levels.display(x) for x in ('viewer', 'user', 'admin')], ['Viewer', 'User', 'Admin'])

    def test_display_names_are_translated(self):
        import json
        with open(os.path.join(REPO, 'app', 'translations', 'zh_Hant.json'), 'r', encoding='utf-8') as fh:
            catalogue = json.load(fh)
        for label in sms_levels.DISPLAY.values():
            self.assertIn(label, catalogue, label)

    def test_no_level_literals_outside_the_level_module(self):
        for rel in SCANNED:
            full = os.path.join(REPO, *rel.split('/'))
            if not os.path.exists(full):
                continue
            with open(full, 'r', encoding='utf-8') as fh:
                for lineno, line in enumerate(fh, 1):
                    if NOT_A_LEVEL in line:
                        continue
                    code = CSP_KEYWORD.sub('', line.split('#', 1)[0])
                    self.assertIsNone(LITERAL.search(code), f'{rel}:{lineno}: {line.strip()}')


if __name__ == '__main__':
    unittest.main()
