import json
import os
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from appmon.db import add_apps, connect
from appmon.intelligence import (watch, add_observation_target, claim_observation,
                                 save_observation, observation_report, compare_apps, work_watchlist)
from appmon.observation_stores import (ObservationResult, google_chart, google_reviews,
                                       apple_reviews, batch_payload)
from appmon.featured import parse_featured
from appmon.stores import Result, FetchError
from appmon.keywords import add_targets, claim_target, save_search
from appmon.keyword_stores import SearchResult
from appmon.analytics import import_ad_observations, import_ad_reports
from appmon.alerts import add_rule, evaluate
from appmon.exports import request_export, work_export
from appmon.web import create_app
from test_monitor import MySQLTestCase


class ObservationParsersTest(unittest.TestCase):
    def test_rpc_extracts_requested_frame_and_rejects_challenges(self):
        response = ")]}'\n123\n" + json.dumps([["wrb.fr", "other", "[]"], ["wrb.fr", "test", "[1,2]"]])
        self.assertEqual(batch_payload(response, "test"), [1, 2])
        for value in ("<html>consent</html>", '[["wrb.fr","test",null]]'):
            with self.assertRaises(FetchError):
                batch_payload(value, "test")

    def test_reviews_validated_and_do_not_swallow_broken_payload(self):
        review = ["review-1", None, 2, None, "app crashes", [1720000000], 3, None, None, None, "1.2"]
        rows, token = google_reviews([[review], [None, "next-page"]])
        self.assertEqual((rows[0]["rating"], token, rows[0]["version"]), (2, "next-page", "1.2"))
        with self.assertRaises(FetchError):
            google_reviews({"error": "bad"})
        with self.assertRaises(FetchError):
            apple_reviews({})
        self.assertEqual(apple_reviews({"feed": {"entry": []}}), [])

    def test_today_only_editorial_cards_and_app_ids(self):
        data = {"data": [{"data": {"shelves": [
            {"contentType": "todayCard", "items": [{"$kind": "TodayCard", "title": "Featured", "clickAction": {"pageUrl": "https://apps.apple.com/us/story/id999"}, "overlay": {"lockup": {"adamId": "123", "title": "App"}}}]},
            {"contentType": "recommendations", "items": [{"adamId": "666"}]}]}}]}
        rows = parse_featured('<script id="serialized-server-data" type="application/json">'+json.dumps(data)+'</script>')
        self.assertEqual(rows[0]["apps"], [{"app_id": "123", "name": "App"}])
        self.assertNotEqual(rows[0]["id"], "999")
        self.assertEqual(len(rows), 1)


