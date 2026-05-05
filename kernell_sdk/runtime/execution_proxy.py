"""
Kernell Pay — Execution Proxy Core (Nivel 1)
════════════════════════════════════════════
Hardened execution proxy for deterministic accounting, 
streaming enforcement (guillotina), financial idempotency, 
and multi-tenant isolation.
"""
import time
import uuid
import json
import logging
import tiktoken
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, Optional, Iterator, Tuple

logger = logging.getLogger("kernell.proxy")

@dataclass
class ExecutionReceipt:
    transaction_id: str
    tenant_id: str
    timestamp_start: float
    timestamp_end: float
    
    requested_model: str
    executed_model: str
    tokens_prompt: int
    tokens_completion: int
    
    reserved_usd: float
    actual_cost_usd: float
    refunded_usd: float
    
    status: str  # COMPLETED, KILLED, FAILED, CACHED
    settlement_status: str = "PENDING"  # PENDING, SETTLED
    kill_reason: Optional[str] = None
    retry_classification: Optional[str] = None


class TenantStore:
    """In-memory simulation of a persistent tenant/idempotency DB (e.g. Redis/SQLite)."""
    def __init__(self):
        import threading
        self._lock = threading.Lock()
        
        self.idempotency_cache: Dict[str, ExecutionReceipt] = {}
        self.active_locks: set = set()
        
        self.tenant_budgets: Dict[str, float] = {}
        self.tenant_spent: Dict[str, float] = {}
        self.tenant_reserved: Dict[str, float] = {}
        
        self.tenant_anomalies: Dict[str, int] = {}
        self.tenant_total_reqs: Dict[str, int] = {}
        
        self.settled_transactions: set = set()

    def check_or_set_processing(self, idempotency_key: str) -> str:
        with self._lock:
            if idempotency_key in self.idempotency_cache:
                return "COMPLETED"
            if idempotency_key in self.active_locks:
                return "PROCESSING"
            self.active_locks.add(idempotency_key)
            return "NEW"
            
    def finalize_idempotency(self, idempotency_key: str, receipt: Optional[ExecutionReceipt]):
        with self._lock:
            if receipt:
                self.idempotency_cache[idempotency_key] = receipt
            self.active_locks.discard(idempotency_key)
            
    def record_anomaly(self, tenant_id: str):
        with self._lock:
            self.tenant_anomalies[tenant_id] = self.tenant_anomalies.get(tenant_id, 0) + 1
            
    def record_request(self, tenant_id: str):
        with self._lock:
            self.tenant_total_reqs[tenant_id] = self.tenant_total_reqs.get(tenant_id, 0) + 1
            
    def get_tenant_stats(self, tenant_id: str) -> Dict[str, int]:
        with self._lock:
            return {
                "anomalies": self.tenant_anomalies.get(tenant_id, 0),
                "total_reqs": self.tenant_total_reqs.get(tenant_id, 0)
            }
            
    def reserve_funds(self, tenant_id: str, amount_usd: float) -> bool:
        with self._lock:
            budget = self.tenant_budgets.get(tenant_id, 10.0)  # Default $10
            spent = self.tenant_spent.get(tenant_id, 0.0)
            reserved = self.tenant_reserved.get(tenant_id, 0.0)
            
            if (spent + reserved + amount_usd) > budget:
                return False
            
            self.tenant_reserved[tenant_id] = reserved + amount_usd
            return True
            
    def settle_funds(self, tenant_id: str, reserved_usd: float, actual_usd: float, transaction_id: str) -> bool:
        with self._lock:
            if transaction_id in self.settled_transactions:
                return False
            self.tenant_reserved[tenant_id] -= reserved_usd
            self.tenant_spent[tenant_id] = self.tenant_spent.get(tenant_id, 0.0) + actual_usd
            self.settled_transactions.add(transaction_id)
            return True


