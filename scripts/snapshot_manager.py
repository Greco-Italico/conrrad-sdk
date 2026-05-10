#!/usr/bin/env python3
"""
kernell-sdk/scripts/snapshot_manager.py
════════════════════════════════════════════════
Archivist — Immutable Receipt Snapshotting
Rotates the live receipts.jsonl into compressed,
cryptographically chained Zstandard (.zst) archives.

Produces two files per snapshot:
  - receipts-{ts}.seq{start}-{end}.zst (Compressed Data)
  - receipts-{ts}.seq{start}-{end}.manifest.json (Metadata)
"""

import os
import json
import time
import glob
import shutil
import hashlib
import subprocess
from datetime import datetime, timezone

RECEIPTS_PATH = "/var/log/kernell/receipts.jsonl"
SNAPSHOTS_DIR = "/var/log/kernell/snapshots"

def _compute_hash(data: dict) -> str:
    canonical = json.dumps(data, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode()).hexdigest()

def get_latest_manifest() -> dict:
    """Finds the most recent snapshot manifest to get the parent hash."""
    manifests = glob.glob(os.path.join(SNAPSHOTS_DIR, "*.manifest.json"))
    valid_manifests = []
    for m in manifests:
        try:
            with open(m, "r") as f:
                data = json.load(f)
                valid_manifests.append((data.get("seq_end", 0), data))
        except Exception:
            pass
    
    if not valid_manifests:
        return None
        
    valid_manifests.sort(key=lambda x: x[0])
    return valid_manifests[-1][1]

def create_snapshot():
    if not os.path.exists(SNAPSHOTS_DIR):
        os.makedirs(SNAPSHOTS_DIR, exist_ok=True)

    if not os.path.exists(RECEIPTS_PATH):
        print("No live receipts file found.")
        return

    file_size = os.path.getsize(RECEIPTS_PATH)
    if file_size == 0:
        print("Live receipts file is empty.")
        return

    # Atomically move the live file
    tmp_path = RECEIPTS_PATH + ".tmp"
    shutil.move(RECEIPTS_PATH, tmp_path)
    
    # We must ensure there is an empty file for tailing programs if they aren't resilient to recreate,
    # but python 'a' mode creates if missing.
    open(RECEIPTS_PATH, "a").close()

    # Parse the moved file to extract metadata
    seq_start = None
    seq_end = None
    count = 0
    payload_hash_obj = hashlib.sha256()

    with open(tmp_path, "rb") as f:
        for line in f:
            if not line.strip():
                continue
            payload_hash_obj.update(line)
            try:
                receipt = json.loads(line)
                seq = int(receipt.get("seq", 0))
                if seq_start is None:
                    seq_start = seq
                seq_end = seq
                count += 1
            except Exception:
                pass

    if count == 0:
        print("No valid receipts found. Reverting.")
        shutil.move(tmp_path, RECEIPTS_PATH)
        return

    payload_hash = payload_hash_obj.hexdigest()
    
    # Chain with previous snapshot
    parent_manifest = get_latest_manifest()
    parent_hash = parent_manifest.get("sha256", "genesis") if parent_manifest else "genesis"
    
    # Combine parent + payload for chained hash
    chained_hash = hashlib.sha256((parent_hash + payload_hash).encode()).hexdigest()

    # Formulate filenames
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    base_name = f"receipts-{ts}.seq{seq_start:06d}-{seq_end:06d}"
    
    zst_path = os.path.join(SNAPSHOTS_DIR, base_name + ".zst")
    manifest_path = os.path.join(SNAPSHOTS_DIR, base_name + ".manifest.json")

    # Compress the file using zstd
    try:
        subprocess.run(["zstd", "-q", "-19", "-T0", tmp_path, "-o", zst_path], check=True)
    except FileNotFoundError:
        print("zstd not found! Falling back to gzip (rename to .gz).")
        zst_path = os.path.join(SNAPSHOTS_DIR, base_name + ".gz")
        subprocess.run(["gzip", "-c", tmp_path], stdout=open(zst_path, "wb"), check=True)
    except subprocess.CalledProcessError as e:
        print(f"Compression failed: {e}")
        shutil.move(tmp_path, RECEIPTS_PATH)
        return

    # Delete tmp file
    os.remove(tmp_path)

    # Generate Manifest
    manifest = {
        "snapshot_id": base_name,
        "schema_version": 1,
        "seq_start": seq_start,
        "seq_end": seq_end,
        "receipt_count": count,
        "payload_hash": payload_hash,
        "parent_snapshot_hash": parent_hash,
        "sha256": chained_hash,
        "created_at": ts
    }

    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"✅ Snapshot created successfully:")
    print(f"   Archive:  {zst_path}")
    print(f"   Manifest: {manifest_path}")
    print(f"   Hash:     {chained_hash[:16]}... (Parent: {parent_hash[:8]}...)")

if __name__ == "__main__":
    create_snapshot()
