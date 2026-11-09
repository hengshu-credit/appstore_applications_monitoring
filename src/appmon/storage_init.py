"""Ordered initialization for OceanBase and authenticated SeaweedFS."""
import sys
import time
from .config import load
from .db import Database, connect
from .objects import for_config


def main():
    cfg=load()
    if '--application' in sys.argv:
        for attempt in range(60):
            try:for_config(cfg).initialize();break
            except Exception:
                if attempt==59:raise
                time.sleep(2)
        db=connect(cfg,initialize=True);db.close()
    else:
        db=Database(cfg['oceanbase'])
        try:
            db.execute('CREATE DATABASE IF NOT EXISTS seaweedfs CHARACTER SET utf8mb4 COLLATE utf8mb4_bin')
            db.execute('''CREATE TABLE IF NOT EXISTS seaweedfs.filemeta (
                dirhash BIGINT NOT NULL,name VARCHAR(766) NOT NULL,
                directory TEXT NOT NULL,meta LONGBLOB,PRIMARY KEY(dirhash,name))
                DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin''')
        finally:db.close()


if __name__=='__main__':main()
