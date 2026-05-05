"""
Kernell OS — Policy Tuner Simulator
═══════════════════════════════════
Offline simulator to validate the stability of the PID Policy Tuner.
Feeds historical log data sequentially to observe how gamma, kill multiplier,
and decay tau evolve under varying economic pressures.
"""
import json
import os
import argparse
from typing import List, Dict

from kernell_sdk.runtime.policy_tuner import PolicyTuner

C_CYAN = '\033[96m'
C_GREEN = '\033[92m'
C_YELLOW = '\033[93m'
C_RED = '\033[91m'
C_RESET = '\033[0m'
C_BOLD = '\033[1m'


def simulate_tuner(log_path: str, chunk_size: int = 50):
    if not os.path.exists(log_path):
        print(f"{C_RED}Log file {log_path} not found. Please provide a valid JSONL.{C_RESET}")
        return

    tuner = PolicyTuner(target_roi_per_req=0.05, max_op_pressure=0.10)
    
    print(f"\n{C_CYAN}{C_BOLD}=== 🎛️ POLICY TUNER OFFLINE SIMULATION ==={C_RESET}")
    print(f"{'Chunk':<6} | {'ROI/req':<8} | {'OP/req':<8} | {'Gamma':<6} | {'Kill_M':<6} | {'Tau(h)':<6} | {'Status'}")
    print("-" * 75)

    chunk_idx = 0
    req_count = 0
    saved = 0.0
    lost = 0.0
    missed = 0.0
    
    with open(log_path, 'r') as f:
        for line in f:
            if not line.strip(): continue
            try:
                entry = json.loads(line)
                req_count += 1
                
                # Mock extraction from hypothetical shadow log
                # For realistic simulation, we use the cost/estimated fields to derive mock ROI
                est = float(entry.get("estimated_cost_usd", 0.0))
                act = float(entry.get("cost_usd", 0.0))
                killed = bool(entry.get("killed", False))
                
                if killed:
                    saved += max(0, est - act)
                if "EXTREME_DRIFT" in str(entry.get("kill_reason", "")):
                    lost += max(0, act - est)
                    
                # Mock Opportunity Cost (Say 10% of successful, cheap requests were over-restricted)
                if not killed and act < (est * 0.5):
                    missed += (est - act) * 0.5

                if req_count >= chunk_size:
                    chunk_idx += 1
                    roi_norm = (saved - lost) / req_count
                    op_norm = missed / req_count
                    
                    state = tuner.update(roi_norm, op_norm)
                    
                    color = C_GREEN if state.controller_status == "STABLE" else (C_RED if state.controller_status == "SATURATED" else C_YELLOW)
                    
                    print(f"{chunk_idx:<6} | {roi_norm:<8.3f} | {op_norm:<8.3f} | {state.budget_gamma:<6.2f} | {state.kill_multiplier:<6.2f} | {state.decay_tau_hours:<6.1f} | {color}{state.controller_status}{C_RESET}")
                    
                    # Reset chunk
                    req_count = 0
                    saved = 0.0
                    lost = 0.0
                    missed = 0.0
                    
            except Exception as e:
                pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-log", type=str, default="audit.jsonl")
    parser.add_argument("--chunk", type=int, default=10, help="Requests per tuning step")
    args = parser.parse_args()
    
    simulate_tuner(args.audit_log, args.chunk)
