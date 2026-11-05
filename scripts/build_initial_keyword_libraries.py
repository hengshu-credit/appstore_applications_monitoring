"""Import local authorized reports and export a versioned keyword/ASO library."""
import argparse
import json
import sys
from pathlib import Path
from appmon.config import load
from appmon.db import connect
from appmon.keyword_library import build


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder')
    parser.add_argument('--bindings', required=True)
    parser.add_argument('--output', default='data/keyword_libraries/v1')
    args = parser.parse_args()
    cfg = load()
    db = connect(cfg)
    try:
        result = build(cfg, db, args.folder, json.loads(Path(args.bindings).read_text(encoding='utf-8')), args.output)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        db.close()


if __name__ == '__main__':
    main()
