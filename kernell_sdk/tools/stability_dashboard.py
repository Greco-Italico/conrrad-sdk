"""
Kernell OS — Stability Dashboard
═════════════════════════════════
The operational control panel for the execution engine.
Not just observability — it answers:
1. Where am I losing money?
2. Where is it over-restricting?
3. What happens if I change inputs? (Simulator)

Usage:
  python -m kernell_sdk.tools.stability_dashboard --audit-log logs/audit.jsonl
  python -m kernell_sdk.tools.stability_dashboard --simulate --conf 0.4 --dens 0.7
"""
import os
import json
import time
import argparse
import threading
from typing import Dict, Any, Tuple

from kernell_sdk.runtime.calibration_engine import CalibrationEngine
from kernell_sdk.runtime.adaptive_budget import AdaptiveBudgetPolicy
from kernell_sdk.runtime.policy_tuner import PolicyTuner

# ANSI Colors
C_RED = '\033[91m'
C_GREEN = '\033[92m'
C_YELLOW = '\033[93m'
C_CYAN = '\033[96m'
C_RESET = '\033[0m'
C_BOLD = '\033[1m'

def print_header(title: str):
    print(f"\n{C_CYAN}{C_BOLD}=== {title} ==={C_RESET}")

def compute_risk_score(error: float, kill_rate: float, drift_rate: float) -> float:
    return ((error - 1.0) * 0.5) + (kill_rate * 1.0) + (drift_rate * 2.0)

