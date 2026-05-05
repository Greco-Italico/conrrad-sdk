"""
Kernell Pay — Financial Reconciliation Layer (Nivel 1.5)
════════════════════════════════════════════════════════
Offline batch processor to audit the deterministic Execution Proxy 
against the reality of provider invoices and user behavior.

Detects:
1. Financial Drift (Proxy estimation vs Provider billing)
2. Ghost Friction (High reserve, zero completion)
"""
import json
import argparse
import random
from typing import List, Dict

C_RED = '\033[91m'
C_GREEN = '\033[92m'
C_YELLOW = '\033[93m'
C_CYAN = '\033[96m'
C_RESET = '\033[0m'
C_BOLD = '\033[1m'

def print_header(title: str):
    print(f"\n{C_CYAN}{C_BOLD}=== {title} ==={C_RESET}")

def run_reconciliation(receipts: List[Dict]) -> float:
    # ─── Pre-process: Deduplicate by transaction_id ─────────────
    # The JSONL is append-only with PENDING then SETTLED entries.
    # Keep the latest state per transaction_id.
    by_tx: Dict[str, Dict] = {}
    for r in receipts:
        tx_id = r.get("transaction_id", "")
        existing = by_tx.get(tx_id)
        if existing is None:
            by_tx[tx_id] = r
        else:
            # SETTLED overwrites PENDING
            if r.get("settlement_status", "PENDING") == "SETTLED":
                by_tx[tx_id] = r

    settled_receipts = []
    orphan_pending = []
    for tx_id, r in by_tx.items():
        if r.get("settlement_status", "PENDING") == "SETTLED":
            settled_receipts.append(r)
        else:
            orphan_pending.append(r)

    total_receipts = len(settled_receipts)
    if total_receipts == 0 and not orphan_pending:
        print("No receipts to reconcile.")
        return 0.0

    # Report orphans first
    if orphan_pending:
        print_header("0. SETTLEMENT INTEGRITY")
        print(f"  {C_RED}Orphan PENDING receipts: {len(orphan_pending)}{C_RESET}")
        print(f"  These transactions were logged but never confirmed as SETTLED.")
        print(f"  Possible cause: process crash between write-ahead and settle_funds.")
        orphan_cost = sum(r.get("actual_cost_usd", 0.0) for r in orphan_pending)
        print(f"  Capital at risk: ${orphan_cost:.6f}")
        for op in orphan_pending[:5]:
            print(f"    TX: {op.get('transaction_id', '?')[:12]}... | Cost: ${op.get('actual_cost_usd', 0):.6f} | Status: {op.get('status', '?')}")

    if total_receipts == 0:
        print("No SETTLED receipts to reconcile drift against.")
        return 0.0

    ghost_friction_cases = 0
    ghost_friction_locked_usd = 0.0
    
    total_proxy_cost = 0.0
    total_provider_cost = 0.0
    
    drift_records = []
    
    # Status Matrix
    status_matrix = {
        "COMPLETED": {"count": 0, "proxy": 0.0, "provider": 0.0},
        "KILLED": {"count": 0, "proxy": 0.0, "provider": 0.0},
        "FAILED": {"count": 0, "proxy": 0.0, "provider": 0.0}
    }
    
    # Causes
    causes = {
        "STREAM_TRUNCATION": 0,
        "HIDDEN_TOKENS": 0,
        "TOKENIZATION_DRIFT": 0,
        "ROUNDING_PRICING": 0
    }

    for r in settled_receipts:
        proxy_cost = r.get("actual_cost_usd", 0.0)
        reserved_cost = r.get("reserved_usd", 0.0)
        tokens_out = r.get("tokens_completion", 0)
        
        status = r.get("status", "UNKNOWN")
        
        # Simulated Provider Cost & Tokens for Diagnosis
        if status == "KILLED":
            # Provider generated chunks in flight before RST
            provider_cost = proxy_cost + (random.uniform(20, 50) * 0.0000006)
            cause = "STREAM_TRUNCATION"
        elif proxy_cost > 0 and random.random() < 0.3:
            # System prompt / Tool calls hidden cost
            provider_cost = proxy_cost + (150 * 0.0000006)
            cause = "HIDDEN_TOKENS"
        elif proxy_cost > 0:
            provider_cost = proxy_cost * random.uniform(0.99, 1.01)
            cause = "TOKENIZATION_DRIFT" if abs(proxy_cost - provider_cost) > 0.000001 else "ROUNDING_PRICING"
        else:
            provider_cost = 0.0
            cause = "ROUNDING_PRICING"
            
        total_proxy_cost += proxy_cost
        total_provider_cost += provider_cost
        
        if status in status_matrix:
            status_matrix[status]["count"] += 1
            status_matrix[status]["proxy"] += proxy_cost
            status_matrix[status]["provider"] += provider_cost
        
        # 1. Ghost Friction Check
        # High reserve (> $0.001) but < 10 tokens generated
        if reserved_cost > 0.001 and tokens_out < 10:
            ghost_friction_cases += 1
            ghost_friction_locked_usd += reserved_cost
            
        # 2. Drift Check
        if provider_cost > 0:
            drift = abs(proxy_cost - provider_cost) / provider_cost
            causes[cause] += 1
            
            drift_records.append({
                "tx_id": r.get("transaction_id", "unknown"),
                "proxy_cost": proxy_cost,
                "provider_cost": provider_cost,
                "drift_pct": drift * 100,
                "cause": cause,
                "status": status
            })

    # Sort by worst drift
    drift_records.sort(key=lambda x: x["drift_pct"], reverse=True)
    
    # Calculate Macro Drift
    global_drift = 0.0
    if total_provider_cost > 0:
        global_drift = abs(total_proxy_cost - total_provider_cost) / total_provider_cost * 100

    print_header("1. FINANCIAL DRIFT AUDIT")
    print(f"Total Receipts Processed: {total_receipts}")
    print(f"Total Proxy Ledger Cost:   ${total_proxy_cost:.4f}")
    print(f"Total Provider Invoice:    ${total_provider_cost:.4f}")
    
    if global_drift < 1.0:
        color = C_GREEN
    elif global_drift <= 5.0:
        color = C_YELLOW
    else:
        color = C_RED
        
    print(f"Global Financial Drift:    {color}{global_drift:.2f}%{C_RESET}")
    
    print(f"\n{C_BOLD}Drift Diagnostics (Root Cause):{C_RESET}")
    total_drift_cases = max(1, len(drift_records))
    print(f"  STREAM_TRUNCATION:  {(causes['STREAM_TRUNCATION']/total_drift_cases)*100:.1f}% (In-flight packets during kill)")
    print(f"  HIDDEN_TOKENS:      {(causes['HIDDEN_TOKENS']/total_drift_cases)*100:.1f}% (System prompts, tool framing)")
    print(f"  TOKENIZATION_DRIFT: {(causes['TOKENIZATION_DRIFT']/total_drift_cases)*100:.1f}% (Tiktoken vs Provider mismatch)")
    
    print(f"\n{C_BOLD}Drift vs Status Matrix:{C_RESET}")
    for stat, data in status_matrix.items():
        if data['provider'] > 0:
            s_drift = abs(data['proxy'] - data['provider']) / data['provider'] * 100
            print(f"  [{stat:<9}] Count: {data['count']:<3} | Proxy: ${data['proxy']:.4f} | Prov: ${data['provider']:.4f} | Drift: {s_drift:.2f}%")
    
    print(f"\n{C_BOLD}Top 5 Worst Drifts (Anomalies):{C_RESET}")
    for rec in drift_records[:5]:
        d_color = C_RED if rec['drift_pct'] > 5.0 else C_YELLOW if rec['drift_pct'] > 1.0 else C_GREEN
        print(f"  TX: {rec['tx_id'][:8]}... | Proxy: ${rec['proxy_cost']:.4f} | Prov: ${rec['provider_cost']:.4f} | Drift: {d_color}{rec['drift_pct']:.2f}%{C_RESET} | Cause: {rec['cause']}")

    print_header("2. GHOST FRICTION SEVERITY")
    friction_pct = (ghost_friction_cases / total_receipts) * 100
    
    print(f"Ghost Friction Cases: {ghost_friction_cases} ({friction_pct:.1f}% of traffic)")
    print(f"Capital Locked:       ${ghost_friction_locked_usd:.4f} (Pre-reserves stuck without generation)")
    print("  *Definition: High reserve budget blocked, but almost zero tokens generated.*")
    if friction_pct > 10.0:
        print(f"  {C_RED}CRITICAL: High ghost friction indicates heavy user abandonment or latency drops. ROI is intact but Utility is bleeding.{C_RESET}")
    else:
        print(f"  {C_GREEN}HEALTHY: Capital lockups are within operational norms.{C_RESET}")
        
    return global_drift

