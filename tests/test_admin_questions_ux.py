"""Structure checks for the single-subject Question Management page (ADR-016)."""
import re
import unittest
from pathlib import Path

from jinja2 import Environment

ROOT = Path(__file__).parents[1] / 'templates'
PAGE = ROOT / 'admin_questions.html'
PARTIALS = ROOT / 'admin_questions'


def _read(path):
    return path.read_text(encoding='utf-8')


class AdminQuestionsTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = {PAGE: _read(PAGE)}
        for p in PARTIALS.rglob('*.html'):
            cls.files[p] = _read(p)
        cls.header = _read(PARTIALS / '_header.html')
        cls.ops = _read(PARTIALS / '_js_ops.html')
        cls.bulk = _read(PARTIALS / '_js_bulk.html')
        cls.state = _read(PARTIALS / '_js_state.html')

    def test_every_template_parses(self):
        env = Environment()
        for path, text in self.files.items():
            with self.subTest(path=path.name):
                env.parse(text)

    def test_subject_picker_has_no_all_option(self):
        select = re.search(r'<select[^>]*id="aqSubject".*?</select>', self.header, re.S).group(0)
        self.assertNotRegex(select, re.compile(r'all subjects', re.I))
        self.assertRegex(select, r'<option value=""[^>]*disabled')

    def test_page_includes_every_partial(self):
        page = self.files[PAGE]
        for name in ('_header', '_filters', '_tree', '_action_bar', '_add_modal', '_bulk_panel',
                     '_js_state', '_js_tree', '_js_ops', '_js_bulk', '_js_add'):
            self.assertIn(f"admin_questions/{name}.html", page)
        self.assertIn("partials/edit_question_modal_js.html", page)

    def test_json_and_form_builders_send_subject_id(self):
        # Every request builder (json / form) must carry subject_id.
        for m in re.finditer(r'request: \(ids, p\) => \(\{ url: AQ\.cfg\.urls\.(\w+), (json|form): (.*?)\}\)', self.ops, re.S):
            with self.subTest(op=m.group(1)):
                body = m.group(3)
                self.assertTrue('subject_id: AQ.subject' in body or 'opIdsForm(' in body, body)
        self.assertIn("fd.append('subject_id', AQ.subject)", self.ops)
        self.assertGreaterEqual(len(re.findall(r'request: \(ids, p\)', self.ops)), 5)

    def test_sse_runner_sends_subject_id(self):
        self.assertIn('question_ids: chunks[idx].join(\',\'), subject_id: BP.subject', self.bulk)

    def test_destructive_ops_require_typed_subject(self):
        for op in ('deleteQuestions', 'deleteAssets', 'combine'):
            block = re.search(rf'\n    {op}: \{{(.*?)\n    \}},', self.ops, re.S).group(1)
            with self.subTest(op=op):
                self.assertIn("danger: () => 'typed'", block)
        self.assertIn("o.action === 'move' || o.overwrite", self.ops)
        self.assertIn("danger: p => p.overwrite ? 'typed' : null", self.ops)

    def test_state_uses_new_storage_key(self):
        self.assertIn("'oqb_adminQuestionsState'", self.state)
        self.assertIn("localStorage.removeItem('oqb_adminQuestionsFilters')", self.state)

    def test_list_calls_send_subject(self):
        self.assertIn('const p = { subject: AQ.subject', self.state)


if __name__ == '__main__':
    unittest.main()
