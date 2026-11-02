"""Agent wake webhook (app/agent_wake.py) and its hooks in the Requests
routes. Pure: a bare Flask app with the Requests blueprint, database calls
stubbed, no create_app(), no network (the thread start is captured)."""
import json
import unittest
import urllib.error
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

from flask import Flask

from app import agent_wake, authz
from app import support_requests as routes
from app import support_requests_service as srs

URL = 'https://hooks.example.test/routines/wake'
SECRET = 'wake-s3cret'
JSON = {'Accept': 'application/json'}


def _req(**kw):
    base = dict(id=42, status='in_progress', last_actor_kind='agent', subject_id='MATC',
                title='Wrong answer in Q3', last_message_at=datetime(2026, 11, 1, 2, 0, 0))
    base.update(kw)
    return SimpleNamespace(**base)


LINK = SimpleNamespace(qid='MATC_2020_P1_Q3', question_id=901, subject_id='MATC')


class PureTests(unittest.TestCase):
    def setUp(self):
        agent_wake._recent.clear()

    def test_should_wake_only_on_transition_into_queue(self):
        self.assertTrue(agent_wake.should_wake(False, 'open', 'user'))
        self.assertTrue(agent_wake.should_wake(False, 'in_progress', 'admin'))
        self.assertTrue(agent_wake.should_wake(False, 'approved', 'admin'))
        self.assertFalse(agent_wake.should_wake(True, 'open', 'user'))      # already queued
        self.assertFalse(agent_wake.should_wake(False, 'open', 'agent'))    # agent spoke last
        self.assertFalse(agent_wake.should_wake(False, 'needs_info', 'user'))
        self.assertFalse(agent_wake.should_wake(False, 'closed', 'user'))

    def test_url_ok(self):
        self.assertTrue(agent_wake.url_ok(URL))
        self.assertTrue(agent_wake.url_ok('http://127.0.0.1:8080/wake'))
        self.assertTrue(agent_wake.url_ok('http://localhost/wake'))
        self.assertFalse(agent_wake.url_ok('http://hooks.example.test/wake'))
        self.assertFalse(agent_wake.url_ok('ftp://hooks.example.test/wake'))
        self.assertFalse(agent_wake.url_ok('https:///nohost'))
        self.assertFalse(agent_wake.url_ok(''))

    def test_payload_has_ids_only(self):
        body = agent_wake.payload(_req(status='open', last_actor_kind='user'), [LINK])
        self.assertEqual(body, {
            'event': 'request.needs_agent', 'request_id': 42, 'status': 'open', 'subject_id': 'MATC',
            'title': 'Wrong answer in Q3', 'last_message_at': '2026-11-01T02:00:00Z',
            'questions': [{'qid': 'MATC_2020_P1_Q3', 'question_id': 901, 'subject_id': 'MATC'}],
        })

    def test_headers(self):
        self.assertNotIn('Authorization', agent_wake.headers(''))
        h = agent_wake.headers(SECRET)
        self.assertEqual(h['Authorization'], f'Bearer {SECRET}')
        self.assertEqual(h['X-Webhook-Secret'], SECRET)

    def test_claim_dedupes_within_window(self):
        self.assertTrue(agent_wake.claim(1, now=100.0))
        self.assertFalse(agent_wake.claim(1, now=102.0))
        self.assertTrue(agent_wake.claim(2, now=102.0))
        self.assertTrue(agent_wake.claim(1, now=100.0 + agent_wake.DEDUPE_SECONDS))

    def test_send_failure_is_logged_without_secret_or_path(self):
        err = urllib.error.URLError('connection refused')
        with mock.patch.object(agent_wake._opener, 'open', side_effect=err) as opened, \
                self.assertLogs('app.agent_wake', 'WARNING') as logs:
            agent_wake.send(URL, b'{}', agent_wake.headers(SECRET), 42)
        self.assertEqual(opened.call_args.kwargs['timeout'], agent_wake.TIMEOUT_SECONDS)
        text = '\n'.join(logs.output)
        self.assertIn('hooks.example.test', text)
        self.assertNotIn(SECRET, text)
        self.assertNotIn('/routines/wake', text)

    def test_notify_never_raises(self):
        cfg = {'AGENT_WAKE_WEBHOOK_URL': URL}
        with mock.patch.object(srs, 'linked_questions', side_effect=RuntimeError('db down')), \
                mock.patch.object(agent_wake, '_start') as start, self.assertLogs('app.agent_wake', 'ERROR'):
            self.assertFalse(agent_wake.notify(_req(status='open', last_actor_kind='user'), False, config=cfg))
        start.assert_not_called()

    def test_notify_refuses_plain_http_to_remote_host(self):
        cfg = {'AGENT_WAKE_WEBHOOK_URL': 'http://hooks.example.test/wake'}
        with mock.patch.object(agent_wake, '_start') as start, self.assertLogs('app.agent_wake', 'WARNING'):
            self.assertFalse(agent_wake.notify(_req(status='open', last_actor_kind='user'), False, config=cfg))
        start.assert_not_called()