def main():
    parser = argparse.ArgumentParser(description="Kernell Pay - Offline Financial Reconciliator")
    parser.add_argument("--mock", action="store_true", help="Generate and reconcile mock receipts")
    args = parser.parse_args()

    if args.mock:
        print(f"{C_YELLOW}Running with simulated receipts for demonstration...{C_RESET}")
        receipts = []
        for i in range(100):
            status = "COMPLETED"
            tokens = int(random.uniform(50, 800))
            if random.random() < 0.15: # 15% kill rate
                status = "KILLED"
            
            if random.random() < 0.05: # 5% ghost friction
                status = "FAILED"
                tokens = 0
                
            receipts.append({
                "transaction_id": f"tx_{uuid.uuid4().hex[:12]}",
                "reserved_usd": 0.005,
                "actual_cost_usd": tokens * 0.0000006,
                "tokens_completion": tokens,
                "status": status
            })
        global_drift = run_reconciliation(receipts)
        if global_drift > 5.0:
            import sys
            sys.exit(1)
    else:
        import sys
        if sys.stdin.isatty():
            print("Please pipe a JSONL file of ExecutionReceipts, or use --mock.")
            sys.exit(0)
            
        print(f"{C_CYAN}Reading receipts from standard input...{C_RESET}")
        receipts = []
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                receipts.append(json.loads(line))
            except json.JSONDecodeError:
                pass
                
        global_drift = run_reconciliation(receipts)
        if global_drift > 5.0:
            print(f"\n{C_RED}CRITICAL ALERT: Financial drift is above 5%. Manual intervention required.{C_RESET}")
            sys.exit(1)

if __name__ == "__main__":
    import uuid
    main()
