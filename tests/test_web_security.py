"""app/web_security.py and app/rate_limit.py (no DB)."""
import unittest

from flask import Flask, request

from app import web_security
from app.rate_limit import RateLimiter


class OriginTests(unittest.TestCase):
    def test_normalise_origin(self):
        n = web_security.normalise_origin
        self.assertEqual(n('https://OQB.mathedu.info'), 'https://oqb.mathedu.info')
        self.assertEqual(n('https://oqb.mathedu.info/'), 'https://oqb.mathedu.info')
        self.assertEqual(n('https://oqb.mathedu.info:443'), 'https://oqb.mathedu.info')
        self.assertEqual(n('http://localhost:5000'), 'http://localhost:5000')
        for bad in ('', None, 'null', 'oqb.mathedu.info', 'ftp://x', 'https://x/path', 'https://u@x',
                    'https://x?q=1', 'https://x:99999'):
            self.assertEqual(n(bad), '', bad)

    def app(self, **config):
        app = Flask(__name__)
        settings = dict(PUBLIC_ORIGIN='https://oqb.mathedu.info', PROXY_FIX_HOPS=0, TRUSTED_PROXIES=None)
        settings.update(config)
        app.config.update(settings)
        seen = {}

        @app.route('/probe', methods=['POST'])
        def probe():
            seen.update(ok=web_security.origin_ok(request), ip=web_security.client_ip(request),
                        url=request.host_url)
            return ''
        return app, seen

    def test_origin_ok(self):
        app, seen = self.app()
        client = app.test_client()
        for origin, ok in (('https://oqb.mathedu.info', True), (None, False), ('null', False),
                           ('http://oqb.mathedu.info', False), ('https://evil.example', False)):
            headers = {'Origin': origin} if origin else {}
            client.post('/probe', headers=headers)
            self.assertEqual(seen['ok'], ok, origin)

    def test_no_public_origin_refuses_everything(self):
        app, seen = self.app(PUBLIC_ORIGIN='')
        app.test_client().post('/probe', headers={'Origin': 'http://localhost'})
        self.assertFalse(seen['ok'])

    def test_proxy_fix(self):
        app, seen = self.app(PROXY_FIX_HOPS=1)
        web_security.init_proxy(app)
        app.test_client().post('/probe', headers={
            'Origin': 'https://oqb.mathedu.info', 'X-Forwarded-For': '198.51.100.7',
            'X-Forwarded-Proto': 'https', 'X-Forwarded-Host': 'oqb.mathedu.info'})
        self.assertTrue(seen['ok'])
        self.assertEqual(seen['ip'], '198.51.100.7')
        self.assertEqual(seen['url'], 'https://oqb.mathedu.info/')

    def test_without_proxy_fix_forwarded_for_is_ignored(self):
        app, seen = self.app()
        web_security.init_proxy(app)
        app.test_client().post('/probe', headers={'X-Forwarded-For': '198.51.100.7'},
                               environ_base={'REMOTE_ADDR': '8.8.4.4'})
        self.assertEqual(seen['ip'], '8.8.4.4')


class RateLimiterTests(unittest.TestCase):
    def test_window(self):
        t = [0.0]
        rl = RateLimiter(3, 60, clock=lambda: t[0])
        self.assertEqual([rl.hit('a') for _ in range(3)], [0, 0, 0])
        self.assertGreater(rl.hit('a'), 0)
        self.assertEqual(rl.hit('b'), 0)
        t[0] = 61
        self.assertEqual(rl.hit('a'), 0)
        rl.reset()
        self.assertEqual(rl.hit('a'), 0)


if __name__ == '__main__':
    unittest.main()
