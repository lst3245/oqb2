"""Pure tests for the effective-level math (app/authz.compute_levels)."""
import unittest

from app.authz import LEVEL_RANK, compute_levels, highest_grant, rank

V, U, A = LEVEL_RANK['viewer'], LEVEL_RANK['user'], LEVEL_RANK['admin']
ALL = ('MATC', 'MAT1', 'ICT', 'ECON')
PRIMARY = {'https://sms.school': False}
BOTH = {'https://sms.school': False, 'https://other.school': True}


class RankTests(unittest.TestCase):
    def test_unknown_and_empty_are_none(self):
        self.assertEqual(rank(None), 0)
        self.assertEqual(rank(''), 0)
        self.assertEqual(rank('owner'), 0)

    def test_order(self):
        self.assertLess(rank('viewer'), rank('user'))
        self.assertLess(rank('user'), rank('admin'))


class ComputeLevelsTests(unittest.TestCase):
    def test_local_only(self):
        self.assertEqual(compute_levels(local={'MATC': 'admin', 'ICT': 'viewer'}),
                         {'MATC': A, 'ICT': V})

    def test_none_and_unknown_grants_are_dropped(self):
        self.assertEqual(compute_levels(local={'MATC': 'none', 'ICT': 'bogus'}), {})

    def test_default_level_covers_every_subject(self):
        out = compute_levels(local={}, sms_defaults=[('https://sms.school', 'viewer')],
                             all_subjects=ALL, issuer_caps=PRIMARY)
        self.assertEqual(out, {s: V for s in ALL})

    def test_highest_source_wins(self):
        out = compute_levels(
            local={'MATC': 'viewer'},
            sms_defaults=[('https://sms.school', 'viewer')],
            sms_subjects=[('https://sms.school', 'uuid-math', 'admin')],
            mapping={'uuid-math': ['MATC', 'MAT1']},
            all_subjects=ALL, issuer_caps=PRIMARY)
        self.assertEqual(out['MATC'], A)
        self.assertEqual(out['MAT1'], A)
        self.assertEqual(out['ICT'], V)

    def test_local_grant_beats_lower_sms_level(self):
        out = compute_levels(
            local={'ICT': 'admin'},
            sms_subjects=[('https://sms.school', 'uuid-ict', 'viewer')],
            mapping={'uuid-ict': ['ICT']}, issuer_caps=PRIMARY)
        self.assertEqual(out, {'ICT': A})

    def test_unmapped_sms_subject_grants_nothing(self):
        out = compute_levels(local={}, sms_subjects=[('https://sms.school', 'uuid-x', 'admin')],
                             mapping={}, issuer_caps=PRIMARY)
        self.assertEqual(out, {})

    def test_capped_issuer_is_clamped(self):
        out = compute_levels(
            local={},
            sms_defaults=[('https://other.school', 'admin')],
            sms_subjects=[('https://other.school', 'uuid-math', 'admin')],
            mapping={'uuid-math': ['MATC']},
            all_subjects=ALL, issuer_caps=BOTH)
        self.assertEqual(out['MATC'], U)
        self.assertEqual(out['ICT'], V)

    def test_rows_from_untrusted_issuers_are_ignored(self):
        out = compute_levels(
            local={},
            sms_defaults=[('https://gone.school', 'admin')],
            sms_subjects=[('https://gone.school', 'uuid-math', 'admin')],
            mapping={'uuid-math': ['MATC']},
            all_subjects=ALL, issuer_caps=PRIMARY)
        self.assertEqual(out, {})

    def test_no_issuer_caps_means_sms_ignored(self):
        out = compute_levels(local={'ECON': 'user'},
                             sms_defaults=[('https://sms.school', 'admin')],
                             all_subjects=ALL)
        self.assertEqual(out, {'ECON': U})

    def test_group_raises_a_subject_with_no_personal_grant(self):
        out = compute_levels(local={}, groups={'ICT': 'user'})
        self.assertEqual(out, {'ICT': U})

    def test_lower_group_does_not_beat_personal_or_sms(self):
        out = compute_levels(
            local={'ICT': 'admin'},
            groups={'ICT': 'viewer', 'MATC': 'user'},
            sms_subjects=[('https://sms.school', 'uuid-ict', 'user')],
            mapping={'uuid-ict': ['ICT']}, issuer_caps=PRIMARY)
        self.assertEqual(out['ICT'], A)
        self.assertEqual(out['MATC'], U)

    def test_group_does_not_lower_sms(self):
        out = compute_levels(
            local={}, groups={'ICT': 'viewer'},
            sms_subjects=[('https://sms.school', 'uuid-ict', 'admin')],
            mapping={'uuid-ict': ['ICT']}, issuer_caps=PRIMARY)
        self.assertEqual(out, {'ICT': A})

    def test_unknown_group_role_is_ignored(self):
        out = compute_levels(local={'ICT': 'viewer'}, groups={'ICT': 'bogus', 'MATC': 'nope'})
        self.assertEqual(out, {'ICT': V})

    def test_omitting_groups_matches_personal_only(self):
        self.assertEqual(compute_levels(local={'ICT': 'user'}),
                         compute_levels(local={'ICT': 'user'}, groups=None))


class HighestGrantTests(unittest.TestCase):
    def test_two_groups_take_the_higher_role(self):
        self.assertEqual(
            highest_grant({'ICT': 'viewer', 'MATC': 'admin'}, {'ICT': 'user', 'ECON': 'viewer'}),
            {'ICT': 'user', 'MATC': 'admin', 'ECON': 'viewer'})

    def test_unknown_roles_are_dropped(self):
        self.assertEqual(highest_grant({'ICT': 'bogus'}, {'MATC': ''}), {})

    def test_empty_maps(self):
        self.assertEqual(highest_grant(), {})
        self.assertEqual(highest_grant({}, None), {})


if __name__ == '__main__':
    unittest.main()
