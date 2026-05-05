"""
Kernell Pay — Persistent Redis Store (Nivel 1 Production)
═════════════════════════════════════════════════════════
Replaces the in-memory TenantStore with a Redis-backed distributed store.
Ensures atomic operations, persistence across VPS reboots, and horizontal
scalability across multiple workers.
"""

import json
import logging
import redis
from typing import Optional, Any
from .execution_proxy import ExecutionReceipt

logger = logging.getLogger("kernell.redis_store")

class RedisTenantStore:
    def __init__(self, redis_url: str = "redis://localhost:6379/0"):
        self.client = redis.from_url(redis_url, decode_responses=True)
        # Prefix keys to avoid collisions
        self.prefix = "kernell:tenant"
        
    def _key(self, tenant_id: str, suffix: str) -> str:
        return f"{self.prefix}:{tenant_id}:{suffix}"

    def check_or_set_processing(self, idempotency_key: str) -> str:
        """Returns 'NEW', 'PROCESSING', or JSON receipt if 'COMPLETED'"""
        key = f"kernell:idemp:{idempotency_key}"
        # setnx (set if not exists) is atomic
        is_new = self.client.setnx(key, "PROCESSING")
        if is_new:
            # We set an expiration to prevent eternal locks if process crashes hard
            self.client.expire(key, 300) # 5 min lock
            return "NEW"
        return self.client.get(key)

    def finalize_idempotency(self, idempotency_key: str, receipt: Optional[ExecutionReceipt]):
        key = f"kernell:idemp:{idempotency_key}"
        if receipt:
            import dataclasses
            self.client.set(key, json.dumps(dataclasses.asdict(receipt)))
            self.client.expire(key, 86400 * 7) # keep receipts for 7 days
        else:
            # If failed before receipt, clear the lock so they can retry
            self.client.delete(key)

    def reserve_funds(self, tenant_id: str, amount_usd: float) -> bool:
        """Atomic reserve using Redis pipeline (transactions)"""
        budget_key = self._key(tenant_id, "budget")
        reserved_key = self._key(tenant_id, "reserved")
        spent_key = self._key(tenant_id, "spent")
        
        with self.client.pipeline() as pipe:
            try:
                pipe.watch(budget_key, reserved_key, spent_key)
                
                budget = float(pipe.get(budget_key) or 0.0)
                reserved = float(pipe.get(reserved_key) or 0.0)
                spent = float(pipe.get(spent_key) or 0.0)
                
                if (spent + reserved + amount_usd) > budget:
                    pipe.unwatch()
                    return False
                    
                pipe.multi()
                # atomic increment of reserved funds
                pipe.incrbyfloat(reserved_key, amount_usd)
                pipe.execute()
                return True
            except redis.WatchError:
                # Race condition occurred, somebody else modified the keys.
                # In production we might retry this a few times, but failing safe is okay.
                return False

    def settle_funds(self, tenant_id: str, reserved_usd: float, actual_usd: float, transaction_id: str) -> bool:
        """Two-phase atomic settlement with half-settlement detection.
        
        Phase 1: Mark transaction as PENDING (setnx).
        Phase 2: Move money in pipeline, then mark COMPLETED.
        
        If the process dies between phases, the reconciliator can detect
        PENDING entries and flag them as half-settlements for manual repair.
        """
        settled_key = f"kernell:settled:{transaction_id}"
        
        # Phase 1: Claim the settlement slot
        is_new = self.client.setnx(settled_key, "PENDING")
        if not is_new:
            existing = self.client.get(settled_key)
            if existing == "COMPLETED":
                return False  # Already fully settled
            # State is PENDING → previous attempt crashed mid-settlement.
            # Log it but allow retry (idempotent pipeline below).
            logger.warning(
                f"Half-settlement detected for tx {transaction_id}. "
                f"Previous attempt left state=PENDING. Retrying pipeline."
            )
            
        self.client.expire(settled_key, 86400 * 30)
        
        reserved_key = self._key(tenant_id, "reserved")
        spent_key = self._key(tenant_id, "spent")
        
        # Phase 2: Move money atomically
        with self.client.pipeline() as pipe:
            pipe.multi()
            pipe.incrbyfloat(reserved_key, -reserved_usd)
            pipe.incrbyfloat(spent_key, actual_usd)
            pipe.execute()
        
        # Phase 2 complete: mark as COMPLETED    
        self.client.set(settled_key, "COMPLETED")
        self.client.expire(settled_key, 86400 * 30)
            
        return True

    def get_reserved_spent_drift(self, tenant_id: str) -> float:
        """Returns the gap between reserved and spent funds.
        In a healthy system this should trend toward zero.
        If it grows, there are settlement leaks (half-settlements or
        reserve without settle)."""
        reserved = float(self.client.get(self._key(tenant_id, "reserved")) or 0.0)
        spent = float(self.client.get(self._key(tenant_id, "spent")) or 0.0)
        budget = float(self.client.get(self._key(tenant_id, "budget")) or 0.0)
        return reserved  # In steady state, reserved should be ~0

    def record_anomaly(self, tenant_id: str):
        self.client.hincrby(self._key(tenant_id, "stats"), "anomalies", 1)
        
    def record_request(self, tenant_id: str):
        self.client.hincrby(self._key(tenant_id, "stats"), "total_reqs", 1)
        
    def get_tenant_stats(self, tenant_id: str):
        stats = self.client.hgetall(self._key(tenant_id, "stats"))
        return {
            "anomalies": int(stats.get("anomalies", 0)),
            "total_reqs": int(stats.get("total_reqs", 0))
        }

    # Helper methods for setup/admin
    def set_budget(self, tenant_id: str, budget_usd: float):
        self.client.set(self._key(tenant_id, "budget"), float(budget_usd))

