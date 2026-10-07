"""Pure tests for the per-user file-browser last folder.

No database and no create_app(): path clamping and the JSON blob only.
"""
import os
import tempfile
import unittest
from types import SimpleNamespace

from app.files_service import (
    FileServiceError,
    dump_location_blob,
    longest_existing_dir,
    lookup_root,
    merge_location_blob,
    normalize_browser_rel_path,
    parse_location_blob,
)


def _root(rid):
    return SimpleNamespace(id=rid)


class NormalizePathTests(unittest.TestCase):
    def test_slashes_and_dot_segments(self):
        self.assertEqual(normalize_browser_rel_path(r'  \a\.\b/ '), 'a/b')
        self.assertEqual(normalize_browser_rel_path(''), '')
        self.assertEqual(normalize_browser_rel_path(None), '')

    def test_rejects_parent_and_drive(self):
        for raw in ('a/../b', '..', 'C:/Windows', 'foo/bar:', 'a/\x00b'):
            with self.assertRaises(FileServiceError):
                normalize_browser_rel_path(raw)


class LongestExistingDirTests(unittest.TestCase):
    def test_walks_up_to_an_existing_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, 'a', 'b'))
            with open(os.path.join(tmp, 'a', 'b', 'file.txt'), 'w', encoding='utf-8') as fh:
                fh.write('x')
            self.assertEqual(longest_existing_dir(tmp, 'a/b'), 'a/b')
            self.assertEqual(longest_existing_dir(tmp, 'a/b/missing'), 'a/b')
            self.assertEqual(longest_existing_dir(tmp, 'a/b/file.txt'), 'a/b')
            self.assertEqual(longest_existing_dir(tmp, 'nope'), '')
            self.assertEqual(longest_existing_dir(tmp, ''), '')
            # Corrupt stored values must not raise on read.
            self.assertEqual(longest_existing_dir(tmp, 'a/../../outside'), '')


class LocationBlobTests(unittest.TestCase):
    def test_parse_drops_junk_and_keeps_a_bad_path_as_root(self):
        self.assertEqual(parse_location_blob(None), {})
        self.assertEqual(parse_location_blob('not-json'), {})
        self.assertEqual(parse_location_blob('[]'), {})
        raw = '{"user": {"root": "shared:MATC", "path": "../x"}, "extra": 1}'
        self.assertEqual(
            parse_location_blob(raw),
            {'user': {'root': 'shared:MATC', 'path': ''}},
        )

    def test_merge_keeps_the_other_scope(self):
        raw = dump_location_blob({
            'user': {'root': 'user', 'path': 'docs'},
            'nope': {'root': 'x', 'path': 'y'},
        })
        merged = merge_location_blob(raw, 'admin', 'source', 'MATC')
        self.assertEqual(parse_location_blob(merged), {
            'user': {'root': 'user', 'path': 'docs'},
            'admin': {'root': 'source', 'path': 'MATC'},
        })
        # Blank requested scope is stored as user and replaces that slot only.
        again = merge_location_blob(merged, 'nope', 'shared:MATC', 'papers')
        parsed = parse_location_blob(again)
        self.assertEqual(parsed['user']['root'], 'shared:MATC')
        self.assertEqual(parsed['admin']['root'], 'source')

    def test_lookup_root_does_not_treat_blank_as_the_first_root(self):
        roots = [_root('user'), _root('shared:MATC')]
        self.assertIsNone(lookup_root(roots, ''))
        self.assertIsNone(lookup_root(roots, None))
        self.assertEqual(lookup_root(roots, 'shared:matc').id, 'shared:MATC')
        self.assertIsNone(lookup_root(roots, 'source'))


if __name__ == '__main__':
    unittest.main()
