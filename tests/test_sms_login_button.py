"""SMS_LOGIN_BUTTON_ENABLED hides the /login button. Handoff and linking stay on."""
import unittest

import smskit
from ssocase import ORIGIN, SsoCase


class LoginButtonSettingTests(SsoCase):
    def test_off_hides_the_button(self):
        self.app.config['SMS_LOGIN_BUTTON_ENABLED'] = False
        resp = self.client.get('/login')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertNotIn('Sign in with SMS', body)
        self.assertNotIn('id="smsSignIn"', body)
        self.assertNotIn('/apps/oqb?origin=', body)

    def test_on_shows_the_button(self):
        self.app.config['SMS_LOGIN_BUTTON_ENABLED'] = True
        body = self.client.get('/login').get_data(as_text=True)
        self.assertIn('id="smsSignIn"', body)
        self.assertIn('Sign in with SMS', body)
        self.assertIn(f'href="{smskit.PRIMARY}/apps/oqb?origin={ORIGIN}"', body)

    def test_off_still_accepts_a_linked_handoff(self):
        self.app.config['SMS_LOGIN_BUTTON_ENABLED'] = False
        self.assertEqual(self.client.get('/sso/sms').status_code, 200)
        self.assert_next(self.submit('teacher_viewer_user'), '/')
        self.assertTrue(self.session_user().startswith(f"{self.users['teacher_a']}:"))

    def test_off_pending_link_hint_still_shows(self):
        self.app.config['SMS_LOGIN_BUTTON_ENABLED'] = False
        self.assert_next(self.submit('unlinked_teacher'), '/sso/sms/link')
        body = self.client.get('/login').get_data(as_text=True)
        self.assertIn('Sign in with your OQB password to link your SMS account.', body)
        self.assertNotIn('id="smsSignIn"', body)
        self.assertNotIn('Sign in with SMS', body)


if __name__ == '__main__':
    unittest.main()
