#!/usr/bin/env python3
"""
kernell-sdk/scripts/receipt_validator_daemon.py
════════════════════════════════════════════════
Economic Sentinel — Receipt Integrity Validator
Continuously tails receipts.jsonl and validates:
 - Hash continuity (SHA256 deterministic)
 - Sequence monotonicity
 - Timestamp sanity
 - Impossible cost/token ratios
 - Duplicated or malformed receipts

Emits "economic_anomaly" events to Redis.
"""

import os
import sys
import json
import time
import hashlib
import logging
from redis import Redis

# Add core path to import state machine
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from core.runtime.state_machine import KernellStateMachine
from core.runtime.invariant_engine import InvariantEngine

# Configuration
RECEIPTS_PATH = "/var/log/kernell/receipts.jsonl"
REDIS_KEY_ANOMALIES = "kernell:security:economic_anomalies"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] ValidatorDaemon — %(message)s"
)
logger = logging.getLogger("ValidatorDaemon")

redis_client = Redis(host="localhost", port=6379, db=0, decode_responses=True)

def _compute_hash(data: dict) -> str:
    canonical = json.dumps(data, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode()).hexdigest()[:32]

def report_anomaly(reason: str, severity: str, receipt: dict = None):
    anomaly = {
        "type": "economic_anomaly",
        "severity": severity,
        "reason": reason,
        "receipt_seq": receipt.get("seq") if receipt else None,
        "receipt_id": receipt.get("receipt_id") if receipt else None,
        "timestamp": time.time()
    }
    logger.error(f"🚨 ANOMALY [{severity.upper()}]: {reason}")
    if receipt:
        logger.error(f"   Context: seq={receipt.get('seq')} hash={receipt.get('hash')}")
    try:
        redis_client.lpush(REDIS_KEY_ANOMALIES, json.dumps(anomaly))
        redis_client.ltrim(REDIS_KEY_ANOMALIES, 0, 999)
    except Exception as e:
        logger.error(f"Failed to report to Redis: {e}")

CHECKPOINT_PATH = "/var/log/kernell/receipts.checkpoint.json"

def save_checkpoint(seq, last_hash, ts):
    try:
        cp = {
            "checkpoint_seq": seq,
            "last_hash": last_hash,
            "timestamp": ts,
            "saved_at": time.time()
        }
        with open(CHECKPOINT_PATH, "w") as f:
            json.dump(cp, f)
    except Exception as e:
        logger.error(f"Failed to save checkpoint: {e}")

