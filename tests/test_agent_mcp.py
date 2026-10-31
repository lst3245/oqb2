"""Pure tests for app.agent_mcp (no app, no DB): token and IP checks,
JSON-RPC dispatch with stub methods, argument summaries and the tool
registry's shape."""
import unittest

from app import agent_mcp as m


CTX = m.AgentContext(agent_name='Grok')


class AuthTests(unittest.TestCase):
    def test_bearer(self):
        self.assertTrue(m.bearer_ok('Bearer s3cret', 's3cret'))
        self.assertTrue(m.bearer_ok('bearer   s3cret  ', 's3cret'))
        self.assertFalse(m.bearer_ok('Bearer wrong', 's3cret'))
        self.assertFalse(m.bearer_ok('Basic s3cret', 's3cret'))
        self.assertFalse(m.bearer_ok('Bearer ', 's3cret'))
        self.assertFalse(m.bearer_ok(None, 's3cret'))
        self.assertFalse(m.bearer_ok('Bearer ', ''))
        self.assertFalse(m.bearer_ok('Bearer x', ''))

    def test_ip_allowed(self):
        loop = '127.0.0.1,::1'
        self.assertTrue(m.ip_allowed('127.0.0.1', loop))
        self.assertTrue(m.ip_allowed('::1', loop))
        self.assertTrue(m.ip_allowed('::ffff:127.0.0.1', loop))
        self.assertFalse(m.ip_allowed('192.168.1.20', loop))
        self.assertTrue(m.ip_allowed('192.168.1.20', '192.168.1.0/24'))
        self.assertTrue(m.ip_allowed('10.0.0.1', 'garbage, *'))
        self.assertTrue(m.ip_allowed('10.0.0.1', 'garbage, 10.0.0.1'))
        self.assertFalse(m.ip_allowed('10.0.0.1', ''))
        self.assertFalse(m.ip_allowed('not-an-ip', '127.0.0.1'))
        self.assertTrue(m.ip_allowed('', '*,127.0.0.1'))

    def test_peer_ip_ignores_forwarded_header(self):
        # ProxyFix rewrote REMOTE_ADDR from a forged X-Forwarded-For.
        forged = {'REMOTE_ADDR': '127.0.0.1',
                  'werkzeug.proxy_fix.orig': {'REMOTE_ADDR': '192.168.1.20'}}
        self.assertEqual(m.peer_ip(forged), '192.168.1.20')
        self.assertEqual(m.peer_ip({'REMOTE_ADDR': '127.0.0.1'}), '127.0.0.1')
        self.assertEqual(m.peer_ip({}), '')

    def test_agent_name(self):
        self.assertEqual(m.clean_agent_name('  Grok\n bot ', 'AI agent'), 'Grok bot')
        self.assertEqual(m.clean_agent_name('', 'Helper'), 'Helper')
        self.assertEqual(m.clean_agent_name(None, ''), 'AI agent')
        self.assertEqual(len(m.clean_agent_name('x' * 500, '')), m.MAX_AGENT_NAME)


