"""
causal_seal.py — CONRRAD Causal Proof Engine

Generates cryptographically chained proof records that capture both
operational events AND physiological state of the runtime.

This is CONRRAD's strategic differentiator vs SwarmSync:
- SwarmSync seals TRANSACTIONS (did the agent deliver?)
- CONRRAD seals PHYSIOLOGY (was the agent cognitively intact while delivering?)

Each CausalProof contains:
  - Event payload (what happened)
  - Physiological snapshot (entropy velocity, schema integrity, thermal state)
  - Frozen Core checksum (proof that the runtime wasn't tampered)
  - SHA-256 chain link to previous proof (append-only, tamper-evident)

Usage:
    from kernell_sdk.compliance import seal_causal_epoch

    proof = seal_causal_epoch(
        session_id="ses_abc123",
        epoch_events=[
            {"type": "tick_collected", "pair": "EURUSD_otc", "count": 8340},
            {"type": "token_refresh", "method": "playwright-stealth", "success": True},
        ],
        physiology={
            "entropy_velocity": 0.023,
            "schema_integrity": 0.97,
            "thermal_state": "nominal",
            "cognitive_half_life_turns": 147,
            "hurst_exponent": 0.52,
        },
    )

    # proof.proof_id  → "cpf_a7f3..."
    # proof.to_json() → audit-ready JSON
    # proof.to_pdf()  → (future) compliance report
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PROOF_VERSION = 1
_HASH_ALGORITHM = "sha256"
_PROOF_ID_PREFIX = "cpf"  # Causal Proof Fingerprint


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------

@dataclass
class PhysiologicalSnapshot:
    """
    Captures the cognitive health of the runtime at seal time.
    This is what makes CONRRAD proofs fundamentally different from
    transaction-only proof systems.
    """
    entropy_velocity: Optional[float] = None
    schema_integrity: Optional[float] = None
    thermal_state: Optional[str] = None          # nominal | elevated | critical
    cognitive_half_life_turns: Optional[int] = None
    governor_interventions: int = 0
    dirty_buffers: int = 0
    context_pressure: Optional[float] = None     # 0.0-1.0
    hurst_exponent: Optional[float] = None       # For autonomous data pipelines
    uptime_seconds: Optional[float] = None

    def health_verdict(self) -> str:
        """Returns a human-readable health assessment."""
        if self.thermal_state == "critical":
            return "DEGRADED"
        if self.entropy_velocity and self.entropy_velocity > 0.5:
            return "ELEVATED_ENTROPY"
        if self.schema_integrity and self.schema_integrity < 0.8:
            return "SCHEMA_DRIFT"
        if self.dirty_buffers > 5:
            return "BUFFER_PRESSURE"
        return "HEALTHY"


@dataclass
class CausalProof:
    """
    An immutable, cryptographically chained proof record.

    Fields:
        proof_id:           Unique identifier (cpf_<hash_prefix>)
        version:            Proof format version
        session_id:         Runtime session that generated this proof
        epoch_index:        Sequential index within the session
        timestamp_utc:      Unix timestamp (UTC)
        events:             List of operational events in this epoch
        physiology:         PhysiologicalSnapshot at seal time
        frozen_core_hash:   SHA-256 of the Frozen Core components
        previous_proof_id:  Chain link to the previous proof (None for genesis)
        epoch_hash:         SHA-256 of the canonical payload
        chain_hash:         SHA-256(epoch_hash + previous_chain_hash)
    """
    proof_id: str
    version: int
    session_id: str
    epoch_index: int
    timestamp_utc: float
    events: List[Dict[str, Any]]
    physiology: Dict[str, Any]
    health_verdict: str
    frozen_core_hash: Optional[str]
    previous_proof_id: Optional[str]
    epoch_hash: str
    chain_hash: str

    def to_json(self, indent: int = 2) -> str:
        """Serialize to audit-ready JSON."""
        return json.dumps(asdict(self), indent=indent, default=str)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)

    @classmethod
    def from_json(cls, data: str) -> "CausalProof":
        """Deserialize from JSON."""
        return cls(**json.loads(data))


# ---------------------------------------------------------------------------
# Proof Chain State (in-memory, per session)
# ---------------------------------------------------------------------------

class _ProofChainState:
    """Maintains the chain state for a session."""

    def __init__(self):
        self._chains: Dict[str, Dict] = {}

    def get_state(self, session_id: str) -> Dict:
        if session_id not in self._chains:
            self._chains[session_id] = {
                "epoch_index": 0,
                "previous_proof_id": None,
                "previous_chain_hash": "0" * 64,  # Genesis sentinel
            }
        return self._chains[session_id]

    def advance(self, session_id: str, proof: CausalProof):
        self._chains[session_id] = {
            "epoch_index": proof.epoch_index + 1,
            "previous_proof_id": proof.proof_id,
            "previous_chain_hash": proof.chain_hash,
        }


# Module-level chain state
_chain_state = _ProofChainState()


# ---------------------------------------------------------------------------
# Core Functions
# ---------------------------------------------------------------------------

def seal_causal_epoch(
    session_id: str,
    epoch_events: List[Dict[str, Any]],
    physiology: Optional[Dict[str, Any]] = None,
    frozen_core_hash: Optional[str] = None,
) -> CausalProof:
    """
    Seal a causal epoch — generating a cryptographically chained proof
    that captures both operational events AND physiological state.

    Args:
        session_id:       Identifier for the runtime session.
        epoch_events:     List of event dicts (what happened in this epoch).
        physiology:       Dict of physiological metrics at seal time.
                          Keys: entropy_velocity, schema_integrity, thermal_state,
                          cognitive_half_life_turns, governor_interventions,
                          dirty_buffers, context_pressure, hurst_exponent, uptime_seconds.
        frozen_core_hash: SHA-256 of the Frozen Core components.
                          If None, attempts to compute from CONRRAD_CORE_PATH env var.

    Returns:
        CausalProof — immutable, chain-linked proof record.

    Example:
        proof = seal_causal_epoch(
            session_id="quotex-collector-i5",
            epoch_events=[{"type": "tick_batch", "count": 8340, "pairs": 10}],
            physiology={"entropy_velocity": 0.02, "hurst_exponent": 0.52},
        )
    """
    # Build physiological snapshot
    snap = PhysiologicalSnapshot(**(physiology or {}))

    # Get chain state
    state = _chain_state.get_state(session_id)
    epoch_index = state["epoch_index"]
    previous_proof_id = state["previous_proof_id"]
    previous_chain_hash = state["previous_chain_hash"]

    # Resolve frozen core hash
    if frozen_core_hash is None:
        frozen_core_hash = _compute_frozen_core_hash()

    # Timestamp
    timestamp = time.time()

    # Build canonical payload for hashing
    canonical = _canonical_payload(
        session_id=session_id,
        epoch_index=epoch_index,
        timestamp=timestamp,
        events=epoch_events,
        physiology=asdict(snap),
        frozen_core_hash=frozen_core_hash,
    )

    # Compute epoch hash
    epoch_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    # Compute chain hash (links to previous)
    chain_input = f"{epoch_hash}:{previous_chain_hash}"
    chain_hash = hashlib.sha256(chain_input.encode("utf-8")).hexdigest()

    # Generate proof ID
    proof_id = f"{_PROOF_ID_PREFIX}_{chain_hash[:16]}"

    # Build proof
    proof = CausalProof(
        proof_id=proof_id,
        version=_PROOF_VERSION,
        session_id=session_id,
        epoch_index=epoch_index,
        timestamp_utc=timestamp,
        events=epoch_events,
        physiology=asdict(snap),
        health_verdict=snap.health_verdict(),
        frozen_core_hash=frozen_core_hash,
        previous_proof_id=previous_proof_id,
        epoch_hash=epoch_hash,
        chain_hash=chain_hash,
    )

    # Advance chain
    _chain_state.advance(session_id, proof)

    return proof


def verify_proof_chain(proofs: List[CausalProof]) -> Dict[str, Any]:
    """
    Verify the integrity of a chain of CausalProofs.

    Checks:
      1. Sequential epoch indices
      2. Chain hash continuity (each links to previous)
      3. Epoch hash integrity (payload matches hash)
      4. No gaps in proof_id linkage

    Returns:
        {
            "valid": True/False,
            "proofs_checked": int,
            "errors": [str],
            "chain_start": str,  # first proof_id
            "chain_end": str,    # last proof_id
            "total_events": int,
            "health_summary": {"HEALTHY": N, "DEGRADED": M, ...},
        }
    """
    errors = []
    health_counts: Dict[str, int] = {}
    total_events = 0

    if not proofs:
        return {
            "valid": False,
            "proofs_checked": 0,
            "errors": ["Empty proof chain"],
            "chain_start": None,
            "chain_end": None,
            "total_events": 0,
            "health_summary": {},
        }

    previous_chain_hash = "0" * 64  # Genesis

    for i, proof in enumerate(proofs):
        # Check epoch index
        if proof.epoch_index != i:
            errors.append(
                f"Epoch {i}: expected index {i}, got {proof.epoch_index}"
            )

        # Verify epoch hash
        canonical = _canonical_payload(
            session_id=proof.session_id,
            epoch_index=proof.epoch_index,
            timestamp=proof.timestamp_utc,
            events=proof.events,
            physiology=proof.physiology,
            frozen_core_hash=proof.frozen_core_hash,
        )
        expected_epoch = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if proof.epoch_hash != expected_epoch:
            errors.append(
                f"Epoch {i}: epoch_hash mismatch (tampering detected)"
            )

        # Verify chain hash
        chain_input = f"{proof.epoch_hash}:{previous_chain_hash}"
        expected_chain = hashlib.sha256(chain_input.encode("utf-8")).hexdigest()
        if proof.chain_hash != expected_chain:
            errors.append(
                f"Epoch {i}: chain_hash broken (chain discontinuity)"
            )

        # Verify linkage
        if i > 0 and proof.previous_proof_id != proofs[i - 1].proof_id:
            errors.append(
                f"Epoch {i}: previous_proof_id mismatch"
            )

        # Accumulate stats
        health = proof.health_verdict
        health_counts[health] = health_counts.get(health, 0) + 1
        total_events += len(proof.events)
        previous_chain_hash = proof.chain_hash

    return {
        "valid": len(errors) == 0,
        "proofs_checked": len(proofs),
        "errors": errors,
        "chain_start": proofs[0].proof_id,
        "chain_end": proofs[-1].proof_id,
        "total_events": total_events,
        "health_summary": health_counts,
    }


# ---------------------------------------------------------------------------
# Internal Helpers
# ---------------------------------------------------------------------------

def _canonical_payload(
    session_id: str,
    epoch_index: int,
    timestamp: float,
    events: List[Dict],
    physiology: Dict,
    frozen_core_hash: Optional[str],
) -> str:
    """
    Create a deterministic canonical string for hashing.
    JSON with sorted keys ensures reproducibility.
    """
    payload = {
        "v": _PROOF_VERSION,
        "sid": session_id,
        "idx": epoch_index,
        "ts": timestamp,
        "events": events,
        "phys": physiology,
        "fch": frozen_core_hash,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _compute_frozen_core_hash() -> Optional[str]:
    """
    Attempt to compute SHA-256 of the Frozen Core directory.
    Uses CONRRAD_CORE_PATH env var if set.
    Returns None if not available (non-critical).
    """
    core_path = os.environ.get("CONRRAD_CORE_PATH")
    if not core_path or not os.path.isdir(core_path):
        return None

    hasher = hashlib.sha256()
    try:
        for root, dirs, files in sorted(os.walk(core_path)):
            dirs.sort()
            for fname in sorted(files):
                if fname.endswith((".py", ".js", ".sql")):
                    fpath = os.path.join(root, fname)
                    with open(fpath, "rb") as f:
                        hasher.update(f.read())
        return hasher.hexdigest()
    except (OSError, PermissionError):
        return None
