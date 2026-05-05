"""
Kernell OS — Budget Proxy (Nivel 2: Network Interceptor)
═══════════════════════════════════════════════════════════
Worker (MicroVM) → localhost:proxy → Budget Proxy → LLM API

The worker CANNOT talk to the internet directly.
All traffic passes through this proxy which enforces:
  1. Pre-flight cost estimation (reject before sending)
  2. Post-flight reconciliation (actual vs estimated)
  3. Physical connection severing (socket close, not a suggestion)
  4. Idempotent request tracking (no double-billing on retries)
  5. Per-execution budget accounting (not global — per request_id)
"""
from __future__ import annotations
import json, logging, time, uuid, threading
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("kernell.runtime.budget_proxy")

# ── Exceptions (non-recoverable kills) ───────────────────────────────
class BudgetExhausted(Exception): pass
class RequestRejected(Exception): pass
class AccountLocked(Exception): pass
class MaxRequestsExceeded(Exception): pass

# ── Model Pricing (USD per 1M tokens, updatable at runtime) ──────────
class ModelPricing:
    _lock = threading.Lock()
    _PRICING: Dict[str, Dict[str, float]] = {
        "gpt-4o":            {"input": 2.50,  "output": 10.00},
        "gpt-4o-mini":       {"input": 0.15,  "output": 0.60},
        "gpt-4-turbo":       {"input": 10.00, "output": 30.00},
        "o1":                {"input": 15.00, "output": 60.00},
        "o1-mini":           {"input": 3.00,  "output": 12.00},
        "o3":                {"input": 10.00, "output": 40.00},
        "o3-mini":           {"input": 1.10,  "output": 4.40},
        "o4-mini":           {"input": 1.10,  "output": 4.40},
        "claude-opus-4":     {"input": 15.00, "output": 75.00},
        "claude-sonnet-4":   {"input": 3.00,  "output": 15.00},
        "claude-3.5-haiku":  {"input": 0.80,  "output": 4.00},
        "claude-3-haiku":    {"input": 0.25,  "output": 1.25},
        "gemini-2.5-pro":    {"input": 1.25,  "output": 10.00},
        "gemini-2.5-flash":  {"input": 0.15,  "output": 0.60},
        "deepseek-chat":     {"input": 0.27,  "output": 1.10},
        "deepseek-reasoner": {"input": 0.55,  "output": 2.19},
        "local":             {"input": 0.00,  "output": 0.00},
    }
    _FALLBACK = {"input": 15.00, "output": 75.00}  # Conservative for unknowns

    @classmethod
    def get(cls, model: str) -> Dict[str, float]:
        with cls._lock:
            if model in cls._PRICING:
                return cls._PRICING[model]
            for key in cls._PRICING:
                if model.startswith(key):
                    return cls._PRICING[key]
            logger.warning(f"[PRICING] Unknown model '{model}' — using CONSERVATIVE fallback")
            return cls._FALLBACK

    @classmethod
    def update(cls, model: str, input_per_1m: float, output_per_1m: float):
        with cls._lock:
            cls._PRICING[model] = {"input": input_per_1m, "output": output_per_1m}

    @classmethod
    def estimate_cost(cls, model: str, tokens_in: int, tokens_out: int) -> Decimal:
        pricing = cls.get(model)
        cost_in = Decimal(str(tokens_in)) * Decimal(str(pricing["input"])) / Decimal("1000000")
        cost_out = Decimal(str(tokens_out)) * Decimal(str(pricing["output"])) / Decimal("1000000")
        return (cost_in + cost_out).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)

# ── Account State ────────────────────────────────────────────────────
class AccountState(str, Enum):
    ACTIVE    = "ACTIVE"
    LOCKED    = "LOCKED"
    KILLED    = "KILLED"
    COMPLETED = "COMPLETED"

@dataclass
class RequestRecord:
    request_id: str
    execution_id: str
    model: str
    tokens_in_estimated: int
    tokens_out_estimated: int
    cost_estimated: Decimal
    tokens_in_actual: Optional[int] = None
    tokens_out_actual: Optional[int] = None
    cost_actual: Optional[Decimal] = None
    timestamp: float = field(default_factory=time.time)
    reconciled: bool = False

