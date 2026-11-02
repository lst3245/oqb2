"""Pure tests for app.support_requests_service (no app, no DB): status
rules, visibility, the agent queue, message rendering and file names."""
import unittest

from app import support_requests_service as srs


class StatusRuleTests(unittest.TestCase):
    def test_labels_cover_every_status_and_category(self):
        self.assertEqual(set(srs.STATUS_LABELS), set(srs.STATUSES))
        self.assertEqual(set(srs.STATUS_BADGES), set(srs.STATUSES))
        self.assertEqual(set(srs.CATEGORY_LABELS), set(srs.CATEGORIES))
        self.assertLessEqual(set(srs.ACTIVE_STATUSES), set(srs.STATUSES))
        self.assertLessEqual(set(srs.AGENT_STATUSES), set(srs.STATUSES))

    def test_manager_sets_anything(self):
        self.assertEqual(set(srs.allowed_statuses('manager', 'open')), set(srs.STATUSES) - {'open'})
        self.assertTrue(srs.can_set_status('manager', 'closed', 'open'))

    def test_agent_cannot_approve_itself(self):
        for current in srs.STATUSES:
            allowed = srs.allowed_statuses('agent', current)
            self.assertNotIn('approved', allowed, current)
            self.assertNotIn('awaiting_approval', allowed, current)
        self.assertTrue(srs.can_set_status('agent', 'open', 'needs_info'))

    def test_reporter_reopens_or_closes_only(self):
        self.assertEqual(srs.allowed_statuses('reporter', 'resolved'), ['open', 'closed'])
        self.assertEqual(srs.allowed_statuses('reporter', 'open'), ['closed'])
        self.assertEqual(srs.allowed_statuses('reporter', 'closed'), [])
        self.assertFalse(srs.can_set_status('reporter', 'awaiting_approval', 'approved'))
        self.assertEqual(srs.allowed_statuses(None, 'open'), [])

    def test_replies(self):
        self.assertTrue(srs.can_reply('manager', 'closed'))
        self.assertTrue(srs.can_reply('reporter', 'resolved'))
        self.assertFalse(srs.can_reply('reporter', 'closed'))
        self.assertFalse(srs.can_reply(None, 'open'))
        self.assertEqual(srs.status_after_reply('reporter', 'needs_info'), 'open')
        self.assertEqual(srs.status_after_reply('reporter', 'resolved'), 'open')
        self.assertEqual(srs.status_after_reply('reporter', 'awaiting_approval'), 'awaiting_approval')
        self.assertEqual(srs.status_after_reply('manager', 'needs_info'), 'open')
        self.assertEqual(srs.status_after_reply('manager', 'resolved'), 'open')
        self.assertEqual(srs.status_after_reply('manager', 'closed'), 'closed')
        self.assertEqual(srs.status_after_reply('manager', 'awaiting_approval'), 'awaiting_approval')
        self.assertEqual(srs.status_after_reply(None, 'resolved'), 'resolved')

    def test_needs_agent(self):
        self.assertTrue(srs.needs_agent('approved', 'admin'))
        self.assertTrue(srs.needs_agent('approved', 'agent'))
        self.assertTrue(srs.needs_agent('open', 'user'))
        self.assertTrue(srs.needs_agent('in_progress', 'admin'))
        self.assertFalse(srs.needs_agent('open', 'agent'))
        self.assertFalse(srs.needs_agent('open', None))
        for status in ('needs_info', 'awaiting_approval', 'resolved', 'closed'):
            self.assertFalse(srs.needs_agent(status, 'user'), status)


class VisibilityTests(unittest.TestCase):
    def test_visible(self):
        base = dict(user_id=5, is_super=False, admin_subjects={'MATC'}, reporter_id=9)
        self.assertTrue(srs.visible(**base, subjects={'MATC', 'PHY'}))
        self.assertFalse(srs.visible(**base, subjects={'PHY'}))
        self.assertFalse(srs.visible(**base, subjects=set()))  # general: super admins + reporter
        self.assertTrue(srs.visible(**dict(base, reporter_id=5), subjects=set()))
        self.assertTrue(srs.visible(**dict(base, is_super=True), subjects=set()))
        self.assertFalse(srs.visible(**dict(base, reporter_id=None), subjects=set()))

    def test_manage_and_approve(self):
        self.assertTrue(srs.manages(is_super=False, admin_subjects={'MATC'}, subjects={'MATC', 'PHY'}))
        self.assertFalse(srs.approves(is_super=False, admin_subjects={'MATC'}, subjects={'MATC', 'PHY'}))
        self.assertTrue(srs.approves(is_super=False, admin_subjects={'MATC', 'PHY'}, subjects={'MATC', 'PHY'}))
        self.assertFalse(srs.approves(is_super=False, admin_subjects={'MATC'}, subjects=set()))
        self.assertTrue(srs.approves(is_super=True, admin_subjects=set(), subjects=set()))

    def test_report_level(self):
        self.assertTrue(srs.report_level_ok('user', 'user'))
        self.assertTrue(srs.report_level_ok('admin', 'user'))
        self.assertFalse(srs.report_level_ok('viewer', 'user'))
        self.assertTrue(srs.report_level_ok('viewer', 'viewer'))
        self.assertFalse(srs.report_level_ok(None, 'viewer'))
        self.assertFalse(srs.report_level_ok('user', 'admin'))
        self.assertTrue(srs.report_level_ok('user', None))  # default minimum is user


