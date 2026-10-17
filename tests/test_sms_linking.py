"""app/sms_linking.py (pure)."""
import unittest
from types import SimpleNamespace as NS

from app import sms_linking
from app.utils import validate_username

SUB = '8d1f7c2e-0c1a-4b7e-9000-000000000004'


class DeriveUsernameTests(unittest.TestCase):
    def derive(self, email, taken=()):
        lower = {t.lower() for t in taken}
        return sms_linking.derive_username(email, SUB, lambda n: n.lower() in lower)

    def test_email_local_part(self):
        self.assertEqual(self.derive('Chan.TM@school.test'), 'chan.tm')

    def test_non_ascii_and_symbols(self):
        name = self.derive('chán+tai man@school.test')
        self.assertTrue(validate_username(name)[0], name)
        self.assertEqual(name, 'chan-tai-man')

    def test_collisions_case_insensitive(self):
        self.assertEqual(self.derive('ctm@x', taken=['CTM']), 'ctm-2')
        self.assertEqual(self.derive('ctm@x', taken=['ctm', 'ctm-2']), 'ctm-3')
        self.assertEqual(self.derive('陳@x', taken=['sms-8d1f7c2e']), 'sms-8d1f7c2e-2')

    def test_fallbacks(self):
        for email in (None, '', '@school.test', '陳大文@school.test', 'con@school.test', '...@x'):
            with self.subTest(email):
                name = self.derive(email)
                self.assertTrue(validate_username(name)[0], name)
        self.assertEqual(self.derive('陳大文@school.test'), 'sms-8d1f7c2e')
        self.assertEqual(self.derive('con@school.test'), 'sms-8d1f7c2e')  # reserved name

    def test_long_local_part(self):
        name = self.derive('a' * 200 + '@x')
        self.assertLessEqual(len(name), 80)
        self.assertTrue(validate_username(name)[0])


class RefusalTests(unittest.TestCase):
    def test_link_refusal(self):
        plain = NS(is_super_admin=False, local_only=False)
        self.assertIsNone(sms_linking.link_refusal(plain, 'https://a'))
        self.assertEqual(sms_linking.link_refusal(NS(is_super_admin=True, local_only=False), 'https://a'),
                         sms_linking.PRIVILEGED)
        self.assertEqual(sms_linking.link_refusal(NS(is_super_admin=False, local_only=True), 'https://a'),
                         sms_linking.PRIVILEGED)
        self.assertEqual(sms_linking.link_refusal(plain, 'https://a', ['https://a']), sms_linking.ALREADY_LINKED)
        self.assertIsNone(sms_linking.link_refusal(plain, 'https://b', ['https://a']))
        self.assertEqual(sms_linking.link_refusal(None, 'https://a'), sms_linking.DISABLED)


class SuggestTests(unittest.TestCase):
    def users(self):
        mk = lambda i, u, d=None, c=None, s=False: NS(id=i, username=u, display_name=d, staff_code=c,
                                                      is_super_admin=s, local_only=False)
        return [mk(1, 'ctm', 'Chan Tai Man'), mk(2, 'wong', 'WONG Siu Ming', 'WSM'),
                mk(3, 'chantm', None), mk(4, 'root', 'Chan Tai Man', s=True), mk(5, 'lee', 'Lee Ka Yan')]

    def test_staff_code_first(self):
        out = sms_linking.suggest_users({'staff_code': 'WSM', 'name': 'Someone Else'}, self.users())
        self.assertEqual((out[0][0].id, out[0][1], out[0][2]), (2, 100, 'staff_code'))

    def test_name_similarity_and_no_privileged(self):
        out = sms_linking.suggest_users({'staff_code': 'CTM', 'name': 'CHAN Tai Man',
                                         'email': 'chantm@school.test'}, self.users())
        ids = [u.id for u, _s, _w in out]
        self.assertEqual(ids[0], 1)
        self.assertIn(3, ids)
        self.assertNotIn(4, ids)
        self.assertNotIn(5, ids)

    def test_nothing_matches(self):
        self.assertEqual(sms_linking.suggest_users({'name': 'Zzzz Qqqq'}, self.users()), [])


if __name__ == '__main__':
    unittest.main()
