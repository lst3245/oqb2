"""Pure tests for printed page-frame detection, consolidation, and cropping.

Does not call create_app() or touch the live database.
"""
import os
import tempfile
import unittest

import numpy as np
from PIL import Image

from app.pdf_import import (
    FrameCropper,
    consolidate_frames,
    crop_page,
    frame_x_extent,
    snap_boxes_to_frames,
)
from app.pdf_layout import detect_page_frame


# 1000x1375 is the same aspect as the 800x1100 example in the request.
# At 800 px wide, default dilation (8 px) turns a 1–3 px vertical rule into
# 17–19 columns, which exceeds max_thick = int(W * 0.02) = 16, so
# detect_page_frame returns None. 1000 px is the smallest nearby size where a
# clean 3 px rectangle survives (max_thick = 20).
W, H = 1000, 1375
LEFT, RIGHT, TOP, BOTTOM = 75, 925, 100, 1275
THICK = 3
# Inner-edge convention: left = (run_end+1)/W, right = run_start/W (and the
# same for top/bottom). Undilated truth for the drawn 3 px rails:
TRUE_LEFT = (LEFT + THICK) / W          # 0.078
TRUE_RIGHT = RIGHT / W                  # 0.925
TRUE_TOP = (TOP + THICK) / H            # ~0.0749
TRUE_BOTTOM = BOTTOM / H                # ~0.9273
RAIL_ATOL = 0.02


def _blank(w=W, h=H):
    return np.full((h, w), 255, dtype=np.uint8)


def _draw_vrail(gray, x, y0, y1, thick=THICK, drift=0):
    span = max(1, y1 - y0)
    for y in range(y0, y1 + 1):
        dx = int(round(drift * (y - y0) / span)) if drift else 0
        gray[y, x + dx:x + dx + thick] = 0


def _draw_hrail(gray, y, x0, x1, thick=THICK):
    gray[y:y + thick, x0:x1 + thick] = 0


def _clutter(gray):
    """Horizontal answer lines plus a few text-like dark blobs."""
    for y in (250, 350, 450, 550, 650, 750, 850):
        gray[y, 188:876] = 40
    for x, y in ((120, 150), (250, 160), (400, 155), (220, 1120)):
        gray[y:y + 8, x:x + 20] = 30


def _draw_frame(gray, *, left=True, right=True, top=True, bottom=True,
                drift_left=0):
    if left:
        _draw_vrail(gray, LEFT, TOP, BOTTOM + THICK - 1, drift=drift_left)
    if right:
        _draw_vrail(gray, RIGHT, TOP, BOTTOM + THICK - 1)
    if top:
        _draw_hrail(gray, TOP, LEFT, RIGHT)
    if bottom:
        _draw_hrail(gray, BOTTOM, LEFT, RIGHT)


def _page(*, left=True, right=True, top=True, bottom=True, drift_left=0,
          clutter=True):
    gray = _blank()
    _draw_frame(gray, left=left, right=right, top=top, bottom=bottom,
                drift_left=drift_left)
    if clutter:
        _clutter(gray)
    return gray


class DetectPageFrameTests(unittest.TestCase):
    def test_clean_rectangle_finds_all_four_rails(self):
        frame = detect_page_frame(_page())
        self.assertIsNotNone(frame)
        self.assertAlmostEqual(frame['left'], TRUE_LEFT, delta=RAIL_ATOL)
        self.assertAlmostEqual(frame['right'], TRUE_RIGHT, delta=RAIL_ATOL)
        self.assertAlmostEqual(frame['top'], TRUE_TOP, delta=RAIL_ATOL)
        self.assertAlmostEqual(frame['bottom'], TRUE_BOTTOM, delta=RAIL_ATOL)

    def test_missing_right_rail_leaves_right_none(self):
        frame = detect_page_frame(_page(right=False))
        self.assertIsNotNone(frame)
        self.assertIsNone(frame['right'])
        self.assertAlmostEqual(frame['left'], TRUE_LEFT, delta=RAIL_ATOL)

    def test_answer_lines_and_text_without_frame_is_none(self):
        gray = _blank()
        _clutter(gray)
        self.assertIsNone(detect_page_frame(gray))

    def test_scanner_shadow_is_not_a_rail(self):
        gray = _blank()
        gray[:, W - 40:W] = 20
        self.assertIsNone(detect_page_frame(gray))

    def test_slightly_skewed_left_rail_still_found(self):
        frame = detect_page_frame(_page(drift_left=6))
        self.assertIsNotNone(frame)
        self.assertAlmostEqual(frame['left'], TRUE_LEFT, delta=RAIL_ATOL)


