"""Classical CV question detection. No Flask app and no database.

Synthetic pages are solid bars on white: a question number is a narrow blob
in the left column, the stem is a wider bar to its right, separated by a gap.
"""
import os
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from app.pdf_cv_detect import (
    detect_cv_page,
    resolve_profile,
    score_boxes,
    suppress_indented_anchors,
)
from app.pdf_layout import skew_angle_degrees


W, H = 900, 1200
FRAME = [0.08, 0.06, 0.92, 0.94]


def _page():
    return np.full((H, W), 255, dtype=np.uint8)


def _bar(gray, y, x0, x1, thick=10, value=20):
    y1 = min(H, y + thick)
    x1 = min(W, x1)
    gray[y:y1, x0:x1] = value


def _digit(gray, y, x=105, w=16, h=26):
    _bar(gray, y, x, x + w, thick=h, value=15)


def _question(gray, y, lines=3, body_x=210, body_w=480):
    _digit(gray, y)
    for i in range(lines):
        _bar(gray, y + i * 28, body_x, body_x + body_w, thick=8)


def _ocr_by_y(bounds):
    """``bounds`` is a list of ``(y_max, qno)`` in increasing y."""
    def reader(anchor):
        y = float(anchor['y'])
        for limit, qno in bounds:
            if y < limit:
                return qno
        return bounds[-1][1]
    return reader


class ProfileTests(unittest.TestCase):
    def test_subject_map_and_solution_override(self):
        self.assertEqual(resolve_profile('MAT1', 'QUE'), 'cq')
        self.assertEqual(resolve_profile('m2', 'QUE'), 'cq')
        self.assertEqual(resolve_profile('MATC', 'QUE'), 'auto')
        self.assertEqual(resolve_profile('ZZZ', 'QUE'), 'auto')
        self.assertEqual(resolve_profile('MATC', 'SOL'), 'sol')
        self.assertEqual(resolve_profile(None, 'SOL'), 'sol')


class AnchorAndBoxTests(unittest.TestCase):
    def _three(self):
        gray = _page()
        # Centred section header — not a question number.
        _bar(gray, 100, 340, 560, thick=12)
        _question(gray, 180)
        _question(gray, 480)
        _question(gray, 800)
        # Indented part markers must not become extra questions.
        _digit(gray, 250, x=230, w=12, h=16)
        _digit(gray, 290, x=230, w=12, h=16)
        return gray

    def test_three_anchors_ignore_header_and_parts(self):
        out = detect_cv_page(
            self._three(), frame=FRAME, use_ocr=True,
            ocr_reader=_ocr_by_y([(0.30, 1), (0.55, 2), (1.0, 3)]))
        qnos = [b['qno'] for b in out['boxes'] if not b.get('continues_prev')]
        self.assertEqual(qnos, [1, 2, 3])
        self.assertFalse(out['low'])
        self.assertGreaterEqual(out['confidence'], 0.7)
        self.assertNotIn('qno_gap', out['reasons'])
        # Header y=100/1200 is above the first box.
        self.assertGreater(out['boxes'][0]['box'][1], 90 / H)
        # Frame inset, not the indented stem (body starts at 210/900).
        for b in out['boxes']:
            self.assertLess(b['box'][0], 0.16)
            self.assertGreater(b['box'][0], 0.07)
            self.assertGreater(b['box'][2], 0.80)

    def test_assumed_numbers_are_low_confidence(self):
        out = detect_cv_page(self._three(), frame=FRAME, use_ocr=False)
        self.assertEqual([b['qno'] for b in out['boxes']], [1, 2, 3])
        self.assertTrue(out['low'])
        self.assertIn('qno_assumed', out['reasons'])
        self.assertLess(out['confidence'], 0.55)

    def test_ocr_gap_is_low_confidence(self):
        out = detect_cv_page(
            self._three(), frame=FRAME, use_ocr=True,
            ocr_reader=_ocr_by_y([(0.30, 1), (0.55, 2), (1.0, 4)]))
        self.assertEqual([b['qno'] for b in out['boxes']], [1, 2, 4])
        self.assertTrue(out['low'])
        self.assertIn('qno_gap', out['reasons'])

    def test_part_indent_is_not_a_question(self):
        gray = _page()
        _question(gray, 200, lines=1)
        _digit(gray, 250, x=240, w=14, h=18)
        _bar(gray, 250, 280, 600, thick=8)
        out = detect_cv_page(
            gray, frame=FRAME, use_ocr=True,
            ocr_reader=lambda _a: 1)
        self.assertEqual(len(out['boxes']), 1)
        self.assertEqual(out['boxes'][0]['qno'], 1)

    def test_leading_body_is_a_continuation(self):
        gray = _page()
        # Stem ink above the first number, number column empty, left-aligned
        # with the body rather than centred.
        _bar(gray, 100, 210, 700, thick=8)
        _bar(gray, 130, 210, 680, thick=8)
        _question(gray, 220)
        out = detect_cv_page(
            gray, frame=FRAME, use_ocr=True, ocr_reader=lambda _a: 5)
        self.assertGreaterEqual(len(out['boxes']), 2)
        self.assertTrue(out['boxes'][0]['continues_prev'])
        self.assertIsNone(out['boxes'][0]['qno'])
        self.assertEqual(out['boxes'][1]['qno'], 5)

    def test_centred_header_is_not_a_continuation(self):
        gray = _page()
        _bar(gray, 100, 340, 560, thick=14)
        _question(gray, 220)
        out = detect_cv_page(
            gray, frame=FRAME, use_ocr=True, ocr_reader=lambda _a: 1)
        self.assertEqual(len(out['boxes']), 1)
        self.assertFalse(out['boxes'][0]['continues_prev'])

    def test_blank_page_is_low(self):
        out = detect_cv_page(_page(), frame=FRAME, use_ocr=False)
        self.assertEqual(out['boxes'], [])
        self.assertTrue(out['low'])
        self.assertIn('no_anchors', out['reasons'])


