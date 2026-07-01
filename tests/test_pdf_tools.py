"""Unit tests for shared PDF-tool page-splitting descriptors."""
import unittest

from app import pdf_tools


def _signature(frag):
    box = frag['ops'][0]['box'] if frag['ops'] else []
    side = 'L' if box == pdf_tools.LEFT_HALF else 'R'
    return frag['page'], side


class Mode1SplitTests(unittest.TestCase):
    def test_legacy_four_page_student_order_is_unchanged(self):
        frags = pdf_tools.split_descriptors(2, 'mode1')
        self.assertEqual(
            [_signature(f) for f in frags],
            [(0, 'R'), (1, 'L'), (1, 'R'), (0, 'L')],
        )

    def test_three_page_student_drops_fourth_padding_half(self):
        frags = pdf_tools.split_descriptors(4, 'mode1', 3)
        self.assertEqual(
            [_signature(f) for f in frags],
            [(0, 'R'), (1, 'L'), (1, 'R'),
             (2, 'R'), (3, 'L'), (3, 'R')],
        )

    def test_seven_page_booklet_drops_eighth_padding_half(self):
        frags = pdf_tools.split_descriptors(4, 'mode1', 7)
        self.assertEqual(
            [_signature(f) for f in frags],
            [(0, 'R'), (1, 'L'), (2, 'R'), (3, 'L'),
             (3, 'R'), (2, 'L'), (1, 'R')],
        )


if __name__ == '__main__':
    unittest.main()
