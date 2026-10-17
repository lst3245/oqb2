"""Static scan of templates/ and static/js/csrf.js (no app, no DB): every
POST form carries ``csrf_field()``, every full page loads csrf.js before
htmx and its page scripts, forms built in JS add the token, and the SSO
landing page loads nothing external."""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / 'templates'
CSRF_JS = "filename='js/csrf.js'"
# Standalone pages that never post with cookies: the SSO landing page posts
# the token by fetch to an exempt endpoint, the failed page has no form.
NO_CSRF_PAGES = {'sso/landing.html', 'sso/failed.html'}
FORM_OPEN = re.compile(r'<form\b[^>]*>', re.I)
POST = re.compile(r'\bmethod\s*=\s*["\']?post\b', re.I)


def _templates():
    for path in sorted(TEMPLATES.rglob('*.html')):
        yield path.relative_to(TEMPLATES).as_posix(), path.read_text(encoding='utf-8')


class FormTests(unittest.TestCase):
    def test_every_post_form_has_csrf_field(self):
        missing, count = [], 0
        for name, text in _templates():
            for m in FORM_OPEN.finditer(text):
                if not POST.search(m.group(0)):
                    continue
                count += 1
                end = text.find('</form>', m.end())
                body = text[m.end():end if end >= 0 else len(text)]
                if 'csrf_field()' not in body:
                    missing.append(f'{name}:{text.count(chr(10), 0, m.start()) + 1}')
        self.assertGreaterEqual(count, 25)
        self.assertEqual(missing, [], 'add {{ csrf_field() }} inside these forms')

    def test_forms_built_in_js_add_the_token(self):
        built = []
        for name, text in _templates():
            for m in re.finditer(r"createElement\(\s*['\"]form['\"]\s*\)", text):
                built.append(name)
                window = text[m.end():m.end() + 1500]
                self.assertIn('OQB.csrfInput()', window, name)
        self.assertIn('dashboard.html', built)
        self.assertIn('generate.html', built)

    def test_raw_xhr_upload_sets_the_header(self):
        text = (TEMPLATES / 'partials' / 'file_browser_js.html').read_text(encoding='utf-8')
        self.assertIn("xhr.setRequestHeader('X-CSRF-Token', OQB.csrfToken())", text)


class PageTests(unittest.TestCase):
    def full_pages(self):
        return [(n, t) for n, t in _templates() if re.search(r'<head\b', t, re.I)]

    def test_full_pages_load_csrf_js_first(self):
        pages = dict(self.full_pages())
        self.assertTrue({'base.html', 'viewer.html', 'markup.html'} <= set(pages))
        for name, text in pages.items():
            if name in NO_CSRF_PAGES:
                self.assertNotIn(CSRF_JS, text, name)
                continue
            with self.subTest(name):
                self.assertIn('<meta name="csrf-token" content="{{ csrf_token() }}">', text)
                at = text.index(CSRF_JS)
                self.assertLess(text.index("filename='js/i18n.js'"), at)
                scripts = [m.start() for m in re.finditer(r'<script\b', text)]
                others = [s for s in scripts if 'i18n' not in text[s:s + 200] and s < at
                          and CSRF_JS not in text[s:s + 200]]
                self.assertEqual(others, [], f'{name}: scripts before csrf.js')

    def test_base_loads_csrf_js_before_htmx(self):
        text = (TEMPLATES / 'base.html').read_text(encoding='utf-8')
        self.assertLess(text.index(CSRF_JS), text.index('htmx.org'))

    def test_logout_is_a_post_form(self):
        text = (TEMPLATES / 'base.html').read_text(encoding='utf-8')
        self.assertNotIn('href="{{ url_for(\'auth.logout\') }}"', text)
        self.assertRegex(text, r'<form method="post" action="\{\{ url_for\(\'auth\.logout\'\) \}\}"')

    def test_landing_page_loads_nothing_external(self):
        text = (TEMPLATES / 'sso' / 'landing.html').read_text(encoding='utf-8')
        refs = re.findall(r'\b(?:src|href|action)\s*=\s*["\']([^"\']*)', text, re.I)
        external = [r for r in refs if re.match(r'(?:[a-z][a-z0-9+.-]*:)?//', r, re.I)]
        self.assertEqual(external, [])
        self.assertNotIn('<link', text)


class ScriptTests(unittest.TestCase):
    def test_csrf_js_matches_the_server_names(self):
        from app import csrf
        js = (ROOT / 'static' / 'js' / 'csrf.js').read_text(encoding='utf-8')
        self.assertIn(f"var HEADER = '{csrf.HEADER}';", js)
        self.assertIn(f"var FIELD = '{csrf.FIELD}';", js)
        self.assertIn('meta[name="csrf-token"]', js)
        for hook in ('window.fetch =', 'xhr.open =', 'xhr.send =', "'htmx:configRequest'",
                     "addEventListener('submit'", 'HTMLFormElement.prototype.submit =',
                     'OQB.csrfToken =', 'OQB.csrfInput ='):
            self.assertIn(hook, js)


if __name__ == '__main__':
    unittest.main()
