"""
Kernell Hardening Pack — Production-Grade Protection Layer

5 critical hardening modules:
1. External Idempotency — Dedupe outbound effects (webhooks, callbacks)
2. HOLD Expiration — Auto-release stale holds to prevent liquidity lock
3. Rate Limiting — Per-wallet, per-endpoint throttling
4. Export Endpoints — Auditable ledger/execution/reconciliation exports
5. WAL Durability — Redis AOF strategy + snapshot policy

Invariant: None of these modules mutate existing data.
They only ADD protective layers on top of the existing stack.
"""

import time
import json
import hashlib
from typing import Optional, Dict, List, Callable
from functools import wraps


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. External Idempotency — Dedupe outbound effects
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class IdempotencyGuard:
    """
    Prevents duplicate external effects (webhooks, payments, callbacks).
    
    Flow:
      1. Before executing external effect, check if idempotency_key exists
      2. If exists → return cached result (no re-execution)
      3. If new → execute, store result with TTL
    
    Storage: Redis hash with TTL for automatic cleanup.
    """

    KEY_PREFIX = "kernell:idempotency:"
    DEFAULT_TTL = 86400  # 24 hours

    def __init__(self, redis_client, ttl: int = None):
        self.r = redis_client
        self.ttl = ttl or self.DEFAULT_TTL

    def _key(self, idempotency_key: str) -> str:
        return f"{self.KEY_PREFIX}{idempotency_key}"

    def check(self, idempotency_key: str) -> Optional[dict]:
        """Check if this key was already processed. Returns cached result or None."""
        raw = self.r.get(self._key(idempotency_key))
        if raw:
            return json.loads(raw)
        return None

    def commit(self, idempotency_key: str, result: dict):
        """Store the result of a processed request."""
        key = self._key(idempotency_key)
        self.r.set(key, json.dumps(result))
        self.r.expire(key, self.ttl)

    def execute_once(self, idempotency_key: str, fn: Callable, *args, **kwargs) -> dict:
        """Execute fn exactly once for this idempotency_key."""
        cached = self.check(idempotency_key)
        if cached:
            cached["_idempotent"] = True
            return cached

        result = fn(*args, **kwargs)
        self.commit(idempotency_key, result)
        result["_idempotent"] = False
        return result

    def generate_key(self, *parts) -> str:
        """Generate deterministic idempotency key from components."""
        payload = ":".join(str(p) for p in parts)
        return hashlib.sha256(payload.encode()).hexdigest()[:32]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. HOLD Expiration — Auto-release stale holds
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class HoldExpirationEngine:
    """
    Prevents liquidity lock from abandoned HOLDs.

    - Tracks all active holds with timestamps
    - Background sweep releases holds older than TTL
    - Released holds generate RELEASE ledger entries (never delete)
    """

    HOLDS_KEY = "kernell:active_holds"
    DEFAULT_TTL = 3600  # 1 hour

    def __init__(self, redis_client, settlement_engine, ttl: int = None):
        self.r = redis_client
        self.settlement = settlement_engine
        self.ttl = ttl or self.DEFAULT_TTL

    def register_hold(self, purchase_id: str, account_id: str,
                      amount: int, created_at: float = None):
        """Register an active hold for expiration tracking."""
        hold_data = json.dumps({
            "purchase_id": purchase_id,
            "account_id": account_id,
            "amount": amount,
            "created_at": created_at or time.time()
        })
        self.r.hset(self.HOLDS_KEY, purchase_id, hold_data)

    def release_hold(self, purchase_id: str):
        """Mark a hold as settled (remove from tracking)."""
        self.r.hdel(self.HOLDS_KEY, purchase_id)

    def sweep_expired(self) -> List[dict]:
        """Sweep and auto-release all expired holds. Returns list of released holds."""
        now = time.time()
        released = []
        all_holds = self.r.hgetall(self.HOLDS_KEY)

        for purchase_id, raw in all_holds.items():
            hold = json.loads(raw)
            age = now - hold["created_at"]

            if age > self.ttl:
                # Auto-release via settlement engine
                try:
                    self.settlement.release(
                        hold["account_id"],
                        hold["amount"],
                        hold["purchase_id"]
                    )
                    self.r.hdel(self.HOLDS_KEY, purchase_id)
                    hold["expired_at"] = now
                    hold["age_seconds"] = age
                    released.append(hold)
                except Exception as e:
                    hold["error"] = str(e)
                    released.append(hold)

        return released

    def get_active_holds(self) -> List[dict]:
        """List all currently active holds."""
        holds = []
        for pid, raw in self.r.hgetall(self.HOLDS_KEY).items():
            hold = json.loads(raw)
            hold["age_seconds"] = time.time() - hold["created_at"]
            holds.append(hold)
        return holds


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. Rate Limiting — Per-wallet, per-endpoint throttling
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class RateLimitError(Exception):
    def __init__(self, message: str, retry_after: int = 0):
        super().__init__(message)
        self.retry_after = retry_after


