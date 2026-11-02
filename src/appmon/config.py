from pathlib import Path
import re
import os
import tomllib
from dotenv import load_dotenv


def load(path="config.toml"):
    path = Path(path).resolve()
    load_dotenv(path.parent / ".env", override=False)
    with path.open("rb") as stream:
        cfg = tomllib.load(stream)
    cfg["data_dir"] = (path.parent / cfg.get("data_dir", "data")).resolve()
    cfg["countries"] = list(dict.fromkeys(c.lower() for c in cfg["countries"]))
    if not cfg["countries"] or any(not re.fullmatch(r"[a-z]{2}", c) for c in cfg["countries"]):
        raise ValueError("countries must contain two-letter storefront codes")
    if not re.fullmatch(r"[a-z]{2}(?:[-_][A-Za-z]{2})?", cfg["language"]):
        raise ValueError("Invalid language")
    for key in ("refresh_hours", "missing_recheck_hours", "lease_seconds", "timeout_seconds",
                "google_interval_seconds", "apple_interval_seconds", "batch_size",
                "missing_confirmations", "discovery_hours", "seed_sync_hours"):
        if cfg[key] <= 0:
            raise ValueError(f"{key} must be positive")
    if cfg["lease_seconds"] < cfg["timeout_seconds"] * 2:
        raise ValueError("lease_seconds must be at least twice timeout_seconds")
    cfg["data_dir"].mkdir(parents=True, exist_ok=True)
    cfg["mysql"] = {
        "host": os.environ.get("MYSQL_HOST", "127.0.0.1"),
        "port": int(os.environ.get("MYSQL_PORT", "3307")),
        "user": os.environ.get("MYSQL_USER", "appmon"),
        "password": os.environ.get("MYSQL_PASSWORD", ""),
        "database": os.environ.get("MYSQL_DATABASE", "appstore_applications_monitoring"),
        "connect_timeout": 15, "read_timeout": 300, "write_timeout": 300,
    }
    return cfg
