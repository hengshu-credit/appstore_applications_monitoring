import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from appmon.db import connect
from appmon.keyword_stores import KeywordClient, SearchResult, parse_apple, parse_google
from appmon.keywords import (add_targets, app_coverage, claim_target, history, ranks,
                            save_search, set_enabled, validate_target, work_keywords)
from appmon.stores import FetchError
from test_monitor import OceanBaseTestCase


def google_page(ids, exact=None, dataset="ds:4"):
    rows = []
    for app_id in ids:
        rows.append([[ [app_id], None, None, app_id ]])
    section = [None] * 24
    section[22] = [rows]
    if exact:
        card = [None] * 17
        detail = [None] * 4
        detail[2] = [["Exact app"]]
        detail[3] = {"12": [[exact]]}
        card[16] = detail
        section[23] = card
    payload = json.dumps([[None, [section]]])
    return (f"<script>var AF_dataServiceRequests = {{'{dataset}': {{id: 'lGYRle'}}}};</script>"
            f"<script>AF_initDataCallback({{key: '{dataset}', data:{payload}, sideChannel: {{}}}});</script>")


class SearchParserTest(unittest.TestCase):
    def test_google_order_exact_match_duplicates_and_moved_dataset(self):
        rows = parse_google(google_page(["com.z.app", "com.a.app", "com.z.app"], "com.top.app", "ds:9"))
        self.assertEqual([a["app_id"] for a in rows], ["com.top.app", "com.z.app", "com.a.app"])

    def test_google_rejects_unknown_layout_instead_of_recording_empty(self):
        for text in ("<a href='/store/apps/details?id=com.example.app'>recommendation</a>",
                     "<script>AF_initDataCallback({key: 'ds:4', data:[[null,[]]], sideChannel: {}});</script>"):
            with self.assertRaises(FetchError):
                parse_google(text)
        self.assertEqual(parse_google(google_page([])), [])

    def test_google_skips_empty_section_before_ordered_results(self):
        empty = [None] * 23
        empty[22] = [[]]
        text = google_page(["com.first.app", "com.second.app"])
        text = text.replace('data:[[null, [', 'data:[[null, [' + json.dumps(empty) + ', ')
        self.assertEqual([a["app_id"] for a in parse_google(text)], ["com.first.app", "com.second.app"])

    def test_apple_order_empty_and_schema_errors(self):
        rows = parse_apple({"bubbles": [{"results": [{"id": 900}, {"id": 12}, {"id": 900}]}]})
        self.assertEqual([a["app_id"] for a in rows], ["900", "12"])
        self.assertEqual(parse_apple({"bubbles": []}), [])
        for value in ({}, {"bubbles": [{}]}, {"bubbles": [{"results": [{"id": "bad"}]}]}):
            with self.assertRaises(FetchError):
                parse_apple(value)

    def test_validation_multilingual_normalization_and_no_silent_us_fallback(self):
        base = dict(store="google", country="TH", language="fil_PH", keyword="  เงินกู้  ")
        value = validate_target(base)
        self.assertEqual((value["keyword"], value["country"], value["language"]), ("เงินกู้", "th", "fil-ph"))
        for change in ({"depth": 0}, {"depth": True}, {"refresh_hours": float("nan")},
                       {"language": "bad tag"}, {"keyword": " "}, {"keyword": "a\0b"},
                       {"store": "apple", "country": "zz"}):
            with self.assertRaises(ValueError):
                validate_target(base | change)