class RateLimiter:
    """
    Sliding window rate limiter using Redis sorted sets.
    
    Supports:
      - Per-account limits (wallet protection)
      - Per-endpoint limits (API protection)
      - Global limits (system protection)
    """

    KEY_PREFIX = "kernell:ratelimit:"

    def __init__(self, redis_client):
        self.r = redis_client
        self.limits = {
            "pay:hold":     {"max": 10,  "window": 60},    # 10 holds/min
            "pay:capture":  {"max": 10,  "window": 60},    # 10 captures/min
            "pay:credit":   {"max": 5,   "window": 60},    # 5 credits/min
            "marketplace:purchase": {"max": 20, "window": 60},  # 20 purchases/min
            "default":      {"max": 100, "window": 60},    # 100 req/min default
        }

    def _key(self, identifier: str, endpoint: str) -> str:
        return f"{self.KEY_PREFIX}{endpoint}:{identifier}"

    def check(self, identifier: str, endpoint: str = "default") -> bool:
        """Check if request is allowed. Raises RateLimitError if exceeded."""
        config = self.limits.get(endpoint, self.limits["default"])
        key = self._key(identifier, endpoint)
        now = time.time()
        window_start = now - config["window"]

        # Clean old entries
        self.r.zremrangebyscore(key, "-inf", window_start)

        # Count current window
        count = self.r.zcard(key)
        if count >= config["max"]:
            # Calculate retry-after
            oldest = self.r.zrange(key, 0, 0, withscores=True)
            retry_after = int(config["window"] - (now - oldest[0][1])) if oldest else config["window"]
            raise RateLimitError(
                f"Rate limit exceeded for {endpoint}: {count}/{config['max']} per {config['window']}s",
                retry_after=max(1, retry_after)
            )

        # Record this request
        self.r.zadd(key, {f"{now}": now})
        self.r.expire(key, config["window"] + 10)  # TTL slightly beyond window

        return True

    def get_usage(self, identifier: str, endpoint: str = "default") -> dict:
        """Get current usage stats for an identifier."""
        config = self.limits.get(endpoint, self.limits["default"])
        key = self._key(identifier, endpoint)
        now = time.time()

        self.r.zremrangebyscore(key, "-inf", now - config["window"])
        count = self.r.zcard(key)

        return {
            "endpoint": endpoint,
            "identifier": identifier,
            "current": count,
            "limit": config["max"],
            "window_seconds": config["window"],
            "remaining": max(0, config["max"] - count)
        }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. Export Engine — Auditable data exports
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class ExportEngine:
    """
    Generates auditable exports of:
      - Ledger entries (full or filtered)
      - Execution history (WAL events for a request)
      - Reconciliation decisions
    
    Format: JSON-lines for streaming, with cryptographic proof.
    """

    def __init__(self, redis_client):
        self.r = redis_client

    def export_ledger(self, account_id: str = None,
                      since: float = None) -> dict:
        """Export ledger entries with integrity proof."""
        entries = self.r.xrange("kernell:ledger")
        results = []

        for stream_id, entry in entries:
            if account_id and entry.get("account_id") != account_id:
                continue
            ts = float(entry.get("ts", 0))
            if since and ts < since:
                continue
            entry["stream_id"] = stream_id
            results.append(entry)

        # Compute integrity hash over all entries
        content = json.dumps(results, sort_keys=True)
        integrity = hashlib.sha256(content.encode()).hexdigest()

        return {
            "type": "ledger_export",
            "account_id": account_id or "ALL",
            "entries": results,
            "count": len(results),
            "exported_at": time.time(),
            "integrity_hash": integrity
        }

    def export_execution(self, request_id: str) -> dict:
        """Export full execution WAL for a specific request."""
        entries = self.r.xrange("kernell:wal")
        events = []

        for stream_id, entry in entries:
            rid = entry.get("request_id", "")
            if request_id in rid:
                entry["stream_id"] = stream_id
                events.append(entry)

        content = json.dumps(events, sort_keys=True)
        integrity = hashlib.sha256(content.encode()).hexdigest()

        return {
            "type": "execution_export",
            "request_id": request_id,
            "events": events,
            "count": len(events),
            "exported_at": time.time(),
            "integrity_hash": integrity
        }

    def export_reconciliation(self, request_id: str) -> dict:
        """Export reconciliation decision trail."""
        # Pull WAL events that are reconciliation-related
        entries = self.r.xrange("kernell:wal")
        recon_events = []

        for stream_id, entry in entries:
            rid = entry.get("request_id", "")
            evt = entry.get("event", "")
            if request_id in rid and evt in ("FREEZE", "FORCE_SYNC", "COMPENSATE"):
                entry["stream_id"] = stream_id
                recon_events.append(entry)

        content = json.dumps(recon_events, sort_keys=True)
        integrity = hashlib.sha256(content.encode()).hexdigest()

        return {
            "type": "reconciliation_export",
            "request_id": request_id,
            "decisions": recon_events,
            "count": len(recon_events),
            "exported_at": time.time(),
            "integrity_hash": integrity
        }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. WAL Durability Config
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class DurabilityManager:
    """
    Configures Redis for production-grade durability.
    
    Recommended settings:
      - appendonly yes
      - appendfsync always (every write flushed to disk)
      - save 60 1000 (RDB snapshot every 60s if 1000+ changes)
    """

    def __init__(self, redis_client):
        self.r = redis_client

    def get_config(self) -> dict:
        """Read current Redis persistence configuration."""
        try:
            aof = self.r.config_get("appendonly")
            fsync = self.r.config_get("appendfsync")
            save = self.r.config_get("save")
            return {
                "appendonly": aof.get("appendonly", "unknown"),
                "appendfsync": fsync.get("appendfsync", "unknown"),
                "save": save.get("save", "unknown"),
                "status": "connected"
            }
        except Exception as e:
            return {"status": "error", "detail": str(e)}

    def harden(self) -> dict:
        """Apply production durability settings."""
        try:
            self.r.config_set("appendonly", "yes")
            self.r.config_set("appendfsync", "always")
            self.r.config_set("save", "60 1000 300 100")
            return {
                "status": "hardened",
                "appendonly": "yes",
                "appendfsync": "always",
                "save": "60 1000 300 100"
            }
        except Exception as e:
            return {"status": "error", "detail": str(e)}

    def verify(self) -> dict:
        """Verify durability settings meet production requirements."""
        config = self.get_config()
        issues = []

        if config.get("appendonly") != "yes":
            issues.append("AOF disabled — data loss risk on crash")
        if config.get("appendfsync") not in ("always", "everysec"):
            issues.append("appendfsync not strict — possible write loss")

        return {
            "config": config,
            "issues": issues,
            "production_ready": len(issues) == 0
        }
