"""
KAP Escrow — DEPRECATED: use conrrad_sdk instead.
===================================================
This package is a backward-compatible wrapper maintained for
causal continuity (L2 alias migration).  All real functionality
lives here but the canonical import path is ``conrrad_sdk``.

Deprecation window: 3–6 months from 2026-05-28.
Migration guide: docs/migration/KAP_TO_CONRRAD_SDK.md
Policy: semantic purity is subordinate to causal continuity.
"""

import os as _os
import warnings as _warnings

# ── Deprecation warning (only when imported directly, not via conrrad_sdk) ──
if not _os.environ.get("_CONRRAD_SDK_CANONICAL"):
    _warnings.warn(
        "kap_escrow is deprecated and will be removed in a future release. "
        "Use conrrad_sdk instead.  "
        "See: docs/migration/KAP_TO_CONRRAD_SDK.md",
        DeprecationWarning,
        stacklevel=2,
    )
    # ── Sunset telemetry ──
    try:
        from conrrad_sdk._sunset_telemetry import record_legacy_import
        record_legacy_import("kap_escrow")
    except Exception:
        pass  # telemetry must never break the runtime

# ── Original exports (unchanged — real code stays here) ──
from kap_escrow.engine import EscrowEngine
from kap_escrow.merkle import MerkleTree, build_tx_merkle
from kap_escrow.signing import sign_tx, verify_tx
from kap_escrow.wal import TransactionWAL
from kap_escrow.a2a_compat import AgentCard, validate_agent_card
from kap_escrow.ap2_compat import Mandate, escrow_from_mandate

__version__ = "1.0.0"
__all__ = [
    "EscrowEngine",
    "MerkleTree",
    "build_tx_merkle",
    "sign_tx",
    "verify_tx",
    "TransactionWAL",
    "AgentCard",
    "validate_agent_card",
    "Mandate",
    "escrow_from_mandate",
]
