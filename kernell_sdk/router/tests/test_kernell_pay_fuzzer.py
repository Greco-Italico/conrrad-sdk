"""
Kernell Pay Fuzzer — Financial Invariant Stress Testing

Tests:
1. Basic HOLD → CAPTURE flow
2. HOLD → RELEASE flow (cancellation)
3. Insufficient funds rejection
4. Full COMPENSATE reversal (zero-sum)
5. Double HOLD attack
6. Fee calculation accuracy
7. Ledger immutability (no balance stored)
8. PaymentHook lifecycle
9. Hold-then-compensate (edge case)
10. Multi-account isolation
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from kernell_sdk.router.kernell_pay import (
    Ledger, LedgerEntry, SettlementEngine, PaymentHook, InsufficientFundsError
)


class FakeRedis:
    """In-memory Redis mock"""
    def __init__(self):
        self.streams = {}
        self._counter = 0

    def xadd(self, stream, fields):
        if stream not in self.streams:
            self.streams[stream] = []
        self._counter += 1
        stream_id = f"{self._counter}-0"
        self.streams[stream].append((stream_id, fields))
        return stream_id

    def xrange(self, stream, min="-", max="+"):
        return self.streams.get(stream, [])


class KernellPayFuzzer:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.results = []

    def assert_eq(self, test_name, actual, expected, field=""):
        if actual == expected:
            self.passed += 1
            self.results.append(("PASS", test_name, ""))
        else:
            self.failed += 1
            self.results.append(("FAIL", test_name, f"expected={expected}, got={actual}"))

    def assert_raises(self, test_name, exc_type, fn):
        try:
            fn()
            self.failed += 1
            self.results.append(("FAIL", test_name, "Expected exception not raised"))
        except exc_type:
            self.passed += 1
            self.results.append(("PASS", test_name, ""))
        except Exception as e:
            self.failed += 1
            self.results.append(("FAIL", test_name, f"Wrong exception: {type(e).__name__}: {e}"))

    def fresh(self):
        """Create fresh ledger + settlement for each test"""
        r = FakeRedis()
        ledger = Ledger(r)
        settlement = SettlementEngine(ledger, fee_bps=300)  # 3%
        return r, ledger, settlement

    def seed_balance(self, ledger, account_id, amount):
        """Seed an account with initial balance"""
        ledger.append(LedgerEntry(
            account_id=account_id,
            delta=amount,
            entry_type="CREDIT",
            request_id="seed",
            memo="Initial balance"
        ))

    def run_all(self):
        print("=" * 70)
        print("  KERNELL PAY FUZZER — Financial Invariant Testing")
        print("=" * 70)

        self.test_basic_hold_capture()
        self.test_hold_release()
        self.test_insufficient_funds()
        self.test_full_compensation_zero_sum()
        self.test_fee_calculation()
        self.test_double_hold_attack()
        self.test_payment_hook_lifecycle()
        self.test_multi_account_isolation()
        self.test_hold_available_balance()
        self.test_ledger_derived_balance()
        self.test_reversal_after_capture()
        self.test_zero_amount_edge()

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

    # ─── Tests ───────────────────────────────────────────────────────────

    def test_basic_hold_capture(self):
        """HOLD 1000 → CAPTURE → fee=30, treasury=970"""
        _, ledger, settlement = self.fresh()
        self.seed_balance(ledger, "user_1", 5000)

        settlement.hold("user_1", 1000, "req-1")
        result = settlement.capture("user_1", 1000, "req-1")

        self.assert_eq("HOLD→CAPTURE: fee", result["fee"], 30)
        self.assert_eq("HOLD→CAPTURE: net", result["net"], 970)
        self.assert_eq("HOLD→CAPTURE: user balance", ledger.get_balance("user_1"), 4000)
        self.assert_eq("HOLD→CAPTURE: fee wallet", ledger.get_balance("fee"), 30)
        self.assert_eq("HOLD→CAPTURE: treasury", ledger.get_balance("treasury"), 970)
        self.assert_eq("HOLD→CAPTURE: system zero", ledger.get_balance("system"), 0)

    def test_hold_release(self):
        """HOLD 500 → RELEASE → user gets funds back"""
        _, ledger, settlement = self.fresh()
        self.seed_balance(ledger, "user_2", 3000)

        settlement.hold("user_2", 500, "req-2")
        self.assert_eq("HOLD: user balance after hold", ledger.get_balance("user_2"), 2500)

        settlement.release("user_2", 500, "req-2")
        self.assert_eq("RELEASE: user balance restored", ledger.get_balance("user_2"), 3000)
        self.assert_eq("RELEASE: system zero", ledger.get_balance("system"), 0)

    def test_insufficient_funds(self):
        """HOLD more than available → InsufficientFundsError"""
        _, ledger, settlement = self.fresh()
        self.seed_balance(ledger, "user_3", 100)

        self.assert_raises(
            "InsufficientFunds: rejected",
            InsufficientFundsError,
            lambda: settlement.hold("user_3", 500, "req-3")
        )
        self.assert_eq("InsufficientFunds: balance unchanged", ledger.get_balance("user_3"), 100)

    def test_full_compensation_zero_sum(self):
        """HOLD → CAPTURE → COMPENSATE → all wallets return to zero"""
        _, ledger, settlement = self.fresh()
        self.seed_balance(ledger, "user_4", 2000)

        settlement.hold("user_4", 1000, "req-4")
        settlement.capture("user_4", 1000, "req-4")

        # After capture: user=1000, fee=30, treasury=970
        settlement.compensate("user_4", 1000, "req-4")

        # After compensate: everything back
        self.assert_eq("COMPENSATE: user restored", ledger.get_balance("user_4"), 2000)
        self.assert_eq("COMPENSATE: fee zero", ledger.get_balance("fee"), 0)
        self.assert_eq("COMPENSATE: treasury zero", ledger.get_balance("treasury"), 0)
        self.assert_eq("COMPENSATE: system zero", ledger.get_balance("system"), 0)

    def test_fee_calculation(self):
        """Fee = 3% of 10000 = 300"""
        _, ledger, settlement = self.fresh()
        self.seed_balance(ledger, "user_5", 50000)

        settlement.hold("user_5", 10000, "req-5")
        result = settlement.capture("user_5", 10000, "req-5")

        self.assert_eq("Fee: 3% of 10000", result["fee"], 300)
        self.assert_eq("Fee: net = 9700", result["net"], 9700)

    def test_double_hold_attack(self):
        """Two holds should both reserve funds — second fails if insufficient"""
        _, ledger, settlement = self.fresh()
        self.seed_balance(ledger, "user_6", 1500)

        settlement.hold("user_6", 1000, "req-6a")

        # Second hold should fail — only 500 available
        self.assert_raises(
            "DoubleHold: second rejected",
            InsufficientFundsError,
            lambda: settlement.hold("user_6", 1000, "req-6b")
        )

    def test_payment_hook_lifecycle(self):
        """PaymentHook: START→HOLD, COMMIT→CAPTURE, full lifecycle"""
        _, ledger, settlement = self.fresh()
        self.seed_balance(ledger, "agent_1", 10000)
        hook = PaymentHook(settlement)

        r1 = hook.on_event("START", "agent_1", 500, "exec-1")
        self.assert_eq("Hook START: held", r1["status"], "held")

        r2 = hook.on_event("COMMIT", "agent_1", 500, "exec-1")
        self.assert_eq("Hook COMMIT: captured", r2["status"], "captured")
        self.assert_eq("Hook: user balance", ledger.get_balance("agent_1"), 9500)

    def test_multi_account_isolation(self):
        """Operations on user_A don't affect user_B"""
        _, ledger, settlement = self.fresh()
        self.seed_balance(ledger, "alice", 5000)
        self.seed_balance(ledger, "bob", 3000)

        settlement.hold("alice", 2000, "req-a")
        settlement.capture("alice", 2000, "req-a")

        self.assert_eq("Isolation: alice debited", ledger.get_balance("alice"), 3000)
        self.assert_eq("Isolation: bob untouched", ledger.get_balance("bob"), 3000)

    def test_hold_available_balance(self):
        """Available = balance - holds"""
        _, ledger, settlement = self.fresh()
        self.seed_balance(ledger, "user_7", 5000)

        settlement.hold("user_7", 2000, "req-7")

        self.assert_eq("Available: total balance", ledger.get_balance("user_7"), 3000)
        self.assert_eq("Available: spendable", ledger.get_available_balance("user_7"), 3000)

    def test_ledger_derived_balance(self):
        """Balance is always derived, never stored"""
        _, ledger, _ = self.fresh()

        # Multiple credits
        for i in range(5):
            ledger.append(LedgerEntry("user_8", 100, "CREDIT", request_id=f"c-{i}"))

        self.assert_eq("Derived: sum of 5x100", ledger.get_balance("user_8"), 500)

        # Debit
        ledger.append(LedgerEntry("user_8", -200, "DEBIT", request_id="d-1"))
        self.assert_eq("Derived: after debit", ledger.get_balance("user_8"), 300)

    def test_reversal_after_capture(self):
        """Reversal creates new entries, doesn't delete old ones"""
        r, ledger, settlement = self.fresh()
        self.seed_balance(ledger, "user_9", 10000)

        settlement.hold("user_9", 5000, "req-9")
        settlement.capture("user_9", 5000, "req-9")

        entries_before = len(r.streams.get("kernell:ledger", []))
        settlement.compensate("user_9", 5000, "req-9")
        entries_after = len(r.streams.get("kernell:ledger", []))

        self.assert_eq("Reversal: entries only added", entries_after > entries_before, True)
        self.assert_eq("Reversal: user whole", ledger.get_balance("user_9"), 10000)

    def test_zero_amount_edge(self):
        """Zero-amount operations should work without errors"""
        _, ledger, settlement = self.fresh()
        self.seed_balance(ledger, "user_10", 1000)

        result = settlement.hold("user_10", 0, "req-zero")
        self.assert_eq("Zero: hold works", result["status"], "held")
        self.assert_eq("Zero: balance unchanged", ledger.get_balance("user_10"), 1000)


if __name__ == "__main__":
    fuzzer = KernellPayFuzzer()
    success = fuzzer.run_all()
    sys.exit(0 if success else 1)