class KeywordTest(OceanBaseTestCase):
    def seed_keyword(self, **kwargs):
        base = dict(store="google", country="th", language="th", keyword="เงินกู้", refresh_hours=1)
        return add_targets(self.db, [base | kwargs])["ids"][0]

    def save(self, target_id, now, ids, error=None):
        target = claim_target(self.db, 60, now, target_id, refresh=True)
        result = SearchResult([dict(app_id=a, name=a) for a in ids], b"recorded response", "test")
        return save_search(self.db, self.cfg, target, result=None if error else result, error=error, now=now)

    def test_roundtrip_changes_disappearance_reappearance_and_error_history(self):
        target = self.seed_keyword()
        self.save(target, 100, ["com.z.app", "com.a.app"])
        self.save(target, 200, ["com.a.app", "com.z.app"])
        report = app_coverage(self.db, "google", "com.a.app")["coverage"][0]
        self.assertEqual((report["position"], report["previous_position"], report["change"]), (1, 2, 1))
        self.assertEqual(ranks(self.db, target)["ranks"][0]["app_id"], "com.a.app")
        self.save(target, 300, [], FetchError("HTTP 429", 900))
        report = app_coverage(self.db, "google", "com.a.app")["coverage"][0]
        self.assertEqual(report["position"], 1)
        self.assertEqual(report["latest_attempt"]["outcome"], "error")
        self.assertEqual(report["latest_success"]["observed_at"], 200)
        self.assertTrue(report["stale"])
        self.assertIsNone(claim_target(self.db, 60, 1199, target))
        self.save(target, 1200, ["com.z.app"])
        report = app_coverage(self.db, "google", "com.a.app")["coverage"][0]
        self.assertEqual(report["status"], "not_observed")
        self.assertIsNone(report["position"])
        self.assertIsNone(report["change"])
        self.assertEqual(report["latest_success"]["returned_count"], 1)
        self.save(target, 1300, ["com.a.app"])
        self.save(target, 1400, ["com.a.app"])
        self.assertEqual(ranks(self.db, target)["ranks"][0]["change"], 0)
        rows = history(self.db, target, "com.a.app")["history"]
        self.assertEqual([r["status"] for r in rows],
                         ["observed", "observed", "not_observed", "error", "observed", "observed"])
        self.assertEqual(self.db.execute("SELECT count(*) n FROM keyword_runs").fetchone()["n"], 6)
        from appmon.objects import for_config
        self.assertTrue(for_config(self.cfg).get(rows[0]["raw_path"]))

    def test_targets_isolate_store_country_language_and_reimport_preserves_history(self):
        targets = [self.seed_keyword(), self.seed_keyword(country="vn", language="vi"),
                   self.seed_keyword(language="en"), self.seed_keyword(store="apple")]
        for n, target in enumerate(targets):
            self.save(target, 100, ["123"] if n == 3 else ["com.a.app"])
        self.assertEqual(len(app_coverage(self.db, "google", "com.a.app")["coverage"]), 3)
        self.assertEqual(len(app_coverage(self.db, "google", "com.a.app", "th", "en")["coverage"]), 1)
        self.assertEqual(len(app_coverage(self.db, "apple", "123")["coverage"]), 1)
        set_enabled(self.db, targets[0], False)
        self.assertEqual(self.seed_keyword(depth=20), targets[0])
        current = ranks(self.db, targets[0])
        self.assertFalse(current["target"]["enabled"])
        self.assertEqual(current["target"]["due"], 3700)
        self.assertEqual(current["latest_success"]["requested_depth"], 50)

    def test_invalid_batch_is_atomic_and_empty_snapshot_is_success(self):
        with self.assertRaises(ValueError):
            add_targets(self.db, [dict(store="google", country="us", language="en", keyword="test"), {}])
        self.assertEqual(self.db.execute("SELECT count(*) n FROM keyword_targets").fetchone()["n"], 0)
        target = self.seed_keyword()
        self.save(target, 100, [])
        self.assertEqual(ranks(self.db, target)["latest_success"]["returned_count"], 0)

    def test_lease_recovery_stale_rejection_pause_and_due(self):
        target = self.seed_keyword()
        first = claim_target(self.db, 60, 100)
        self.assertIsNone(claim_target(self.db, 60, 120))
        second = claim_target(self.db, 60, 161)
        result = SearchResult([dict(app_id="com.a.app")], b"", "test")
        self.assertFalse(save_search(self.db, self.cfg, first, result, now=162))
        set_enabled(self.db, target, False)
        self.assertFalse(save_search(self.db, self.cfg, second, result, now=163))
        self.assertIsNone(claim_target(self.db, 60, 164, target, refresh=True))
        set_enabled(self.db, target, True)
        self.save(target, 200, ["com.a.app"])
        self.assertIsNone(claim_target(self.db, 60, 3799))
        self.assertIsNotNone(claim_target(self.db, 60, 3800))

    def test_parallel_claim_skips_locked_keyword(self):
        first, second = self.seed_keyword(), self.seed_keyword(keyword="loan")
        other = connect(self.cfg)
        try:
            self.db.begin()
            self.db.execute("SELECT * FROM keyword_targets WHERE id=%s FOR UPDATE", (first,))
            claimed = claim_target(other, 60, 100)
            self.assertEqual(claimed["id"], second)
        finally:
            self.db.rollback()
            other.close()

    def test_worker_retries_and_refresh_collects_only_once(self):
        target = self.seed_keyword()
        class Broken:
            def search(self, target):
                raise FetchError("HTTP 503")
        result = work_keywords(self.cfg, self.db, 10, Broken())
        self.assertEqual((result["processed"], result["error"]), (1, 1))
        with self.assertRaises(ValueError):
            work_keywords(self.cfg, self.db, refresh=True)
        class Good:
            def search(self, target):
                return SearchResult([dict(app_id="com.a.app")], b"", "test")
        result = work_keywords(self.cfg, self.db, 10, Good(), target, True)
        self.assertEqual((result["processed"], result["ok"]), (1, 1))

    def test_snapshot_write_failure_rolls_back_history_and_progress(self):
        target = self.seed_keyword()
        job = claim_target(self.db, 60, 100)
        original = self.db.executemany
        def fail_ranks(sql, values):
            if sql.startswith("INSERT INTO keyword_ranks"):
                raise RuntimeError("disk write failure")
            return original(sql, values)
        result = SearchResult([dict(app_id="com.a.app")], b"raw", "test")
        with patch.object(self.db, "executemany", side_effect=fail_ranks):
            with self.assertRaisesRegex(RuntimeError, "disk write failure"):
                save_search(self.db, self.cfg, job, result, now=101)
        self.assertEqual(self.db.execute("SELECT count(*) n FROM keyword_runs").fetchone()["n"], 0)
        self.assertEqual(self.db.execute("SELECT count(*) n FROM apps").fetchone()["n"], 0)
        self.assertIsNone(ranks(self.db, target)["latest_success"])
        self.assertTrue(save_search(self.db, self.cfg, job, result, now=102))

    def test_cli_multilingual_json_redirected_to_gbk_console(self):
        self.seed_keyword()
        env = dict(os.environ, OCEANBASE_DATABASE=self.database_name, OCEANBASE_USER=self.options["user"],
                   OCEANBASE_PASSWORD=self.options["password"], PYTHONIOENCODING="gbk")
        process = subprocess.run([sys.executable, "-m", "appmon", "keywords", "list"],
                                 env=env, capture_output=True, check=True)
        self.assertEqual(json.loads(process.stdout.decode("utf-8"))[0]["keyword"], "เงินกู้")

    def test_store_requests_preserve_locale_and_depth(self):
        client = KeywordClient(self.cfg, self.db)
        class Response:
            status_code = 200
            content = b"response"
            url = "https://example.com"
            text = google_page(["com.z.app", "com.a.app"])
            def json(self):
                return {"bubbles": [{"results": [{"id": 9}, {"id": 1}]}]}
        with patch.object(client, "get", return_value=Response()) as get:
            target = validate_target(dict(store="google", country="vn", language="vi", keyword="vay tiền", depth=1))
            result = client.search(target)
            self.assertEqual([a["app_id"] for a in result.apps], ["com.z.app"])
            self.assertEqual(get.call_args.args[2]["hl"], "vi")
            self.assertEqual(get.call_args.args[2]["gl"], "vn")
            target.update(store="apple", country="ph", language="fil")
            self.assertEqual(client.search(target).apps[0]["app_id"], "9")
            self.assertEqual(get.call_args.kwargs["headers"]["X-Apple-Store-Front"], "143474,24 t:native")
            self.assertEqual(get.call_args.kwargs["headers"]["Accept-Language"], "fil")


if __name__ == "__main__":
    unittest.main()
