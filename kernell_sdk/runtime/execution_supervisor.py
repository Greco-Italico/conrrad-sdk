"""
Kernell OS — Execution Supervisor
══════════════════════════════════
The "cerebro económico". Lives OUTSIDE the worker process.

Responsibilities:
  1. Create execution accounts with atomic budget escrow
  2. Track depth, time, and cost in real-time
  3. Coordinate kill decisions WITHOUT asking the agent
  4. Integrate with EconomicEngine for KERN settlement
  5. Bridge BudgetProxy ↔ EconomicEngine ↔ Watchdog

Pipeline:
  Client Request → Router → Supervisor.begin() → BudgetProxy.create_account()
                           → Worker executes (budget-gated)
                           → Supervisor.complete() → EconomicEngine.commit()
"""
from __future__ import annotations
import logging, time, uuid, threading
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional, Callable

from .budget_proxy import BudgetProxy, AccountLocked, BudgetExhausted

logger = logging.getLogger("kernell.runtime.supervisor")


class ExecutionStatus(str, Enum):
    PENDING     = "PENDING"
    RUNNING     = "RUNNING"
    COMPLETED   = "COMPLETED"
    FAILED      = "FAILED"
    KILLED      = "KILLED"
    TIMED_OUT   = "TIMED_OUT"


@dataclass
class ExecutionRecord:
    """Complete record of a supervised execution."""
    execution_id: str
    agent_id: str
    budget_usd: float
    status: ExecutionStatus = ExecutionStatus.PENDING
    tx_id: Optional[str] = None          # EconomicEngine transaction ID
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    kill_reason: Optional[str] = None
    depth: int = 0
    max_depth: int = 10
    max_time_seconds: float = 30.0
    request_count: int = 0
    total_cost_usd: Decimal = field(default_factory=lambda: Decimal("0"))
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def elapsed_seconds(self) -> float:
        if not self.started_at:
            return 0.0
        end = self.completed_at or time.time()
        return round(end - self.started_at, 3)

    @property
    def is_terminal(self) -> bool:
        return self.status in (ExecutionStatus.COMPLETED, ExecutionStatus.FAILED,
                               ExecutionStatus.KILLED, ExecutionStatus.TIMED_OUT)


