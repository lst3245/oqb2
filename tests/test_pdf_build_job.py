"""In-process state for a lazy PDF build. No database and no Word."""
import unittest

from app import generator


class PdfBuildJobTests(unittest.TestCase):
    def setUp(self):
        self.fid = 9_100_000
        with generator._pdf_build_lock:
            generator._pdf_builds.pop(self.fid, None)

    def tearDown(self):
        with generator._pdf_build_lock:
            generator._pdf_builds.pop(self.fid, None)

    def test_second_start_joins_the_running_job(self):
        self.assertTrue(generator._begin_pdf_build(self.fid))
        self.assertFalse(generator._begin_pdf_build(self.fid))
        self.assertEqual(generator._pdf_build_state(self.fid), ('building', None))

    def test_a_failed_job_can_be_started_again(self):
        generator._mark_pdf_build(self.fid, 'failed', 'boom')
        self.assertEqual(generator._pdf_build_state(self.fid), ('failed', 'boom'))
        self.assertTrue(generator._begin_pdf_build(self.fid))
        self.assertEqual(generator._pdf_build_state(self.fid)[0], 'building')

    def test_ready_clears_the_job(self):
        self.assertTrue(generator._begin_pdf_build(self.fid))
        generator._mark_pdf_build(self.fid, 'ready')
        self.assertEqual(generator._pdf_build_state(self.fid), ('absent', None))


if __name__ == '__main__':
    unittest.main()
