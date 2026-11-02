"""Recheck the delivered compressed datasets without extracting large TSV files."""
import csv
import hashlib
import json
import lzma
from collections import Counter
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "docs/data-manifest.json").read_text(encoding="utf-8"))
    for item in manifest:
        path = root / "data/raw" / item["file"]
        if path.stat().st_size != item["bytes"]:
            raise ValueError(f"File size mismatch: {path.name}")
        sha = hashlib.sha256()
        blob = hashlib.sha1(f"blob {item['bytes']}\0".encode())
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                sha.update(chunk)
                blob.update(chunk)
        if sha.hexdigest() != item["sha256"] or blob.hexdigest() != item["git_blob"]:
            raise ValueError(f"Checksum mismatch: {path.name}")
        counts = Counter()
        with lzma.open(path, "rt", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter="\t")
            if reader.fieldnames != item["columns"]:
                raise ValueError(f"Header mismatch: {path.name}")
            for row in reader:
                counts[row["store"]] += 1
        if dict(counts) != item["by_store"] or sum(counts.values()) != item["rows"]:
            raise ValueError(f"Row count mismatch: {path.name}")
        print(json.dumps({"file": path.name, "rows": sum(counts.values()), "sha256": sha.hexdigest(), "ok": True}))


if __name__ == "__main__":
    main()
