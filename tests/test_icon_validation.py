import unittest
from appmon.app_icons import source_url,media_type
from appmon.stores import FetchError


class IconValidationTest(unittest.TestCase):
    def test_store_cdn_allowlist_rejects_private_urls_and_credentials(self):
        self.assertEqual(source_url('http://is1-ssl.mzstatic.com/image.png'),'https://is1-ssl.mzstatic.com/image.png')
        self.assertTrue(source_url('https://play-lh.googleusercontent.com/example'))
        for url in ['http://127.0.0.1/secret','file:///etc/passwd','https://mzstatic.com.evil.test/x','https://secret@is1.mzstatic.com/x','https://is1.mzstatic.com:8080/x']:
            self.assertIsNone(source_url(url))
    def test_html_and_svg_cannot_be_served_as_cached_app_icons(self):
        for raw in [b'<html>error</html>',b'<svg onload="bad()"/>',b'not an image']:
            with self.assertRaises(FetchError):media_type(raw)
