"""
Kernell OS — Adaptive Budgeting Policy
══════════════════════════════════════
Policy layer that bridges the Risk Engine and Execution Supervisor.
Couples confidence and surface density directly to physical constraints.

"The system physically cannot be aggressive where it is uncertain."

Outputs an Execution Profile dictating:
  1. Effective Budget (USD)
  2. Allowed Model Tier
  3. Max Output Tokens
"""
from dataclasses import dataclass
from typing import Dict, Any, Optional

from .calibration_engine import CalibrationEngine


@dataclass
class ExecutionProfile:
    """The constrained parameters for an execution."""
    effective_budget_usd: float
    model_name: str
    max_output_tokens: int
    metadata: Dict[str, Any]


class AdaptiveBudgetPolicy:
    """
    Computes deterministic execution constraints based on uncertainty.
    Uses a quadratic confidence curve to aggressively penalize low-confidence
    zones while unlocking full resources for known, high-confidence regimes.
    """

    def __init__(
        self,
        floor_multiplier: float = 0.1,  # Minimum 10% budget to avoid instant death
        tier1_model: str = "gpt-4o",
        tier2_model: str = "gpt-4o-mini",
        tier3_model: str = "gemini-2.5-flash",
        calibration_engine: Optional[CalibrationEngine] = None,
    ):
        self.floor_multiplier = floor_multiplier
        self.tier1_model = tier1_model
        self.tier2_model = tier2_model
        self.tier3_model = tier3_model
        self.calibration_engine = calibration_engine
        
        # Rough cost per token out for invariant checking
        self.pricing = {
            self.tier1_model: 0.0000150,  # $15 / 1M
            self.tier2_model: 0.0000006,  # $0.60 / 1M
            self.tier3_model: 0.0000003,  # $0.30 / 1M
        }

    def compute_profile(
        self,
        base_budget_usd: float,
        base_max_tokens: int,
        confidence: float,
        surface_density: float,
        gamma: float = 2.0
    ) -> ExecutionProfile:
        """
        Compute the effective execution profile enforcing strict economic invariants.
        """
        # Constrain inputs
        raw_confidence = max(0.0, min(1.0, confidence))
        density = max(0.0, min(1.0, surface_density))
        gamma = max(1.0, min(5.0, gamma))
        
        # 0. Calibration Phase (Control Theory Feedback Loop)
        if self.calibration_engine:
            confidence = self.calibration_engine.calibrate("unknown", raw_confidence, density)
        else:
            confidence = raw_confidence

        # 1. Calculate Effective Budget (Exponential Curve)
        raw_budget = base_budget_usd * (confidence ** gamma) * density
        floor_budget = base_budget_usd * self.floor_multiplier
        effective_budget = max(floor_budget, raw_budget)

        # 2. Determine Intelligence Target
        if confidence >= 0.7:
            target_model = self.tier1_model
        elif confidence >= 0.3:
            target_model = self.tier2_model
        else:
            target_model = self.tier3_model

        # 3. Invariant: Graceful Downgrade
        # Never allow an expensive model if the budget cannot support a minimum viable response (e.g. 250 tokens)
        min_viable_tokens = 250
        
        while target_model != self.tier3_model:
            cpt = self.pricing.get(target_model, 0.0000003)
            if (effective_budget / cpt) >= min_viable_tokens:
                break
            # Downgrade cascade
            target_model = self.tier2_model if target_model == self.tier1_model else self.tier3_model
            
        model = target_model
        final_cpt = self.pricing.get(model, 0.0000003)

        # 4. Invariant: Strict Token Alignment
        # Tokens must be physically backed by the effective budget
        budget_backed_tokens = int(effective_budget / final_cpt)
        confidence_backed_tokens = int(base_max_tokens * confidence)
        
        # The system restricts tokens both by intelligence (confidence) and physical reality (budget)
        max_tokens = min(budget_backed_tokens, confidence_backed_tokens, base_max_tokens)
        max_tokens = max(min_viable_tokens, max_tokens)

        return ExecutionProfile(
            effective_budget_usd=round(effective_budget, 4),
            model_name=model,
            max_output_tokens=max_tokens,
            metadata={
                "base_budget": base_budget_usd,
                "raw_confidence": raw_confidence,
                "adjusted_confidence": round(confidence, 4),
                "density": density,
                "gamma_applied": gamma,
                "penalty_factor": round((confidence ** gamma) * density, 4)
            }
        )