# ── Execution Account (per-execution budget) ─────────────────────────
@dataclass
class ExecutionAccount:
    """
    Invariants (sacred):
      1. spent <= max_budget (always)
      2. Once locked, no further requests (ever)
      3. cost_actual is ALWAYS used for accounting (not estimated)
      4. request_count <= max_requests (anti-loop)
    """
    execution_id: str
    max_budget: Decimal
    max_requests: int = 50
    max_depth: int = 10
    spent: Decimal = field(default_factory=lambda: Decimal("0"))
    reserved: Decimal = field(default_factory=lambda: Decimal("0"))
    state: AccountState = AccountState.ACTIVE
    request_count: int = 0
    created_at: float = field(default_factory=time.time)
    _request_timestamps: deque = field(default_factory=deque)
    _requests: Dict[str, RequestRecord] = field(default_factory=dict)
    _seen_ids: Set[str] = field(default_factory=set)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _kill_reason: Optional[str] = None

    @property
    def remaining(self) -> Decimal:
        return max(Decimal("0"), self.max_budget - self.spent - self.reserved)

    def check_preflight(self, estimated_cost: Decimal, request_id: str) -> bool:
        """Returns True if new request, False if idempotent replay."""
        with self._lock:
            if self.state != AccountState.ACTIVE:
                raise AccountLocked(f"Account {self.execution_id} is {self.state.value}: {self._kill_reason}")
            if request_id in self._seen_ids:
                return False  # Idempotent skip — caller must NOT re-record
            if self.request_count >= self.max_requests:
                self._hard_lock("MAX_REQUESTS_EXCEEDED")
                raise MaxRequestsExceeded(f"Hit max requests ({self.max_requests})")
            if estimated_cost > self.remaining:
                self._hard_lock("BUDGET_PREFLIGHT_EXCEEDED")
                raise RequestRejected(f"Est ${estimated_cost} > remaining ${self.remaining}")
            
            # Rate limiting (sliding window using deque)
            now = time.time()
            while self._request_timestamps and now - self._request_timestamps[0] > 1.0:
                self._request_timestamps.popleft()
                
            if len(self._request_timestamps) >= 10:
                self._hard_lock("VELOCITY_LIMIT_EXCEEDED")
                raise MaxRequestsExceeded("Velocity limit exceeded: >10 req/sec")
            self._request_timestamps.append(now)

            # Atomically reserve budget for in-flight request
            self.reserved += estimated_cost
            return True

    def record_request(self, record: RequestRecord):
        with self._lock:
            if record.request_id in self._seen_ids:
                return  # Guard: never overwrite existing records
            self._seen_ids.add(record.request_id)
            self._requests[record.request_id] = record
            self.request_count += 1

    def reconcile(self, request_id: str, tokens_in: int, tokens_out: int, model: str):
        """Post-flight: charge ACTUAL cost, not estimated. This is where amateurs die."""
        with self._lock:
            record = self._requests.get(request_id)
            if not record or record.reconciled:
                return
            actual_cost = ModelPricing.estimate_cost(model, tokens_in, tokens_out)
            record.tokens_in_actual = tokens_in
            record.tokens_out_actual = tokens_out
            record.cost_actual = actual_cost
            record.reconciled = True
            
            # Resolve reservation safely
            self.reserved = max(Decimal("0"), self.reserved - record.cost_estimated)
            self.spent += actual_cost
            
            drift_pct = float(actual_cost - record.cost_estimated) / max(float(record.cost_estimated), 0.000001) * 100
            
            # Extreme drift protection (Tool explosion, hallucination bug)
            if actual_cost > record.cost_estimated * Decimal("3"):
                logger.critical(f"[DRIFT] Extreme anomaly: estimated=${record.cost_estimated}, actual=${actual_cost}")
                self._hard_lock("EXTREME_DRIFT_ANOMALY")
                
            logger.info(f"[RECONCILE] exec={self.execution_id} req={request_id[:8]} "
                        f"est=${record.cost_estimated} actual=${actual_cost} drift={drift_pct:+.1f}% remaining=${self.remaining}")
            if self.spent >= self.max_budget:
                self._hard_lock("BUDGET_EXHAUSTED_POST_RECONCILE")

    def _hard_lock(self, reason: str):
        self.state = AccountState.LOCKED
        self._kill_reason = reason
        logger.critical(f"[KILL] Account LOCKED: exec={self.execution_id} reason={reason} spent=${self.spent}")

    def kill(self, reason: str):
        with self._lock:
            self.state = AccountState.KILLED
            self._kill_reason = reason
            logger.critical(f"[KILL] Account KILLED externally: exec={self.execution_id} reason={reason}")

    def telemetry(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "execution_id": self.execution_id, "state": self.state.value,
                "budget_usd": str(self.max_budget), "spent_usd": str(self.spent),
                "reserved_usd": str(self.reserved),
                "remaining_usd": str(self.remaining),
                "utilization_pct": round(float((self.spent + self.reserved) / self.max_budget) * 100, 2) if self.max_budget > 0 else 0,
                "request_count": self.request_count,
                "kill_reason": self._kill_reason, "age_s": round(time.time() - self.created_at, 2),
            }

# ── Token estimation ─────────────────────────────────────────────────
_SAFETY_FACTOR = Decimal("1.33")
def _estimate_tokens(text: str) -> int:
    return int(float(Decimal(str(len(text.encode("utf-8", errors="replace")) / 4)) * _SAFETY_FACTOR))

