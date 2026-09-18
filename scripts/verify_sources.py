"""Verify every tracked upstream source file against the pinned content hashes."""
import hashlib
import json
import os
from pathlib import Path
root = Path(__file__).resolve().parents[1]
lock = json.loads((root / "UPSTREAM.lock.json").read_text())
for source in lock["sources"]:
    for name, expected in source["sha256"].items():
        path = root / source["directory"] / name
        content = os.readlink(path).encode() if path.is_symlink() else path.read_bytes()
        actual = hashlib.sha256(content).hexdigest()
        if actual != expected:
            raise SystemExit(f"Source mismatch: {path}")
    print(f"Verified {source['file_count']} files at {source['revision']} ({source['directory']})")