class KernellExecutionProxy:
    def __init__(self, store: Any, audit_log_path: Optional[str] = "/var/log/kernell/receipts.jsonl"):
        self.store = store
        self.audit_log_path = audit_log_path
        
        # Exact token costs (outbound)
        self.pricing = {
            "gpt-4o": 0.0000150,
            "gpt-4o-mini": 0.0000006,
            "gemini-2.5-flash": 0.0000003,
        }

    def _check_circuit_breaker(self, tenant_id: str):
        stats = self.store.get_tenant_stats(tenant_id)
        anomalies = stats["anomalies"]
        reqs = stats["total_reqs"]
        
        if reqs >= 10 and (anomalies / reqs) > 0.20:
            raise PermissionError(f"403 Forbidden: Tenant {tenant_id} in QUARANTINE due to extreme drift.")

    def _count_tokens(self, text: str, model_name: str) -> int:
        try:
            encoding = tiktoken.encoding_for_model(model_name)
        except KeyError:
            encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text))

    def execute_stream(
        self,
        tenant_id: str,
        idempotency_key: str,
        target_model: str,
        effective_budget_usd: float,
        prompt_tokens: int,
        mock_llm_stream: Iterator[str]  # Simulates provider stream
    ) -> Iterator[str]:
        """
        Executes a stream with physical token tracking and guillotina cutoff.
        Returns an iterator of chunks. The last chunk may be a JSON receipt.
        """
        # 1. State Idempotency
        idemp_state = self.store.check_or_set_processing(idempotency_key)
        if idemp_state == "COMPLETED":
            receipt = self.store.idempotency_cache[idempotency_key]
            yield f"[KERNELL_PAY: CACHED] {json.dumps(asdict(receipt))}"
            return
        elif idemp_state == "PROCESSING":
            yield "[KERNELL_PAY: 429 CONFLICT - TRANSACTION IN PROGRESS]"
            return

        receipt = None
        transaction_id = str(uuid.uuid4())
        try:
            # 2. Circuit Breaker
            self._check_circuit_breaker(tenant_id)
            
            # 3. Financial Pre-Reserve (Atomic)
            if not self.store.reserve_funds(tenant_id, effective_budget_usd):
                yield "[KERNELL_PAY: 402 PAYMENT REQUIRED - INSUFFICIENT BUDGET]"
                return
                
            self.store.record_request(tenant_id)

            # 4. Execution State
            cost_per_token = self.pricing.get(target_model, 0.0000150)
            tokens_generated = 0
            current_cost = 0.0
            status = "COMPLETED"
            kill_reason = None
            retry_classification = None
            
            start_ts = time.time()
            
            # 5. Stream Enforcement Loop
            for chunk in mock_llm_stream:
                chunk_tokens = self._count_tokens(chunk, target_model)
                tokens_generated += chunk_tokens
                current_cost = tokens_generated * cost_per_token
                
                # PHYSICAL GUILLOTINA
                if current_cost > effective_budget_usd:
                    status = "KILLED"
                    kill_reason = "BUDGET_EXHAUSTED"
                    retry_classification = "POLICY_INDUCED"
                    
                    partial_evt = json.dumps({
                        "event": "PARTIAL_EXECUTION", 
                        "tokens": tokens_generated, 
                        "cost_usd": round(current_cost, 6)
                    })
                    yield f"\n{partial_evt}\n"
                    yield json.dumps({"error": "EXECUTION_HALTED_BUDGET_EXCEEDED", "reason": kill_reason}) + "\n"
                    break
                    
                yield chunk
            
            end_ts = time.time()
            
            if kill_reason:
                self.store.record_anomaly(tenant_id)
            
            # 6. Build Receipt BEFORE settlement (Write-Ahead)
            receipt = ExecutionReceipt(
                transaction_id=transaction_id,
                tenant_id=tenant_id,
                timestamp_start=start_ts,
                timestamp_end=end_ts,
                requested_model=target_model,
                executed_model=target_model,
                tokens_prompt=prompt_tokens,
                tokens_completion=tokens_generated,
                reserved_usd=effective_budget_usd,
                actual_cost_usd=current_cost,
                refunded_usd=max(0.0, effective_budget_usd - current_cost),
                status=status,
                kill_reason=kill_reason,
                retry_classification=retry_classification
            )
            
            # 7. Write-Ahead Log: persist receipt as PENDING before moving money.
            self._write_receipt(receipt)
            _settle_start = time.time()
            
            # 8. Atomic Financial Settlement (Idempotent)
            self.store.settle_funds(tenant_id, effective_budget_usd, current_cost, transaction_id)
            
            # 9. Mark receipt as SETTLED (append-only confirmation)
            receipt.settlement_status = "SETTLED"
            self._write_receipt(receipt)
            self._record_settlement_latency(_settle_start)
            
            yield f"\n[RECEIPT] {json.dumps(asdict(receipt))}"

        except PermissionError as pe:
            yield f"\n[KERNELL_PAY: ERROR] {str(pe)}"
        except Exception as e:
            # En caso de catástrofe del provider (5xx), debemos liquidar
            status = "FAILED"
            retry_classification = "PROVIDER_ERROR"
            receipt = ExecutionReceipt(
                transaction_id=transaction_id,
                tenant_id=tenant_id,
                timestamp_start=start_ts,
                timestamp_end=time.time(),
                requested_model=target_model,
                executed_model=target_model,
                tokens_prompt=prompt_tokens,
                tokens_completion=tokens_generated,
                reserved_usd=effective_budget_usd,
                actual_cost_usd=current_cost,
                refunded_usd=0.0,
                status=status,
                kill_reason=None,
                retry_classification=retry_classification
            )
            self._write_receipt(receipt)
            self.store.settle_funds(tenant_id, effective_budget_usd, current_cost, transaction_id)
            receipt.settlement_status = "SETTLED"
            self._write_receipt(receipt)
            yield f"\n[KERNELL_PAY: ERROR] {str(e)}"
        finally:
            self.store.finalize_idempotency(idempotency_key, receipt)

    # ─── Audit Write-Ahead ──────────────────────────────────────

    _receipts_emitted = 0
    _receipts_written = 0
    _settlement_latencies_ms: list = []

    def _write_receipt(self, receipt: 'ExecutionReceipt'):
        """Write receipt to audit JSONL with fsync for OS-level durability.
        Failure here is CRITICAL — it means the reconciliator will have a
        blind spot for this transaction."""
        KernellExecutionProxy._receipts_emitted += 1
        if not self.audit_log_path:
            return
        try:
            import os
            with open(self.audit_log_path, "a") as f:
                f.write(json.dumps(asdict(receipt)) + "\n")
                f.flush()
                os.fsync(f.fileno())
            KernellExecutionProxy._receipts_written += 1
        except Exception as ex:
            logger.critical(
                f"CRITICAL: Failed to write receipt {receipt.transaction_id}. "
                f"Ledger will have a blind spot. Error: {ex}"
            )

    def _record_settlement_latency(self, pending_ts: float):
        """Track time between PENDING write and SETTLED confirmation."""
        latency_ms = (time.time() - pending_ts) * 1000
        KernellExecutionProxy._settlement_latencies_ms.append(latency_ms)
        if latency_ms > 500:
            logger.warning(
                f"Settlement latency {latency_ms:.1f}ms exceeds 500ms threshold. "
                f"Redis or I/O bottleneck possible."
            )