class ConsolidateFramesTests(unittest.TestCase):
    def test_empty_list(self):
        self.assertEqual(consolidate_frames([]), [])

    def test_below_min_detected_frac_is_all_none(self):
        raw = [None, None, {'left': 0.05, 'right': 0.90}, None, None]
        self.assertEqual(consolidate_frames(raw), [None] * 5)

    def test_odd_page_inherits_odd_parity_median(self):
        even = {'left': 0.05, 'right': 0.90}
        odd = {'left': 0.09, 'right': 0.94}
        raw = [even, odd, even, odd, even, None]
        out = consolidate_frames(raw)
        self.assertEqual(len(out), 6)
        for i in (0, 2, 4):
            self.assertEqual(out[i]['source'], 'detected')
            self.assertEqual(out[i]['box'][0], 0.05)
            self.assertEqual(out[i]['box'][2], 0.90)
        for i in (1, 3):
            self.assertEqual(out[i]['source'], 'detected')
            self.assertEqual(out[i]['box'][0], 0.09)
            self.assertEqual(out[i]['box'][2], 0.94)
        self.assertEqual(out[5]['source'], 'inferred')
        self.assertAlmostEqual(out[5]['box'][0], 0.09)
        self.assertAlmostEqual(out[5]['box'][2], 0.94)

    def test_missing_rail_filled_from_median_width(self):
        raw = [
            {'left': 0.05, 'right': 0.90},
            {'left': 0.05, 'right': None},
        ]
        out = consolidate_frames(raw)
        self.assertEqual(out[0]['source'], 'detected')
        self.assertEqual(out[1]['source'], 'inferred')
        self.assertAlmostEqual(out[1]['box'][0], 0.05)
        self.assertAlmostEqual(out[1]['box'][2], 0.90)

    def test_implausible_width_inferred_from_same_parity(self):
        raw = [
            {'left': 0.05, 'right': 0.90},
            {'left': 0.09, 'right': 0.94},
            {'left': 0.05, 'right': 0.90},
            {'left': 0.20, 'right': 0.40},
        ]
        out = consolidate_frames(raw)
        self.assertEqual(out[3]['source'], 'inferred')
        self.assertAlmostEqual(out[3]['box'][0], 0.09)
        self.assertAlmostEqual(out[3]['box'][2], 0.94)
        self.assertEqual(out[0]['source'], 'detected')
        self.assertEqual(out[1]['source'], 'detected')

    def test_missing_top_bottom_fall_back_to_full_page(self):
        raw = [{'left': 0.05, 'right': 0.90}, {'left': 0.05, 'right': 0.90}]
        out = consolidate_frames(raw)
        for entry in out:
            self.assertEqual(entry['box'][1], 0.0)
            self.assertEqual(entry['box'][3], 1.0)


class FrameXExtentTests(unittest.TestCase):
    def test_none_frame(self):
        self.assertIsNone(frame_x_extent(None, 0.005))
        self.assertIsNone(frame_x_extent({}, 0.005))

    def test_inset_applied(self):
        ext = frame_x_extent({'box': [0.05, 0, 0.9, 1]}, 0.005)
        self.assertEqual(ext, (0.055, 0.895))

    def test_too_narrow_is_none(self):
        self.assertIsNone(frame_x_extent({'box': [0.40, 0, 0.48, 1]}, 0.0))


class SnapBoxesToFramesTests(unittest.TestCase):
    def test_snaps_x_only_on_framed_pages(self):
        frames = [
            {'box': [0.05, 0.0, 0.90, 1.0], 'source': 'detected'},
            None,
        ]
        items = [
            {'page': 0, 'box': [0.10, 0.2, 0.80, 0.4]},
            {'page': 0, 'box': [0.055, 0.1, 0.895, 0.3]},
            {'page': 1, 'box': [0.10, 0.2, 0.80, 0.4]},
            {'page': 5, 'box': [0.10, 0.2, 0.80, 0.4]},
        ]
        changed = snap_boxes_to_frames(items, frames, 0.005)
        self.assertEqual(changed, 1)
        self.assertEqual(items[0]['box'], [0.055, 0.2, 0.895, 0.4])
        self.assertEqual(items[1]['box'], [0.055, 0.1, 0.895, 0.3])
        self.assertEqual(items[2]['box'], [0.10, 0.2, 0.80, 0.4])
        self.assertEqual(items[3]['box'], [0.10, 0.2, 0.80, 0.4])


class CropPageVerticalTrimTests(unittest.TestCase):
    def test_vertical_keeps_width_both_shrinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'page.png')
            im = Image.new('RGB', (400, 300), (255, 255, 255))
            for x in range(200, 220):
                for y in range(100, 120):
                    im.putpixel((x, y), (0, 0, 0))
            im.save(path)

            box = [0.1, 0.1, 0.9, 0.9]
            untrimmed_w = int(0.9 * 400) - int(0.1 * 400)
            self.assertEqual(untrimmed_w, 320)

            vert = crop_page(path, box, pad_frac=0, trim_white=True,
                             trim_axis='vertical')
            self.assertEqual(vert.size[0], untrimmed_w)
            # 20 px square + 8 px pad each side, clamped inside the crop.
            self.assertEqual(vert.size[1], 20 + 16)

            both = crop_page(path, box, pad_frac=0, trim_white=True,
                             trim_axis='both')
            self.assertEqual(both.size[0], 20 + 16)
            self.assertEqual(both.size[1], 20 + 16)


class FrameCropperTests(unittest.TestCase):
    def test_is_snapped_and_target_width(self):
        meta = {
            'que': {
                'pages': [{'index': 0, 'width': 1000, 'height': 1400}],
                'frames': [{'box': [0.05, 0, 0.90, 1], 'source': 'detected'}],
            }
        }
        cropper = FrameCropper(meta, inset_frac=0.005, pad_frac=0,
                               trim_white=True, whiteness=250,
                               normalise=False)
        self.assertTrue(cropper.is_snapped('que', 0, [0.055, 0.2, 0.895, 0.4]))
        self.assertFalse(cropper.is_snapped('que', 0, [0.10, 0.2, 0.895, 0.4]))
        self.assertEqual(
            cropper.target_w['que'],
            int(0.895 * 1000) - int(0.055 * 1000),
        )


if __name__ == '__main__':
    unittest.main()
