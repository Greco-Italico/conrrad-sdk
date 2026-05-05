"""
Kernell Pay — Case Explorer (Post-Mortem Offline Tool)
═══════════════════════════════════════════════════════
Offline analysis tool for navigating receipts after the 72h experiment.

Does NOT touch production: reads JSONL, reconstructs sessions, 
filters edge cases, and enables systematic autopsy.

Usage:
  cat receipts.jsonl | python3 case_explorer.py
  cat receipts.jsonl | python3 case_explorer.py --filter destructive
  cat receipts.jsonl | python3 case_explorer.py --filter waste_loop
  cat receipts.jsonl | python3 case_explorer.py --filter salvageable
  cat receipts.jsonl | python3 case_explorer.py --depth 3
  cat receipts.jsonl | python3 case_explorer.py --sample 9
  cat receipts.jsonl | python3 case_explorer.py --transitions
  python3 case_explorer.py --mock
"""

import json
import sys
import argparse
import random
from typing import List, Dict, Optional
from collections import defaultdict
from dataclasses import dataclass, field

# Import from behavioral_analyzer (same package)
from kernell_sdk.tools.behavioral_analyzer import (
    reconstruct_sessions,
    classify_outcome,
    classify_kill,
    Session,
)

C_RED = '\033[91m'
C_GREEN = '\033[92m'
C_YELLOW = '\033[93m'
C_CYAN = '\033[96m'
C_DIM = '\033[2m'
C_RESET = '\033[0m'
C_BOLD = '\033[1m'

KILL_COLORS = {
    "USEFUL_KILL": C_GREEN,
    "DESTRUCTIVE_KILL": C_RED,
    "WASTE_LOOP": C_YELLOW,
    "TERMINAL_KILL": C_DIM,
}

OUTCOME_COLORS = {
    "SUCCESS": C_GREEN,
    "NEAR_MISS": C_YELLOW,
    "INSTANT_DEATH": C_RED,
    "WASTE": C_RED,
    "ABANDON": C_DIM,
}

# ─── Session Analysis ───────────────────────────────────────────

@dataclass
class AnnotatedSession:
    """A session enriched with kill classifications and salvageability."""
    session: Session
    kill_types: List[Optional[str]] = field(default_factory=list)
    salvageable_indices: List[int] = field(default_factory=list)

    @property
    def has_destructive_kills(self) -> bool:
        return "DESTRUCTIVE_KILL" in self.kill_types

    @property
    def has_waste_loops(self) -> bool:
        return "WASTE_LOOP" in self.kill_types

    @property
    def has_terminal_kills(self) -> bool:
        return "TERMINAL_KILL" in self.kill_types

    @property
    def is_salvageable(self) -> bool:
        return len(self.salvageable_indices) > 0

    @property
    def depth(self) -> int:
        return self.session.attempt_count

    @property
    def total_cost(self) -> float:
        return self.session.total_cost


def annotate_session(session: Session) -> AnnotatedSession:
    """Enrich a session with kill classifications and salvageability estimates."""
    annotated = AnnotatedSession(session=session)

    for i, outcome in enumerate(session.outcomes):
        if outcome in ("NEAR_MISS", "INSTANT_DEATH"):
            kill_type = classify_kill(session, i)
            annotated.kill_types.append(kill_type)

            # Salvageability heuristic:
            # If actual_cost >= 80% of reserved AND was killed → likely salvageable
            r = session.receipts[i]
            reserved = r.get("reserved_usd", 0.0)
            actual = r.get("actual_cost_usd", 0.0)
            if reserved > 0 and (actual / reserved) >= 0.80:
                annotated.salvageable_indices.append(i)
        else:
            annotated.kill_types.append(None)

    return annotated


# ─── Display Functions ──────────────────────────────────────────

