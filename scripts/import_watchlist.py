"""Import already downloaded public Watchlist snapshots into the configured local DB."""
import argparse
import json
import sys
from appmon.config import load
from appmon.db import connect
from appmon.watchlist_import import read_snapshot, import_watchlists


def main():
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',default='config.toml')
    parser.add_argument('--google',required=True)
    parser.add_argument('--apple',required=True)
    args=parser.parse_args()
    snapshots, hashes={},{}
    for store in ('google','apple'):
        snapshots[store],hashes[store]=read_snapshot(getattr(args,store),store)
    cfg=load(args.config);db=connect(cfg)
    try:print(json.dumps(import_watchlists(db,cfg,snapshots,hashes),ensure_ascii=False,indent=2))
    finally:db.close()


if __name__=='__main__':main()
