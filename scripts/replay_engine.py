#!/usr/bin/env python3
"""
kernell-sdk/scripts/replay_engine.py
════════════════════════════════════
Kernell OS Receipt Replay Engine

Provides causal traceability and historical reconstruction of economic 
and cognitive incidents.
"""

import os
import sys
import json
import uuid
import hashlib
import argparse
import subprocess
from datetime import datetime, timezone

# Configuration
ENGINE_VERSION = "1.2.0"
DEFAULT_RECEIPTS_PATH = "/var/log/kernell/receipts.jsonl"
RECEIPTS_PATH = os.environ.get("KERNELL_RECEIPTS_PATH_OVERRIDE", DEFAULT_RECEIPTS_PATH)

def parse_args():
    parser = argparse.ArgumentParser(description="Kernell OS Replay Engine - Causal Reconstruction")
    parser.add_argument("--snapshot", type=str, help="Path to .zst or .gz snapshot archive")
    parser.add_argument("--from-seq", type=int, help="Start replay from sequence number")
    parser.add_argument("--to-seq", type=int, help="End replay at sequence number")
    parser.add_argument("--agent", type=str, help="Filter by agent ID")
    parser.add_argument("--provider", type=str, help="Filter by provider")
    parser.add_argument("--incident", type=str, help="Reconstruct specific incident (e.g. retry_storm, fallback_cascade)")
    parser.add_argument("--export", action="store_true", help="Output signed replay session as JSON")
    return parser.parse_args()

def get_snapshot_hash(snapshot_path):
    manifest_path = snapshot_path.replace(".zst", ".manifest.json").replace(".gz", ".manifest.json")
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path, "r") as f:
                return json.load(f).get("sha256", "unknown")
        except: pass
    return "unknown_or_live"

def analyze_incident(incident_type, receipts):
    """
    Detects and reconstructs complex incidents from a sequence of receipts.
    Returns the analyzed events for signature hashing.
    """
    events = []
    if incident_type == "retry_storm":
        storm_count = 0
        for r in receipts:
            if not r.get("success", True):
                storm_count += 1
                events.append(f"[{r.get('ts_iso')}] ⚠️ Seq {r.get('seq')}: {r.get('agent_id')} -> {r.get('provider')} ({r.get('model')}) FAILED ({r.get('error')})")
            else:
                if storm_count > 0:
                    events.append(f"[{r.get('ts_iso')}] ✅ Seq {r.get('seq')}: Resolved after {storm_count} retries via {r.get('provider')} ({r.get('model')}). Cost: ${r.get('cost_usd')}")
                storm_count = 0
    
    elif incident_type == "fallback_cascade":
        cascade_chain = []
        for r in receipts:
            if not r.get("success", True):
                cascade_chain.append(r.get("provider"))
            else:
                if cascade_chain:
                    events.append(f"[{r.get('ts_iso')}] 📉 Fallback Cascade Detected:")
                    events.append(f"    Path: {' -> '.join(cascade_chain)} -> {r.get('provider')} [SUCCESS]")
                    events.append(f"    Survival Cost: ${r.get('cost_usd')}")
                cascade_chain = []
    
    return events

def stream_receipts(snapshot_path):
    """Generator that yields lines from either a live file or compressed snapshot."""
    if snapshot_path:
        if snapshot_path.endswith('.zst'):
            proc = subprocess.Popen(["zstd", "-dc", snapshot_path], stdout=subprocess.PIPE, text=True)
            for line in proc.stdout:
                yield line
            proc.wait()
        elif snapshot_path.endswith('.gz'):
            proc = subprocess.Popen(["gzip", "-dc", snapshot_path], stdout=subprocess.PIPE, text=True)
            for line in proc.stdout:
                yield line
            proc.wait()
        else:
            with open(snapshot_path, "r") as f:
                for line in f:
                    yield line
    else:
        if not os.path.exists(RECEIPTS_PATH):
            print(f"❌ Ledger not found at {RECEIPTS_PATH}")
            sys.exit(1)
        with open(RECEIPTS_PATH, "r") as f:
            for line in f:
                yield line

def replay():
    args = parse_args()
    
    filtered_receipts = []
    total_cost = 0.0
    total_tokens = 0

    if not args.export:
        print("⏪ Kernell OS Replay Engine")
        print(f"Reading ledger: {args.snapshot if args.snapshot else RECEIPTS_PATH}")
        print("-" * 60)

    for line in stream_receipts(args.snapshot):
        if not line.strip(): continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        
        seq = r.get("seq")
        if args.from_seq and seq < args.from_seq: continue
        if args.to_seq and seq > args.to_seq: continue
        if args.agent and r.get("agent_id") != args.agent: continue
        if args.provider and r.get("provider") != args.provider: continue
        
        filtered_receipts.append(r)
        
        # Output progressively if not exporting JSON
        if not args.incident and not args.export:
            status = "✅" if r.get("success") else "❌"
            cost_str = r.get("cost_usd", "0.0")
            if isinstance(cost_str, float): cost_str = f"{cost_str:.8f}"
            print(f"{status} [{r.get('ts_iso')}] Seq: {seq:<4} | {r.get('agent_id'):<12} | {r.get('provider'):<10} | {r.get('model'):<20} | ${cost_str}")
        
        if r.get("success"):
            try: total_cost += float(r.get("cost_usd", 0.0))
            except ValueError: pass
            total_tokens += r.get("tokens_total", 0)

    analysis_events = []
    if args.incident:
        analysis_events = analyze_incident(args.incident, filtered_receipts)
        if not args.export:
            print(f"\n🔍 Analyzing Incident: {args.incident.upper()}")
            print("=" * 60)
            for evt in analysis_events:
                print(evt)

    # ─── Signed Replay Session Artifact ───
    
    # Compute deterministic result hash
    result_payload = {
        "matched_count": len(filtered_receipts),
        "total_cost": total_cost,
        "total_tokens": total_tokens,
        "analysis_events": analysis_events,
        "first_seq": filtered_receipts[0].get("seq") if filtered_receipts else None,
        "last_seq": filtered_receipts[-1].get("seq") if filtered_receipts else None
    }
    canonical_result = json.dumps(result_payload, sort_keys=True, separators=(',', ':'))
    result_hash = hashlib.sha256(canonical_result.encode()).hexdigest()

    # Compute deterministic session hash for auditability
    filters_payload = {
        "incident": args.incident,
        "from_seq": args.from_seq,
        "to_seq": args.to_seq,
        "agent": args.agent,
        "provider": args.provider
    }
    canonical_filters = json.dumps(filters_payload, sort_keys=True, separators=(',', ':'))
    snapshot_hash = get_snapshot_hash(args.snapshot) if args.snapshot else "live"
    
    deterministic_session_hash = hashlib.sha256(
        (snapshot_hash + canonical_filters + result_hash).encode()
    ).hexdigest()

    session_artifact = {
        "replay_session_id": str(uuid.uuid4()),
        "deterministic_session_hash": deterministic_session_hash,
        "snapshot_hash": snapshot_hash,
        "filters": filters_payload,
        "engine_version": ENGINE_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "result_hash": result_hash
    }

    if args.export:
        print(json.dumps(session_artifact, indent=2))
    else:
        if not args.incident:
            print("-" * 60)
            print(f"📊 SUMMARY: {len(filtered_receipts)} receipts matched.")
            print(f"   Total Cost:   ${total_cost:.8f}")
            print(f"   Total Tokens: {total_tokens}")
        
        print("-" * 60)
        print("🔐 Signed Replay Session Context:")
        print(json.dumps(session_artifact, indent=2))

if __name__ == "__main__":
    replay()
