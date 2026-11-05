import unittest
from appmon.public_sources import community_metrics, web_trends


class PublicSourcesTest(unittest.TestCase):
    def test_directory_uses_observed_form_and_rejects_external_credential_targets(self):
        from appmon.directory_source import login_form,metrics
        html='<form method="post" action="/login"><input type="hidden" name="csrf" value="fixture"><input type="email" name="email"><input type="password" name="password"></form>'
        url,fields=login_form(html,'https://www.directory.example/login','fixture@example.com','secret',['www.directory.example'])
        self.assertEqual(url,'https://www.directory.example/login')
        self.assertEqual(fields,{'csrf':'fixture','email':'fixture@example.com','password':'secret'})
        with self.assertRaises(ValueError):login_form(html.replace('action="/login"','action="https://other.example/collect"'),'https://www.directory.example/login','email','secret',['www.directory.example'])
        with self.assertRaises(ValueError):login_form(html.replace('method="post"','method="get"'),'https://www.directory.example/login','email','secret',['www.directory.example'])
        result=metrics('<a href="/app/com.example.app">Test</a> Installs 1.7 billion Last updated today','com.example.app')
        self.assertEqual(result['values_display'],{'total_installs':'1.7 billion'})
    def test_displayed_estimates_keep_scope_ranges_and_missing_fields(self):
        result=community_metrics('<h1>123</h1><script>999M monthly active users</script><b>$10M+</b> monthly revenue est. Weekly +2.5M','apple','123')
        self.assertEqual(result['scope'],'worldwide')
        self.assertIsNone(result['country'])
        self.assertEqual(result['values_display'],{'monthly_revenue_estimate':'$10M+','weekly_install_change':'2.5M'})
        with self.assertRaises(ValueError):community_metrics('<p>Sign in</p>','apple','123')

    def test_web_feed_does_not_become_app_store_hot_search_or_fake_traffic(self):
        result=web_trends(b'<rss xmlns:ht="https://feed.example/rss"><channel><item><title>example</title><ht:approx_traffic>10K+</ht:approx_traffic></item><item><title>second</title></item></channel></rss>','JP')
        self.assertEqual(result['scope'],'web_search')
        self.assertEqual(result['items'][0]['traffic_display'],'10K+')
        self.assertIsNone(result['items'][1]['traffic_display'])
        with self.assertRaises(ValueError):web_trends(b'<html>Login required</html>','US')
        with self.assertRaises(ValueError):web_trends(b'<!DOCTYPE rss [<!ENTITY foo "x">]><rss/>','US')
