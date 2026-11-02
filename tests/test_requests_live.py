"""Live thread updates: GET /requests/<id>/live and the shared thread body
partial (app/support_requests.py, templates/partials/request_thread_body.html).
Pure: a bare Flask app with the Requests blueprint, database calls stubbed,
no create_app()."""
import os
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

from flask import Flask, render_template

from app import support_requests as routes
from app import support_requests_service as srs
from app.i18n import t

TEMPLATES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'templates')
JSON = {'Accept': 'application/json'}
WHEN = datetime(2026, 11, 1, 2, 0, 0)


def _app():
    app = Flask(__name__, template_folder=TEMPLATES)
    app.config.update(LOGIN_DISABLED=True, REQUESTS_ENABLED=True)
    app.register_blueprint(routes.support_requests_bp)
    app.jinja_env.globals.update(t=t, current_user=SimpleNamespace(id=7, is_authenticated=True))
    return app


def _req(**kw):
    base = dict(id=42, status='open', category='question', subject_id='MATC', title='Wrong answer',
                reporter_id=7, created_at=WHEN, closed_by=None, last_actor_kind='user')
    base.update(kw)
    return SimpleNamespace(**base)


def _msg(mid, **kw):
    base = dict(id=mid, kind='message', author_kind='user', author_user_id=7, agent_name=None,
                body='See the picture', meta=None, created_at=WHEN)
    base.update(kw)
    return SimpleNamespace(**base)


class LiveRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = _app()
        self.client = self.app.test_client()
        self.req = _req()
        self.rendered = []
        for p in (
            mock.patch.object(routes, 'db'),
            mock.patch.object(routes, 'current_user', SimpleNamespace(id=7, is_authenticated=True)),
            mock.patch.object(routes, '_request_or_404',
                              return_value=(self.req, SimpleNamespace(role='reporter', manage=False))),
            mock.patch.object(routes, '_thread_context', return_value={}),
            mock.patch.object(routes, 'render_template',
                              side_effect=lambda name, **ctx: self.rendered.append(name) or '<div id="rqThread"></div>'),
            mock.patch.object(srs, 'last_message_id', return_value=12),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.mark_read = mock.patch.object(srs, 'mark_read').start()
        self.addCleanup(mock.patch.stopall)

    def poll(self, **params):
        resp = self.client.get(f'/requests/{self.req.id}/live', query_string=params, headers=JSON)
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        return resp.get_json()

    def test_unchanged_sends_no_html(self):
        data = self.poll(after=12, status='open')
        self.assertEqual(data, {'success': True, 'changed': False, 'last_message_id': 12, 'status': 'open'})
        self.assertEqual(self.rendered, [])

    def test_new_message_sends_the_body(self):
        data = self.poll(after=11, status='open')
        self.assertTrue(data['changed'])
        self.assertIn('rqThread', data['html'])
        self.assertEqual(self.rendered, ['partials/request_thread_body.html'])

    def test_status_change_alone_counts(self):
        self.req.status = 'resolved'
        data = self.poll(after=12, status='open')
        self.assertTrue(data['changed'])
        self.assertEqual(data['status'], 'resolved')

    def test_marks_read_only_when_visible(self):
        self.poll(after=12, status='open', read='0')
        self.mark_read.assert_not_called()
        self.poll(after=12, status='open', read='1')
        self.mark_read.assert_called_once_with(7, 42, 12)

    def test_is_a_passive_endpoint(self):
        from app import web_security
        self.assertIn('support_requests.thread_live', web_security.PASSIVE_ENDPOINTS)


class ThreadBodyTests(unittest.TestCase):
    """The partial both the page and the poll render."""

    def render(self, req, messages, attachments, can_reply=True):
        app = _app()
        ctx = dict(
            req=req, access=SimpleNamespace(role='reporter', reporter=True, approve=False, manage=False),
            messages=messages, attachments=attachments, links=[], live={}, viewable=set(), editable=set(),
            names={7: 'Teacher'}, batches=[], pending_id=None, statuses=['closed'], can_reply=can_reply,
            status_labels=srs.STATUS_LABELS, status_badges=srs.STATUS_BADGES, category_labels=srs.CATEGORY_LABELS,
            render_body=srs.render_body, load_meta=srs.load_meta, is_inline_image=srs.is_inline_image,
            limits={'max_files': 10, 'max_mb': 20})
        with app.test_request_context('/requests/42'):
            return render_template('partials/request_thread_body.html', **ctx)

    def test_live_regions_and_state(self):
        html = self.render(_req(), [_msg(11), _msg(12, author_kind='agent', author_user_id=None, agent_name='Grok')], {})
        for region in ('id="rqHeader"', 'id="rqThread"', 'id="rqReplyArea"', 'id="rqReplyNotice"',
                       'id="rqStatusBox"', 'id="rqBatches"', 'id="rqComposer"'):
            self.assertIn(region, html)
        self.assertIn('data-last-id="12"', html)
        self.assertIn('data-status="open"', html)
        self.assertIn('data-can-reply="1"', html)

    def test_closed_thread_has_no_composer(self):
        html = self.render(_req(status='closed'), [_msg(11)], {}, can_reply=False)
        self.assertIn('data-can-reply="0"', html)
        self.assertNotIn('id="rqComposer"', html)

    def test_images_open_the_preview_not_a_tab(self):
        img = SimpleNamespace(id=5, mime='image/png', original_name='shot.png', size=2048)
        doc = SimpleNamespace(id=6, mime='application/pdf', original_name='notes.pdf', size=4096)
        html = self.render(_req(), [_msg(11)], {11: [img, doc]})
        self.assertIn('class="rq-zoom"', html)
        self.assertIn('data-download="/requests/attachment/5?download=1"', html)
        self.assertNotIn('target="_blank"', html)
        self.assertIn('href="/requests/attachment/6"', html)
        self.assertEqual(html.count('rq-zoom'), 1)


if __name__ == '__main__':
    unittest.main()