class PostRuleTests(unittest.TestCase):
    def test_mcq_bottom_stops_before_the_ruling(self):
        gray = _page()
        _digit(gray, 180)
        _bar(gray, 180, 210, 700, thick=8)          # stem
        for i, y in enumerate((240, 275, 310, 345)):
            _bar(gray, y, 240, 420, thick=10)       # options A–D
        _bar(gray, 400, 90, 820, thick=3, value=0)  # blank ruling
        _question(gray, 520)
        out = detect_cv_page(
            gray, frame=FRAME, profile='auto', use_ocr=True,
            ocr_reader=_ocr_by_y([(0.40, 1), (1.0, 2)]))
        self.assertEqual([b['qno'] for b in out['boxes']], [1, 2])
        bottom = out['boxes'][0]['box'][3] * H
        self.assertGreater(bottom, 345)
        self.assertLess(bottom, 400)

    def test_cq_keeps_a_sparse_marks_blob(self):
        gray = _page()
        _digit(gray, 180)
        _bar(gray, 180, 210, 740, thick=10)
        _bar(gray, 214, 210, 740, thick=10)
        # 8 px wide: under the full-width row threshold, on the right.
        _bar(gray, 250, 700, 708, thick=12, value=0)
        _question(gray, 420)
        out = detect_cv_page(
            gray, frame=FRAME, profile='cq', use_ocr=True,
            ocr_reader=_ocr_by_y([(0.30, 1), (1.0, 2)]))
        self.assertGreaterEqual(len(out['boxes']), 2)
        bottom = out['boxes'][0]['box'][3] * H
        self.assertGreater(bottom, 250)
        self.assertLess(bottom, 420)

    def test_centred_footer_is_cut_from_the_question(self):
        gray = _page()
        _question(gray, 180, lines=2)
        _bar(gray, 340, 340, 560, thick=12)  # centred "Section B"
        _question(gray, 480)
        out = detect_cv_page(
            gray, frame=FRAME, use_ocr=True,
            ocr_reader=_ocr_by_y([(0.35, 1), (1.0, 2)]))
        self.assertEqual(len(out['boxes']), 2)
        self.assertLess(out['boxes'][0]['box'][3] * H, 340)

    def test_mcq_profile_penalises_a_page_without_options(self):
        gray = _page()
        _question(gray, 200, lines=4)
        _question(gray, 500, lines=4)
        out = detect_cv_page(
            gray, frame=FRAME, profile='mcq', use_ocr=True,
            ocr_reader=_ocr_by_y([(0.35, 1), (1.0, 2)]))
        self.assertTrue(out['low'])
        self.assertIn('mcq_options_unclear', out['reasons'])

    def test_blank_ruling_band_is_not_a_box(self):
        gray = _page()
        # Only rulings — nothing digit-like with a stem.
        for y in (200, 280, 360, 440):
            _bar(gray, y, 100, 800, thick=2, value=0)
        out = detect_cv_page(gray, frame=FRAME, profile='sol', use_ocr=False)
        self.assertEqual(out['boxes'], [])
        self.assertTrue(out['low'])


