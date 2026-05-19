"""
CONRRAD Compliance Layer — Causal Audit Infrastructure

Provides cryptographically sealed proof records for AI agent operations.
Compatible with EU AI Act, SOC 2, and ISO 42001 audit frameworks.

Unlike transaction-only proof systems (e.g. SwarmSync), CONRRAD seals
PHYSIOLOGICAL STATE alongside operational events — proving not just
that an agent acted, but that it maintained cognitive integrity while acting.
"""

from .causal_seal import seal_causal_epoch, CausalProof, verify_proof_chain

__all__ = ["seal_causal_epoch", "CausalProof", "verify_proof_chain"]
