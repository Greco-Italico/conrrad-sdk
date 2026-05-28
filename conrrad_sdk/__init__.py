"""
CONRRAD SDK — Canonical Runtime Surface
========================================
Governed causal runtime for persistent cognitive systems.

    pip install conrrad-sdk

This is the CANONICAL entry point. Legacy packages ``kap_escrow``
and ``kernell_sdk`` are deprecated wrappers that re-export from here.

Migration guide: docs/migration/KAP_TO_CONRRAD_SDK.md
Policy: CONRRAD_SEMANTIC_ONTOLOGY.md — L2 alias migration.
"""

from __future__ import annotations

import os as _os

# ── Internal flag: suppress deprecation warnings from legacy packages
#    when WE are the ones importing them.  This flag is checked by
#    kap_escrow/__init__.py and kernell_sdk/__init__.py.
_os.environ["_CONRRAD_SDK_CANONICAL"] = "1"

__version__ = "2.0.0"
__author__ = "CONRRAD"

# ═══════════════════════════════════════════════════════════════════════
# Re-exports from kernell_sdk  (agent framework — the real code stays
# in kernell_sdk/ until L3 physical migration post-P1)
# ═══════════════════════════════════════════════════════════════════════
from kernell_sdk.agent import Agent                                      # noqa: E402
from kernell_sdk.memory import Memory                                    # noqa: E402
from kernell_sdk.cluster import (                                        # noqa: E402
    ClusterNode, ClusterDiscovery, BountyBoard, Bounty, MemorySync,
)
from kernell_sdk.wallet import Wallet                                    # noqa: E402
from kernell_sdk.config import KernellConfig as ConrradConfig            # noqa: E402
from kernell_sdk.config import KernellConfig                             # noqa: E402  # compat alias
from kernell_sdk.sandbox import ResourceLimits, AgentPermissions         # noqa: E402
from kernell_sdk.identity import AgentPassport, SecurityError            # noqa: E402
from kernell_sdk.gui import AgentGUI                                     # noqa: E402
from kernell_sdk.dashboard import CommandCenter                          # noqa: E402
from kernell_sdk.telemetry import HardwareFingerprint                    # noqa: E402
from kernell_sdk.budget import TokenBudget                               # noqa: E402
from kernell_sdk.resilience import CircuitBreaker, CircuitOpenError      # noqa: E402
from kernell_sdk.tracing import TraceContext, get_current_trace_id       # noqa: E402
from kernell_sdk.health import SLOMonitor, HealthStatus                  # noqa: E402
from kernell_sdk.skill_loader import SkillLoader, SkillConfig            # noqa: E402
from kernell_sdk.token_estimator import estimate_tokens                  # noqa: E402
from kernell_sdk.persister import ToolResultPersister                    # noqa: E402
from kernell_sdk.llm import (                                            # noqa: E402
    BaseLLMProvider, OllamaProvider, AnthropicProvider,
    OpenAIProvider, LLMRouter, ComplexityLevel, LLMMessage,
)
from kernell_sdk.delegation import SubAgentManager, TaskQueue            # noqa: E402
from kernell_sdk.learning.loop import LearningLoop, TaskTrace            # noqa: E402

# ═══════════════════════════════════════════════════════════════════════
# Re-exports from kap_escrow  (escrow engine — Rust bindings stay in
# kap_escrow/kap_core.abi3.so until L3)
# ═══════════════════════════════════════════════════════════════════════
from kap_escrow.engine import EscrowEngine                               # noqa: E402
from kap_escrow.merkle import MerkleTree, build_tx_merkle                # noqa: E402
from kap_escrow.signing import sign_tx, verify_tx                        # noqa: E402
from kap_escrow.wal import TransactionWAL                                # noqa: E402
from kap_escrow.a2a_compat import AgentCard, validate_agent_card         # noqa: E402
from kap_escrow.ap2_compat import Mandate, escrow_from_mandate           # noqa: E402

# ═══════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════
__all__ = [
    # ── Identity ──
    "__version__",
    "ConrradConfig",
    # ── Agent framework (from kernell_sdk) ──
    "Agent",
    "Memory",
    "ClusterNode",
    "ClusterDiscovery",
    "BountyBoard",
    "Bounty",
    "MemorySync",
    "Wallet",
    "KernellConfig",  # compat alias — use ConrradConfig
    "ResourceLimits",
    "AgentPermissions",
    "AgentPassport",
    "SecurityError",
    "AgentGUI",
    "CommandCenter",
    "HardwareFingerprint",
    "TokenBudget",
    "CircuitBreaker",
    "CircuitOpenError",
    "TraceContext",
    "get_current_trace_id",
    "SLOMonitor",
    "HealthStatus",
    "SkillLoader",
    "SkillConfig",
    "estimate_tokens",
    "ToolResultPersister",
    "BaseLLMProvider",
    "OllamaProvider",
    "AnthropicProvider",
    "OpenAIProvider",
    "LLMRouter",
    "ComplexityLevel",
    "LLMMessage",
    "SubAgentManager",
    "TaskQueue",
    "LearningLoop",
    "TaskTrace",
    # ── Escrow engine (from kap_escrow) ──
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
