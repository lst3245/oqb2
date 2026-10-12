"""Unit tests for per-type answer settings and the ANS/SOL fallback (no live DB)."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask

from app import generator


def _q(qid, q_type='CQ', answer='', content=(), parent=None, id=None):
    return SimpleNamespace(qid=qid, q_type=q_type, answer=answer,
                           content=set(content), parent=parent, id=id)


def _has_content(question, asset_type):
    return asset_type == 'QUE' or asset_type in question.content


_ZERO_SPACING = {
    'before_mode': 'lines', 'before_lines': 0,
    'after_mode': 'lines', 'after_lines': 0,
}
_SPACING = {'mc': dict(_ZERO_SPACING), 'cq': dict(_ZERO_SPACING)}
_KEY_OPTIONS = {
    'enabled': True, 'layout': 'table', 'columns': 5,
    'max_rows': None, 'include_seq': True, 'range_title': False,
}


class NormalizeAnswerConfigTests(unittest.TestCase):
    def test_legacy_modes_map_to_placement_and_both_types(self):
        cases = {
            'QUE_ONLY': ('none', 'ANS', 'ANS'),
            'QUE_ANS': ('inline', 'ANS', 'ANS'),
            'QUE_SOL': ('inline', 'SOL', 'SOL'),
            'QUE_THEN_ANS': ('end', 'ANS', 'ANS'),
            'QUE_THEN_SOL': ('end', 'SOL', 'SOL'),
        }
        for mode, (placement, mc, cq) in cases.items():
            config = generator._normalize_answer_config(mode)
            self.assertEqual((config['placement'], config['mc'], config['cq']),
                             (placement, mc, cq), mode)
            self.assertFalse(config['fallback'], mode)

    def test_legacy_compact_maps_mc_to_key(self):
        config = generator._normalize_answer_config(
            None, {'answer_mode': 'QUE_THEN_ANS', 'compact_mc_answers': True})
        self.assertEqual((config['mc'], config['cq']), ('KEY', 'ANS'))
        config = generator._normalize_answer_config(
            None, {'answer_mode': 'QUE_THEN_SOL', 'compact_mc_answers': 'on'})
        self.assertEqual(config['mc'], 'SOL')

    def test_new_form_keys(self):
        config = generator._normalize_answer_config(None, {
            'answer_placement': 'end', 'answer_type_mc': 'KEY',
            'answer_type_cq': 'SOL', 'answer_fallback': 'on',
        })
        self.assertEqual(config, {'placement': 'end', 'mc': 'KEY',
                                  'cq': 'SOL', 'fallback': True})
        # Unchecked checkbox is simply absent from the form.
        config = generator._normalize_answer_config(None, {
            'answer_placement': 'inline', 'answer_type_mc': 'none',
            'answer_type_cq': 'ANS',
        })
        self.assertEqual((config['mc'], config['fallback']), ('none', False))

    def test_invalid_values_are_clamped(self):
        config = generator._normalize_answer_config(None, {
            'answer_placement': 'inline', 'answer_type_mc': 'KEY',
            'answer_type_cq': 'KEY',
        })
        self.assertEqual((config['mc'], config['cq']), ('ANS', 'ANS'))
        config = generator._normalize_answer_config(None, {'answer_placement': 'bogus'})
        self.assertEqual(config['placement'], 'none')
        self.assertEqual(generator._normalize_answer_config(None, {})['placement'], 'none')

    def test_dict_passes_through(self):
        raw = {'placement': 'end', 'mc': 'SOL', 'cq': 'none', 'fallback': True}
        self.assertEqual(generator._normalize_answer_config(raw), raw)

    def test_mc_key_options_gate_on_key_type(self):
        on = generator._parse_mc_answer_key_options(
            {'mc_key_include_seq': 'on'},
            {'placement': 'end', 'mc': 'KEY', 'cq': 'SOL', 'fallback': True},
            True,
        )
        self.assertTrue(on['enabled'])
        self.assertTrue(on['include_seq'])
        off = generator._parse_mc_answer_key_options(
            {'mc_key_include_seq': 'on'},
            {'placement': 'end', 'mc': 'ANS', 'cq': 'SOL', 'fallback': True},
            True,
        )
        self.assertFalse(off['enabled'])
        self.assertFalse(off['include_seq'])


@patch.object(generator, '_question_has_content', side_effect=_has_content)
class PickAnswerSourceTests(unittest.TestCase):
    def _emitted(self):
        return {'ANS': set(), 'SOL': set()}

    def test_solution_missing_falls_back_to_answer(self, _mock):
        q = _q('Q1', content={'ANS'})
        self.assertEqual(generator._pick_answer_source(q, 'SOL', True, self._emitted()),
                         (q, 'ANS'))

    def test_answer_missing_falls_back_to_solution(self, _mock):
        q = _q('Q1', content={'SOL'})
        self.assertEqual(generator._pick_answer_source(q, 'ANS', True, self._emitted()),
                         (q, 'SOL'))

    def test_fallback_off_keeps_requested_type(self, _mock):
        q = _q('Q1', content={'ANS'})
        self.assertEqual(generator._pick_answer_source(q, 'SOL', False, self._emitted()),
                         (q, 'SOL'))

    def test_nothing_available_names_requested_type(self, _mock):
        q = _q('Q1')
        self.assertEqual(generator._pick_answer_source(q, 'SOL', True, self._emitted()),
                         (q, 'SOL'))

    def test_ancestor_with_requested_type_wins_over_leaf_fallback(self, _mock):
        stem = _q('Q3', content={'SOL'}, id=1)
        part = _q('Q3a', content={'ANS'}, parent=stem, id=2)
        emitted = self._emitted()
        self.assertEqual(generator._pick_answer_source(part, 'SOL', True, emitted),
                         (stem, 'SOL'))
        # Already printed for this stem run: covered, no fallback to ANS.
        sibling = _q('Q3b', content={'ANS'}, parent=stem, id=3)
        self.assertEqual(generator._pick_answer_source(sibling, 'SOL', True, emitted),
                         (sibling, 'SOL'))


class AnswerTextFallbackTests(unittest.TestCase):
    def test_answer_text_counts_as_answer_for_solution_request(self):
        no_assets = SimpleNamespace(query=SimpleNamespace(
            filter_by=lambda **_kw: SimpleNamespace(first=lambda: None)))
        q = _q('Q1', answer='B', id=7)
        with patch.object(generator, 'QuestionAsset', no_assets):
            self.assertEqual(
                generator._pick_answer_source(q, 'SOL', True, {'ANS': set(), 'SOL': set()}),
                (q, 'ANS'),
            )


class PerTypeDocumentTests(unittest.TestCase):
    def _render(self, questions, config, mc_answer_key_options=None):
        for i, q in enumerate(questions, start=100):
            q.id = q.id or i
        calls = []

        def fake_add(doc, question, asset_type, show_qid, *_args, **_kwargs):
            calls.append((question.qid, asset_type))
            doc.add_paragraph(f'{asset_type}:{question.qid}')

        app = Flask(__name__)
        app.config['SOURCE_PATH'] = '.'
        with app.app_context(), patch.object(
            generator, 'add_question_content_to_doc', side_effect=fake_add
        ), patch.object(generator, '_question_has_content', side_effect=_has_content):
            doc, _ = generator.create_word_document(
                questions, config, _SPACING,
                show_qid=True, show_qid_answer=True, show_seq_no=True,
                mc_answer_key_options=mc_answer_key_options,
            )
        return doc, calls

    def test_inline_uses_each_type(self):
        questions = [_q('Q1', 'MC', content={'ANS'}), _q('Q2', 'CQ', content={'SOL'})]
        _doc, calls = self._render(
            questions, {'placement': 'inline', 'mc': 'ANS', 'cq': 'SOL', 'fallback': True})
        self.assertEqual(calls, [('Q1', 'QUE'), ('Q1', 'ANS'), ('Q2', 'QUE'), ('Q2', 'SOL')])

    def test_end_key_for_mc_and_solution_for_cq(self):
        questions = [
            _q('Q1', 'MC', answer='A', content={'ANS'}),
            _q('Q2', 'CQ', content={'ANS'}),
            _q('Q3', 'MC', answer='', content={'SOL'}),
        ]
        doc, calls = self._render(
            questions, {'placement': 'end', 'mc': 'KEY', 'cq': 'SOL', 'fallback': True},
            mc_answer_key_options=_KEY_OPTIONS,
        )
        answers = [c for c in calls if c[1] != 'QUE']
        # Q2 has no SOL -> ANS; Q3 is not key-eligible -> ANS missing -> SOL.
        self.assertEqual(answers, [('Q2', 'ANS'), ('Q3', 'SOL')])
        self.assertEqual(len(doc.tables), 1)
        self.assertIn('ANSWERS AND SOLUTIONS', [p.text for p in doc.paragraphs])

    def test_end_skips_types_set_to_none(self):
        questions = [_q('Q1', 'MC', content={'ANS'}), _q('Q2', None, content={'SOL'})]
        doc, calls = self._render(
            questions, {'placement': 'end', 'mc': 'none', 'cq': 'SOL', 'fallback': False})
        self.assertEqual([c for c in calls if c[1] != 'QUE'], [('Q2', 'SOL')])
        self.assertIn('SOLUTIONS', [p.text for p in doc.paragraphs])

    def test_end_with_no_answer_types_has_no_section(self):
        questions = [_q('Q1', 'MC'), _q('Q2', 'CQ')]
        doc, calls = self._render(
            questions, {'placement': 'end', 'mc': 'none', 'cq': 'none', 'fallback': True})
        self.assertEqual(calls, [('Q1', 'QUE'), ('Q2', 'QUE')])
        texts = [p.text for p in doc.paragraphs]
        self.assertNotIn('ANSWERS', texts)
        self.assertNotIn('SOLUTIONS', texts)

    def test_legacy_string_still_renders(self):
        questions = [_q('Q1', 'CQ', content={'ANS'})]
        doc, calls = self._render(questions, 'QUE_THEN_SOL')
        # Legacy configs keep fallback off: the placeholder path names SOL.
        self.assertEqual(calls, [('Q1', 'QUE'), ('Q1', 'SOL')])
        self.assertIn('SOLUTIONS', [p.text for p in doc.paragraphs])


if __name__ == '__main__':
    unittest.main()
