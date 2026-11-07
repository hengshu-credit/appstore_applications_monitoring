from pathlib import Path
import re
import os
import math
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
    cfg.setdefault('google_rpc_batch_size', 20)
    cfg.setdefault('collector_concurrency',4)
    cfg.setdefault('collection_batch_size',2000)
    for key,low,high in [('collector_concurrency',1,8),('collection_batch_size',50,2000)]:
        if type(cfg[key]) is not int or not low<=cfg[key]<=high:
            raise ValueError(f'{key} must be an integer from {low} to {high}')
    if type(cfg['google_rpc_batch_size']) is not int or not 1<=cfg['google_rpc_batch_size']<=20:
        raise ValueError('google_rpc_batch_size must be an integer from 1 to 20')
    cfg.setdefault('apple_lookup_batch_size', 50)
    if type(cfg['apple_lookup_batch_size']) is not int or not 1<=cfg['apple_lookup_batch_size']<=50:
        raise ValueError('apple_lookup_batch_size must be an integer from 1 to 50')
    cfg.setdefault('global_interval_seconds', 5)
    cfg.setdefault('cooldown_seconds', 900)
    for key, low, high in [('global_interval_seconds',0.1,3600),('cooldown_seconds',60,86400)]:
        value=cfg[key]
        if type(value) not in (int,float) or not math.isfinite(value) or not low<=value<=high:
            raise ValueError(f'{key} must be between {low} and {high}')
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
