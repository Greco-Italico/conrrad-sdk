"""
Kernell Pay — Immutable Ledger & Settlement Engine

Core invariants:
1. Balance is NEVER stored as mutable state — always derived from ledger
2. Every financial mutation goes through the ledger (append-only)
3. Reversals are new entries, never deletions
4. Every ledger entry links to a request_id for full WAL traceability
5. Fee collection is atomic with settlement
"""

import time
import json
import hashlib
from typing import Optional, Dict, Any, List


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. Ledger Entry Types
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ENTRY_TYPES = {
    "CREDIT":    "Funds added to account",
    "DEBIT":     "Funds removed from account",
    "FEE":       "Platform fee collected",
    "REVERSAL":  "Compensating entry (undo)",
    "HOLD":      "Funds reserved (not yet settled)",
    "CAPTURE":   "Held funds settled",
    "RELEASE":   "Held funds returned",
    "TRANSFER":  "Inter-wallet movement",
}

WALLETS = {
    "system":   "kernell:wallet:system",
    "fee":      "kernell:wallet:fee",
    "treasury": "kernell:wallet:treasury",
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. Ledger (append-only, immutable)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class LedgerEntry:
    def __init__(self, account_id: str, delta: int, entry_type: str,
                 currency: str = "KERN", request_id: str = "",
                 memo: str = "", metadata: dict = None):
        self.account_id = account_id
        self.delta = delta
        self.entry_type = entry_type
        self.currency = currency
        self.request_id = request_id
        self.memo = memo
        self.metadata = metadata or {}
        self.ts = time.time()
        self.fingerprint = self._compute_fp()

    def _compute_fp(self) -> str:
        payload = f"{self.account_id}:{self.delta}:{self.entry_type}:{self.request_id}:{self.ts}"
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        return {
            "account_id": self.account_id,
            "delta": str(self.delta),
            "entry_type": self.entry_type,
            "currency": self.currency,
            "request_id": self.request_id,
            "memo": self.memo,
            "metadata": json.dumps(self.metadata),
            "ts": str(self.ts),
            "fingerprint": self.fingerprint,
        }


class Ledger:
    STREAM = "kernell:ledger"

    def __init__(self, redis_client):
        self.r = redis_client

    def append(self, entry: LedgerEntry) -> str:
        """Append entry to immutable ledger. Returns stream ID."""
        if entry.entry_type not in ENTRY_TYPES:
            raise ValueError(f"Invalid entry type: {entry.entry_type}")
        return self.r.xadd(self.STREAM, entry.to_dict())

    def get_balance(self, account_id: str, currency: str = "KERN") -> int:
        """Derive balance from ledger — NEVER from mutable state."""
        entries = self.r.xrange(self.STREAM)
        balance = 0
        for _, e in entries:
            if e.get("account_id") == account_id and e.get("currency", "KERN") == currency:
                balance += int(e.get("delta", 0))
        return balance

    def get_entries(self, account_id: str = None, request_id: str = None,
                    entry_type: str = None) -> List[Dict]:
        """Query ledger entries with optional filters."""
        entries = self.r.xrange(self.STREAM)
        results = []
        for stream_id, e in entries:
            if account_id and e.get("account_id") != account_id:
                continue
            if request_id and e.get("request_id") != request_id:
                continue
            if entry_type and e.get("entry_type") != entry_type:
                continue
            e["stream_id"] = stream_id
            results.append(e)
        return results

    def get_holds(self, account_id: str) -> int:
        """Calculate total held (reserved but unsettled) funds."""
        entries = self.r.xrange(self.STREAM)
        holds = 0
        for _, e in entries:
            if e.get("account_id") != account_id:
                continue
            if e.get("entry_type") == "HOLD":
                holds += abs(int(e.get("delta", 0)))
            elif e.get("entry_type") in ("CAPTURE", "RELEASE"):
                holds -= abs(int(e.get("delta", 0)))
        return max(0, holds)

    def get_available_balance(self, account_id: str) -> int:
        """Available = balance (holds are already reflected as debits)."""
        return self.get_balance(account_id)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. Settlement Engine (HOLD → CAPTURE flow)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class SettlementEngine:
    """
    Two-phase settlement:
      1. HOLD    — reserve funds (debit user, hold in system)
      2. CAPTURE — settle the hold (move to fee + treasury)
      3. RELEASE — cancel the hold (refund to user)
    """

    DEFAULT_FEE_BPS = 300  # 3% = 300 basis points

    def __init__(self, ledger: Ledger, fee_bps: int = None):
        self.ledger = ledger
        self.fee_bps = fee_bps or self.DEFAULT_FEE_BPS

    def hold(self, account_id: str, amount: int, request_id: str) -> dict:
        """Phase 1: Reserve funds from user account."""
        available = self.ledger.get_available_balance(account_id)
        if available < amount:
            raise InsufficientFundsError(
                f"Insufficient funds: available={available}, required={amount}"
            )

        # Debit user
        self.ledger.append(LedgerEntry(
            account_id=account_id,
            delta=-amount,
            entry_type="HOLD",
            request_id=request_id,
            memo=f"Hold for execution {request_id}"
        ))

        # Credit system wallet
        self.ledger.append(LedgerEntry(
            account_id="system",
            delta=amount,
            entry_type="HOLD",
            request_id=request_id,
            memo=f"Hold received from {account_id}"
        ))

        return {"status": "held", "amount": amount, "request_id": request_id}

    def capture(self, account_id: str, amount: int, request_id: str) -> dict:
        """Phase 2: Settle the hold — split into fee + treasury."""
        fee = (amount * self.fee_bps) // 10000
        net = amount - fee

        # Release from system hold
        self.ledger.append(LedgerEntry(
            account_id="system",
            delta=-amount,
            entry_type="CAPTURE",
            request_id=request_id,
            memo=f"Capture settlement for {request_id}"
        ))

        # Fee to fee wallet
        if fee > 0:
            self.ledger.append(LedgerEntry(
                account_id="fee",
                delta=fee,
                entry_type="FEE",
                request_id=request_id,
                memo=f"Fee {self.fee_bps}bps on {amount} KERN"
            ))

        # Net to treasury
        self.ledger.append(LedgerEntry(
            account_id="treasury",
            delta=net,
            entry_type="CAPTURE",
            request_id=request_id,
            memo=f"Net settlement for {request_id}"
        ))

        return {
            "status": "captured",
            "gross": amount,
            "fee": fee,
            "net": net,
            "fee_bps": self.fee_bps,
            "request_id": request_id
        }

    def release(self, account_id: str, amount: int, request_id: str) -> dict:
        """Cancel hold — refund to user."""
        # Release from system
        self.ledger.append(LedgerEntry(
            account_id="system",
            delta=-amount,
            entry_type="RELEASE",
            request_id=request_id,
            memo=f"Release hold for {request_id}"
        ))

        # Refund to user
        self.ledger.append(LedgerEntry(
            account_id=account_id,
            delta=amount,
            entry_type="RELEASE",
            request_id=request_id,
            memo=f"Hold released for {request_id}"
        ))

        return {"status": "released", "amount": amount, "request_id": request_id}

    def compensate(self, account_id: str, amount: int, request_id: str) -> dict:
        """Full reversal — undo a captured settlement."""
        fee = (amount * self.fee_bps) // 10000
        net = amount - fee

        # Reverse treasury
        self.ledger.append(LedgerEntry(
            account_id="treasury",
            delta=-net,
            entry_type="REVERSAL",
            request_id=request_id,
            memo=f"Reversal of settlement {request_id}"
        ))

        # Reverse fee
        if fee > 0:
            self.ledger.append(LedgerEntry(
                account_id="fee",
                delta=-fee,
                entry_type="REVERSAL",
                request_id=request_id,
                memo=f"Fee reversal for {request_id}"
            ))

        # Credit user back
        self.ledger.append(LedgerEntry(
            account_id=account_id,
            delta=amount,
            entry_type="REVERSAL",
            request_id=request_id,
            memo=f"Full compensation for {request_id}"
        ))

        return {"status": "compensated", "amount": amount, "request_id": request_id}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. Exceptions
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class InsufficientFundsError(Exception):
    pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. WAL Integration Hook
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class PaymentHook:
    """
    Hooks into the execution lifecycle:
      START  → HOLD funds
      COMMIT → CAPTURE (settle)
      COMPENSATE → REVERSAL
      FAILED → RELEASE hold
    """

    def __init__(self, settlement: SettlementEngine):
        self.settlement = settlement

    def on_event(self, event_type: str, account_id: str,
                 amount: int, request_id: str) -> Optional[dict]:

        if event_type == "START":
            return self.settlement.hold(account_id, amount, request_id)

        elif event_type == "COMMIT":
            return self.settlement.capture(account_id, amount, request_id)

        elif event_type == "COMPENSATE":
            return self.settlement.compensate(account_id, amount, request_id)

        elif event_type in ("FAILED", "RELEASE"):
            return self.settlement.release(account_id, amount, request_id)

        return None