def print_session_detail(ann: AnnotatedSession, index: int = 0):
    """Print a full session sequence with attempt details."""
    s = ann.session
    print(f"\n{C_CYAN}{C_BOLD}{'─' * 60}")
    print(f"  SESSION #{index + 1}  |  Tenant: {s.tenant_id}  |  Depth: {s.attempt_count}")
    print(f"  Final: {s.final_outcome}  |  Cost: ${s.total_cost:.6f}  |  Duration: {s.duration_seconds:.1f}s")
    if ann.is_salvageable:
        print(f"  {C_YELLOW}⚠ LIKELY SALVAGEABLE ({len(ann.salvageable_indices)} attempts near budget){C_CYAN}")
    print(f"{'─' * 60}{C_RESET}\n")

    for i, (receipt, outcome) in enumerate(zip(s.receipts, s.outcomes)):
        tokens = receipt.get("tokens_completion", 0)
        cost = receipt.get("actual_cost_usd", 0.0)
        reserved = receipt.get("reserved_usd", 0.0)
        status = receipt.get("status", "?")
        model = receipt.get("executed_model", "?")
        utilization = (cost / reserved * 100) if reserved > 0 else 0

        # Kill type annotation
        kill_str = ""
        if ann.kill_types[i]:
            kt = ann.kill_types[i]
            color = KILL_COLORS.get(kt, C_RESET)
            kill_str = f"  {color}← {kt}{C_RESET}"

        salvage_str = ""
        if i in ann.salvageable_indices:
            salvage_str = f"  {C_YELLOW}★ SALVAGEABLE{C_RESET}"

        # Outcome color
        oc = OUTCOME_COLORS.get(outcome, C_RESET)

        # Gap from previous
        gap_str = ""
        if i > 0:
            prev_end = s.receipts[i - 1].get("timestamp_end", 0)
            curr_start = receipt.get("timestamp_start", 0)
            gap = curr_start - prev_end
            gap_str = f"  {C_DIM}(gap: {gap:.1f}s){C_RESET}"

        print(f"  Attempt {i + 1}/{s.attempt_count}{gap_str}")
        print(f"    {oc}{outcome:<15}{C_RESET} | {status:<9} | {model}")
        print(f"    Tokens: {tokens:>5}  |  Cost: ${cost:.6f}  |  Reserved: ${reserved:.6f}  |  Util: {utilization:.0f}%{kill_str}{salvage_str}")

    print()


def print_transition_analysis(annotated_sessions: List[AnnotatedSession]):
    """Show success/failure rates by depth — the frontier visualization."""
    print(f"\n{C_CYAN}{C_BOLD}{'═' * 60}")
    print(f"  DEPTH TRANSITION ANALYSIS")
    print(f"  (Where does the system change behavior?)")
    print(f"{'═' * 60}{C_RESET}\n")

    depth_buckets = defaultdict(lambda: {"success": 0, "abandon": 0, "total": 0})
    for ann in annotated_sessions:
        d = min(ann.depth, 6)
        depth_buckets[d]["total"] += 1
        if ann.session.has_success:
            depth_buckets[d]["success"] += 1
        else:
            depth_buckets[d]["abandon"] += 1

    for depth in sorted(depth_buckets.keys()):
        b = depth_buckets[depth]
        total = b["total"]
        succ_rate = b["success"] / total * 100 if total > 0 else 0
        aband_rate = b["abandon"] / total * 100 if total > 0 else 0

        label = f"{depth}+" if depth >= 6 else str(depth)

        # Color based on success rate
        if succ_rate >= 70:
            bar_color = C_GREEN
        elif succ_rate >= 40:
            bar_color = C_YELLOW
        else:
            bar_color = C_RED

        succ_bar = "█" * int(succ_rate / 5)
        aband_bar = "░" * int(aband_rate / 5)

        print(f"  Depth {label:<3} | {bar_color}{succ_bar}{C_RED}{aband_bar}{C_RESET} | "
              f"✓ {succ_rate:.0f}% ({b['success']})  ✗ {aband_rate:.0f}% ({b['abandon']})  "
              f"n={total}")

    # Find the frontier
    print(f"\n  {C_BOLD}Frontier Detection:{C_RESET}")
    prev_rate = 100
    for depth in sorted(depth_buckets.keys()):
        b = depth_buckets[depth]
        total = b["total"]
        if total == 0:
            continue
        succ_rate = b["success"] / total * 100
        drop = prev_rate - succ_rate
        if drop > 15:
            label = f"{depth}+" if depth >= 6 else str(depth)
            print(f"  {C_RED}⚠ Major drop at Depth {label}: {prev_rate:.0f}% → {succ_rate:.0f}% (Δ{drop:.0f}%){C_RESET}")
            print(f"    → This is where request-based pricing likely breaks.")
        prev_rate = succ_rate

    print()


