import unittest
from appmon.public_sources import appgoblin_metrics, google_web_trends


class PublicSourcesTest(unittest.TestCase):
    def test_appbrain_uses_observed_form_and_rejects_external_credential_targets(self):
        from appmon.appbrain_source import login_form,metrics
        html='<form method="post" action="/login"><input type="hidden" name="csrf" value="fixture"><input type="email" name="email"><input type="password" name="password"></form>'
        url,fields=login_form(html,'https://www.appbrain.com/login','fixture@example.com','secret')
        self.assertEqual(url,'https://www.appbrain.com/login')
        self.assertEqual(fields,{'csrf':'fixture','email':'fixture@example.com','password':'secret'})
        with self.assertRaises(ValueError):login_form(html.replace('action="/login"','action="https://other.example/collect"'),'https://www.appbrain.com/login','email','secret')
        with self.assertRaises(ValueError):login_form(html.replace('method="post"','method="get"'),'https://www.appbrain.com/login','email','secret')
        result=metrics('<a href="/app/com.example.app">Test</a> Installs 1.7 billion Last updated today','com.example.app')
        self.assertEqual(result['values_display'],{'total_installs':'1.7 billion'})
    def test_displayed_estimates_keep_scope_ranges_and_missing_fields(self):
        result=appgoblin_metrics('<h1>123</h1><script>999M monthly active users</script><b>$10M+</b> monthly revenue est. Weekly +2.5M','apple','123')
        self.assertEqual(result['scope'],'worldwide')
        self.assertIsNone(result['country'])
        self.assertEqual(result['values_display'],{'monthly_revenue_estimate':'$10M+','weekly_install_change':'2.5M'})
        with self.assertRaises(ValueError):appgoblin_metrics('<p>Sign in</p>','apple','123')

    def test_web_feed_does_not_become_app_store_hot_search_or_fake_traffic(self):
        result=google_web_trends(b'<rss xmlns:ht="https://trends.google.com/trending/rss"><channel><item><title>example</title><ht:approx_traffic>10K+</ht:approx_traffic></item><item><title>second</title></item></channel></rss>','JP')
        self.assertEqual(result['scope'],'web_search')
        self.assertEqual(result['items'][0]['traffic_display'],'10K+')
        self.assertIsNone(result['items'][1]['traffic_display'])
        with self.assertRaises(ValueError):google_web_trends(b'<html>Login required</html>','US')
        with self.assertRaises(ValueError):google_web_trends(b'<!DOCTYPE rss [<!ENTITY foo "x">]><rss/>','US')
