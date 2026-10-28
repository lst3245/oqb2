"""Rendered question-list sheets: missing part QUE is a note, not a warning.

No app and no database — the partial is rendered with a stub Jinja env.
"""
import unittest
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape


ROOT = Path(__file__).parents[1]
NOTE = 'No question image for this part.'
WARNING = 'No preview available'


def _url_for(endpoint, **kwargs):
    if endpoint == 'dashboard.get_asset_preview':
        return f'/asset/{kwargs["asset_id"]}'
    return f'/{endpoint}'


def _t(text, **kwargs):
    return text.format(**kwargs) if kwargs else text


def _card(**over):
    card = {
        'id': 1,
        'qid': 'PHYS_DSE_2025_P2B_Q9',
        'source': 'DSE',
        'year': 2025,
        'paper': 'P2B',
        'section': 'P2B',
        'qno_end': None,
        'part': None,
        'part_label': None,
        'level': None,
        'q_type': 'CQ',
        'subject': 'PHYS',
        'major_topic': '21 Astronomy — Orbital Motion (Paper 2)',
        'major_subtopic': 'Escape Velocity',
        'minor_topics': [],
        'subtopics': [],
        'chapter': None,
        'subchapter': None,
        'description': None,
        'correct_percentage': None,
        'marks': None,
        'whole_marks': None,
        'preview_mode': None,
        'preview_version': None,
        'que_asset_ids': [],
        'preview_doc_asset_id': None,
        'preview_doc_filename': None,
        'preview_doc_file_path': None,
        'has_que': False,
        'has_ans': False,
        'has_sol': False,
        'has_whole': False,
        'whole_asset_ids': [],
        'answer': None,
        'comment': None,
        'has_answer_text': False,
        'has_comment': False,
        'breadcrumb': [],
        'stem_qid': None,
        'stem_preview': None,
        'child_count': 0,
        'depth': 1,
        'leaf_ids': [],
        'needs_prev_parts': False,
    }
    card.update(over)
    return card


def _render(groups, questions):
    env = Environment(
        loader=FileSystemLoader(str(ROOT / 'templates')),
        autoescape=select_autoescape(['html']),
    )
    env.globals['t'] = _t
    env.globals['url_for'] = _url_for
    env.globals['config'] = {'AI_TOOLS_ENABLED': False}
    template = env.get_template('partials/question_list.html')
    return template.render(
        questions=questions,
        question_groups=groups,
        all_question_ids=[q['id'] for q in questions],
        all_question_units=[],
        total=1,
        total_parts=len(questions),
        total_pages=1,
        page=1,
        admin_subjects=[],
    )


def _between(html, start_id, end_id=None):
    start = html.index(f'data-question-id="{start_id}"')
    end = html.index(f'data-question-id="{end_id}"', start + 1) if end_id else len(html)
    return html[start:end]


class HierarchyPreviewTests(unittest.TestCase):
    def setUp(self):
        stem = _card(
            id=1, has_que=True, preview_mode='image', que_asset_ids=[900],
            child_count=2, depth=0, leaf_ids=[4, 5],
        )
        substem = _card(
            id=3, qid='PHYS_DSE_2025_P2B_Q9c', part='c', depth=1,
            child_count=2, leaf_ids=[4, 5],
            breadcrumb=[{'kind': 'part', 'label': '(c)'}],
        )
        bare = _card(
            id=4, qid='PHYS_DSE_2025_P2B_Q9ci', part='i', depth=2,
            marks=3, needs_prev_parts=True,
            breadcrumb=[{'kind': 'part', 'label': '(i)'}],
        )
        imaged = _card(
            id=5, qid='PHYS_DSE_2025_P2B_Q9cii', part='ii', depth=2,
            has_que=True, preview_mode='image', que_asset_ids=[501],
            breadcrumb=[{'kind': 'part', 'label': '(ii)'}],
        )
        self.html = _render(
            [{
                'stem': stem,
                'leaves': [bare, imaged],
                'rows': [
                    {'kind': 'substem', 'card': substem},
                    {'kind': 'leaf', 'card': bare},
                    {'kind': 'leaf', 'card': imaged},
                ],
            }],
            [bare, imaged],
        )

    def test_part_without_que_is_a_note_not_a_warning(self):
        row = _between(self.html, 4, 5)
        self.assertIn(NOTE, row)
        self.assertIn('qsheet-paper--empty', row)
        self.assertNotIn(WARNING, row)
        self.assertNotIn('alert-warning', row)
        self.assertNotIn('/asset/900', row)
        self.assertNotIn('/asset/501', row)

    def test_empty_substem_keeps_the_shared_text_note(self):
        row = _between(self.html, 3, 4)
        self.assertIn('No shared text for (c); the sub-parts stand alone.', row)
        self.assertIn('qsheet-paper--empty', row)
        self.assertNotIn(WARNING, row)
        self.assertNotIn('alert-warning', row)
        self.assertNotIn(NOTE, row)

    def test_stem_image_is_shown_once_and_part_que_still_renders(self):
        self.assertEqual(self.html.count('/asset/900'), 1)
        row = _between(self.html, 5)
        self.assertIn('/asset/501', row)
        self.assertIn('question-image', row)
        self.assertNotIn(NOTE, row)
        self.assertNotIn(WARNING, row)
        self.assertNotIn('qsheet-paper--empty', row)
        self.assertNotIn(WARNING, self.html)
        self.assertNotIn('alert-warning', self.html)

    def test_topic_badge_is_printed_once(self):
        row = _between(self.html, 4, 5)
        self.assertIn('21 Astronomy — Orbital Motion (Paper 2)', row)
        self.assertNotIn('21 21', row)


class StandalonePreviewTests(unittest.TestCase):
    def test_card_with_nothing_to_show_keeps_the_warning(self):
        card = _card(id=8, has_que=False, preview_mode=None, stem_preview=None)
        html = _render([{'stem': None, 'leaves': [card], 'rows': None}], [card])
        self.assertIn(WARNING, html)
        self.assertIn('alert-warning', html)
        self.assertNotIn(NOTE, html)

    def test_part_outside_its_group_uses_stem_preview_without_the_warning(self):
        card = _card(
            id=9, part='i', part_label='i', stem_qid='PHYS_DSE_2025_P2B_Q9',
            has_que=False, preview_mode=None,
            stem_preview=_card(
                id=1, qid='PHYS_DSE_2025_P2B_Q9',
                has_que=True, preview_mode='image', que_asset_ids=[900],
            ),
        )
        html = _render([{'stem': None, 'leaves': [card], 'rows': None}], [card])
        self.assertIn('/asset/900', html)
        self.assertIn('Shared background (PHYS_DSE_2025_P2B_Q9)', html)
        self.assertIn(NOTE, html)
        self.assertNotIn(WARNING, html)
        self.assertEqual(html.count('/asset/900'), 1)

    def test_part_outside_its_group_still_shows_its_own_que(self):
        card = _card(
            id=10, part='a', part_label='a', stem_qid='PHYS_DSE_2025_P2B_Q9',
            has_que=True, preview_mode='image', que_asset_ids=[501],
            stem_preview=_card(
                id=1, has_que=True, preview_mode='image', que_asset_ids=[900],
            ),
        )
        html = _render([{'stem': None, 'leaves': [card], 'rows': None}], [card])
        self.assertIn('/asset/900', html)
        self.assertIn('/asset/501', html)
        self.assertNotIn(NOTE, html)
        self.assertNotIn(WARNING, html)


if __name__ == '__main__':
    unittest.main()
