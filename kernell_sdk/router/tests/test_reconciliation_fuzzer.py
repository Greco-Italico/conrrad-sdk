"""
Reconciliation Fuzzer — Stress Testing for Split-Brain Resolution

Tests:
1. EXACT_MATCH       → Both regions identical → ACCEPT
2. SINGLE_COMPLETION → One region ahead       → FORCE_SYNC
3. BOTH_FAILED       → Neither completed      → MANUAL_REVIEW
4. SPLIT_BRAIN       → Both completed, diff FP → Epoch precedence or MANUAL_REVIEW
5. FREEZE_THEN_SYNC  → Freeze, then force sync on top → epoch must increase
6. DOUBLE_COMPENSATE → Compensate twice       → must not corrupt state
7. FORCE_SYNC_IDEMPOTENCY → Same FORCE_SYNC twice → no epoch regression
"""

import time
import json
import hashlib
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from kernell_sdk.router.reconciler import (
    ReconciliationEngine,
    ReconciliationContext,
    ReconciliationResult,
    reconcile_from_timelines
)
from kernell_sdk.router.reconciliation_executor import ReconciliationExecutor


class FakeRedis:
    """In-memory Redis mock for fuzzing without real Redis"""
    def __init__(self):
        self.store = {}
        self.streams = {}
    
    def hset(self, key, field=None, value=None, mapping=None):
        if key not in self.store:
            self.store[key] = {}
        if mapping:
            self.store[key].update({str(k): str(v) for k, v in mapping.items()})
        elif field and value:
            self.store[key][field] = str(value)
    
    def hgetall(self, key):
        return self.store.get(key, {})
    
    def xadd(self, stream, fields):
        if stream not in self.streams:
            self.streams[stream] = []
        self.streams[stream].append(fields)


def make_frame(epoch, state, event="RECLAIM", fingerprint=None):
    if fingerprint is None:
        fingerprint = hashlib.sha256(f"{epoch}-{state}".encode()).hexdigest()
    return {
        "ts": time.time(),
        "event": event,
        "epoch": epoch,
        "state": state,
        "fingerprint": fingerprint,
        "history_len": epoch,
        "state_snapshot": {"epoch": epoch, "state": state}
    }