class ScoreTests(unittest.TestCase):
    def test_overlap_lowers_confidence(self):
        boxes = [
            {'qno': 1, 'box': [0.1, 0.1, 0.9, 0.5], 'confidence': 0.9,
             'continues_prev': False, 'reasons': []},
            {'qno': 2, 'box': [0.1, 0.3, 0.9, 0.7], 'confidence': 0.9,
             'continues_prev': False, 'reasons': []},
        ]
        scored = score_boxes(boxes)
        self.assertTrue(scored['low'])
        self.assertIn('overlap', scored['reasons'])
        self.assertLess(scored['confidence'], 0.55)

    def test_clean_sequence_stays_high(self):
        boxes = [
            {'qno': i, 'box': [0.1, 0.1 + i * 0.2, 0.9, 0.25 + i * 0.2],
             'confidence': 0.9, 'continues_prev': False, 'reasons': []}
            for i in (1, 2, 3)
        ]
        scored = score_boxes(boxes)
        self.assertFalse(scored['low'])
        self.assertGreaterEqual(scored['confidence'], 0.9)

    def test_empty_ink_reason_is_low(self):
        boxes = [{
            'qno': 1, 'box': [0.1, 0.1, 0.9, 0.3], 'confidence': 0.15,
            'continues_prev': False, 'reasons': ['empty_ink'],
        }]
        scored = score_boxes(boxes)
        self.assertTrue(scored['low'])
        self.assertIn('empty_ink', scored['reasons'])

    def test_suppress_indented_part_anchor(self):
        anchors = [
            {'y': 0.20, 'x0': 0.10},
            {'y': 0.24, 'x0': 0.16},
            {'y': 0.55, 'x0': 0.10},
        ]
        out = suppress_indented_anchors(anchors)
        self.assertEqual([a['y'] for a in out], [0.20, 0.55])


class DeskewTests(unittest.TestCase):
    def test_horizontal_lines_need_no_rotation(self):
        im = Image.new('L', (400, 500), 255)
        px = im.load()
        for y in range(40, 480, 30):
            for x in range(20, 380):
                px[x, y] = 0
        self.assertEqual(skew_angle_degrees(im), 0.0)

    def test_tilted_lines_report_a_correcting_angle(self):
        im = Image.new('RGB', (480, 600), (255, 255, 255))
        px = im.load()
        for y in range(30, 570, 24):
            for x in range(15, 465):
                px[x, y] = (0, 0, 0)
        tilted = im.rotate(-2.0, resample=Image.BILINEAR, expand=False,
                           fillcolor=(255, 255, 255))
        angle = skew_angle_degrees(tilted)
        self.assertGreater(abs(angle), 0.8)
        self.assertLess(abs(angle), 4.0)