def print_edge_sample(annotated_sessions: List[AnnotatedSession], n: int = 9):
    """Sample edge cases from three buckets for manual autopsy."""
    near_miss = [a for a in annotated_sessions if any(
        o == "NEAR_MISS" for o in a.session.outcomes
    )]
    waste_loop = [a for a in annotated_sessions if a.has_waste_loops]
    destructive = [a for a in annotated_sessions if a.has_destructive_kills]
    deep_success = [a for a in annotated_sessions if a.depth > 1 and a.session.has_success]

    per_bucket = max(1, n // 3)

    print(f"\n{C_CYAN}{C_BOLD}{'═' * 60}")
    print(f"  EDGE CASE SAMPLER ({n} cases for manual autopsy)")
    print(f"{'═' * 60}{C_RESET}")

    # Bucket 1: Near misses (almost made it)
    print(f"\n{C_YELLOW}{C_BOLD}  ── NEAR MISS (almost succeeded) ──{C_RESET}")
    sample_nm = random.sample(near_miss, min(per_bucket, len(near_miss))) if near_miss else []
    for i, ann in enumerate(sample_nm):
        print_session_detail(ann, i)
    if not sample_nm:
        print(f"  {C_DIM}(no near misses found){C_RESET}")

    # Bucket 2: Waste loops or destructive kills
    print(f"\n{C_RED}{C_BOLD}  ── DESTRUCTIVE / WASTE (system failed the user) ──{C_RESET}")
    combined = {id(a): a for a in waste_loop + destructive}  # deduplicate
    combined = list(combined.values())
    sample_bad = random.sample(combined, min(per_bucket, len(combined))) if combined else []
    for i, ann in enumerate(sample_bad):
        print_session_detail(ann, i)
    if not sample_bad:
        print(f"  {C_DIM}(no destructive/waste cases found){C_RESET}")

    # Bucket 3: Deep successes (multi-step convergence)
    print(f"\n{C_GREEN}{C_BOLD}  ── DEEP SUCCESS (multi-step convergence) ──{C_RESET}")
    sample_succ = random.sample(deep_success, min(per_bucket, len(deep_success))) if deep_success else []
    for i, ann in enumerate(sample_succ):
        print_session_detail(ann, i)
    if not sample_succ:
        print(f"  {C_DIM}(no multi-step successes found){C_RESET}")


def print_budget_simulation(annotated_sessions: List[AnnotatedSession]):
    """Simulate what would happen with +10%, +20%, +50% budget."""
    print(f"\n{C_CYAN}{C_BOLD}{'═' * 60}")
    print(f"  BUDGET WHAT-IF SIMULATOR")
    print(f"  (How many kills become successes with more budget?)")
    print(f"{'═' * 60}{C_RESET}\n")

    salvageable = [a for a in annotated_sessions if a.is_salvageable]
    total_kills = sum(1 for a in annotated_sessions
                      for o in a.session.outcomes
                      if o in ("NEAR_MISS", "INSTANT_DEATH"))

    for pct in [10, 20, 50]:
        # Estimate: kills with utilization > (100-pct)% would be saved
        threshold = (100 - pct) / 100
        would_save = 0
        cost_delta = 0.0

        for ann in annotated_sessions:
            for i, outcome in enumerate(ann.session.outcomes):
                if outcome not in ("NEAR_MISS", "INSTANT_DEATH"):
                    continue
                r = ann.session.receipts[i]
                reserved = r.get("reserved_usd", 0.0)
                actual = r.get("actual_cost_usd", 0.0)
                if reserved > 0 and (actual / reserved) >= threshold:
                    would_save += 1
                    # Estimate additional cost: the gap between actual and what they'd use
                    cost_delta += reserved * (pct / 100)

        save_rate = would_save / total_kills * 100 if total_kills > 0 else 0
        print(f"  +{pct}% budget:")
        print(f"    Kills saved:     {would_save} / {total_kills} ({save_rate:.1f}%)")
        print(f"    Extra cost:      ${cost_delta:.6f}")
        print(f"    Trade-off:       {'Worth it' if save_rate > 20 else 'Marginal' if save_rate > 5 else 'Not worth it'}")
        print()


# ─── CLI ────────────────────────────────────────────────────────

def load_receipts(args) -> List[Dict]:
    """Load receipts from stdin, file, or generate mock."""
    if args.mock:
        from kernell_sdk.tools.behavioral_analyzer import generate_mock_receipts
        print(f"{C_YELLOW}Using mock data...{C_RESET}")
        return generate_mock_receipts(200)

    if args.file:
        with open(args.file) as f:
            lines = f.readlines()
    elif not sys.stdin.isatty():
        lines = sys.stdin.readlines()
    else:
        print("Pipe a JSONL file, use --file, or use --mock.")
        sys.exit(0)

    receipts = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
            # Deduplicate: keep SETTLED over PENDING
            receipts.append(r)
        except json.JSONDecodeError:
            pass

    # Deduplicate by transaction_id (keep latest / SETTLED)
    by_tx: Dict[str, Dict] = {}
    for r in receipts:
        tx_id = r.get("transaction_id", "")
        existing = by_tx.get(tx_id)
        if existing is None:
            by_tx[tx_id] = r
        else:
            if r.get("settlement_status", "PENDING") == "SETTLED":
                by_tx[tx_id] = r
    return list(by_tx.values())


def main():
    parser = argparse.ArgumentParser(
        description="Kernell Pay — Case Explorer (Post-Mortem)"
    )
    parser.add_argument("--mock", action="store_true", help="Use generated mock data")
    parser.add_argument("--file", type=str, help="Path to receipts JSONL file")
    parser.add_argument("--gap", type=float, default=120.0,
                        help="Session gap threshold in seconds")
    parser.add_argument("--filter", type=str, default=None,
                        choices=["destructive", "waste_loop", "terminal",
                                 "salvageable", "success", "all"],
                        help="Filter sessions by type")
    parser.add_argument("--depth", type=int, default=None,
                        help="Show only sessions with depth >= N")
    parser.add_argument("--sample", type=int, default=None,
                        help="Sample N edge cases for autopsy (default: all)")
    parser.add_argument("--transitions", action="store_true",
                        help="Show depth transition analysis")
    parser.add_argument("--simulate-budget", action="store_true",
                        help="Run budget what-if simulation")
    parser.add_argument("--limit", type=int, default=20,
                        help="Max sessions to display")
    args = parser.parse_args()

    receipts = load_receipts(args)
    if not receipts:
        print("No receipts to explore.")
        sys.exit(0)

    sessions = reconstruct_sessions(receipts, session_gap_seconds=args.gap)
    annotated = [annotate_session(s) for s in sessions]

    print(f"\n{C_CYAN}Loaded {len(receipts)} receipts → {len(sessions)} sessions{C_RESET}")

    # Apply filters
    filtered = annotated

    if args.filter == "destructive":
        filtered = [a for a in annotated if a.has_destructive_kills]
    elif args.filter == "waste_loop":
        filtered = [a for a in annotated if a.has_waste_loops]
    elif args.filter == "terminal":
        filtered = [a for a in annotated if a.has_terminal_kills]
    elif args.filter == "salvageable":
        filtered = [a for a in annotated if a.is_salvageable]
    elif args.filter == "success":
        filtered = [a for a in annotated if a.session.has_success and a.depth > 1]

    if args.depth:
        filtered = [a for a in filtered if a.depth >= args.depth]

    # Edge case sampler mode
    if args.sample:
        print_edge_sample(annotated, args.sample)
        return

    # Transition analysis mode
    if args.transitions:
        print_transition_analysis(annotated)
        return

    # Budget simulation mode
    if args.simulate_budget:
        print_budget_simulation(annotated)
        return

    # Default: show filtered sessions
    print(f"{C_CYAN}Showing {min(len(filtered), args.limit)} of {len(filtered)} matching sessions{C_RESET}")

    for i, ann in enumerate(filtered[:args.limit]):
        print_session_detail(ann, i)

    # Always show summary
    if filtered:
        depths = [a.depth for a in filtered]
        costs = [a.total_cost for a in filtered]
        salvageable = sum(1 for a in filtered if a.is_salvageable)
        print(f"\n{C_BOLD}Summary:{C_RESET}")
        print(f"  Matching sessions:  {len(filtered)}")
        print(f"  Avg depth:          {sum(depths) / len(depths):.1f}")
        print(f"  Max depth:          {max(depths)}")
        print(f"  Total cost:         ${sum(costs):.6f}")
        print(f"  Salvageable:        {salvageable} ({salvageable / len(filtered) * 100:.1f}%)")


if __name__ == "__main__":
    main()
