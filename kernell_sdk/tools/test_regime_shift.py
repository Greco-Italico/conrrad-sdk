"""
Kernell OS — Regime Shift Test
══════════════════════════════
Tests the Policy Tuner's ability to react to a sudden catastrophe
and then gracefully recover when conditions return to normal, without
getting permanently trauma-locked.
"""
import sys
import json
from kernell_sdk.runtime.policy_tuner import PolicyTuner

def main():
    tuner = PolicyTuner(target_roi_per_req=0.05, max_op_pressure=0.10)
    
    def simulate_phase(name: str, num_requests: int, error_profile: str):
        print(f"\n--- Phase: {name} ({num_requests} reqs) ---")
        for i in range(num_requests):
            if error_profile == "STABLE":
                roi_norm = 0.05
                op_norm = 0.01
            elif error_profile == "CATASTROPHE":
                roi_norm = -0.80  # Huge loss
                op_norm = 0.0
                
            # Print state every 10 iterations to watch evolution
            if i % 10 == 0:
                state = tuner.update(roi_norm, op_norm)
                print(f"Req {i:<3}: Gamma={state.budget_gamma:.2f}, KillMult={state.kill_multiplier:.2f}, Tau={state.decay_tau_hours:.1f}h | Status={state.controller_status}")
            else:
                tuner.update(roi_norm, op_norm)
                
    simulate_phase("Initial Stable", 100, "STABLE")
    simulate_phase("Catastrophic Drift", 50, "CATASTROPHE")
    simulate_phase("Recovery (Stable Again)", 200, "STABLE")

if __name__ == "__main__":
    main()