class DispatchTests(unittest.TestCase):
    def setUp(self):
        def echo(params, ctx):
            return {'params': params, 'agent': ctx.agent_name}

        def refuse(params, ctx):
            raise m.RpcError(m.INVALID_PARAMS, 'bad params')

        self.methods = {'echo': echo, 'refuse': refuse}

    def call(self, msg):
        return m.handle_message(msg, CTX, self.methods)

    def test_result(self):
        out = self.call({'jsonrpc': '2.0', 'id': 1, 'method': 'echo', 'params': {'a': 1}})
        self.assertEqual(out, {'jsonrpc': '2.0', 'id': 1, 'result': {'params': {'a': 1}, 'agent': 'Grok'}})

    def test_errors(self):
        self.assertEqual(self.call({'id': 1, 'method': 'echo'})['error']['code'], m.INVALID_REQUEST)
        self.assertEqual(self.call('nope')['error']['code'], m.INVALID_REQUEST)
        self.assertEqual(self.call({'jsonrpc': '2.0', 'id': 2, 'method': 'missing'})['error']['code'],
                         m.METHOD_NOT_FOUND)
        self.assertEqual(self.call({'jsonrpc': '2.0', 'id': 3, 'method': 'refuse'})['error'],
                         {'code': m.INVALID_PARAMS, 'message': 'bad params'})
        self.assertEqual(self.call({'jsonrpc': '2.0', 'id': 4, 'method': 'echo', 'params': 5})['error']['code'],
                         m.INVALID_PARAMS)

    def test_notifications_and_responses_get_no_answer(self):
        self.assertIsNone(self.call({'jsonrpc': '2.0', 'method': 'echo'}))
        self.assertIsNone(self.call({'jsonrpc': '2.0', 'method': 'missing'}))
        self.assertIsNone(self.call({'jsonrpc': '2.0', 'id': 9, 'result': {}}))

    def test_batch(self):
        out = m.handle_body([{'jsonrpc': '2.0', 'id': 1, 'method': 'echo'},
                             {'jsonrpc': '2.0', 'method': 'echo'}], CTX, self.methods)
        self.assertEqual([r['id'] for r in out], [1])
        self.assertIsNone(m.handle_body([{'jsonrpc': '2.0', 'method': 'echo'}], CTX, self.methods))
        self.assertEqual(m.handle_body([], CTX, self.methods)['error']['code'], m.INVALID_REQUEST)

    def test_initialize(self):
        out = m.handle_message({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                                'params': {'protocolVersion': '2025-03-26'}}, CTX)
        self.assertEqual(out['result']['protocolVersion'], '2025-03-26')
        self.assertIn('tools', out['result']['capabilities'])
        self.assertEqual(m.negotiate_version('1999-01-01'), m.PROTOCOL_VERSIONS[0])


class HelperTests(unittest.TestCase):
    def test_summarize_cuts_long_values(self):
        text = m.summarize_args({'data_base64': 'A' * 5000, 'items': list(range(80)), 'reason': 'ok'})
        self.assertIn('(5000 chars)', text)
        self.assertIn('(80 items)', text)
        self.assertIn('"reason": "ok"', text)
        self.assertLessEqual(len(m.summarize_args({f'k{i}': 'x' * 290 for i in range(40)})),
                             m.SUMMARY_CHARS + 3)

    def test_results(self):
        self.assertEqual(m.text_result({'a': 1})['structuredContent'], {'a': 1})
        self.assertEqual(m.text_result([1, 2])['structuredContent'], {'items': [1, 2]})
        self.assertTrue(m.error_result('no')['isError'])


class RegistryTests(unittest.TestCase):
    def test_tools_are_well_formed(self):
        tools = m.registry()
        self.assertGreaterEqual(len(tools), 26)
        for name, t in tools.items():
            with self.subTest(tool=name):
                self.assertEqual(t.schema['type'], 'object')
                self.assertIn('agent_name', t.schema['properties'])
                for key in t.schema.get('required', ()):
                    self.assertIn(key, t.schema['properties'])
                self.assertTrue(t.description)
                if t.thread_note:
                    self.assertTrue(t.writes)

    def test_every_write_takes_a_reason_and_none_deletes(self):
        for name, t in m.registry().items():
            if t.writes:
                self.assertIn('reason', t.schema['properties'], name)
            self.assertFalse(name.startswith('delete'), name)

    def test_library_writes_require_a_reason(self):
        for name in ('update_question_fields', 'rename_question', 'set_parent', 'upload_asset',
                     'replace_asset', 'add_taxonomy_node', 'rename_taxonomy_node'):
            t = m.registry()[name]
            self.assertIn('reason', t.schema['required'], name)
            self.assertTrue(t.thread_note, name)
        self.assertIn('request_id', m.registry()['replace_asset'].schema['required'])

    def test_playbook_is_served(self):
        text = m.read_playbook()
        self.assertIn('request_approval', text)
        self.assertNotIn('playbook file is missing', text)


if __name__ == '__main__':
    unittest.main()
