#!/usr/bin/python3
"""Verify this small evidence bundle and its report without invoking the product."""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parent
repo = root.parents[2]
manifest = json.loads((root / "manifest.json").read_text())
for record in manifest["artifacts"]:
    path = root / record["file"]
    data = path.read_bytes()
    assert len(data) == record["bytes"], f"size mismatch: {path}"
    assert hashlib.sha256(data).hexdigest() == record["sha256"], f"hash mismatch: {path}"
record = manifest["report"]
data = (repo / record["file"]).read_bytes()
assert len(data) == record["bytes"], "report size mismatch"
assert hashlib.sha256(data).hexdigest() == record["sha256"], "report hash mismatch"
print(f"Verified {len(manifest['artifacts'])} evidence files and report; status={manifest['status']}")