def render_dashboard(engine: CalibrationEngine, financial_metrics: Dict[str, float]):
    os.system('cls' if os.name == 'nt' else 'clear')
    print(f"{C_BOLD}Kernell OS — Adaptive Execution Command Center{C_RESET}")
    print(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")

    stats = engine.get_stats_snapshot()
    if not stats:
        print(f"{C_YELLOW}No calibration data available. Waiting for logs...{C_RESET}")
        return

    # 1. RISK HEATMAP
    print_header("1. 🌡️ RISK HEATMAP")
    print(f"{'Bucket':<20} | {'Samples':<7} | {'AvgErr':<7} | {'Kills%':<7} | {'Drift%':<7} | {'Risk':<10}")
    print("-" * 75)
    for key, data in sorted(stats.items(), key=lambda x: compute_risk_score(x[1]['avg_error'], x[1]['kill_rate'], x[1]['drift_rate']), reverse=True):
        risk = compute_risk_score(data['avg_error'], data['kill_rate'], data['drift_rate'])
        
        if risk >= 0.5: color = C_RED; status = "CRÍTICO"
        elif risk >= 0.1: color = C_YELLOW; status = "WARNING"
        else: color = C_GREEN; status = "ESTABLE"
            
        print(f"{color}{key:<20} | {data['samples']:<7} | {data['avg_error']:<7.2f} | {data['kill_rate']*100:<6.1f}% | {data['drift_rate']*100:<6.1f}% | {status:<10}{C_RESET}")

    # 2. MONEY LEAK DETECTOR & COST OF CONSERVATISM
    print_header("2. 💸 MONEY LEAK & OPPORTUNITY COST")
    saved = financial_metrics["saved_usd"]
    lost = financial_metrics["lost_drift_usd"]
    missed = financial_metrics["opportunity_cost_usd"]
    net = saved - lost
    net_col = C_GREEN if net >= 0 else C_RED
    
    print(f"Proxy Avoided Spend (Kills): {C_GREEN}+${saved:.4f}{C_RESET}")
    print(f"Extreme Drift Cost (Leaks):  {C_RED}-${lost:.4f}{C_RESET}")
    print(f"Net System ROI:              {net_col}${net:.4f}{C_RESET}")
    print(f"Missed Opportunity (Under-use):{C_YELLOW}-${missed:.4f}{C_RESET}")

    # 3. OVER-RESTRICTION ZONES
    print_header("3. 🧊 OVER-RESTRICTION ZONES")
    found_over_restricted = False
    for key, data in stats.items():
        if data['avg_error'] < 0.8 and data['kill_rate'] == 0.0 and data['samples'] >= 5:
            found_over_restricted = True
            print(f"{C_YELLOW}Bucket {key} is safe (Err: {data['avg_error']:.2f}, Kills: 0%) but may be artificially bottlenecked.{C_RESET}")
    if not found_over_restricted:
        print(f"{C_GREEN}No significant over-restriction detected.{C_RESET}")

    # 4. TEMPORAL DRIFT VIEW
    print_header("4. ⏱️ TEMPORAL DRIFT VIEW")
    print(f"{'Bucket':<20} | {'Age (min)':<10} | {'Status'}")
    print("-" * 50)
    for key, data in sorted(stats.items(), key=lambda x: x[1]['age_s'], reverse=True):
        age_m = data['age_s'] / 60.0
        if age_m > 60:
            print(f"{C_YELLOW}{key:<20} | {age_m:<10.1f} | Decaying (Old memory){C_RESET}")
        else:
            print(f"{C_GREEN}{key:<20} | {age_m:<10.1f} | Active{C_RESET}")

    # 5. SHADOW MODE: DECISION DELTA
    print_header("5. 🧠 SHADOW VS REAL (DECISION DELTA)")
    shadow = financial_metrics.get("shadow", {})
    total = shadow.get("total_evaluated", 0)
    if total > 0:
        downgrades_pct = (shadow.get("model_downgrades", 0) / total) * 100
        budget_reduction_pct = (shadow.get("budget_reduction_sum", 0) / shadow.get("original_budget_sum", 1)) * 100
        
        print(f"Total Executions Evaluated: {total}")
        print(f"Model Downgrades Suggested: {C_YELLOW}{downgrades_pct:.1f}%{C_RESET}")
        print(f"Budget Reductions Suggested: {C_CYAN}-{budget_reduction_pct:.1f}%{C_RESET}")
        print(f"Kills That Would Have Happened: {C_RED}{shadow.get('shadow_kills', 0)}{C_RESET}")
        print(f"Estimated Savings (Shadow): {C_GREEN}+${shadow.get('shadow_savings_usd', 0):.4f}{C_RESET}")
        print(f"False Positives (Overkill Risk): {C_RED}{shadow.get('overkill_risk', 0)}{C_RESET} (Killed by shadow but was successful & cheap)")
        print(f"Epistemic Risk (Counterfactual): {C_YELLOW}{shadow.get('counterfactual_uncertainty', 0)}{C_RESET} (Model was changed, outcome unknown)")
    else:
        print(f"{C_YELLOW}No shadow data collected yet.{C_RESET}")

    # 6. POLICY TUNER STATE
    print_header("6. 🎛️ POLICY TUNER STATE")
    tuner_state = financial_metrics.get("tuner_state")
    if total > 0 and tuner_state:
        roi_norm = net / total
        op_norm = missed / total
        
        color = C_GREEN if tuner_state.controller_status == "STABLE" else (C_RED if tuner_state.controller_status == "SATURATED" else C_YELLOW)
        print(f"ROI Error:            {roi_norm:.3f}/req")
        print(f"Opportunity Pressure: {op_norm:.3f}/req")
        print(f"Quality Penalty:      {financial_metrics.get('quality_penalty', 0.0):.3f}/req\n")
        print(f"Kill Aggressiveness:  {tuner_state.kill_multiplier:.2f}x")
        print(f"Budget Gamma:         {tuner_state.budget_gamma:.2f}")
        print(f"Decay Tau:            {tuner_state.decay_tau_hours:.1f}h\n")
        print(f"Controller State:     {color}{tuner_state.controller_status}{C_RESET}")
    else:
        print(f"{C_YELLOW}Tuner requires more data to activate.{C_RESET}")


def live_tail(engine: CalibrationEngine, filepath: str, financial_metrics: Dict[str, float]):
    """Simulate tail -f on the JSONL log file and update metrics."""
    if not os.path.exists(filepath):
        # Create it empty if it doesn't exist just to avoid crash
        open(filepath, 'a').close()
        
    with open(filepath, 'r') as f:
        # Jump to end for live mode, or read all if we want historical context
        # Let's read all first for context
        for line in f:
            if not line.strip(): continue
            try:
                entry = json.loads(line)
                engine.process_log_entry(entry)
                
                # --- Shadow Mode Evaluation ---
                policy = AdaptiveBudgetPolicy(calibration_engine=engine)
                conf = float(entry.get("confidence", 0.5))
                dens = float(entry.get("density", 0.5))
                real_model = entry.get("model", "unknown")
                real_cost = float(entry.get("cost_usd", 0.0))
                real_killed = bool(entry.get("killed", False))
                base_budget = float(entry.get("base_budget_usd", 1.0))
                
                shadow_metrics = financial_metrics.setdefault("shadow", {
                    "total_evaluated": 0, "model_downgrades": 0,
                    "budget_reduction_sum": 0.0, "original_budget_sum": 0.0,
                    "shadow_kills": 0, "shadow_savings_usd": 0.0, 
                    "overkill_risk": 0, "counterfactual_uncertainty": 0
                })
                
                shadow_metrics["total_evaluated"] += 1
                shadow_metrics["original_budget_sum"] += base_budget
                
                # What would the system have done?
                profile = policy.compute_profile(base_budget, 4000, conf, dens)
                
                if profile.model_name != real_model and profile.model_name != policy.tier1_model:
                    shadow_metrics["model_downgrades"] += 1
                    
                shadow_metrics["budget_reduction_sum"] += (base_budget - profile.effective_budget_usd)
                
                # Opportunity Cost: Is this bucket extremely safe?
                key = engine._get_context_key(real_model, conf, dens)
                stats = engine._stats.get(key)
                if stats and stats.samples >= 5 and stats.avg_error < 0.8 and stats.kill_rate == 0.0:
                    financial_metrics["opportunity_cost_usd"] += (base_budget - profile.effective_budget_usd)
                
                # Would we have killed it?
                if real_cost > profile.effective_budget_usd:
                    if real_model != profile.model_name:
                        # Cannot definitively say it's a valid kill or false positive because the model changed.
                        shadow_metrics["counterfactual_uncertainty"] += 1
                    else:
                        shadow_metrics["shadow_kills"] += 1
                        # If it wasn't killed in reality and cost less than 2x the budget, maybe it was a false positive?
                        if not real_killed and real_cost < (profile.effective_budget_usd * 1.5):
                            shadow_metrics["overkill_risk"] += 1
                        else:
                            shadow_metrics["shadow_savings_usd"] += (real_cost - profile.effective_budget_usd)
                # ------------------------------

                # Update financial metrics (Real Kills)
                if entry.get("killed"):
                    # Rough estimate of saved money (estimated - actual)
                    saved = max(0, float(entry.get("estimated_cost_usd", 0)) - float(entry.get("cost_usd", 0)))
                    financial_metrics["saved_usd"] += saved
                if "EXTREME_DRIFT" in str(entry.get("kill_reason")):
                    drift_lost = max(0, float(entry.get("cost_usd", 0)) - float(entry.get("estimated_cost_usd", 0)))
                    financial_metrics["lost_drift_usd"] += drift_lost
            except: pass
            
        # Tail loop
        while True:
            where = f.tell()
            line = f.readline()
            if not line:
                time.sleep(1.0)
                f.seek(where)
            else:
                try:
                    entry = json.loads(line)
                    engine.process_log_entry(entry)
                    
                    # Track Retries
                    retries = int(entry.get("retries", 0))
                    financial_metrics["total_retries"] = financial_metrics.get("total_retries", 0) + retries
                    
                    # --- Shadow Mode Evaluation ---
                    policy = AdaptiveBudgetPolicy(calibration_engine=engine)
                    conf = float(entry.get("confidence", 0.5))
                    dens = float(entry.get("density", 0.5))
                    real_model = entry.get("model", "unknown")
                    real_cost = float(entry.get("cost_usd", 0.0))
                    real_killed = bool(entry.get("killed", False))
                    base_budget = float(entry.get("base_budget_usd", 1.0))
                    
                    shadow_metrics = financial_metrics.setdefault("shadow", {
                        "total_evaluated": 0, "model_downgrades": 0,
                        "budget_reduction_sum": 0.0, "original_budget_sum": 0.0,
                        "shadow_kills": 0, "shadow_savings_usd": 0.0, 
                        "overkill_risk": 0, "counterfactual_uncertainty": 0
                    })
                    
                    shadow_metrics["total_evaluated"] += 1
                    shadow_metrics["original_budget_sum"] += base_budget
                    
                    tuner = financial_metrics.get("tuner")
                    current_gamma = tuner.state.budget_gamma if tuner else 2.0
                    profile = policy.compute_profile(base_budget, 4000, conf, dens, gamma=current_gamma)
                    
                    if profile.model_name != real_model and profile.model_name != policy.tier1_model:
                        shadow_metrics["model_downgrades"] += 1
                        
                    shadow_metrics["budget_reduction_sum"] += (base_budget - profile.effective_budget_usd)
                    
                    key = engine._get_context_key(real_model, conf, dens)
                    stats = engine._stats.get(key)
                    if stats and stats.samples >= 5 and stats.avg_error < 0.8 and stats.kill_rate == 0.0:
                        financial_metrics["opportunity_cost_usd"] += (base_budget - profile.effective_budget_usd)
                    
                    if real_cost > profile.effective_budget_usd:
                        if real_model != profile.model_name:
                            shadow_metrics["counterfactual_uncertainty"] += 1
                        else:
                            shadow_metrics["shadow_kills"] += 1
                            if not real_killed and real_cost < (profile.effective_budget_usd * 1.5):
                                shadow_metrics["overkill_risk"] += 1
                            else:
                                shadow_metrics["shadow_savings_usd"] += (real_cost - profile.effective_budget_usd)
                    # ------------------------------

                    if entry.get("killed"):
                        saved = max(0, float(entry.get("estimated_cost_usd", 0)) - float(entry.get("cost_usd", 0)))
                        financial_metrics["saved_usd"] += saved
                    if "EXTREME_DRIFT" in str(entry.get("kill_reason")):
                        drift_lost = max(0, float(entry.get("cost_usd", 0)) - float(entry.get("estimated_cost_usd", 0)))
                        financial_metrics["lost_drift_usd"] += drift_lost
                except: pass
                
            # Periodic Tuner Update
            shadow = financial_metrics.get("shadow", {})
            total = shadow.get("total_evaluated", 0)
            if total > 0 and (total % 10 == 0):
                net = financial_metrics["saved_usd"] - financial_metrics["lost_drift_usd"]
                missed = financial_metrics["opportunity_cost_usd"]
                retries = financial_metrics.get("total_retries", 0)
                
                roi_norm = net / total
                op_norm = missed / total
                q_penalty = retries / total
                financial_metrics["quality_penalty"] = q_penalty
                
                tuner = financial_metrics.get("tuner")
                if tuner:
                    financial_metrics["tuner_state"] = tuner.update(
                        roi_norm=roi_norm, 
                        op_pressure=op_norm,
                        quality_penalty=q_penalty
                    )


def simulator_mode(conf: float, dens: float, gamma: float = 2.0):
    """What-If Simulator"""
    print_header("🧪 WHAT-IF SIMULATOR")
    
    # Load snapshot if exists
    engine = CalibrationEngine()
    engine.load_state("calibration_snapshot.json")
    policy = AdaptiveBudgetPolicy(calibration_engine=engine)
    
    profile = policy.compute_profile(
        base_budget_usd=1.0, 
        base_max_tokens=4000, 
        confidence=conf, 
        surface_density=dens,
        gamma=gamma
    )
    
    print(f"Inputs: Confidence={conf:.2f}, Density={dens:.2f}, Gamma={gamma:.2f}")
    print(f"\n{C_BOLD}Execution Profile Constraint:{C_RESET}")
    print(f"  Effective Budget: {C_CYAN}${profile.effective_budget_usd:.4f}{C_RESET}")
    print(f"  Target Model:     {C_YELLOW}{profile.model_name}{C_RESET}")
    print(f"  Max Output:       {profile.max_output_tokens} tokens")
    print(f"\n{C_BOLD}Internal Telemetry:{C_RESET}")
    for k, v in profile.metadata.items():
        print(f"  {k}: {v}")
        
    print_header("📉 CONFIDENCE SENSITIVITY SWEEP")
    print(f"{'Conf':<5} | {'Model':<16} | {'Budget':<10} | {'Tokens':<6}")
    print("-" * 45)
    for c_val in [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]:
        prof = policy.compute_profile(1.0, 4000, c_val, dens, gamma=gamma)
        print(f"{c_val:<5.1f} | {prof.model_name:<16} | ${prof.effective_budget_usd:<9.4f} | {prof.max_output_tokens:<6}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-log", type=str, default="audit.jsonl", help="Path to audit JSONL")
    parser.add_argument("--simulate", action="store_true", help="Run in What-If Simulator mode")
    parser.add_argument("--conf", type=float, default=0.5, help="Simulate: Base confidence")
    parser.add_argument("--dens", type=float, default=0.5, help="Simulate: Surface density")
    parser.add_argument("--gamma", type=float, default=2.0, help="Simulate: Policy Gamma")
    
    args = parser.parse_args()

    if args.simulate:
        simulator_mode(args.conf, args.dens, args.gamma)
    else:
        engine = CalibrationEngine()
        tuner = PolicyTuner()
        financial_metrics = {
            "saved_usd": 0.0, 
            "lost_drift_usd": 0.0, 
            "opportunity_cost_usd": 0.0,
            "total_retries": 0,
            "quality_penalty": 0.0,
            "tuner": tuner,
            "tuner_state": tuner.state
        }
        
        # Background tail thread
        t = threading.Thread(target=live_tail, args=(engine, args.audit_log, financial_metrics), daemon=True)
        t.start()
        
        try:
            while True:
                render_dashboard(engine, financial_metrics)
                time.sleep(2.0)
        except KeyboardInterrupt:
            print("\nExiting dashboard.")