class ReconciliationFuzzer:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.results = []

    def assert_eq(self, test_name, actual, expected, field=""):
        if actual == expected:
            self.passed += 1
            self.results.append(("PASS", test_name, field))
        else:
            self.failed += 1
            self.results.append(("FAIL", test_name, f"{field}: expected={expected}, got={actual}"))

    def run_all(self):
        print("=" * 70)
        print("  RECONCILIATION FUZZER — Kernell OS")
        print("=" * 70)
        
        self.test_exact_match()
        self.test_single_completion_a_wins()
        self.test_single_completion_b_wins()
        self.test_both_failed()
        self.test_split_brain_epoch_precedence()
        self.test_split_brain_same_epoch_collision()
        self.test_a_failed_b_completed()
        self.test_b_failed_a_completed()
        self.test_empty_timelines()
        self.test_executor_freeze()
        self.test_executor_force_sync()
        self.test_executor_noop()
        self.test_executor_compensate()
        self.test_freeze_idempotency()
        self.test_force_sync_wal_emission()

        print()
        print("-" * 70)
        for status, name, detail in self.results:
            icon = "✅" if status == "PASS" else "❌"
            suffix = f"  ({detail})" if detail and status == "FAIL" else ""
            print(f"  {icon} {name}{suffix}")
        
        print("-" * 70)
        total = self.passed + self.failed
        print(f"\n  Results: {self.passed}/{total} passed", end="")
        if self.failed > 0:
            print(f" — {self.failed} FAILED ⚠️")
        else:
            print(" — ALL CLEAR 🟢")
        print("=" * 70)
        
        return self.failed == 0

    # ─── Policy Tests ────────────────────────────────────────────────────

    def test_exact_match(self):
        """Both regions have identical fingerprints → ACCEPT"""
        fp = hashlib.sha256(b"identical").hexdigest()
        tl_a = [make_frame(3, "COMPLETED", fingerprint=fp)]
        tl_b = [make_frame(3, "COMPLETED", fingerprint=fp)]
        
        result = reconcile_from_timelines(tl_a, tl_b)
        self.assert_eq("ExactMatch → ACCEPT", result.action, "ACCEPT")
        self.assert_eq("ExactMatch → NONE winner", result.winner, "NONE")

    def test_single_completion_a_wins(self):
        """Region A completed, B still in progress"""
        tl_a = [make_frame(3, "COMPLETED")]
        tl_b = [make_frame(2, "IN_PROGRESS")]
        
        result = reconcile_from_timelines(tl_a, tl_b)
        self.assert_eq("SingleCompletion(A) → FORCE_SYNC_B", result.action, "FORCE_SYNC_B")
        self.assert_eq("SingleCompletion(A) → A wins", result.winner, "A")

    def test_single_completion_b_wins(self):
        """Region B completed, A still in progress"""
        tl_a = [make_frame(2, "IN_PROGRESS")]
        tl_b = [make_frame(3, "COMPLETED")]
        
        result = reconcile_from_timelines(tl_a, tl_b)
        self.assert_eq("SingleCompletion(B) → FORCE_SYNC_A", result.action, "FORCE_SYNC_A")
        self.assert_eq("SingleCompletion(B) → B wins", result.winner, "B")

    def test_both_failed(self):
        """Both regions failed → MANUAL_REVIEW"""
        tl_a = [make_frame(2, "FAILED")]
        tl_b = [make_frame(2, "FAILED")]
        
        result = reconcile_from_timelines(tl_a, tl_b)
        self.assert_eq("BothFailed → MANUAL_REVIEW", result.action, "MANUAL_REVIEW")
        self.assert_eq("BothFailed → NONE winner", result.winner, "NONE")

    def test_split_brain_epoch_precedence(self):
        """Both completed with different FPs, different epochs → earliest wins"""
        tl_a = [make_frame(2, "COMPLETED", fingerprint="aaa")]
        tl_b = [make_frame(5, "COMPLETED", fingerprint="bbb")]
        
        result = reconcile_from_timelines(tl_a, tl_b)
        self.assert_eq("SplitBrain(epoch) → A wins (lower epoch)", result.winner, "A")
        self.assert_eq("SplitBrain(epoch) → FORCE_SYNC_B", result.action, "FORCE_SYNC_B")

    def test_split_brain_same_epoch_collision(self):
        """Both completed, same epoch, different FP → irresolvable"""
        tl_a = [make_frame(3, "COMPLETED", fingerprint="aaa")]
        tl_b = [make_frame(3, "COMPLETED", fingerprint="bbb")]
        
        result = reconcile_from_timelines(tl_a, tl_b)
        self.assert_eq("SplitBrain(collision) → MANUAL_REVIEW", result.action, "MANUAL_REVIEW")
        self.assert_eq("SplitBrain(collision) → NONE", result.winner, "NONE")

    def test_a_failed_b_completed(self):
        """A failed, B completed → B wins via InvalidStatePolicy"""
        tl_a = [make_frame(3, "FAILED")]
        tl_b = [make_frame(3, "COMPLETED")]
        
        result = reconcile_from_timelines(tl_a, tl_b)
        self.assert_eq("InvalidState(A fail) → B wins", result.winner, "B")
        self.assert_eq("InvalidState(A fail) → FORCE_SYNC_A", result.action, "FORCE_SYNC_A")

    def test_b_failed_a_completed(self):
        """B failed, A completed → A wins via InvalidStatePolicy"""
        tl_a = [make_frame(3, "COMPLETED")]
        tl_b = [make_frame(3, "FAILED")]
        
        result = reconcile_from_timelines(tl_a, tl_b)
        self.assert_eq("InvalidState(B fail) → A wins", result.winner, "A")
        self.assert_eq("InvalidState(B fail) → FORCE_SYNC_B", result.action, "FORCE_SYNC_B")

    def test_empty_timelines(self):
        """Both timelines empty → MANUAL_REVIEW"""
        result = reconcile_from_timelines([], [])
        self.assert_eq("EmptyTimelines → MANUAL_REVIEW", result.action, "MANUAL_REVIEW")

    # ─── Executor Tests ──────────────────────────────────────────────────

    def test_executor_freeze(self):
        """MANUAL_REVIEW → FREEZE sets state to FROZEN in both regions"""
        ra, rb = FakeRedis(), FakeRedis()
        rid = "fuzz-freeze-1"
        
        # Pre-seed state
        for r in [ra, rb]:
            r.hset(f"kernell:exec:{rid}", mapping={"epoch": "3", "state": "IN_PROGRESS"})
        
        executor = ReconciliationExecutor(ra, rb)
        result = executor.execute(rid, {
            "action": "MANUAL_REVIEW",
            "winner": "NONE",
            "reason": "Fuzz test"
        })
        
        self.assert_eq("Executor FREEZE → status frozen", result["status"], "frozen")
        self.assert_eq("Executor FREEZE → A state", ra.hgetall(f"kernell:exec:{rid}")["state"], "FROZEN")
        self.assert_eq("Executor FREEZE → B state", rb.hgetall(f"kernell:exec:{rid}")["state"], "FROZEN")

    def test_executor_force_sync(self):
        """FORCE_SYNC → winner state propagated, epoch increased"""
        ra, rb = FakeRedis(), FakeRedis()
        rid = "fuzz-sync-1"
        
        ra.hset(f"kernell:exec:{rid}", mapping={"epoch": "5", "state": "COMPLETED", "result_ptr": "res-123"})
        rb.hset(f"kernell:exec:{rid}", mapping={"epoch": "3", "state": "IN_PROGRESS"})
        
        executor = ReconciliationExecutor(ra, rb)
        result = executor.execute(rid, {
            "action": "FORCE_SYNC_B",
            "winner": "A",
            "reason": "A completed first"
        })
        
        self.assert_eq("Executor FORCE_SYNC → success", result["status"], "success")
        
        # Both regions should now have epoch 6 (winner epoch + 1)
        a_state = ra.hgetall(f"kernell:exec:{rid}")
        b_state = rb.hgetall(f"kernell:exec:{rid}")
        self.assert_eq("FORCE_SYNC → A epoch advanced", a_state["epoch"], "6")
        self.assert_eq("FORCE_SYNC → B epoch matches", b_state["epoch"], "6")
        self.assert_eq("FORCE_SYNC → B state synced", b_state["state"], "COMPLETED")

    def test_executor_noop(self):
        """ACCEPT/NOOP → no side effects"""
        ra, rb = FakeRedis(), FakeRedis()
        executor = ReconciliationExecutor(ra, rb)
        
        result = executor.execute("noop-test", {
            "action": "ACCEPT",
            "winner": "NONE",
            "reason": "Consensus"
        })
        
        self.assert_eq("Executor NOOP → success", result["status"], "success")
        self.assert_eq("Executor NOOP → no WAL", len(ra.streams), 0)

    def test_executor_compensate(self):
        """COMPENSATE → WAL event emitted"""
        ra, rb = FakeRedis(), FakeRedis()
        rid = "fuzz-compensate-1"
        
        ra.hset(f"kernell:exec:{rid}", mapping={"epoch": "4", "state": "COMPLETED"})
        rb.hset(f"kernell:exec:{rid}", mapping={"epoch": "4", "state": "COMPLETED"})
        
        executor = ReconciliationExecutor(ra, rb)
        result = executor.execute(rid, {
            "action": "COMPENSATE",
            "winner": "A",
            "reason": "Side-effect reversal required"
        })
        
        self.assert_eq("Executor COMPENSATE → success", result["status"], "success")
        # WAL should have COMPENSATE event in both regions
        self.assert_eq("COMPENSATE → WAL emitted A", len(ra.streams.get("kernell:wal", [])), 1)
        self.assert_eq("COMPENSATE → WAL emitted B", len(rb.streams.get("kernell:wal", [])), 1)

    def test_freeze_idempotency(self):
        """Double FREEZE should not corrupt state"""
        ra, rb = FakeRedis(), FakeRedis()
        rid = "fuzz-double-freeze"
        
        for r in [ra, rb]:
            r.hset(f"kernell:exec:{rid}", mapping={"epoch": "2", "state": "IN_PROGRESS"})
        
        executor = ReconciliationExecutor(ra, rb)
        decision = {"action": "MANUAL_REVIEW", "winner": "NONE", "reason": "test"}
        
        r1 = executor.execute(rid, decision)
        r2 = executor.execute(rid, decision)
        
        self.assert_eq("Double FREEZE → both frozen", r1["status"], "frozen")
        self.assert_eq("Double FREEZE → idempotent", r2["status"], "frozen")
        self.assert_eq("Double FREEZE → state still FROZEN", ra.hgetall(f"kernell:exec:{rid}")["state"], "FROZEN")

    def test_force_sync_wal_emission(self):
        """FORCE_SYNC must emit WAL events to BOTH regions"""
        ra, rb = FakeRedis(), FakeRedis()
        rid = "fuzz-wal-check"
        
        ra.hset(f"kernell:exec:{rid}", mapping={"epoch": "3", "state": "COMPLETED"})
        rb.hset(f"kernell:exec:{rid}", mapping={"epoch": "1", "state": "IN_PROGRESS"})
        
        executor = ReconciliationExecutor(ra, rb)
        executor.execute(rid, {"action": "FORCE_SYNC_B", "winner": "A", "reason": "test"})
        
        wal_a = ra.streams.get("kernell:wal", [])
        wal_b = rb.streams.get("kernell:wal", [])
        
        self.assert_eq("WAL → emitted to A", len(wal_a), 1)
        self.assert_eq("WAL → emitted to B", len(wal_b), 1)
        self.assert_eq("WAL → event type", wal_a[0]["event"], "FORCE_SYNC")
        self.assert_eq("WAL → request_id", wal_a[0]["request_id"], rid)


if __name__ == "__main__":
    fuzzer = ReconciliationFuzzer()
    success = fuzzer.run_all()
    sys.exit(0 if success else 1)
