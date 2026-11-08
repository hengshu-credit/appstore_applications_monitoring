"""Compress/deduplicate existing detail bodies without deleting historical rows."""
import argparse
import json
import time
from pathlib import Path
from appmon.config import load
from appmon.db import connect
from appmon.content_compaction import compact, verify, reclaim


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--table', choices=('snapshots', 'listings', 'all'), default='all')
    parser.add_argument('--reclaim', action='store_true')
    parser.add_argument('--verify-only', action='store_true')
    parser.add_argument('--output', default='data/exports/content-compaction-verification.json')
    args = parser.parse_args()
    cfg = load()
    cfg['mysql']['read_timeout'] = 7200
    db = connect(cfg)
    report = {'started': time.time(), 'tables': []}
    last_print = [0]
    def progress(table, count):
        if time.monotonic() - last_print[0] > 20:
            print(json.dumps({'table': table, 'rows': count}), flush=True)
            last_print[0] = time.monotonic()
    try:
        tables = ('snapshots', 'listings') if args.table == 'all' else (args.table,)
        for table in tables:
            if not args.verify_only:
                compact(db, cfg, table, progress=progress)
            result = verify(db, cfg, table, progress=progress)
            if args.reclaim:
                state = db.execute('SELECT status FROM content_compactions WHERE name=%s', (table,)).fetchone()
                if state['status'] != 'reclaimed':
                    reclaim(db, cfg, table)
                result['reclaimed'] = True
            report['tables'].append(result)
            Path(args.output).write_text(json.dumps(report, indent=2), encoding='utf8')
            print(json.dumps(result), flush=True)
        report['blobs'] = db.execute('SELECT COUNT(*) bodies,SUM(raw_bytes) original_bytes,SUM(OCTET_LENGTH(body)) compressed_bytes FROM content_blobs').fetchone()
        report['finished'] = time.time()
        Path(args.output).write_text(json.dumps(report, indent=2, default=str), encoding='utf8')
        print(json.dumps(report, default=str), flush=True)
    finally:
        db.close()


if __name__ == '__main__':
    main()
