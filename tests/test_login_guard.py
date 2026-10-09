"""Login throttle, proxy client IP, and post-login redirect. No database."""
import unittest

from app.login_guard import (
    LoginThrottle,
    client_ip,
    retry_phrase,
    safe_next_url,
)


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class SafeNextTests(unittest.TestCase):
    def test_keeps_same_site_paths(self):
        self.assertEqual(safe_next_url('/dashboard/'), '/dashboard/')
        self.assertEqual(safe_next_url('/admin/users?x=1'), '/admin/users?x=1')

    def test_drops_off_site_and_tricks(self):
        for target in (
            None, '', 'dashboard', 'https://evil.example/', '//evil.example',
            '///evil.example', '/\\evil.example', '/\tevil', ' /\\dashboard',
        ):
            self.assertIsNone(safe_next_url(target), target)


class ClientIpTests(unittest.TestCase):
    def test_direct_client_ignores_forwarded_headers(self):
        ip = client_ip('8.8.8.8', '1.2.3.4', '9.9.9.9', None)
        self.assertEqual(ip, '8.8.8.8')

    def test_trusted_proxy_prefers_x_real_ip(self):
        ip = client_ip('192.168.7.1', '203.0.113.9', '1.1.1.1, 203.0.113.9', None)
        self.assertEqual(ip, '203.0.113.9')

    def test_forwarded_for_uses_the_hop_the_proxy_added(self):
        ip = client_ip('192.168.7.1', None, '1.1.1.1, 203.0.113.10', None)
        self.assertEqual(ip, '203.0.113.10')

    def test_pinned_proxy_list(self):
        trusted = ('192.168.7.1',)
        self.assertEqual(
            client_ip('192.168.7.1', None, '203.0.113.11', trusted),
            '203.0.113.11',
        )
        self.assertEqual(
            client_ip('192.168.7.70', '203.0.113.11', None, trusted),
            '192.168.7.70',
        )

    def test_empty_trust_list_ignores_headers(self):
        self.assertEqual(
            client_ip('192.168.7.1', '203.0.113.12', None, ()),
            '192.168.7.1',
        )


class ThrottleTests(unittest.TestCase):
    def test_username_cap_and_window(self):
        clock = _Clock()
        throttle = LoginThrottle(ip_limit=100, user_limit=2, window_seconds=60, clock=clock)
        self.assertEqual(throttle.retry_after('10.0.0.1', 'Ada'), 0)
        throttle.record_failure('10.0.0.1', 'Ada')
        throttle.record_failure('10.0.0.8', 'ada')
        wait = throttle.retry_after('10.0.0.9', 'ADA')
        self.assertGreater(wait, 0)
        clock.now += 61
        self.assertEqual(throttle.retry_after('10.0.0.9', 'Ada'), 0)

    def test_success_clears_the_username_only(self):
        clock = _Clock()
        throttle = LoginThrottle(ip_limit=2, user_limit=8, window_seconds=60, clock=clock)
        throttle.record_failure('10.0.0.1', 'Ada')
        throttle.record_failure('10.0.0.1', 'Bea')
        self.assertGreater(throttle.retry_after('10.0.0.1', 'Cara'), 0)
        throttle.record_success('Ada')
        self.assertGreater(throttle.retry_after('10.0.0.1', 'Cara'), 0)
        self.assertEqual(throttle.retry_after('10.0.0.2', 'Ada'), 0)

    def test_retry_phrase(self):
        self.assertEqual(retry_phrase(1), '1 second')
        self.assertEqual(retry_phrase(45), '45 seconds')
        self.assertEqual(retry_phrase(60), '1 minute')
        self.assertEqual(retry_phrase(61), '2 minutes')
