"""Collect market-specific public finance/ASO candidates with resumable evidence."""
import argparse
import json
import sys
from pathlib import Path

from appmon.config import load
from appmon.db import connect
from appmon.locales import PREFERENCES
from appmon.public_keywords import Fetcher, collect, import_release, make_jobs, reparse


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--countries', default='all', help='all or comma-separated country codes')
    parser.add_argument('--sources', default='suggest,apple,google')
    parser.add_argument('--pages', help='Reviewed country-specific website/ad case JSON')
    parser.add_argument('--output', required=True)
    parser.add_argument('--seeds', type=int, default=3)
    parser.add_argument('--workers', type=int, default=6)
    parser.add_argument('--interval', type=float, default=1)
    parser.add_argument('--proxy', help='Optional explicit HTTP proxy; TLS verification stays enabled')
    parser.add_argument('--retry-errors', action='store_true')
    parser.add_argument('--import-db', action='store_true')
    parser.add_argument('--import-only', action='store_true')
    parser.add_argument('--reparse', action='store_true', help='Reparse saved raw evidence without fetching')
    args = parser.parse_args()
    if not 1 <= args.workers <= 12 or not 1 <= args.seeds <= 10 or not .5 <= args.interval <= 60:
        parser.error('workers 1-12, seeds 1-10, interval 0.5-60 seconds')
    countries = sorted(PREFERENCES['countries']) if args.countries == 'all' else args.countries.lower().split(',')
    sources = args.sources.split(',')
    if not sources or any(s not in ('suggest', 'apple', 'google', 'pages') for s in sources):
        parser.error('sources must be suggest,apple,google or pages')
    pages = json.loads(Path(args.pages).read_text(encoding='utf-8')) if args.pages else []
    if args.reparse:
        result = reparse(args.output)
        print(json.dumps({k: v for k, v in result.items() if k != 'coverage'}, ensure_ascii=False, indent=2))
    elif not args.import_only:
        result = collect(make_jobs(countries, sources, args.seeds, pages), args.output,
                         workers=args.workers, fetcher=Fetcher(interval=args.interval, proxy=args.proxy),
                         retry_errors=args.retry_errors)
        print(json.dumps({k: v for k, v in result.items() if k != 'coverage'}, ensure_ascii=False, indent=2))
    if args.import_db or args.import_only:
        db = connect(load(), initialize=False)
        try:
            print(json.dumps(import_release(db, args.output), ensure_ascii=False))
        finally:
            db.close()


if __name__ == '__main__':
    main()