class LlmBoundaryTests(unittest.TestCase):
    def test_cv_module_does_not_reference_the_llm_client(self):
        path = os.path.join(os.path.dirname(__file__), '..', 'app', 'pdf_cv_detect.py')
        with open(path, encoding='utf-8') as f:
            src = f.read()
        self.assertNotIn('llm_client', src)

    def test_detect_page_cv_never_calls_the_model(self):
        from flask import Flask
        from app.pdf_import import DETECT_METHODS, detect_page

        self.assertEqual(DETECT_METHODS, ('llm', 'cv'))
        gray = _page()
        _question(gray, 200)
        fd, path = tempfile.mkstemp(suffix='.png')
        os.close(fd)
        try:
            Image.fromarray(gray, mode='L').save(path)
            app = Flask(__name__)
            app.config['PDF_IMPORT_ASSIST_PAD_PCT'] = 0.6
            app.config['PDF_IMPORT_REFINE_GROW_PCT'] = 2.0
            app.config['PDF_IMPORT_CV_LOW_CONFIDENCE'] = 0.55
            app.config['PDF_IMPORT_FRAME_INSET_PCT'] = 0.5
            with app.app_context():
                with patch('app.llm_client.chat', side_effect=AssertionError('llm')), \
                     patch('app.llm_client.prepare_image', side_effect=AssertionError('prep')):
                    boxes, raw = detect_page(
                        None, path, 'QUE', 800, method='cv', mode='exam',
                        frame=FRAME, subject_id='MATC')
            self.assertTrue(boxes)
            self.assertIn('cv profile=', raw)
            with app.app_context():
                with patch('app.llm_client.chat', side_effect=AssertionError('llm')):
                    with self.assertRaises(RuntimeError):
                        detect_page(None, path, 'QUE', 800, method='cv', mode='generic')
        finally:
            os.remove(path)

    def test_detect_page_llm_still_calls_the_model(self):
        from flask import Flask
        from app.pdf_import import detect_page

        app = Flask(__name__)
        app.config['PDF_IMPORT_LLM_TIMEOUT_SECONDS'] = 0
        cfg = type('C', (), {'id': 1, 'model_name': 'mock', 'timeout_seconds': 30})()
        parsed = [{
            'qno': 3, 'label': '3', 'box': [0.1, 0.2, 0.8, 0.4],
            'continues_prev': False, 'continues_next': False,
        }]
        with app.app_context():
            with patch('app.llm_client.prepare_image', return_value=('abc', 'image/png')), \
                 patch('app.llm_client.sent_image_size', return_value=(20, 20)), \
                 patch('app.llm_client.box_coord_order', return_value='xyxy'), \
                 patch('app.llm_client.chat', return_value=('RAW', {})) as chat, \
                 patch('app.ai_prompts.build_pdf_box_system', return_value='sys'), \
                 patch('app.ai_prompts.build_pdf_box_user_text', return_value='user'), \
                 patch('app.ai_prompts.parse_question_boxes', return_value=parsed), \
                 patch('app.pdf_cv_detect.detect_cv_page',
                       side_effect=AssertionError('cv')):
                boxes, raw = detect_page(cfg, 'page.png', 'QUE', 800, method='llm')
        self.assertEqual(raw, 'RAW')
        self.assertEqual(boxes[0]['qno'], 3)
        chat.assert_called_once()
        # Unknown legacy names fall back to the plain LLM path.
        with app.app_context():
            with patch('app.llm_client.prepare_image', return_value=('abc', 'image/png')), \
                 patch('app.llm_client.sent_image_size', return_value=(20, 20)), \
                 patch('app.llm_client.box_coord_order', return_value='xyxy'), \
                 patch('app.llm_client.chat', return_value=('RAW', {})) as chat2, \
                 patch('app.ai_prompts.build_pdf_box_system', return_value='sys'), \
                 patch('app.ai_prompts.build_pdf_box_user_text', return_value='user'), \
                 patch('app.ai_prompts.parse_question_boxes', return_value=parsed):
                detect_page(cfg, 'page.png', 'QUE', 800, method='refine')
        chat2.assert_called_once()
