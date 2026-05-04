"""
Kernell Marketplace — Agent-to-Agent Commerce Engine

Built on top of Kernell Pay's immutable ledger.

Flow:
1. Creator registers a Skill with pricing
2. Buyer purchases → HOLD from buyer
3. Execution completes → CAPTURE with revenue split:
   - Creator gets (100% - platform_fee)
   - Platform gets platform_fee (default 3%)
4. Execution fails → RELEASE hold back to buyer

Revenue Split:
  CAPTURE 1000 KERN →
    Creator:  970 KERN (97%)
    Platform:  30 KERN (3% fee)
"""

import time
import json
import hashlib
from typing import Optional, Dict, List

from kernell_sdk.router.kernell_pay import (
    Ledger, LedgerEntry, SettlementEngine, InsufficientFundsError
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. Skill Registry
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class Skill:
    def __init__(self, skill_id: str, creator_id: str, name: str,
                 price: int, currency: str = "KERN",
                 description: str = "", category: str = "general"):
        self.skill_id = skill_id
        self.creator_id = creator_id
        self.name = name
        self.price = price
        self.currency = currency
        self.description = description
        self.category = category
        self.created_at = time.time()
        self.active = True

    def to_dict(self) -> dict:
        return {
            "skill_id": self.skill_id,
            "creator_id": self.creator_id,
            "name": self.name,
            "price": self.price,
            "currency": self.currency,
            "description": self.description,
            "category": self.category,
            "created_at": self.created_at,
            "active": self.active
        }


class SkillRegistry:
    HASH_KEY = "kernell:marketplace:skills"

    def __init__(self, redis_client):
        self.r = redis_client

    def register(self, skill: Skill) -> str:
        self.r.hset(self.HASH_KEY, skill.skill_id, json.dumps(skill.to_dict()))
        return skill.skill_id

    def get(self, skill_id: str) -> Optional[dict]:
        raw = self.r.hget(self.HASH_KEY, skill_id)
        if not raw:
            return None
        return json.loads(raw)

    def list_all(self) -> List[dict]:
        raw = self.r.hgetall(self.HASH_KEY)
        skills = []
        for k, v in raw.items():
            data = json.loads(v)
            if data.get("active", True):
                skills.append(data)
        return skills

    def list_by_creator(self, creator_id: str) -> List[dict]:
        return [s for s in self.list_all() if s.get("creator_id") == creator_id]

    def deactivate(self, skill_id: str):
        skill = self.get(skill_id)
        if skill:
            skill["active"] = False
            self.r.hset(self.HASH_KEY, skill_id, json.dumps(skill))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. Purchase Engine (Revenue Split)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class PurchaseEngine:
    """
    Handles the full purchase lifecycle with revenue split:
      1. purchase()  → HOLD from buyer
      2. complete()  → CAPTURE with split (creator + platform fee)
      3. cancel()    → RELEASE hold back to buyer
      4. refund()    → REVERSAL of completed purchase
    """

    def __init__(self, ledger: Ledger, registry: SkillRegistry, fee_bps: int = 300):
        self.ledger = ledger
        self.registry = registry
        self.fee_bps = fee_bps  # Platform fee in basis points (300 = 3%)

    def purchase(self, buyer_id: str, skill_id: str) -> dict:
        """Initiate purchase: HOLD funds from buyer."""
        skill = self.registry.get(skill_id)
        if not skill:
            raise SkillNotFoundError(f"Skill {skill_id} not found")
        if not skill.get("active", True):
            raise SkillNotFoundError(f"Skill {skill_id} is inactive")

        price = skill["price"]
        creator_id = skill["creator_id"]
        purchase_id = f"purchase-{skill_id}-{buyer_id}-{int(time.time())}"

        # Check buyer has sufficient funds
        available = self.ledger.get_available_balance(buyer_id)
        if available < price:
            raise InsufficientFundsError(
                f"Insufficient funds: available={available}, required={price}"
            )

        # HOLD from buyer
        self.ledger.append(LedgerEntry(
            account_id=buyer_id,
            delta=-price,
            entry_type="HOLD",
            request_id=purchase_id,
            memo=f"Purchase hold: {skill['name']}",
            metadata={"skill_id": skill_id, "creator_id": creator_id}
        ))

        # Credit system wallet
        self.ledger.append(LedgerEntry(
            account_id="system",
            delta=price,
            entry_type="HOLD",
            request_id=purchase_id,
            memo=f"Hold received from {buyer_id}"
        ))

        return {
            "status": "held",
            "purchase_id": purchase_id,
            "skill_id": skill_id,
            "buyer_id": buyer_id,
            "creator_id": creator_id,
            "price": price
        }

    def complete(self, purchase_id: str, buyer_id: str,
                 skill_id: str, creator_id: str, price: int) -> dict:
        """Complete purchase: CAPTURE with revenue split."""
        fee = (price * self.fee_bps) // 10000
        creator_payout = price - fee

        # Release from system hold
        self.ledger.append(LedgerEntry(
            account_id="system",
            delta=-price,
            entry_type="CAPTURE",
            request_id=purchase_id,
            memo=f"Capture for {purchase_id}"
        ))

        # Platform fee
        if fee > 0:
            self.ledger.append(LedgerEntry(
                account_id="fee",
                delta=fee,
                entry_type="FEE",
                request_id=purchase_id,
                memo=f"Platform fee {self.fee_bps}bps on {price} KERN"
            ))

        # Creator payout
        self.ledger.append(LedgerEntry(
            account_id=creator_id,
            delta=creator_payout,
            entry_type="CREDIT",
            request_id=purchase_id,
            memo=f"Revenue for skill {skill_id}",
            metadata={"skill_id": skill_id, "buyer_id": buyer_id}
        ))

        return {
            "status": "completed",
            "purchase_id": purchase_id,
            "gross": price,
            "fee": fee,
            "creator_payout": creator_payout,
            "fee_bps": self.fee_bps
        }

    def cancel(self, purchase_id: str, buyer_id: str, price: int) -> dict:
        """Cancel purchase: RELEASE hold back to buyer."""
        # Release from system
        self.ledger.append(LedgerEntry(
            account_id="system",
            delta=-price,
            entry_type="RELEASE",
            request_id=purchase_id,
            memo=f"Release for {purchase_id}"
        ))

        # Refund to buyer
        self.ledger.append(LedgerEntry(
            account_id=buyer_id,
            delta=price,
            entry_type="RELEASE",
            request_id=purchase_id,
            memo=f"Purchase cancelled: {purchase_id}"
        ))

        return {"status": "cancelled", "purchase_id": purchase_id, "refunded": price}

    def refund(self, purchase_id: str, buyer_id: str,
               creator_id: str, price: int) -> dict:
        """Refund a completed purchase: REVERSAL entries."""
        fee = (price * self.fee_bps) // 10000
        creator_payout = price - fee

        # Reverse creator payout
        self.ledger.append(LedgerEntry(
            account_id=creator_id,
            delta=-creator_payout,
            entry_type="REVERSAL",
            request_id=purchase_id,
            memo=f"Refund reversal for {purchase_id}"
        ))

        # Reverse platform fee
        if fee > 0:
            self.ledger.append(LedgerEntry(
                account_id="fee",
                delta=-fee,
                entry_type="REVERSAL",
                request_id=purchase_id,
                memo=f"Fee reversal for {purchase_id}"
            ))

        # Credit buyer back
        self.ledger.append(LedgerEntry(
            account_id=buyer_id,
            delta=price,
            entry_type="REVERSAL",
            request_id=purchase_id,
            memo=f"Full refund for {purchase_id}"
        ))

        return {"status": "refunded", "purchase_id": purchase_id, "amount": price}

    def get_creator_earnings(self, creator_id: str) -> dict:
        """Get total earnings for a creator from the ledger."""
        entries = self.ledger.get_entries(account_id=creator_id)
        total_earned = sum(int(e.get("delta", 0)) for e in entries
                         if e.get("entry_type") == "CREDIT")
        total_reversed = abs(sum(int(e.get("delta", 0)) for e in entries
                                if e.get("entry_type") == "REVERSAL"))
        return {
            "creator_id": creator_id,
            "total_earned": total_earned,
            "total_reversed": total_reversed,
            "net_earnings": total_earned - total_reversed,
            "currency": "KERN"
        }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. Exceptions
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class SkillNotFoundError(Exception):
    pass
