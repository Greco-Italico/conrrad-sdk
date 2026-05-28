"""
Kernell SDK — DEPRECATED: use conrrad_sdk instead.
====================================================
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
        "kernell_sdk is deprecated and will be removed in a future release. "
        "Use conrrad_sdk instead.  "
        "See: docs/migration/KAP_TO_CONRRAD_SDK.md",
        DeprecationWarning,
        stacklevel=2,
    )
    # ── Sunset telemetry ──
    try:
        from conrrad_sdk._sunset_telemetry import record_legacy_import
        record_legacy_import("kernell_sdk")
    except Exception:
        pass  # telemetry must never break the runtime

# ── Original exports (unchanged — real code stays here) ──
from importlib.metadata import version as _pkg_version, PackageNotFoundError

try:
    __version__ = _pkg_version("conrrad-sdk")
except PackageNotFoundError:
    try:
        __version__ = _pkg_version("kernell-os")
    except PackageNotFoundError:
        __version__ = "dev"

__author__ = "CONRRAD"

from kernell_sdk.agent import Agent
from kernell_sdk.memory import Memory
from kernell_sdk.cluster import ClusterNode, ClusterDiscovery, BountyBoard, Bounty, MemorySync
from kernell_sdk.wallet import Wallet
from kernell_sdk.config import KernellConfig
from kernell_sdk.sandbox import ResourceLimits, AgentPermissions
from kernell_sdk.identity import AgentPassport, SecurityError
from kernell_sdk.gui import AgentGUI
from kernell_sdk.dashboard import CommandCenter
from kernell_sdk.telemetry import HardwareFingerprint
from kernell_sdk.budget import TokenBudget
from kernell_sdk.resilience import CircuitBreaker, CircuitOpenError
from kernell_sdk.tracing import TraceContext, get_current_trace_id
from kernell_sdk.health import SLOMonitor, HealthStatus
from kernell_sdk.skill_loader import SkillLoader, SkillConfig
from kernell_sdk.token_estimator import estimate_tokens
from kernell_sdk.persister import ToolResultPersister
from kernell_sdk.llm import (
    BaseLLMProvider, OllamaProvider, AnthropicProvider,
    OpenAIProvider, LLMRouter, ComplexityLevel, LLMMessage
)
from kernell_sdk.delegation import SubAgentManager, TaskQueue
from kernell_sdk.learning.loop import LearningLoop, TaskTrace

__all__ = [
    "Agent", "Memory", "ClusterNode", "ClusterDiscovery", "BountyBoard", "Bounty", "MemorySync",
    "Wallet", "KernellConfig",
    "ResourceLimits", "AgentPermissions", "AgentPassport",
    "AgentGUI", "CommandCenter",
    "HardwareFingerprint", "SecurityError",
    "TokenBudget", "CircuitBreaker", "CircuitOpenError",
    "TraceContext", "get_current_trace_id",
    "SLOMonitor", "HealthStatus",
    "SkillLoader", "SkillConfig",
    "estimate_tokens", "ToolResultPersister",
    "BaseLLMProvider", "OllamaProvider", "AnthropicProvider",
    "OpenAIProvider", "LLMRouter", "ComplexityLevel", "LLMMessage",
    "SubAgentManager", "TaskQueue",
    "LearningLoop", "TaskTrace",
    "__version__",
]