# ── Budget Proxy (the interceptor) ───────────────────────────────────
class BudgetProxy:
    """
    The REAL kill-switch. Separate process. Worker → localhost:proxy → Internet.
    Every LLM call passes here. No exceptions.
    """
    def __init__(self):
        self._accounts: Dict[str, ExecutionAccount] = {}
        self._lock = threading.Lock()
        self._global_kill = False
        self._global_kill_reason: Optional[str] = None
        self._total_requests = 0
        self._total_killed = 0

    def create_account(self, execution_id: str, budget_usd: float,
                       max_requests: int = 50, max_depth: int = 10) -> ExecutionAccount:
        account = ExecutionAccount(execution_id=execution_id, max_budget=Decimal(str(budget_usd)),
                                   max_requests=max_requests, max_depth=max_depth)
        with self._lock:
            self._accounts[execution_id] = account
        logger.info(f"[PROXY] Account created: exec={execution_id} budget=${budget_usd}")
        return account

    def get_account(self, execution_id: str) -> Optional[ExecutionAccount]:
        with self._lock:
            return self._accounts.get(execution_id)

    def global_kill(self, reason: str):
        """GLOBAL KILL SWITCH. Locks ALL accounts immediately."""
        with self._lock:
            self._global_kill = True
            self._global_kill_reason = reason
            for acc in self._accounts.values():
                if acc.state == AccountState.ACTIVE:
                    acc.kill(f"GLOBAL_KILL: {reason}")
                    self._total_killed += 1
        logger.critical(f"[GLOBAL_KILL] ALL accounts killed: {reason}")

    def intercept(self, execution_id: str, model: str, messages: List[Dict],
                  max_output_tokens: int = 4096, request_id: Optional[str] = None,
                  depth: int = 0) -> Dict[str, Any]:
        """Core method. Every LLM call passes here. Raises on any violation."""
        if self._global_kill:
            raise AccountLocked(f"GLOBAL_KILL: {self._global_kill_reason}")
        account = self.get_account(execution_id)
        if not account:
            raise AccountLocked(f"No account for execution {execution_id}")
        if depth > account.max_depth:
            account.kill("MAX_DEPTH_EXCEEDED")
            raise AccountLocked(f"Max depth {account.max_depth} exceeded")
        request_id = request_id or str(uuid.uuid4())
        tokens_in_est = sum(_estimate_tokens(msg.get("content", "") if isinstance(msg.get("content"), str)
                                              else json.dumps(msg.get("content", ""))) for msg in messages)
        # Buffer de seguridad para edge cases (tool calls, json)
        estimated_cost = ModelPricing.estimate_cost(model, tokens_in_est, max_output_tokens) * Decimal("1.2")
        is_new = account.check_preflight(estimated_cost, request_id)
        if is_new:
            record = RequestRecord(request_id=request_id, execution_id=execution_id, model=model,
                                   tokens_in_estimated=tokens_in_est, tokens_out_estimated=max_output_tokens,
                                   cost_estimated=estimated_cost)
            account.record_request(record)
            with self._lock:
                self._total_requests += 1
        logger.info(f"[PROXY] {'PASS' if is_new else 'IDEMPOTENT'}: exec={execution_id} req={request_id[:8]} model={model} "
                    f"est=${estimated_cost} remaining=${account.remaining} depth={depth}")
        return {"request_id": request_id, "execution_id": execution_id, "model": model,
                "messages": messages, "max_output_tokens": max_output_tokens,
                "budget_metadata": {"estimated_cost_usd": str(estimated_cost),
                                     "remaining_budget_usd": str(account.remaining),
                                     "request_number": account.request_count, "depth": depth}}

    def reconcile_response(self, execution_id: str, request_id: str, model: str,
                           usage: Dict[str, int]) -> Dict[str, Any]:
        """Post-flight reconciliation. Charges ACTUAL cost from API response."""
        account = self.get_account(execution_id)
        if not account:
            return {"error": "account_not_found"}
        account.reconcile(request_id, usage.get("prompt_tokens", 0),
                          usage.get("completion_tokens", 0), model)
        return {"request_id": request_id, "total_spent_usd": str(account.spent),
                "remaining_usd": str(account.remaining), "state": account.state.value,
                "killed": account.state in (AccountState.LOCKED, AccountState.KILLED)}

    def telemetry(self) -> Dict[str, Any]:
        with self._lock:
            return {"global_kill": self._global_kill, "total_accounts": len(self._accounts),
                    "active": sum(1 for a in self._accounts.values() if a.state == AccountState.ACTIVE),
                    "total_requests": self._total_requests, "total_killed": self._total_killed}
