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

    def test_per_type_answer_controls_exist(self):
        for element_id, name in (
            ('answerPlacement', 'answer_placement'),
            ('answerTypeMc', 'answer_type_mc'),
            ('answerTypeCq', 'answer_type_cq'),
            ('answerFallback', 'answer_fallback'),
        ):
            self.assertRegex(self.template, rf'id="{element_id}"[^>]*name="{name}"|name="{name}"[^>]*id="{element_id}"')
        self.assertRegex(self.template, r'id="answerFallback"[^>]*checked')
        self.assertIn('id="answerTypeMcKey"', self.template)
        self.assertNotIn('name="answer_mode"', self.template)
        self.assertNotIn('id="compactMcAnswers"', self.template)

    def test_marks_spacing_mode_controls_exist(self):
        for name, el_id in (('mc_before_mode', 'mcBeforeMarks'), ('mc_after_mode', 'mcAfterMarks'),
                            ('cq_before_mode', 'cqBeforeMarks'), ('cq_after_mode', 'cqAfterMarks')):
            self.assertRegex(self.template, rf'name="{name}" id="{el_id}" value="marks"')
        self.assertIn('id="marksRulesModal"', self.template)
        self.assertIn("restoreSpacingMode('cqAfter', opts.cq_after_mode)", self.template)

    def test_marks_rules_per_paper_override(self):
        self.assertIn('id="marksRulesApply"', self.template)
        self.assertIn("url_for('generator.check_marks_rules')", self.template)
        self.assertIn("formData.set('marks_rules_override'", self.template)
        self.assertIn('savedGenerationOptions.marks_rules_override', self.template)
        self.assertNotIn('View marks rules', self.template)
        self.assertIn('marks-after-per', self.template)
        self.assertIn('<option value="question">', self.template)

    def test_answer_line_style_controls_exist(self):
        for name, el_id in (('mc_after_style', 'mcAfterStyle'), ('cq_after_style', 'cqAfterStyle')):
            self.assertIn(f'name="{name}" id="{el_id}"', self.template)
            self.assertIn(f"{name}: document.getElementById('{el_id}')", self.template)
        self.assertIn('<option value="ruled">{{ t(\'Answer lines\') }}</option>', self.template)

    def test_hierarchy_mode_control_exists(self):
        self.assertIn('id="hierarchyMode"', self.template)
        self.assertIn('name="hierarchy_mode"', self.template)
        self.assertIn('value="selected"', self.template)
        self.assertIn('value="whole"', self.template)


if __name__ == '__main__':
    unittest.main()
