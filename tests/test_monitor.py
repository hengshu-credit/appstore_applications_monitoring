import json
import lzma
import tempfile
import os
import uuid
import concurrent.futures
import unittest
from pathlib import Path
from unittest.mock import patch

from appmon.config import load
from appmon.db import Database, TABLES, add_apps, claim, connect, enqueue, reserve, status
from appmon.ingest import import_file
from appmon.monitor import save_result, work
from appmon.stores import Client, FetchError, Result


class MySQLTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cfg = load(Path(__file__).parents[1] / 'config.toml')
        cls.options = dict(cfg['mysql'])
        cls.options.update(user='root', password=os.environ.get('MYSQL_ROOT_PASSWORD', ''))
        admin_options = dict(cls.options)
        admin_options.pop('database')
        cls.admin = Database(admin_options)
        cls.database_name = 'appmon_test_' + uuid.uuid4().hex
        cls.admin.execute(f'CREATE DATABASE `{cls.database_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin')
        cls.options['database'] = cls.database_name
        bootstrap = connect({'mysql': cls.options})
        bootstrap.close()

    @classmethod
    def tearDownClass(cls):
        assert cls.database_name.startswith('appmon_test_')
        cls.admin.execute(f'DROP DATABASE `{cls.database_name}`')
        cls.admin.close()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.cfg = load(Path(__file__).parents[1] / "config.toml")
        self.cfg.update(data_dir=self.folder, min_free_gb=0)
        self.cfg['mysql'] = self.options
        self.cfg['source_profiles'] = {
            'community': {'origin':'https://community.example','hosts':['community.example']},
            'directory': {'origin':'https://www.directory.example','hosts':['www.directory.example','developers.directory.example','directory.example']},
            'marketdata': {'origin':'https://market.example/v2','hosts':['market.example']},
            'web_feed': {'origin':'https://feed.example/rss','hosts':['feed.example']},
        }
        self.db = connect(self.cfg, allow_migration=True)
        for table in TABLES:
            self.db.execute(f'TRUNCATE TABLE `{table}`')
        from appmon.auth import initialize
        initialize(self.db)

    def login_admin(self, client):
        from appmon.auth import initialize
        initialize(self.db)
        credentials = {'username': 'test-admin', 'password': 'test-password-1234'}
        result = client.post('/api/auth/initialize', json=credentials)
        self.assertIn(result.status_code, (200, 409), result.text)
        result = client.post('/api/auth/login', json=credentials)
        self.assertEqual(result.status_code, 200, result.text)
        client.headers['X-CSRF-Token'] = result.json()['csrf_token']
        return client

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()