class ExecutionSupervisor:
    """
    Orchestrates execution lifecycle with economic enforcement.

    begin() → creates budget account + economic reservation
    check() → validates time/depth/budget invariants (called by watchdog)
    complete() → commits economic transaction
    kill() → force-terminates execution + rolls back economic transaction
    """

    def __init__(
        self,
        proxy: BudgetProxy,
        economic_engine=None,       # Optional EconomicEngine integration
        default_budget_usd: float = 0.50,
        default_max_time: float = 30.0,
        default_max_depth: int = 10,
        default_max_requests: int = 50,
        on_kill: Optional[Callable[[str, str], None]] = None,  # callback(exec_id, reason)
    ):
        self.proxy = proxy
        self.economic_engine = economic_engine
        self.default_budget = default_budget_usd
        self.default_max_time = default_max_time
        self.default_max_depth = default_max_depth
        self.default_max_requests = default_max_requests
        self._on_kill = on_kill

        self._executions: Dict[str, ExecutionRecord] = {}
        self._lock = threading.Lock()

    def begin(
        self,
        agent_id: str,
        budget_usd: Optional[float] = None,
        max_time: Optional[float] = None,
        max_depth: Optional[int] = None,
        max_requests: Optional[int] = None,
        metadata: Optional[Dict] = None,
    ) -> ExecutionRecord:
        """
        Begin a supervised execution.

        1. Reserve funds in EconomicEngine (if available)
        2. Create budget account in BudgetProxy
        3. Return execution record

        Raises ValueError if insufficient funds.
        """
        budget = budget_usd or self.default_budget
        max_t = max_time or self.default_max_time
        max_d = max_depth or self.default_max_depth
        max_r = max_requests or self.default_max_requests

        execution_id = f"exec-{uuid.uuid4().hex[:12]}"

        # Step 1: Reserve in EconomicEngine (atomic escrow)
        tx_id = None
        if self.economic_engine:
            try:
                tx_id = self.economic_engine.authorize(
                    amount=budget,
                    context={"execution_id": execution_id, "agent_id": agent_id},
                )
            except ValueError as e:
                raise ValueError(f"Cannot begin execution: {e}")

        # Step 2: Create budget account in proxy
        self.proxy.create_account(
            execution_id=execution_id,
            budget_usd=budget,
            max_requests=max_r,
            max_depth=max_d,
        )

        # Step 3: Create execution record
        record = ExecutionRecord(
            execution_id=execution_id,
            agent_id=agent_id,
            budget_usd=budget,
            status=ExecutionStatus.RUNNING,
            tx_id=tx_id,
            started_at=time.time(),
            max_depth=max_d,
            max_time_seconds=max_t,
            metadata=metadata or {},
        )

        with self._lock:
            self._executions[execution_id] = record

        logger.info(
            f"[SUPERVISOR] BEGIN: exec={execution_id} agent={agent_id} "
            f"budget=${budget} max_time={max_t}s max_depth={max_d}"
        )
        return record

    def check(self, execution_id: str) -> Dict[str, Any]:
        """
        Invariant check. Called by the watchdog on every tick.

        Checks:
          1. Wall-clock timeout
          2. Budget exhaustion (via proxy account)
          3. Depth violation

        Returns status dict. Kills execution if any invariant violated.
        """
        with self._lock:
            record = self._executions.get(execution_id)
        if not record or record.is_terminal:
            return {"status": "not_found_or_terminal"}

        violations = []

        # 1. Time check
        if record.elapsed_seconds > record.max_time_seconds:
            violations.append(f"TIMEOUT: {record.elapsed_seconds:.1f}s > {record.max_time_seconds}s")

        # 2. Budget check (from proxy)
        account = self.proxy.get_account(execution_id)
        if account:
            if account.state.value in ("LOCKED", "KILLED"):
                violations.append(f"BUDGET_KILLED: {account._kill_reason}")
            record.total_cost_usd = account.spent
            record.request_count = account.request_count

        if violations:
            reason = " | ".join(violations)
            self.kill(execution_id, reason)
            return {"status": "KILLED", "violations": violations}

        return {
            "status": "OK",
            "elapsed_s": record.elapsed_seconds,
            "cost_usd": str(record.total_cost_usd),
            "requests": record.request_count,
            "depth": record.depth,
        }

    def complete(self, execution_id: str) -> Dict[str, Any]:
        """
        Complete an execution normally.
        Commits economic transaction with actual cost.
        """
        with self._lock:
            record = self._executions.get(execution_id)
        if not record:
            return {"error": "not_found"}
        if record.is_terminal:
            return {"error": "already_terminal", "status": record.status.value}

        # Get actual cost from proxy
        account = self.proxy.get_account(execution_id)
        if account:
            record.total_cost_usd = account.spent

        record.status = ExecutionStatus.COMPLETED
        record.completed_at = time.time()

        # Commit in EconomicEngine
        if self.economic_engine and record.tx_id:
            try:
                self.economic_engine.commit(record.tx_id)
            except Exception as e:
                logger.error(f"[SUPERVISOR] EconomicEngine commit failed: {e}")

        # Close proxy account to prevent reuse
        self.proxy.close_account(execution_id)

        logger.info(
            f"[SUPERVISOR] COMPLETE: exec={execution_id} "
            f"cost=${record.total_cost_usd} time={record.elapsed_seconds}s "
            f"requests={record.request_count}"
        )
        return self._record_to_dict(record)

    def kill(self, execution_id: str, reason: str) -> Dict[str, Any]:
        """
        Force-terminate an execution. No negotiation.

        1. Kill proxy account (severs network)
        2. Rollback economic transaction (release funds)
        3. Fire on_kill callback (for process termination)
        """
        with self._lock:
            record = self._executions.get(execution_id)
        if not record:
            return {"error": "not_found"}
        if record.is_terminal:
            return {"status": record.status.value, "already_killed": True}

        record.status = ExecutionStatus.KILLED
        record.kill_reason = reason
        record.completed_at = time.time()

        # Kill proxy account
        account = self.proxy.get_account(execution_id)
        if account:
            account.kill(reason)
            record.total_cost_usd = account.spent

        # Rollback economic reservation
        if self.economic_engine and record.tx_id:
            try:
                self.economic_engine.rollback(record.tx_id)
            except Exception as e:
                logger.error(f"[SUPERVISOR] Rollback failed: {e}")

        # Fire callback (e.g., kill VM, kill process)
        if self._on_kill:
            try:
                self._on_kill(execution_id, reason)
            except Exception as e:
                logger.error(f"[SUPERVISOR] on_kill callback failed: {e}")

        logger.critical(
            f"[SUPERVISOR] KILLED: exec={execution_id} reason={reason} "
            f"cost=${record.total_cost_usd} time={record.elapsed_seconds}s"
        )
        return self._record_to_dict(record)

    def list_active(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [
                self._record_to_dict(r)
                for r in self._executions.values()
                if not r.is_terminal
            ]

    def _record_to_dict(self, r: ExecutionRecord) -> Dict[str, Any]:
        return {
            "execution_id": r.execution_id, "agent_id": r.agent_id,
            "status": r.status.value, "budget_usd": r.budget_usd,
            "cost_usd": str(r.total_cost_usd), "elapsed_s": r.elapsed_seconds,
            "requests": r.request_count, "depth": r.depth,
            "kill_reason": r.kill_reason,
        }