class RouteTests(unittest.TestCase):
    """The create / reply / approval routes schedule the POST after their
    commit when the thread newly needs the agent."""

    def setUp(self):
        agent_wake._recent.clear()
        self.app = Flask(__name__)
        self.app.config.update(LOGIN_DISABLED=True, REQUESTS_ENABLED=True,
                               AGENT_WAKE_WEBHOOK_URL=URL, AGENT_WAKE_WEBHOOK_SECRET=SECRET,
                               REQUESTS_MAX_ATTACHMENTS_PER_MESSAGE=10, REQUESTS_MAX_ATTACHMENT_MB=20)
        self.app.register_blueprint(routes.support_requests_bp)
        self.client = self.app.test_client()
        self.started = []
        for p in (
            mock.patch.object(routes, 'db'),
            mock.patch.object(routes, 'current_user', SimpleNamespace(id=7, is_authenticated=True)),
            mock.patch.object(srs, 'mark_read'),
            mock.patch.object(srs, 'linked_questions', side_effect=lambda ids: {i: [LINK] for i in ids}),
            mock.patch.object(agent_wake, '_start', side_effect=lambda *a: self.started.append(a)),
        ):
            p.start()
            self.addCleanup(p.stop)

    def _thread(self, req, **access):
        access = SimpleNamespace(**dict({'role': 'reporter', 'approve': False}, **access))
        p = mock.patch.object(routes, '_request_or_404', return_value=(req, access))
        p.start()
        self.addCleanup(p.stop)

    @staticmethod
    def _acts(status, actor):
        def act(req, *args, **kwargs):
            req.status, req.last_actor_kind = status, actor
            return SimpleNamespace(id=5)
        return act

    def _reply(self, req, status='open', actor='user'):
        with mock.patch.object(srs, 'reply', side_effect=self._acts(status, actor)):
            resp = self.client.post(f'/requests/{req.id}/messages', data={'body': 'Still wrong, see page 2'},
                                    headers=JSON)
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))

    def _create(self):
        new = _req(id=77, status='open', last_actor_kind='user', subject_id=None, title='Export broken')
        with mock.patch.object(srs, 'can_report', return_value=True), \
                mock.patch.object(authz, 'is_super_admin', return_value=False), \
                mock.patch.object(authz, 'subjects_with_level', return_value=set()), \
                mock.patch.object(srs, 'create_request', return_value=(new, SimpleNamespace(id=1))):
            resp = self.client.post('/requests/new', data={'title': 'Export broken', 'body': 'secret detail',
                                                           'category': 'app_bug'}, headers=JSON)
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))

    def test_create_schedules_wake(self):
        self._create()
        self.assertEqual(len(self.started), 1)
        target, url, body, hdrs, request_id = self.started[0]
        self.assertIs(target, agent_wake.send)
        self.assertEqual((url, request_id), (URL, 77))
        sent = json.loads(body)
        self.assertEqual(sent['event'], 'request.needs_agent')
        self.assertEqual(sent['request_id'], 77)
        self.assertNotIn('secret detail', body.decode('utf-8'))
        self.assertEqual(hdrs['X-Webhook-Secret'], SECRET)

    def test_create_skips_when_url_empty(self):
        self.app.config['AGENT_WAKE_WEBHOOK_URL'] = ''
        self._create()
        self.assertEqual(self.started, [])

    def test_reply_that_puts_thread_in_queue_schedules_wake(self):
        req = _req(status='needs_info', last_actor_kind='agent')
        self._thread(req)
        self._reply(req)
        self.assertEqual(len(self.started), 1)
        sent = json.loads(self.started[0][2])
        self.assertEqual((sent['request_id'], sent['status']), (42, 'open'))
        self.assertEqual(sent['questions'], [{'qid': 'MATC_2020_P1_Q3', 'question_id': 901, 'subject_id': 'MATC'}])
        self.assertNotIn('Still wrong', self.started[0][2].decode('utf-8'))

    def test_reply_skips_when_url_empty(self):
        self.app.config['AGENT_WAKE_WEBHOOK_URL'] = ''
        req = _req(status='needs_info', last_actor_kind='agent')
        self._thread(req)
        self._reply(req)
        self.assertEqual(self.started, [])

    def test_reply_to_thread_already_waiting_does_not_fire(self):
        req = _req(status='open', last_actor_kind='user')
        self._thread(req)
        self._reply(req)
        self.assertEqual(self.started, [])

    def test_rapid_second_wake_is_deduplicated(self):
        req = _req(status='needs_info', last_actor_kind='agent')
        self._thread(req)
        self._reply(req)
        req.status, req.last_actor_kind = 'in_progress', 'agent'   # the agent answered quickly
        self._reply(req, status='in_progress', actor='user')
        self.assertEqual(len(self.started), 1)

    def test_approval_decision_schedules_wake(self):
        req = _req(status='awaiting_approval', last_actor_kind='agent')
        self._thread(req, role='manager', approve=True)
        with mock.patch.object(srs, 'decide_approval', side_effect=self._acts('approved', 'admin')):
            resp = self.client.post('/requests/42/approval', json={'decision': 'approve'}, headers=JSON)
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        self.assertEqual(len(self.started), 1)
        self.assertEqual(json.loads(self.started[0][2])['status'], 'approved')

    def _real_reply(self, req, access):
        """``srs.reply`` itself (its status rule), with only the DB write stubbed."""
        def add_message(r, *, actor_kind=None, new_status=None, **kwargs):
            r.last_actor_kind = actor_kind
            if new_status:
                r.status = new_status
            return SimpleNamespace(id=9)
        p = mock.patch.object(routes, '_request_or_404', return_value=(req, access))
        p.start()
        self.addCleanup(p.stop)
        with mock.patch.object(srs, 'add_message', side_effect=add_message):
            resp = self.client.post(f'/requests/{req.id}/messages', data={'body': 'Not fixed yet'}, headers=JSON)
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))

    def test_admin_reply_to_resolved_thread_reopens_and_wakes(self):
        req = _req(status='resolved', last_actor_kind='agent')
        self._real_reply(req, srs.Access('manager', True, True, True, True))
        self.assertEqual((req.status, req.last_actor_kind), ('open', 'admin'))
        self.assertEqual(len(self.started), 1)

    def test_remark_on_closed_thread_does_not_wake(self):
        req = _req(status='closed', last_actor_kind='agent')
        self._real_reply(req, srs.Access('manager', True, True, True, False))
        self.assertEqual(req.status, 'closed')
        self.assertEqual(self.started, [])

    def test_failed_reply_does_not_fire(self):
        req = _req(status='needs_info', last_actor_kind='agent')
        self._thread(req)
        with mock.patch.object(srs, 'reply', side_effect=srs.RequestError('This request is closed.')):
            resp = self.client.post('/requests/42/messages', data={'body': 'x'}, headers=JSON)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.started, [])


if __name__ == '__main__':
    unittest.main()
