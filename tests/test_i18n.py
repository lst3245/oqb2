"""Pure tests for app.i18n (no create_app, no DB). Spec: docs/modules/i18n.md."""
import json
import os
import re
import unittest
from types import SimpleNamespace as NS

from flask import Flask

from app import i18n
from app.ai_prompts import _taxonomy_block, TAXONOMY_ALIAS_NOTE

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# t('...') / _t('...') / OQB.t('...') with ONE string literal as the first
# argument (followed by `)` or `,`). Template literals and concatenations are
# not scanned — keep catalogue strings as single plain literals.
_CALL = re.compile(
    r"""(?:(?<![\w.])_?t|OQB\.t)\(\s*"""
    r"""(?:'((?:[^'\\\n]|\\.)*)'|"((?:[^"\\\n]|\\.)*)")\s*[,)]""")
_SCAN_DIRS = ('templates', 'app', os.path.join('static', 'js'))
_SCAN_EXT = ('.html', '.py', '.js')
_PLACEHOLDER = re.compile(r'\{(\w+)\}')


def _unescape(s):
    return re.sub(r'\\(.)', lambda m: {'n': '\n', 't': '\t'}.get(m.group(1), m.group(1)), s)


def _scan_literals():
    found = {}
    for d in _SCAN_DIRS:
        for dirpath, _, files in os.walk(os.path.join(ROOT, d)):
            for f in files:
                if not f.endswith(_SCAN_EXT):
                    continue
                path = os.path.join(dirpath, f)
                with open(path, encoding='utf-8') as fh:
                    src = fh.read()
                for m in _CALL.finditer(src):
                    text = _unescape(m.group(1) if m.group(1) is not None else m.group(2))
                    found.setdefault(text, os.path.relpath(path, ROOT))
    return found


class BilingualNameTests(unittest.TestCase):
    def test_modes(self):
        node = NS(name='Planet Earth', name_zh='地球')
        self.assertEqual(i18n.display_name(node, 'en'), 'Planet Earth')
        self.assertEqual(i18n.display_name(node, 'zh'), '地球')
        self.assertEqual(i18n.display_name(node, 'both'), 'Planet Earth 地球')

    def test_zh_falls_back_to_english(self):
        for zh in (None, '', '   '):
            node = NS(name='Rocks', name_zh=zh)
            self.assertEqual(i18n.display_name(node, 'zh'), 'Rocks')
            self.assertEqual(i18n.display_name(node, 'both'), 'Rocks')

    def test_identical_names_not_doubled(self):
        self.assertEqual(i18n.bilingual('DSE', 'DSE', 'both'), 'DSE')

    def test_dicts_none_and_missing_attribute(self):
        self.assertEqual(i18n.display_name({'name': 'Acids', 'name_zh': '酸'}, 'zh'), '酸')
        self.assertIsNone(i18n.display_name(None, 'zh'))
        self.assertEqual(i18n.display_name(NS(name='Old row'), 'zh'), 'Old row')

    def test_default_mode_outside_request_is_english(self):
        self.assertEqual(i18n.display_name(NS(name='A', name_zh='甲')), 'A')

    def test_doc_name_defaults_to_english(self):
        node = NS(name='A', name_zh='甲')
        self.assertEqual(i18n.doc_name(node, None), 'A')
        self.assertEqual(i18n.doc_name(node, 'zh'), '甲')