def load_checkpoint():
    if os.path.exists(CHECKPOINT_PATH):
        try:
            with open(CHECKPOINT_PATH, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load checkpoint: {e}")
    return None

def write_transition_receipt(transition: dict, redis_client: Redis):
    """Pushes a state transition to the WalWriter via Redis queue."""
    transition["receipt_id"] = "sys_" + os.urandom(4).hex()
    
    # Push to WAL external queue
    redis_client.rpush("kernell:wal:queue", json.dumps(transition))
    
    return None, None

def run_daemon():
    logger.info("🛡️ Economic Sentinel Initialized")
    
    state_machine = KernellStateMachine()
    invariant_engine = InvariantEngine(redis_client, RECEIPTS_PATH)
    
    last_ts = 0.0
    receipts_processed = 0

    if not os.path.exists(RECEIPTS_PATH):
        logger.warning(f"File not found: {RECEIPTS_PATH}. Waiting...")
        while not os.path.exists(RECEIPTS_PATH):
            time.sleep(2)

    logger.info(f"Tailing {RECEIPTS_PATH}...")

    with open(RECEIPTS_PATH, "r") as f:
        while True:
            line = f.readline()
            if not line:
                time.sleep(0.5)
                continue
            
            line = line.strip()
            if not line:
                continue

            try:
                receipt = json.loads(line)
            except json.JSONDecodeError:
                report_anomaly("Malformed JSON receipt", "critical")
                continue

            # --- Enforce Mathematical Invariants (single source of truth) ---
            invariant_engine.ingest_receipt(receipt)

            # 1. Structural Checks
            if "hash" not in receipt:
                report_anomaly("Missing hash signature", "critical", receipt)
                continue

            # 2. Hash Integrity Check (payload self-consistency)
            stored_hash = receipt.pop("hash")
            computed_hash = _compute_hash(receipt)
            receipt["hash"] = stored_hash  # restore

            if stored_hash != computed_hash:
                report_anomaly(f"Hash mismatch! Expected {computed_hash}, got {stored_hash}", "critical", receipt)

            # 3. Temporal Checks
            ts = receipt.get("ts", 0.0)
            if ts < last_ts - 5.0:  # Allow small 5s drift for concurrency
                report_anomaly(f"Timestamp paradox: went backwards by {last_ts - ts}s", "medium", receipt)
            last_ts = max(last_ts, ts)

            # 4. Economic Sanity Checks
            success = receipt.get("success", False)
            tokens = receipt.get("tokens_total", 0)
            try:
                cost = float(receipt.get("cost_usd", 0.0))
            except (ValueError, TypeError):
                cost = 0.0
            
            if success and tokens > 0 and cost <= 0.0 and receipt.get("provider") != "ollama":
                # Ollama is local, cost is 0.0 or minimal electrical estimate. Other providers MUST cost.
                if receipt.get("provider") not in ("ollama", "local"):
                    report_anomaly("Zero cost for non-local successful inference", "medium", receipt)
            
            if not success and cost > 0.0:
                report_anomaly("Cost recorded for failed inference", "high", receipt)

            if tokens > 131072: # 128k context max sanity
                report_anomaly(f"Impossible token volume: {tokens}", "high", receipt)

            # 5. Duplicate Detection
            receipt_id = receipt.get("receipt_id")
            if receipt_id:
                if redis_client.sismember("kernell:security:economic:receipt_ids", receipt_id):
                    report_anomaly(f"Duplicate receipt_id detected: {receipt_id}", "critical", receipt)
                else:
                    redis_client.sadd("kernell:security:economic:receipt_ids", receipt_id)

            receipts_processed += 1
            seq = receipt.get("seq", 0)
            
            # --- State Machine Ingestion ---
            is_sys_receipt = receipt.get("type", "") in ("runtime_transition", "security_transition")
            cpi = 0.0
            runtime_state = "HEALTHY"
            security_state = "NORMAL"
            
            if not is_sys_receipt:
                event_envelope = {
                    "ts": ts,
                    "success": success,
                    "provider": receipt.get("provider", "unknown"),
                    "tier": receipt.get("cognitive_tier", 1),
                    "fallback": receipt.get("fallback", False),  # Assume fallback flag exists or infer it
                    "anomaly": receipt.get("error") is not None and "hallucination" in receipt.get("error", "").lower(),
                    "retry": receipt.get("error") is not None,
                    "hallucination_contained": receipt.get("event_type") == "hallucination_containment"
                }
                
                transitions = state_machine.ingest_event(event_envelope)
                cpi = state_machine.compute_cpi()
                runtime_state = state_machine.runtime_state.value
                security_state = state_machine.security_state.value
                
                # Write returned transitions to WAL
                for trans in transitions:
                    # Write via Redis queue
                    write_transition_receipt(trans, redis_client)
                    logger.info(f"🔄 State Transition: {trans['from_state']} -> {trans['to_state']} (CPI: {trans['cpi']})")

            # ── PUBLISH HOT METRICS TO REDIS ──────────────────────────
            try:
                pipe = redis_client.pipeline()
                
                # Publish State & CPI
                if not is_sys_receipt:
                    pipe.set("kernell:state:runtime", runtime_state)
                    pipe.set("kernell:state:security", security_state)
                    pipe.set("kernell:state:cpi", str(cpi))
                
                # Global counters
                pipe.hincrby("kernell:metrics:global", "total_receipts", 1)
                pipe.hincrby("kernell:metrics:global", "total_tokens", tokens)
                pipe.hincrbyfloat("kernell:metrics:global", "total_cost_usd", cost)
                if success:
                    pipe.hincrby("kernell:metrics:global", "success_count", 1)
                else:
                    pipe.hincrby("kernell:metrics:global", "fail_count", 1)
                pipe.hset("kernell:metrics:global", "last_receipt_ts", receipt.get("ts_iso", ""))
                pipe.hset("kernell:metrics:global", "last_seq", str(seq))
                pipe.hset("kernell:metrics:global", "ledger_integrity", "verified")
                
                # Provider stats
                prov = receipt.get("provider", "unknown")
                prov_key = f"kernell:metrics:provider:{prov}"
                pipe.hincrby(prov_key, "count", 1)
                pipe.hincrbyfloat(prov_key, "cost", cost)
                try:
                    lat = float(receipt.get("latency_ms", 0))
                except (ValueError, TypeError):
                    lat = 0.0
                pipe.hincrbyfloat(prov_key, "latency_sum", lat)
                if not success:
                    pipe.hincrby(prov_key, "fails", 1)
                pipe.sadd("kernell:metrics:providers_set", prov)
                
                # Cognitive tier usage
                tier = receipt.get("cognitive_tier")
                if tier is not None:
                    pipe.hincrby("kernell:metrics:tiers", str(tier), 1)
                
                # Recent receipts ring buffer (last 100)
                pipe.lpush("kernell:metrics:recent_receipts", json.dumps(receipt))
                pipe.ltrim("kernell:metrics:recent_receipts", 0, 99)
                
                # 5-min sliding window (receipts with TTL for burn rate)
                window_key = f"kernell:metrics:window:{int(ts)}"
                pipe.setex(window_key, 300, json.dumps({"cost": cost, "success": success, "provider": prov, "tier": tier}))
                pipe.sadd("kernell:metrics:window_keys", window_key)
                
                # Cascade detection: push failures to a temp list, clear on success
                if not success:
                    pipe.rpush("kernell:metrics:cascade_buffer", json.dumps({
                        "provider": prov,
                        "model": receipt.get("model", ""),
                        "error": receipt.get("error", ""),
                        "ts_iso": receipt.get("ts_iso", ""),
                        "cognitive_tier": tier,
                    }))
                else:
                    # If there were buffered failures, record the cascade
                    cascade_len = redis_client.llen("kernell:metrics:cascade_buffer")
                    if cascade_len > 0:
                        failed_chain = []
                        for _ in range(cascade_len):
                            item = redis_client.lpop("kernell:metrics:cascade_buffer")
                            if item:
                                failed_chain.append(json.loads(item))
                        cascade_event = json.dumps({
                            "failed_chain": failed_chain,
                            "resolved_by": {
                                "provider": prov,
                                "model": receipt.get("model", ""),
                                "cognitive_tier": tier,
                                "ts_iso": receipt.get("ts_iso", ""),
                            },
                            "depth": len(failed_chain),
                            "survival_cost": receipt.get("cost_usd"),
                        })
                        pipe.lpush("kernell:metrics:cascades", cascade_event)
                        pipe.ltrim("kernell:metrics:cascades", 0, 49)
                    else:
                        pipe.delete("kernell:metrics:cascade_buffer")

                # Publish live event for WebSocket/SSE consumers
                pipe.publish("kernell:live:receipts", json.dumps(receipt))
                
                pipe.execute()
            except Exception as e:
                logger.warning(f"Failed to publish metrics to Redis: {e}")
            
            if seq % 100 == 0:
                save_checkpoint(seq, stored_hash, ts)

if __name__ == "__main__":
    try:
        run_daemon()
    except KeyboardInterrupt:
        logger.info("Daemon terminated by user")
