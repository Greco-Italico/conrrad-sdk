"""
Marketplace Fuzzer — Revenue Split & Commerce Invariant Testing
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from kernell_sdk.router.kernell_pay import Ledger, LedgerEntry, InsufficientFundsError
from kernell_sdk.router.marketplace import (
    Skill, SkillRegistry, PurchaseEngine, SkillNotFoundError
)
import json


class FakeRedis:
    def __init__(self):
        self.streams = {}
        self.hashes = {}
        self._counter = 0

    def xadd(self, stream, fields):
        if stream not in self.streams:
            self.streams[stream] = []
        self._counter += 1
        sid = f"{self._counter}-0"
        self.streams[stream].append((sid, fields))
        return sid

    def xrange(self, stream, min="-", max="+"):
        return self.streams.get(stream, [])

    def hset(self, key, field=None, value=None, mapping=None):
        if key not in self.hashes:
            self.hashes[key] = {}
        if mapping:
            self.hashes[key].update({str(k): str(v) for k, v in mapping.items()})
        elif field is not None and value is not None:
            self.hashes[key][field] = value

    def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    def hgetall(self, key):
        return self.hashes.get(key, {})


class MarketplaceFuzzer:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.results = []

    def assert_eq(self, name, actual, expected):
        if actual == expected:
            self.passed += 1
            self.results.append(("PASS", name, ""))
        else:
            self.failed += 1
            self.results.append(("FAIL", name, f"expected={expected}, got={actual}"))

    def assert_raises(self, name, exc_type, fn):
        try:
            fn()
            self.failed += 1
            self.results.append(("FAIL", name, "No exception raised"))
        except exc_type:
            self.passed += 1
            self.results.append(("PASS", name, ""))

    def fresh(self):
        r = FakeRedis()
        ledger = Ledger(r)
        registry = SkillRegistry(r)
        engine = PurchaseEngine(ledger, registry, fee_bps=300)
        return r, ledger, registry, engine

    def seed(self, ledger, account_id, amount):
        ledger.append(LedgerEntry(account_id, amount, "CREDIT", request_id="seed"))

    def run_all(self):
        print("=" * 70)
        print("  MARKETPLACE FUZZER — Commerce Invariant Testing")
        print("=" * 70)

        self.test_skill_registration()
        self.test_full_purchase_lifecycle()
        self.test_revenue_split_accuracy()
        self.test_purchase_insufficient_funds()
        self.test_purchase_nonexistent_skill()
        self.test_cancel_refund()
        self.test_full_refund_zero_sum()
        self.test_creator_earnings()
        self.test_multi_purchase_isolation()
        self.test_conservation_law()

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

    def test_skill_registration(self):
        _, _, registry, _ = self.fresh()
        skill = Skill("s1", "creator_a", "Data Analysis", 500)
        registry.register(skill)
        fetched = registry.get("s1")
        self.assert_eq("Register: skill exists", fetched is not None, True)
        self.assert_eq("Register: name", fetched["name"], "Data Analysis")
        self.assert_eq("Register: price", fetched["price"], 500)

    def test_full_purchase_lifecycle(self):
        _, ledger, registry, engine = self.fresh()
        self.seed(ledger, "buyer", 10000)
        registry.register(Skill("s2", "creator_b", "ML Model", 1000))

        r1 = engine.purchase("buyer", "s2")
        self.assert_eq("Purchase: held", r1["status"], "held")
        self.assert_eq("Purchase: buyer balance after hold", ledger.get_balance("buyer"), 9000)

        r2 = engine.complete(r1["purchase_id"], "buyer", "s2", "creator_b", 1000)
        self.assert_eq("Complete: status", r2["status"], "completed")
        self.assert_eq("Complete: creator paid", ledger.get_balance("creator_b"), 970)
        self.assert_eq("Complete: fee collected", ledger.get_balance("fee"), 30)
        self.assert_eq("Complete: system zero", ledger.get_balance("system"), 0)

    def test_revenue_split_accuracy(self):
        _, ledger, registry, engine = self.fresh()
        self.seed(ledger, "buyer2", 50000)
        registry.register(Skill("s3", "creator_c", "Trading Bot", 10000))

        r1 = engine.purchase("buyer2", "s3")
        r2 = engine.complete(r1["purchase_id"], "buyer2", "s3", "creator_c", 10000)

        self.assert_eq("Split: fee = 300 (3%)", r2["fee"], 300)
        self.assert_eq("Split: creator = 9700", r2["creator_payout"], 9700)
        self.assert_eq("Split: fee wallet", ledger.get_balance("fee"), 300)
        self.assert_eq("Split: creator wallet", ledger.get_balance("creator_c"), 9700)

    def test_purchase_insufficient_funds(self):
        _, ledger, registry, engine = self.fresh()
        self.seed(ledger, "poor_buyer", 100)
        registry.register(Skill("s4", "creator_d", "Expensive Skill", 5000))

        self.assert_raises("InsufficientFunds: rejected",
                          InsufficientFundsError,
                          lambda: engine.purchase("poor_buyer", "s4"))
        self.assert_eq("InsufficientFunds: balance unchanged",
                       ledger.get_balance("poor_buyer"), 100)

    def test_purchase_nonexistent_skill(self):
        _, ledger, _, engine = self.fresh()
        self.seed(ledger, "buyer3", 5000)
        self.assert_raises("NonexistentSkill: rejected",
                          SkillNotFoundError,
                          lambda: engine.purchase("buyer3", "nonexistent"))

    def test_cancel_refund(self):
        _, ledger, registry, engine = self.fresh()
        self.seed(ledger, "buyer4", 5000)
        registry.register(Skill("s5", "creator_e", "Web Scraper", 2000))

        r1 = engine.purchase("buyer4", "s5")
        self.assert_eq("Cancel: buyer after hold", ledger.get_balance("buyer4"), 3000)

        r2 = engine.cancel(r1["purchase_id"], "buyer4", 2000)
        self.assert_eq("Cancel: status", r2["status"], "cancelled")
        self.assert_eq("Cancel: buyer restored", ledger.get_balance("buyer4"), 5000)
        self.assert_eq("Cancel: system zero", ledger.get_balance("system"), 0)

    def test_full_refund_zero_sum(self):
        _, ledger, registry, engine = self.fresh()
        self.seed(ledger, "buyer5", 10000)
        registry.register(Skill("s6", "creator_f", "API Gateway", 3000))

        r1 = engine.purchase("buyer5", "s6")
        engine.complete(r1["purchase_id"], "buyer5", "s6", "creator_f", 3000)

        # Refund
        engine.refund(r1["purchase_id"], "buyer5", "creator_f", 3000)

        self.assert_eq("Refund: buyer whole", ledger.get_balance("buyer5"), 10000)
        self.assert_eq("Refund: creator zero", ledger.get_balance("creator_f"), 0)
        self.assert_eq("Refund: fee zero", ledger.get_balance("fee"), 0)
        self.assert_eq("Refund: system zero", ledger.get_balance("system"), 0)

    def test_creator_earnings(self):
        _, ledger, registry, engine = self.fresh()
        self.seed(ledger, "buyer6", 50000)
        registry.register(Skill("s7", "creator_g", "Skill A", 1000))
        registry.register(Skill("s8", "creator_g", "Skill B", 2000))

        # Two purchases
        r1 = engine.purchase("buyer6", "s7")
        engine.complete(r1["purchase_id"], "buyer6", "s7", "creator_g", 1000)

        r2 = engine.purchase("buyer6", "s8")
        engine.complete(r2["purchase_id"], "buyer6", "s8", "creator_g", 2000)

        earnings = engine.get_creator_earnings("creator_g")
        # 1000*0.97 + 2000*0.97 = 970 + 1940 = 2910
        self.assert_eq("Earnings: net", earnings["net_earnings"], 2910)

    def test_multi_purchase_isolation(self):
        _, ledger, registry, engine = self.fresh()
        self.seed(ledger, "alice", 10000)
        self.seed(ledger, "bob", 10000)
        registry.register(Skill("s9", "creator_h", "Shared Skill", 1000))

        r1 = engine.purchase("alice", "s9")
        engine.complete(r1["purchase_id"], "alice", "s9", "creator_h", 1000)

        self.assert_eq("Isolation: alice debited", ledger.get_balance("alice"), 9000)
        self.assert_eq("Isolation: bob untouched", ledger.get_balance("bob"), 10000)

    def test_conservation_law(self):
        """Total KERN in system must always equal total credits."""
        _, ledger, registry, engine = self.fresh()
        self.seed(ledger, "u1", 20000)
        self.seed(ledger, "u2", 15000)
        registry.register(Skill("s10", "c1", "Skill X", 5000))

        total_credits = 35000

        r1 = engine.purchase("u1", "s10")
        engine.complete(r1["purchase_id"], "u1", "s10", "c1", 5000)

        # Sum all balances
        total = (ledger.get_balance("u1") + ledger.get_balance("u2") +
                 ledger.get_balance("c1") + ledger.get_balance("fee") +
                 ledger.get_balance("treasury") + ledger.get_balance("system"))

        self.assert_eq("Conservation: total KERN preserved", total, total_credits)


if __name__ == "__main__":
    fuzzer = MarketplaceFuzzer()
    success = fuzzer.run_all()
    sys.exit(0 if success else 1)