class NameMatchingTests(unittest.TestCase):
    def test_normalize_strips_alias_suffix_and_whitespace(self):
        self.assertEqual(i18n.normalize_name('  Planet   Earth [中: 地球] '), 'planet earth')
        self.assertEqual(i18n.normalize_name('Planet Earth [中：地球]'), 'planet earth')
        self.assertEqual(i18n.normalize_name(None), '')

    def test_variants(self):
        self.assertEqual(i18n.name_variants('Planet Earth', '地球'),
                         ['planet earth', '地球', 'planet earth 地球'])
        self.assertEqual(i18n.name_variants('Rocks', None), ['rocks'])

    def test_index_matches_every_form(self):
        a = NS(id=1, name='Planet Earth', name_zh='地球')
        b = NS(id=2, name='Rocks', name_zh=None)
        idx = i18n.build_name_index([a, b])
        for raw in ('Planet Earth', 'planet earth', '地球', 'Planet Earth 地球',
                    'Planet Earth [中: 地球]'):
            self.assertIs(i18n.lookup_name(idx, raw), a, raw)
        self.assertIs(i18n.lookup_name(idx, 'ROCKS'), b)
        self.assertIsNone(i18n.lookup_name(idx, 'Metals'))
        self.assertIsNone(i18n.lookup_name(idx, ''))

    def test_english_wins_over_a_chinese_collision(self):
        english = NS(id=1, name='Acid', name_zh=None)
        other = NS(id=2, name='Base', name_zh='Acid')
        idx = i18n.build_name_index([other, english])
        self.assertIs(i18n.lookup_name(idx, 'acid'), english)

    def test_later_english_duplicate_wins(self):
        first = NS(id=1, name='Dup', name_zh=None)
        second = NS(id=2, name='Dup', name_zh=None)
        self.assertIs(i18n.lookup_name(i18n.build_name_index([first, second]), 'dup'), second)


class DocLanguageTests(unittest.TestCase):
    def test_explicit_choice_wins(self):
        for lang in ('en', 'zh', 'both'):
            self.assertEqual(i18n.doc_lang(lang, ['CH', 'EN']), lang)

    def test_auto_follows_top_version(self):
        expected = {'EN': 'en', 'ENO': 'en', 'CH': 'zh', 'CHO': 'zh', 'BI': 'both'}
        for top, lang in expected.items():
            self.assertEqual(i18n.doc_lang('auto', [top, 'EN']), lang, top)

    def test_auto_defaults(self):
        self.assertEqual(i18n.doc_lang('auto', None), 'en')
        self.assertEqual(i18n.doc_lang('auto', []), 'en')
        self.assertEqual(i18n.doc_lang('bogus', ['CH']), 'zh')
        self.assertEqual(i18n.doc_lang('auto', ['xx']), 'en')

    def test_labels(self):
        self.assertEqual(i18n.doc_label('answers', 'en'), 'ANSWERS')
        self.assertEqual(i18n.doc_label('answers', 'zh'), '答案')
        self.assertEqual(i18n.doc_label('solutions', 'both'), 'SOLUTIONS 題解')
        for key in i18n.DOC_LABELS:
            for lang in ('en', 'zh', 'both'):
                self.assertTrue(i18n.doc_label(key, lang))


class TranslateTests(unittest.TestCase):
    CAT = {'Logout ({username})': '登出（{username}）', 'Dashboard': '主頁'}

    def test_english_passthrough_with_params(self):
        self.assertEqual(i18n.translate('Logout ({username})', 'en', self.CAT, username='amy'),
                         'Logout (amy)')

    def test_chinese_with_params(self):
        self.assertEqual(i18n.translate('Logout ({username})', 'zh', self.CAT, username='amy'),
                         '登出（amy）')

    def test_missing_key_falls_back(self):
        self.assertEqual(i18n.translate('Not in catalogue', 'zh', self.CAT), 'Not in catalogue')

    def test_bad_params_leave_text(self):
        self.assertEqual(i18n.translate('Hello {name}', 'en', self.CAT, other=1), 'Hello {name}')
        self.assertEqual(i18n.translate(None, 'zh', self.CAT), '')

    def test_request_cookies(self):
        app = Flask(__name__)
        with app.test_request_context(headers={'Cookie': 'oqb_lang=zh; oqb_tax_lang=both'}):
            self.assertEqual(i18n.ui_lang(), 'zh')
            self.assertEqual(i18n.tax_lang(), 'both')
            self.assertEqual(i18n.t('Dashboard'), i18n.load_catalogue().get('Dashboard', 'Dashboard'))
            self.assertEqual(i18n.display_name(NS(name='A', name_zh='甲')), 'A 甲')
        with app.test_request_context(headers={'Cookie': 'oqb_lang=fr; oqb_tax_lang=xx'}):
            self.assertEqual(i18n.ui_lang(), 'en')
            self.assertEqual(i18n.tax_lang(), 'en')
            self.assertEqual(i18n.t('Dashboard'), 'Dashboard')

    def test_catalogue_route(self):
        app = Flask(__name__)
        app.register_blueprint(i18n.i18n_bp)
        client = app.test_client()
        resp = client.get('/i18n/zh.js')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertTrue(body.startswith('window.OQB_I18N = '))
        payload = json.loads(body[len('window.OQB_I18N = '):].rstrip().rstrip(';'))
        self.assertNotIn('_comment', payload)
        self.assertEqual(client.get('/i18n/en.js').status_code, 404)


