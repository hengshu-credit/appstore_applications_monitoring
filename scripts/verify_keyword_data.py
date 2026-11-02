"""Read-only verification of persisted keyword snapshots and reverse coverage."""
import argparse
import gzip
import hashlib
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from appmon.config import load
from appmon.db import connect
from appmon.keyword_stores import parse_apple, parse_google
from appmon.keywords import app_coverage


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--output", default="data/keyword-verification.json")
    args = parser.parse_args()
    cfg = load(args.config)
    db = connect(cfg)
    try:
        runs = db.execute('''SELECT r.*,t.store,t.country,t.language,t.keyword
            FROM keyword_runs r JOIN keyword_targets t ON t.id=r.target_id ORDER BY r.id''').fetchall()
        checked = []
        for run in runs:
            rows = db.execute("SELECT app_id,position FROM keyword_ranks WHERE run_id=%s ORDER BY position",
                              (run["id"],)).fetchall()
            if run["outcome"] == "error":
                assert not rows and run["returned_count"] is None
                continue
            raw_path = run["raw_path"].replace("\\", "/")
            raw = gzip.decompress((cfg["data_dir"] / raw_path).read_bytes())
            assert Path(raw_path).stem == hashlib.sha256(raw).hexdigest()
            apps = parse_apple(json.loads(raw)) if run["store"] == "apple" else parse_google(raw.decode("utf-8"))
            expected = apps[:run["requested_depth"]]
            assert [row["position"] for row in rows] == list(range(1, len(expected) + 1))
            assert [row["app_id"] for row in rows] == [app["app_id"] for app in expected]
            assert len(rows) == run["returned_count"]
            checked.append({k: run[k] for k in ("id", "target_id", "store", "country", "language", "keyword",
                                               "requested_depth", "returned_count", "observed_at")} |
                           {"raw_path": raw_path, "replayed": True})
        coverage = app_coverage(db, "google", "com.mocamoca", "ph")
        report = {"verified_at_shanghai": datetime.now(timezone(timedelta(hours=8))).isoformat(),
                  "successful_snapshots": len(checked), "rank_rows": sum(r["returned_count"] for r in checked),
                  "error_attempts": len(runs) - len(checked), "snapshots": checked,
                  "reverse_query_example": coverage}
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({k: v for k, v in report.items() if k not in ("snapshots", "reverse_query_example")}))
    finally:
        db.close()


if __name__ == "__main__":
    main()
