"""
Kernell OS — Policy Tuner (Meta-Controller)
═══════════════════════════════════════════
A 2nd-order PID controller that auto-tunes the economic policies.
Instead of controlling budget directly, it adjusts:
  - Gamma (Budget curve steepness)
  - Kill Multiplier (Aggressiveness of penalties)
  - Decay Tau (Memory retention)

Objective: Optimize (Net ROI - Opportunity Pressure)
"""
import math
import logging
from dataclasses import dataclass

logger = logging.getLogger("kernell.runtime.tuner")


@dataclass
class TunerState:
    kill_multiplier: float = 1.0
    budget_gamma: float = 2.0
    decay_tau_hours: float = 24.0
    controller_status: str = "STABLE"


class PolicyTuner:
    def __init__(
        self,
        target_roi_per_req: float = 0.05,  # Want to save avg $0.05 per request
        max_op_pressure: float = 0.10,     # Max acceptable missed opportunity per req
        max_daily_loss_usd: float = 50.0,  # CIRCUIT BREAKER threshold
        max_false_positive_rate: float = 0.15, # CIRCUIT BREAKER threshold
        kp: float = 0.5,
        ki: float = 0.1,
        kd: float = 0.2,
    ):
        self.target_roi = target_roi_per_req
        self.max_op_pressure = max_op_pressure
        self.max_daily_loss_usd = max_daily_loss_usd
        self.max_false_positive_rate = max_false_positive_rate
        
        self.kp = kp
        self.ki = ki
        self.kd = kd
        
        self.integral = 0.0
        self.prev_error = 0.0
        
        self.state = TunerState()

    def update(
        self, 
        roi_norm: float, 
        op_pressure: float,
        daily_loss_usd: float = 0.0,
        false_positive_rate: float = 0.0,
        quality_penalty: float = 0.0
    ) -> TunerState:
        """
        Feed the PID with normalized operational signals.
        roi_norm: (saved - drift_lost - overkill) / total_requests
        op_pressure: opportunity_cost / total_requests
        daily_loss_usd: Total absolute drift lost today
        false_positive_rate: Overkill counts / total kills
        quality_penalty: Retry rate or other quality degradation proxies
        """
        # 0. Global Governance Kill Switch
        if daily_loss_usd > self.max_daily_loss_usd or false_positive_rate > self.max_false_positive_rate:
            self.state.controller_status = "CIRCUIT_BREAKER_TRIPPED"
            # Force max safety
            self.state.kill_multiplier = 1.5
            self.state.budget_gamma = 3.0
            self.state.decay_tau_hours = 72.0
            return self.state
        # 1. Error calculation
        roi_error = self.target_roi - roi_norm
        
        # Penalize strongly if opportunity cost is strangling the system
        # If op_pressure > max, it reduces the apparent ROI error (forces relaxation)
        # Also apply quality_penalty to relax constraints if execution quality degrades (e.g. retries)
        adjusted_error = roi_error + (op_pressure * 0.5) + (quality_penalty * 0.2)

        # 2. Dead zone (avoid micro-oscillations)
        if abs(adjusted_error) < 0.01:
            self.state.controller_status = "STABLE"
            self.integral *= 0.95
            
            # Baseline Regression (Healing)
            # Slowly heal the system back to defaults when there's no stress
            self.state.kill_multiplier += (1.0 - self.state.kill_multiplier) * 0.01
            self.state.budget_gamma += (2.0 - self.state.budget_gamma) * 0.01
            self.state.decay_tau_hours += (24.0 - self.state.decay_tau_hours) * 0.01
            
            return self.state

        # 3. PID Terms (With Anti-Windup Freeze)
        is_saturated = (
            abs(self.state.budget_gamma - 3.0) < 0.01 or
            abs(self.state.budget_gamma - 1.5) < 0.01 or
            abs(self.state.kill_multiplier - 1.5) < 0.01 or
            abs(self.state.kill_multiplier - 0.5) < 0.01
        )
        
        # Only accumulate integral if the system actually has degrees of freedom to act
        if not is_saturated:
            self.integral += adjusted_error
        
        # Anti-windup (reset integral if ROI plunges deeply or integral explodes)
        if self.integral > 5.0 or self.integral < -5.0 or roi_norm < -0.5:
            self.integral = 0.0
            
        derivative = adjusted_error - self.prev_error

        output = (
            self.kp * adjusted_error +
            self.ki * self.integral +
            self.kd * derivative
        )

        self.prev_error = adjusted_error

        # 4. Rate limiting output change (max 0.1 step)
        output = max(-0.1, min(0.1, output))

        # 5. Mapping to Structural Parameters
        # A positive output means we need more protection (ROI is too low)
        # A negative output means we need less protection (OP Pressure is high)
        
        # Kill Aggressiveness [0.5, 1.5]
        new_kill = self.state.kill_multiplier + (output * 0.5)
        self.state.kill_multiplier = max(0.5, min(1.5, new_kill))
        
        # Budget Gamma [1.5, 3.0] (Higher = steeper curve = less budget for low conf)
        new_gamma = self.state.budget_gamma + (output * 1.0)
        self.state.budget_gamma = max(1.5, min(3.0, new_gamma))
        
        # Decay Tau [6h, 72h] (Higher = longer memory = unforgiving)
        new_tau = self.state.decay_tau_hours + (output * 24.0)
        self.state.decay_tau_hours = max(6.0, min(72.0, new_tau))

        # Set status
        if abs(self.state.budget_gamma - 3.0) < 0.01 or abs(self.state.budget_gamma - 1.5) < 0.01:
            self.state.controller_status = "SATURATED"
        else:
            self.state.controller_status = "ADJUSTING"

        return self.state