class CatalogueTests(unittest.TestCase):
    def setUp(self):
        with open(i18n.CATALOGUE_PATH, encoding='utf-8') as fh:
            self.raw = json.load(fh)
        self.cat = {k: v for k, v in self.raw.items() if not k.startswith('_')}

    def test_values_are_non_empty_strings(self):
        for k, v in self.cat.items():
            self.assertIsInstance(v, str, k)
            self.assertTrue(v.strip(), k)

    def test_placeholders_match(self):
        for k, v in self.cat.items():
            self.assertEqual(set(_PLACEHOLDER.findall(k)), set(_PLACEHOLDER.findall(v)), k)

    def test_every_literal_is_translated(self):
        missing = {text: where for text, where in _scan_literals().items()
                   if text not in self.cat}
        self.assertEqual(missing, {}, 'add these to app/translations/zh_Hant.json')

    def test_dynamic_keys_are_translated(self):
        """Strings the UI passes to t()/OQB.t() through a variable."""
        from app.ai_prompts import PROMPTS_REGISTRY
        from app.settings import REGISTRY
        from app.utils import GROUP_NONE_LABELS, VERSION_LABELS
        texts = set(VERSION_LABELS.values()) | set(GROUP_NONE_LABELS.values())
        for spec in REGISTRY.values():
            texts |= {spec['group'], spec['label'], spec['help']}
        for spec in PROMPTS_REGISTRY.values():
            texts |= {spec['group'], spec['label'], spec['description']}
        missing = sorted(s for s in texts if s and s not in self.cat)
        self.assertEqual(missing, [], 'add these to app/translations/zh_Hant.json')


class TagPromptTaxonomyTests(unittest.TestCase):
    @staticmethod
    def _topic(name, name_zh=None, subs=(), hidden=False, description=None):
        return NS(name=name, name_zh=name_zh, hidden=hidden, description=description,
                  subtopics=list(subs))

    def test_alias_listed_and_header_note_only_when_present(self):
        sub = NS(name='Rocks', name_zh='岩石', hidden=False, description='igneous only')
        topics = [self._topic('Planet Earth', '地球', [sub])]
        out = _taxonomy_block('TOPICS (hint{alias}):', topics, 'subtopics')
        self.assertIn(TAXONOMY_ALIAS_NOTE, out.splitlines()[0])
        self.assertIn('- Planet Earth [中: 地球]', out)
        self.assertIn('    * Rocks [中: 岩石] \u2014 igneous only', out)

    def test_english_only_subject_keeps_old_prompt(self):
        topics = [self._topic('Algebra', None, [NS(name='Indices', hidden=False)])]
        out = _taxonomy_block('TOPICS (hint{alias}):', topics, 'subtopics')
        self.assertEqual(out, 'TOPICS (hint):\n- Algebra\n    * Indices')

    def test_hidden_nodes_and_empty(self):
        topics = [self._topic('Shown', '顯示'), self._topic('Gone', '隱藏', hidden=True)]
        out = _taxonomy_block('T{alias}:', topics, 'subtopics')
        self.assertNotIn('Gone', out)
        self.assertEqual(_taxonomy_block('T{alias}:', [], 'subtopics'), 'T:\n  (none defined)')


if __name__ == '__main__':
    unittest.main()
