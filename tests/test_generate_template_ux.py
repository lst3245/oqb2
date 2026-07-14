"""Regression checks for the generation-options page structure."""
import re
import unittest
from pathlib import Path

from jinja2 import Environment


TEMPLATE_PATH = Path(__file__).parents[1] / 'templates' / 'generate.html'


class GenerationOptionsTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = TEMPLATE_PATH.read_text(encoding='utf-8')

    def test_template_has_valid_jinja_syntax(self):
        Environment().parse(self.template)

    def test_sequential_numbering_is_default_and_precedes_compact_keys(self):
        sequential = re.search(
            r'id="showSeqNo"[^>]*name="show_seq_no"[^>]*checked',
            self.template,
        )
        self.assertIsNotNone(sequential)
        self.assertLess(
            self.template.index('id="showSeqNo"'),
            self.template.index('id="compactMcAnswerKeyPanel"'),
        )

    def test_compact_numbering_is_hidden_instead_of_disabled(self):
        self.assertIn('id="mcKeyNumberingOptions"', self.template)
        self.assertIn('id="mcKeyNumberingHint"', self.template)
        self.assertNotIn(
            "document.getElementById('mcKeyIncludeSeq').disabled",
            self.template,
        )
        self.assertNotIn(
            "document.getElementById('mcKeyRangeTitle').disabled",
            self.template,
        )

    def test_advanced_sections_and_summaries_exist(self):
        for element_id in (
            'assetOptionsCollapse',
            'assetOptionsSummary',
            'structureOptionsCollapse',
            'structureOptionsSummary',
            'spacingOptionsCollapse',
            'spacingOptionsSummary',
        ):
            self.assertIn(f'id="{element_id}"', self.template)
        self.assertIn('syncAdvancedOptionSummaries({openCustomized: true})',
                      self.template)

    def test_answer_mode_changes_do_not_overwrite_qid_choice(self):
        self.assertNotIn('showQidAnswerCheckbox.checked', self.template)
        self.assertIn('id="applySpacingToAnsDiv"', self.template)


if __name__ == '__main__':
    unittest.main()