class MonitorTest(MySQLTestCase):
    def seed(self):
        with self.db:
            add_apps(self.db, [("google", "com.example.app", "Old", None)], "test", 10)
        enqueue(self.db, ["us"], "en")

    def job(self, now):
        return claim(self.db, ["us"], "en", 60, now)

    def complete(self, now, result=None, error=None):
        job = self.job(now)
        self.assertIsNotNone(job)
        self.assertTrue(save_result(self.db, self.cfg, job, result, error, now))

    def test_import_validation_dedupe_and_repeat(self):
        path = self.folder / "apps.tsv.xz"
        with lzma.open(path, "wt", encoding="utf-8") as out:
            out.write("store\tstore_id\tapp_name\nAndroid\tcom.example.app\tA\niOS\t1234\tB\n"
                      "google\tcom.example.app\tA2\ninvalid\tfoo\tX\n")
        result = import_file(self.db, path, "fixture", 0, 2)
        self.assertEqual((result["processed"], result["valid"], result["added"], result["rejected"]), (4, 3, 2, 1))
        self.assertEqual(import_file(self.db, path, "fixture", 0), result)
        self.assertEqual(self.db.execute("SELECT count(*) count FROM apps").fetchone()["count"], 2)

    def test_import_checkpoint_after_interruption(self):
        path = self.folder / "apps.tsv"
        path.write_text("store\tstore_id\ngoogle\tcom.a.one\ngoogle\tcom.a.two\napple\t123\n")
        with patch("appmon.ingest.disk_check", side_effect=[None, OSError("disk full")]):
            with self.assertRaises(OSError):
                import_file(self.db, path, "test", 0, 2)
        row = self.db.execute("SELECT * FROM imports").fetchone()
        self.assertEqual((row["processed"], row["status"]), (2, "interrupted"))
        result = import_file(self.db, path, "test", 0, 2)
        self.assertEqual((result["processed"], result["added"]), (3, 3))

    def test_unexpected_header_rejected(self):
        path = self.folder / "broken.tsv"
        path.write_text("id\n123\n")
        with self.assertRaises(ValueError):
            import_file(self.db, path, "bad", 0)

    def test_leases_recover_and_reject_stale_worker(self):
        self.seed()
        first = self.job(100)
        self.assertIsNone(self.job(120))
        second = self.job(161)
        self.assertNotEqual(first["token"], second["token"])
        self.assertFalse(save_result(self.db, self.cfg, first, Result("ok", {"name": "stale"}), now=162))
        self.assertTrue(save_result(self.db, self.cfg, second, Result("ok", {"name": "fresh"}), now=163))

    def test_changes_missing_errors_and_reappearance(self):
        self.seed()
        self.complete(100, Result("ok", {"name": "A", "version": "1"}, b"first"))
        self.complete(86500, Result("ok", {"name": "A", "version": "1"}, b"unchanged"))
        self.assertEqual(self.db.execute("SELECT count(*) count FROM snapshots").fetchone()["count"], 1)
        self.complete(172900, Result("ok", {"name": "A", "version": "2"}, b"update"))
        self.complete(259300, Result("missing", raw=b"missing"))
        self.complete(345700, error=FetchError("HTTP 429", 900))
        row = self.db.execute("SELECT * FROM listings").fetchone()
        self.assertEqual((row["misses"], row["state"]), (1, "suspected_missing"))
        self.complete(346600, Result("missing"))
        self.complete(433000, Result("missing"))
        self.assertEqual(self.db.execute("SELECT state FROM listings").fetchone()["state"], "region_unavailable")
        self.complete(519400, Result("ok", {"name": "A", "version": "2"}))
        kinds = [r["kind"] for r in self.db.execute("SELECT kind FROM events ORDER BY id")]
        self.assertEqual(kinds, ["first_verified", "metadata_changed", "region_unavailable", "reappeared"])

    def test_country_state_independence(self):
        self.seed()
        enqueue(self.db, ["gb"], "en")
        self.complete(100, Result("ok", {"name": "A"}))
        gb = claim(self.db, ["gb"], "en", 60, 100)
        save_result(self.db, self.cfg, gb, Result("missing"), now=100)
        states = {r["country"]: r["state"] for r in self.db.execute("SELECT country,state FROM listings")}
        self.assertEqual(states, {"us": "available", "gb": "suspected_missing"})

    def test_rate_reservations_shared_across_connections(self):
        other = connect(self.cfg)
        try:
            self.assertEqual(reserve(self.db, "rate:apple", 3.5, 100), 0)
            self.assertEqual(reserve(other, "rate:apple", 3.5, 100), 3.5)
            self.assertEqual(reserve(self.db, "rate:google", 2.5, 100), 0)
        finally:
            other.close()

    def test_historical_import_does_not_replace_verified_title(self):
        self.seed()
        self.complete(100, Result("ok", {"name": "Current title", "category": "TOOLS"}))
        with self.db:
            add_apps(self.db, [("google", "com.example.app", "Old export title", "OLD")], "old-export", 200)
        row = self.db.execute("SELECT name,category FROM apps").fetchone()
        self.assertEqual(tuple(row.values()), ("Current title", "TOOLS"))

    def test_worker_error_retries_without_false_delisting(self):
        self.seed()
        class Broken:
            def fetch(self, job):
                raise FetchError("HTTP 503")
        result = work(self.cfg, self.db, 1, Broken())
        self.assertEqual(result["error"], 1)
        self.assertEqual(self.db.execute("SELECT count(*) count FROM listings").fetchone()["count"], 0)
        self.assertEqual(status(self.db)["jobs"]["retrying"], 1)

    def test_bad_apple_json_is_error_not_missing(self):
        client = Client(self.cfg, self.db)
        class Response:
            status_code = 200
            content = b'{}'
            def json(self):
                return {}
        with patch.object(client, "get", return_value=Response()):
            with self.assertRaises(FetchError):
                client.fetch({"store": "apple", "app_id": "123", "country": "us"})

    def test_empty_valid_apple_response_is_missing(self):
        client = Client(self.cfg, self.db)
        class Response:
            status_code = 200
            content = b'{}'
            def json(self):
                return {"resultCount": 0, "results": []}
        with patch.object(client, "get", return_value=Response()):
            self.assertEqual(client.fetch({"store": "apple", "app_id": "123", "country": "us"}).outcome, "missing")

    def test_google_parser_failure_is_not_missing(self):
        client = Client(self.cfg, self.db)
        class Response:
            status_code = 200
            content = b'<html>temporary error</html>'
            text = content.decode()
            url = 'https://play.google.com/store/apps/details?id=com.example.app'
        with patch.object(client, "get", return_value=Response()):
            with self.assertRaises(FetchError):
                client.fetch({"store": "google", "app_id": "com.example.app", "country": "us", "language": "en"})

    def test_google_developer_id_is_decoded_before_query_encoding(self):
        client = Client(self.cfg, self.db)
        class Response:
            status_code = 200
            url = 'https://play.google.com/store/apps/developer'
            text = '<a href="/store/apps/details?id=com.whatsapp">App</a>'
        with patch.object(client, "get", return_value=Response()) as get:
            result = client.discover("google", "us", "developer", "WhatsApp+LLC")
            self.assertEqual(get.call_args.args[2]["id"], "WhatsApp LLC")
            self.assertEqual(result[0][1], "com.whatsapp")

    def test_mysql_claim_skips_row_locked_by_other_worker(self):
        with self.db:
            add_apps(self.db, [("google", "com.a.one", None, None),
                               ("google", "com.a.two", None, None)], "test")
        enqueue(self.db, ["us"], "en")
        other = connect(self.cfg)
        try:
            self.db.begin()
            locked = self.db.execute("SELECT * FROM jobs ORDER BY due LIMIT 1 FOR UPDATE").fetchone()
            claimed = claim(other, ["us"], "en", 60, 100)
            self.assertIsNotNone(claimed)
            self.assertNotEqual(claimed["app_id"], locked["app_id"])
        finally:
            self.db.rollback()
            other.close()

    def test_mysql_parallel_rate_reservations_are_serialized(self):
        def reserve_once(_):
            connection = connect(self.cfg, initialize=False)
            try:
                return reserve(connection, "concurrent-rate", 3.5, 100)
            finally:
                connection.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            waits = list(pool.map(reserve_once, range(4)))
        self.assertEqual(sorted(waits), [0, 3.5, 7.0, 10.5])

    def test_mysql_unicode_and_case_sensitive_identity(self):
        with self.db:
            added = add_apps(self.db, [("google", "com.App.Example", "应用 🐈", None),
                                      ("google", "com.app.example", "另一个应用", None)], "unicode")
        self.assertEqual(added, 2)
        self.assertEqual(self.db.execute("SELECT name FROM apps WHERE app_id=%s", ("com.App.Example",)).fetchone()["name"], "应用 🐈")

    def test_mysql_transaction_rolls_back_data_and_checkpoint(self):
        with self.assertRaises(RuntimeError):
            with self.db:
                add_apps(self.db, [("apple", "123", "Temporary", None)], "rollback")
                raise RuntimeError("interrupted")
        self.assertEqual(self.db.execute("SELECT count(*) count FROM apps").fetchone()["count"], 0)

    def test_mysql_blocks_workers_during_incomplete_migration(self):
        self.db.execute("INSERT INTO migration_progress(source_sha256,table_name) VALUES(%s,%s)", ("a" * 64, "apps"))
        with self.assertRaisesRegex(RuntimeError, "Migration incomplete"):
            connect(self.cfg)
        self.db.execute("UPDATE migration_progress SET status='verified'")

    def legacy_fixture(self):
        import sqlite3
        path = self.folder / "legacy.sqlite3"
        old = sqlite3.connect(path)
        for table in ("apps", "imports", "jobs", "listings", "snapshots", "events", "requests", "schedules", "discovery_runs"):
            columns = self.db.execute(f"SHOW COLUMNS FROM `{table}`").fetchall()
            definitions = []
            primary = []
            for c in columns:
                kind = "REAL" if "double" in c["Type"] else "INTEGER" if "int" in c["Type"] else "TEXT"
                definitions.append(f'`{c["Field"]}` {kind}')
                if c["Key"] == "PRI":
                    primary.append(f'`{c["Field"]}`')
            old.execute(f"CREATE TABLE `{table}` ({','.join(definitions)}, PRIMARY KEY({','.join(primary)}))")
        old.executemany("INSERT INTO apps VALUES(?,?,?,?,?,?,?)", [
            ("google", "com.Example.app", "应用 🐈", None, 1.5, 2.5, "legacy"),
            ("google", "com.example.app", "Another", "tools", 2.0, 3.0, "legacy")])
        old.commit()
        old.close()
        return path

    def test_mysql_migration_resume_and_full_content_verification(self):
        from appmon.migrate import migrate
        path = self.legacy_fixture()
        original = Database.executemany
        calls = 0
        def fail_second_batch(connection, sql, rows):
            nonlocal calls
            if sql.startswith("INSERT INTO `apps`"):
                calls += 1
                if calls == 2:
                    raise RuntimeError("simulated interruption")
            return original(connection, sql, rows)
        with patch.object(Database, "executemany", fail_second_batch):
            with self.assertRaisesRegex(RuntimeError, "simulated interruption"):
                migrate(self.cfg, path, batch_size=1)
        self.assertEqual(self.db.execute("SELECT rows_copied FROM migration_progress WHERE table_name='apps'").fetchone()["rows_copied"], 1)
        with self.assertRaisesRegex(RuntimeError, "Migration incomplete"):
            connect(self.cfg)
        report = migrate(self.cfg, path, batch_size=1)
        self.assertEqual(report["tables"]["apps"]["rows"], 2)
        self.assertTrue(all(r["verified"] for r in report["tables"].values()))

    def test_mysql_migration_refuses_to_overwrite_existing_data(self):
        from appmon.migrate import migrate
        path = self.legacy_fixture()
        self.seed()
        with self.assertRaisesRegex(ValueError, "empty destination"):
            migrate(self.cfg, path)
        self.assertEqual(self.db.execute("SELECT count(*) count FROM apps").fetchone()["count"], 1)

    def test_mysql_concurrent_import_cannot_double_count_checkpoint(self):
        from appmon.ingest import file_hash
        path = self.folder / "apps.tsv"
        path.write_text("store\tstore_id\napple\t123\n", encoding="utf-8")
        other = connect(self.cfg)
        try:
            with self.db.named_lock("import:" + file_hash(path)):
                with self.assertRaisesRegex(RuntimeError, "already importing"):
                    import_file(other, path, "test", 0)
            result = import_file(other, path, "test", 0)
            self.assertEqual((result["processed"], result["valid"], result["added"]), (1, 1, 1))
        finally:
            other.close()


if __name__ == "__main__":
    unittest.main()
