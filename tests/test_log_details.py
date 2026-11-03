import unittest
from appmon.log_details import diagnostics,sanitize


class LogDetailsTest(unittest.TestCase):
    def test_diagnostics_keep_stage_and_frames_without_credentials_or_headers(self):
        try:
            try:raise OSError('https://user:password@example.test/?token=private')
            except OSError as cause:raise RuntimeError('Bearer private-value') from cause
        except RuntimeError as error:
            detail=diagnostics(error,stage='network',timeout_seconds=30)
        self.assertEqual(detail['error_type'],'RuntimeError')
        self.assertEqual(detail['cause_type'],'OSError')
        self.assertEqual(detail['timeout_seconds'],30)
        self.assertNotIn('user:password',detail['stack'])
        # Diagnostic sanitization is also applied to any free-text note.
        safe=sanitize({'headers':'Authorization: Bearer hidden','body':'secret','note':'password="private" token=hidden https://example.test/?key=private'})
        self.assertNotIn('headers',safe);self.assertNotIn('body',safe)
        self.assertNotIn('private',safe['note']);self.assertNotIn('hidden',safe['note'])