class RenderTests(unittest.TestCase):
    def test_escapes_html(self):
        html = str(srs.render_body('<script>alert(1)</script> & "x"'))
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)

    def test_markup_subset(self):
        html = str(srs.render_body('**bold** and `code`\nline 2'))
        self.assertIn('<strong>bold</strong>', html)
        self.assertIn('<code>code</code>', html)
        self.assertIn('<br>', html)
        block = str(srs.render_body('see\n```\n<b>raw</b>\n```\nafter'))
        self.assertIn('<pre class="oqb-msg-code">&lt;b&gt;raw&lt;/b&gt;</pre>', block)
        self.assertIn('``` open', str(srs.render_body('``` open')))

    def test_links_cannot_break_out(self):
        html = str(srs.render_body('see https://example.com/a?b=1&c=2.'))
        self.assertIn('<a href="https://example.com/a?b=1&amp;c=2"', html)
        self.assertTrue(html.endswith('</a>.'))
        html = str(srs.render_body('https://x.test/"onmouseover="alert(1)'))
        self.assertIn('href="https://x.test/"', html)
        self.assertNotIn('onmouseover="', html)
        self.assertNotIn('<a', str(srs.render_body('javascript:alert(1) data:text/html,x')))


class FileTests(unittest.TestCase):
    def test_safe_filename(self):
        self.assertEqual(srs.safe_filename('..\\..\\evil.png'), 'evil.png')
        self.assertEqual(srs.safe_filename('/etc/passwd'), 'passwd')
        self.assertEqual(srs.safe_filename('a<b>:c?.txt'), 'a_b_c_.txt')
        self.assertEqual(srs.safe_filename('...'), 'file')
        self.assertEqual(srs.safe_filename(None), 'file')
        long = srs.safe_filename('x' * 300 + '.png')
        self.assertLessEqual(len(long), srs.MAX_NAME)
        self.assertTrue(long.endswith('.png'))

    def test_stored_name(self):
        name = srs.stored_name(12, 'Screen Shot.PNG')
        self.assertRegex(name, r'^12_[0-9a-f]{10}\.png$')
        self.assertRegex(srs.stored_name(3, 'noext'), r'^3_[0-9a-f]{10}$')
        self.assertRegex(srs.stored_name(3, 'x.weird ext'), r'^3_[0-9a-f]{10}$')

    def test_mime(self):
        self.assertEqual(srs.guess_mime('a.png'), 'image/png')
        self.assertEqual(srs.guess_mime('a.bin', 'application/octet-stream'), 'application/octet-stream')
        self.assertEqual(srs.guess_mime('a.png', 'image/jpeg; q=1'), 'image/jpeg')
        self.assertTrue(srs.is_inline_image('image/PNG'))
        self.assertFalse(srs.is_inline_image('image/svg+xml'))

    def test_check_files(self):
        mb = 1024 * 1024
        srs.check_files([{'name': 'a', 'data': b'x'}], max_count=1, max_bytes=mb)
        with self.assertRaises(srs.RequestError):
            srs.check_files([{'name': 'a', 'data': b'x'}] * 2, max_count=1, max_bytes=mb)
        with self.assertRaises(srs.RequestError):
            srs.check_files([{'name': 'a', 'data': b'x' * (mb + 1)}], max_count=1, max_bytes=mb)
        with self.assertRaises(srs.RequestError):
            srs.check_files([{'name': 'a', 'data': b''}], max_count=1, max_bytes=mb)

    def test_load_meta(self):
        self.assertEqual(srs.load_meta('{"a": 1}'), {'a': 1})
        self.assertEqual(srs.load_meta('[1]'), {})
        self.assertEqual(srs.load_meta('nope'), {})
        self.assertEqual(srs.load_meta(None), {})


if __name__ == '__main__':
    unittest.main()