class IntelligenceTest(MySQLTestCase):
    def target(self, **kwargs):
        return add_observation_target(self.db, dict(kind="chart", store="google", country="us", language="en", refresh_hours=1) | kwargs)["id"]

    def record(self, target_id, now, ids, error=None):
        self.db.execute("UPDATE observation_targets SET due=0 WHERE id=%s", (target_id,))
        t = claim_observation(self.db, 60, now)
        self.assertEqual(t["id"], target_id)
        return save_observation(self.db, self.cfg, t, error=error) if error else save_observation(
            self.db, self.cfg, t, ObservationResult([dict(id=i, app_id=i, name=i) for i in ids], b"raw", "fixture", "returned_chart_range"), now=now)

    def test_chart_history_enter_exit_and_failures_keep_success(self):
        identifier = self.target()
        self.record(identifier, 100, ["com.a.app", "com.b.app"])
        self.record(identifier, 200, ["com.b.app", "com.c.app"])
        r = observation_report(self.db, identifier)
        self.assertEqual(r["items"][0]["change"], 1)
        self.assertEqual(r["items"][1]["status"], "entered_observed_range")
        self.assertEqual(r["left_observed_range"][0]["app_id"], "com.a.app")
        self.record(identifier, 300, [], FetchError("HTTP 429", 900))
        r = observation_report(self.db, identifier)
        self.assertTrue(r["stale"])
        self.assertEqual(r["latest_success"]["observed_at"], 200)
        self.assertEqual(r["latest_attempt"]["outcome"], "error")

    def test_stale_lease_cannot_publish_and_invalid_batch_is_atomic(self):
        self.target()
        first = claim_observation(self.db, 60, 100)
        replacement = claim_observation(self.db, 60, 161)
        result = ObservationResult([dict(id="com.a.app", app_id="com.a.app")], b"raw", "fixture", "range")
        self.assertFalse(save_observation(self.db, self.cfg, first, result, now=162))
        self.assertTrue(save_observation(self.db, self.cfg, replacement, result, now=163))
        self.assertEqual(self.db.execute("SELECT count(*) n FROM observation_runs").fetchone()["n"], 1)
        self.db.execute("UPDATE observation_targets SET due=0")
        current = claim_observation(self.db, 60, 200)
        duplicate = ObservationResult(result.items*2, b"", "fixture", "range")
        with self.assertRaises(ValueError):
            save_observation(self.db, self.cfg, current, duplicate)
        self.assertEqual(self.db.execute("SELECT count(*) n FROM observation_runs").fetchone()["n"], 1)

    def test_review_edits_keep_history_and_current_unique(self):
        identifier = self.target(kind="reviews", app_id="com.a.app")
        for now, content in ((100, "first"), (200, "edited")):
            self.db.execute("UPDATE observation_targets SET due=0")
            t = claim_observation(self.db, 60, now)
            save_observation(self.db, self.cfg, t, ObservationResult([dict(id="review-1", rating=2, content=content)], b"", "fixture", "recent"), now=now)
        self.assertEqual(self.db.execute("SELECT count(*) n FROM reviews").fetchone()["n"], 1)
        self.assertEqual(self.db.execute("SELECT content FROM reviews").fetchone()["content"], "edited")
        self.assertEqual(self.db.execute("SELECT count(*) n FROM observation_items").fetchone()["n"], 2)

    def test_priority_watchlist_obeys_region_and_pause(self):
        watch(self.db, dict(store="google", app_id="com.a.app", country="th", language="th", refresh_hours=2))
        watch(self.db, dict(store="google", app_id="com.b.app", country="us", language="en"))
        self.db.execute("UPDATE watchlist SET enabled=0 WHERE app_id='com.b.app'")
        class Fake:
            def fetch(self, job):
                self.job = job
                return Result("ok", {"name": "Observed", "rating": 4.2}, b"detail")
        client = Fake()
        result = work_watchlist(self.cfg, self.db, 5, client)
        self.assertEqual(result["processed"], 1)
        self.assertEqual((client.job["country"], client.job["language"]), ("th", "th"))
        row = self.db.execute("SELECT * FROM listings").fetchone()
        self.assertEqual(row["state"], "available")
        self.assertEqual(self.db.execute("SELECT count(*) n FROM snapshots").fetchone()["n"], 1)

    def test_alerts_are_deduplicated_and_lagged_keyword_runs_use_their_own_history(self):
        add_rule(self.db, dict(kind="keyword_move", threshold=2))
        self.db.execute("UPDATE alert_rules SET created_at=0")
        identifier = add_targets(self.db, [dict(store="google", country="us", language="en", keyword="chat")])["ids"][0]
        for now, ids in ((100, ["com.a.app", "com.b.app", "com.c.app"]), (200, ["com.c.app", "com.b.app", "com.a.app"]), (300, ["com.c.app", "com.b.app", "com.a.app"])):
            target = claim_target(self.db, 60, now, identifier, refresh=True)
            save_search(self.db, self.cfg, target, SearchResult([dict(app_id=i, name=i) for i in ids], b"", "test"), now=now)
        self.assertEqual(evaluate(self.db)["created"], 2)
        self.assertEqual(evaluate(self.db)["created"], 0)
        times = self.db.execute("SELECT DISTINCT observed_at FROM alerts").fetchall()
        self.assertEqual(times, [{"observed_at": 200.0}])

    def test_replaced_watch_lease_is_not_counted_as_success(self):
        watch(self.db, dict(store="google", app_id="com.a.app", country="us", language="en"))
        db = self.db
        class Replaced:
            def fetch(self, job):
                db.execute("UPDATE jobs SET token='new-owner',lease_until=%s", (time.time()+300,))
                return Result("ok", {"name": "Obsolete"}, b"obsolete")
        result = work_watchlist(self.cfg, self.db, 1, Replaced())
        self.assertEqual((result["processed"], result["ok"], result["stale"]), (1, 0, 1))
        self.assertEqual(self.db.execute("SELECT count(*) n FROM listings").fetchone()["n"], 0)

    def test_export_roundtrip_filters_and_no_arbitrary_table_or_path(self):
        add_apps(self.db, [("apple", "123", "A", None), ("google", "com.a.app", "G", None)], "fixture")
        request = request_export(self.db, "apps", {"store": "apple"})
        result = work_export(self.cfg, self.db)
        self.assertEqual(result["rows"], 1)
        rows = (self.folder / "exports" / (request["id"] + ".jsonl")).read_text(encoding="utf-8").splitlines()
        self.assertEqual(json.loads(rows[0])["app_id"], "123")
        for kind, filters in (("jobs;DROP TABLE apps", {}), ("apps", {"unknown": "x"}), ("apps", {"store": []})):
            with self.assertRaises(ValueError):
                request_export(self.db, kind, filters)

    def test_ad_evidence_and_actual_reports_validation_and_deduplication(self):
        evidence = dict(store="apple", app_id="123", country="us", keyword="loan", source="owned observation",
                        evidence_url="https://example.org/evidence", placement="search_ad", observed_at=time.time())
        self.assertEqual(import_ad_observations(self.db, [evidence])["added"], 1)
        self.assertEqual(import_ad_observations(self.db, [evidence])["added"], 0)
        with self.assertRaises(ValueError):
            import_ad_observations(self.db, [evidence | {"evidence_url": "javascript:alert(1)"}])
        report = dict(app_id="123", country="us", report_date="2026-11-03", campaign="campaign", keyword="loan",
                      impressions=10, taps=2, installs=1, spend="2.50", currency="USD", source="owned report")
        import_ad_reports(self.db, [report])
        import_ad_reports(self.db, [report | {"impressions": 20}])
        self.assertEqual(self.db.execute("SELECT count(*) n FROM ad_reports").fetchone()["n"], 1)
        self.assertEqual(self.db.execute("SELECT impressions FROM ad_reports").fetchone()["impressions"], 20)
        with self.assertRaises(ValueError):
            import_ad_reports(self.db, [report, report | {"spend": "NaN"}])
        self.assertEqual(self.db.execute("SELECT impressions FROM ad_reports").fetchone()["impressions"], 20)

    def test_api_auth_origin_crud_history_and_export(self):
        with patch.dict(os.environ, {"APPMON_API_TOKEN": "test-token-for-the-independent-console"}):
            client = TestClient(create_app(self.cfg))
        self.assertEqual(client.get('/api/overview').status_code, 401)
        auth = {"Authorization": "Bearer test-token-for-the-independent-console"}
        self.assertEqual(client.post('/api/session', json={}, headers=auth).status_code, 200)
        self.assertEqual(client.get('/api/overview').status_code, 200)
        self.assertEqual(client.post('/api/watchlist', json={}, headers={"Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(client.get('/api/overview', headers={"Host": "evil.example"}).status_code, 403)
        value = dict(store="apple", app_id="123", country="us", language="en")
        self.assertEqual(client.post('/api/watchlist', json=value).status_code, 200)
        self.assertEqual(len(client.get('/api/watchlist').json()), 1)
        self.assertEqual(client.get('/api/apps?store=apple&q=123').json()["items"][0]["app_id"], "123")
        self.assertIsNone(client.get('/api/apps/apple/123').json()["listing"])
        self.assertEqual(client.post('/api/targets', json=value | {"kind": "reviews"}).status_code, 200)
        self.assertEqual(client.post('/api/keywords', json=[value | {"keyword": "chat"}]).status_code, 200)
        self.assertEqual(client.get('/api/openapi.json').status_code, 200)
        result = client.post('/api/exports', json={"kind": "apps"}).json()
        self.assertEqual(client.get('/api/exports/'+result['id']+'/download').status_code, 409)
        work_export(self.cfg, self.db)
        download = client.get('/api/exports/'+result['id']+'/download')
        self.assertEqual(download.status_code, 200)
        self.assertEqual(json.loads(download.text)["app_id"], "123")
        self.assertEqual(client.get('/').status_code, 404)  # Python serves data only.

    def test_comparison_rejects_cross_market_and_missing_data_is_not_zero(self):
        first = dict(store="apple", app_id="123", country="us", language="en")
        result = compare_apps(self.db, [first, first | {"app_id": "456"}])
        self.assertEqual(result["keywords"], [])
        self.assertIsNone(result["apps"][0]["payload"])
        with self.assertRaises(ValueError):
            compare_apps(self.db, [first, first | {"country": "th"}])


if __name__ == '__main__':
    unittest.main()
