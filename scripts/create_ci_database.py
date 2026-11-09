"""Create the disposable CI database; never creates or alters a production database."""
import os
from appmon.config import load
from appmon.db import Database

cfg = load()
if os.environ.get("CI") != "true" or cfg["oceanbase"]["database"] != "appmon_ci":
    raise RuntimeError("This helper is restricted to the appmon_ci database in CI")
options = dict(cfg["oceanbase"])
options.pop("database")
db = Database(options)
try:
    db.execute("CREATE DATABASE IF NOT EXISTS appmon_ci CHARACTER SET utf8mb4 COLLATE utf8mb4_bin")
finally:
    db.close()
