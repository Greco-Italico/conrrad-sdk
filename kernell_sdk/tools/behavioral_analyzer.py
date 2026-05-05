"""
Kernell Pay — Behavioral Intelligence Layer (Nivel 1.6)
═══════════════════════════════════════════════════════
Converts flat ExecutionReceipts into user behavior sequences.

Answers the question the financial layer cannot:
  "Is the system disciplining thought... or killing it?"

Classifies every execution into one of 5 outcomes:
  SUCCESS       — completed within budget
  NEAR_MISS     — killed at 80-100% budget utilization
  INSTANT_DEATH — killed at <30% budget utilization
  WASTE         — completed but with very low token efficiency
  ABANDON       — killed, no retry follows within the session window

Derives session-level metrics:
  Completion Rate (per logical attempt, not per request)
  Retry Depth (how many attempts before success or abandon)
  Recovery Rate (% of kills that eventually lead to success)
  Time-to-Abandon (how fast users give up)
"""

import json
import sys
import argparse
from typing import List, Dict, Optional
from collections import defaultdict
from dataclasses import dataclass, field

C_RED = '\033[91m'
C_GREEN = '\033[92m'
C_YELLOW = '\033[93m'
C_CYAN = '\033[96m'
C_RESET = '\033[0m'
C_BOLD = '\033[1m'

# ─── Outcome Classification ─────────────────────────────────────

def classify_outcome(receipt: Dict) -> str:
    """Classify a single receipt into a behavioral outcome."""
    status = receipt.get("status", "UNKNOWN")
    reserved = receipt.get("reserved_usd", 0.0)
    actual = receipt.get("actual_cost_usd", 0.0)
    tokens = receipt.get("tokens_completion", 0)

    if status == "COMPLETED":
        # Check for waste: completed but almost no useful tokens relative to cost
        if reserved > 0 and tokens < 5:
            return "WASTE"
        return "SUCCESS"

    if status in ("KILLED", "FAILED"):
        if reserved <= 0:
            return "INSTANT_DEATH"

        utilization = actual / reserved if reserved > 0 else 0.0

        if utilization >= 0.80:
            return "NEAR_MISS"
        elif utilization < 0.30:
            return "INSTANT_DEATH"
        else:
            # Mid-range kill — treat as near miss if tokens were substantial
            return "NEAR_MISS" if tokens > 20 else "INSTANT_DEATH"

    return "WASTE"


# ─── Session Reconstruction ─────────────────────────────────────

@dataclass
class Session:
    """A logical user session: a sequence of attempts toward the same goal."""
    tenant_id: str
    receipts: List[Dict] = field(default_factory=list)
    outcomes: List[str] = field(default_factory=list)

    @property
    def attempt_count(self) -> int:
        return len(self.receipts)

    @property
    def final_outcome(self) -> str:
        return self.outcomes[-1] if self.outcomes else "UNKNOWN"

    @property
    def has_success(self) -> bool:
        return "SUCCESS" in self.outcomes

    @property
    def total_cost(self) -> float:
        return sum(r.get("actual_cost_usd", 0.0) for r in self.receipts)

    @property
    def duration_seconds(self) -> float:
        if len(self.receipts) < 2:
            return 0.0
        first = self.receipts[0].get("timestamp_start", 0.0)
        last = self.receipts[-1].get("timestamp_end", 0.0)
        return last - first

    @property
    def is_abandoned(self) -> bool:
        """A session is abandoned if the last outcome is not SUCCESS."""
        return self.final_outcome != "SUCCESS"


def reconstruct_sessions(
    receipts: List[Dict],
    session_gap_seconds: float = 120.0
) -> List[Session]:
    """
    Group receipts into sessions per tenant.

    A new session starts when the gap between consecutive requests
    from the same tenant exceeds `session_gap_seconds`.
    """
    # Sort by tenant, then by timestamp
    sorted_receipts = sorted(
        receipts,
        key=lambda r: (r.get("tenant_id", ""), r.get("timestamp_start", 0.0))
    )

    sessions: List[Session] = []
    tenant_groups: Dict[str, List[Dict]] = defaultdict(list)

    for r in sorted_receipts:
        tid = r.get("tenant_id", "unknown")
        tenant_groups[tid].append(r)

    for tid, tenant_receipts in tenant_groups.items():
        current_session = Session(tenant_id=tid)

        for r in tenant_receipts:
            ts = r.get("timestamp_start", 0.0)

            if current_session.receipts:
                last_ts = current_session.receipts[-1].get("timestamp_end", 0.0)
                gap = ts - last_ts

                if gap > session_gap_seconds:
                    # Close current session, start a new one
                    sessions.append(current_session)
                    current_session = Session(tenant_id=tid)

            outcome = classify_outcome(r)
            current_session.receipts.append(r)
            current_session.outcomes.append(outcome)

        if current_session.receipts:
            sessions.append(current_session)

    return sessions


# ─── Kill Classification ────────────────────────────────────────

def classify_kill(session: Session, kill_index: int) -> str:
    """
    Determines if a kill within a session was useful or destructive.

    Classification is based on CONVERGENCE (did the session eventually
    reach a valid outcome?), not on raw token count.

    USEFUL_KILL:      Kill followed by a retry that succeeds with FEWER or
                      equal tokens → model was inefficient, proxy was right to cut.
    DESTRUCTIVE_KILL: Kill followed by a retry that succeeds with MORE tokens
                      → the system asfixiated a valid trajectory that needed room.
    WASTE_LOOP:       Kill followed by more kills/failures, session never succeeds
                      → neither the model nor the system could solve it.
    TERMINAL_KILL:    Kill with no retry → user abandoned immediately.
    """
    remaining = session.receipts[kill_index + 1:]
    if not remaining:
        return "TERMINAL_KILL"

    killed_tokens = session.receipts[kill_index].get("tokens_completion", 0)

    # Look ahead: did the session EVER converge to SUCCESS after this kill?
    for future_receipt in remaining:
        future_status = future_receipt.get("status", "UNKNOWN")
        if future_status == "COMPLETED":
            future_tokens = future_receipt.get("tokens_completion", 0)
            if future_tokens <= killed_tokens:
                # The successful attempt was more efficient than the killed one.
                # The model was being wasteful; the proxy saved money.
                return "USEFUL_KILL"
            else:
                # The successful attempt needed MORE tokens to finish.
                # The proxy cut a valid trajectory too early.
                return "DESTRUCTIVE_KILL"

    # Session never recovered after this kill → spiral of retries without resolution
    return "WASTE_LOOP"


# ─── Analysis Engine ────────────────────────────────────────────

def analyze_sessions(sessions: List[Session]) -> Dict:
    """Compute aggregate behavioral metrics from reconstructed sessions."""
    total_sessions = len(sessions)
    if total_sessions == 0:
        return {}

    completed_sessions = sum(1 for s in sessions if s.has_success)
    abandoned_sessions = sum(1 for s in sessions if s.is_abandoned)

    retry_depths = [s.attempt_count for s in sessions if s.attempt_count > 1]
    single_attempt = sum(1 for s in sessions if s.attempt_count == 1)

    # Kill analysis
    useful_kills = 0
    destructive_kills = 0
    waste_loops = 0
    terminal_kills = 0

    all_outcomes = defaultdict(int)
    for s in sessions:
        for i, outcome in enumerate(s.outcomes):
            all_outcomes[outcome] += 1
            if outcome in ("NEAR_MISS", "INSTANT_DEATH"):
                kill_type = classify_kill(s, i)
                if kill_type == "USEFUL_KILL":
                    useful_kills += 1
                elif kill_type == "DESTRUCTIVE_KILL":
                    destructive_kills += 1
                elif kill_type == "WASTE_LOOP":
                    waste_loops += 1
                else:
                    terminal_kills += 1

    # Depth-of-attempt distribution
    depth_distribution = defaultdict(int)
    for s in sessions:
        depth_distribution[min(s.attempt_count, 5)] += 1  # cap at 5+ bucket

    # Time-to-abandon for abandoned sessions
    abandon_durations = [
        s.duration_seconds for s in sessions
        if s.is_abandoned and s.duration_seconds > 0
    ]

    total_kills = useful_kills + destructive_kills + waste_loops + terminal_kills

    return {
        "total_sessions": total_sessions,
        "completion_rate": completed_sessions / total_sessions if total_sessions else 0,
        "abandon_rate": abandoned_sessions / total_sessions if total_sessions else 0,
        "single_attempt_pct": single_attempt / total_sessions if total_sessions else 0,
        "avg_retry_depth": sum(retry_depths) / len(retry_depths) if retry_depths else 1.0,
        "max_retry_depth": max(retry_depths) if retry_depths else 1,
        "recovery_rate": completed_sessions / max(1, completed_sessions + abandoned_sessions),
        "outcome_distribution": dict(all_outcomes),
        "kill_analysis": {
            "useful_kills": useful_kills,
            "destructive_kills": destructive_kills,
            "waste_loops": waste_loops,
            "terminal_kills": terminal_kills,
            "kill_utility_ratio": useful_kills / total_kills if total_kills else 0,
        },
        "depth_distribution": dict(depth_distribution),
        "time_to_abandon": {
            "avg_seconds": sum(abandon_durations) / len(abandon_durations) if abandon_durations else 0,
            "max_seconds": max(abandon_durations) if abandon_durations else 0,
        },
    }


# ─── Report Renderer ────────────────────────────────────────────

def print_report(metrics: Dict):
    print(f"\n{C_CYAN}{C_BOLD}{'═' * 60}")
    print(f"  KERNELL PAY — BEHAVIORAL INTELLIGENCE REPORT")
    print(f"{'═' * 60}{C_RESET}\n")

    # 1. Session Overview
    print(f"{C_BOLD}1. SESSION OVERVIEW{C_RESET}")
    print(f"   Total Sessions:     {metrics['total_sessions']}")

    cr = metrics['completion_rate']
    cr_color = C_GREEN if cr >= 0.7 else C_YELLOW if cr >= 0.4 else C_RED
    print(f"   Completion Rate:    {cr_color}{cr:.1%}{C_RESET}")

    ar = metrics['abandon_rate']
    ar_color = C_GREEN if ar <= 0.2 else C_YELLOW if ar <= 0.5 else C_RED
    print(f"   Abandon Rate:       {ar_color}{ar:.1%}{C_RESET}")

    print(f"   Single-Attempt %:   {metrics['single_attempt_pct']:.1%}")
    print(f"   Avg Retry Depth:    {metrics['avg_retry_depth']:.1f}")
    print(f"   Max Retry Depth:    {metrics['max_retry_depth']}")
    print(f"   Recovery Rate:      {metrics['recovery_rate']:.1%}")

    # 2. Outcome Distribution
    print(f"\n{C_BOLD}2. OUTCOME DISTRIBUTION{C_RESET}")
    dist = metrics.get("outcome_distribution", {})
    total_outcomes = sum(dist.values()) or 1
    for outcome, count in sorted(dist.items(), key=lambda x: -x[1]):
        pct = count / total_outcomes * 100
        bar = "█" * int(pct / 2)
        color = {
            "SUCCESS": C_GREEN,
            "NEAR_MISS": C_YELLOW,
            "INSTANT_DEATH": C_RED,
            "WASTE": C_RED,
            "ABANDON": C_RED,
        }.get(outcome, C_RESET)
        print(f"   {color}{outcome:<15}{C_RESET} {bar} {pct:.1f}% ({count})")

    # 3. Kill Intelligence
    print(f"\n{C_BOLD}3. KILL INTELLIGENCE{C_RESET}")
    ka = metrics.get("kill_analysis", {})
    print(f"   {C_GREEN}Useful Kills:      {ka.get('useful_kills', 0)}{C_RESET}  (model was wasteful, proxy saved money)")
    print(f"   {C_RED}Destructive Kills: {ka.get('destructive_kills', 0)}{C_RESET}  (proxy asfixiated a valid trajectory)")
    print(f"   {C_YELLOW}Waste Loops:       {ka.get('waste_loops', 0)}{C_RESET}  (retries never converged — unsolvable or wrong model)")
    print(f"   {C_YELLOW}Terminal Kills:    {ka.get('terminal_kills', 0)}{C_RESET}  (user abandoned after kill)")

    kur = ka.get('kill_utility_ratio', 0)
    kur_color = C_GREEN if kur >= 0.6 else C_YELLOW if kur >= 0.3 else C_RED
    print(f"   Kill Utility Ratio: {kur_color}{kur:.1%}{C_RESET}")

    if kur < 0.3:
        print(f"   {C_RED}⚠ ALERT: System is destroying more value than it protects.{C_RESET}")
        print(f"   {C_RED}  → Consider workflow budgeting for complex tasks.{C_RESET}")
    elif kur >= 0.6:
        print(f"   {C_GREEN}✓ System is disciplining, not punishing.{C_RESET}")

    # 3b. Depth of Attempt
    print(f"\n{C_BOLD}3b. DEPTH OF ATTEMPT{C_RESET}")
    dd = metrics.get("depth_distribution", {})
    total_s = metrics.get("total_sessions", 1)
    for depth in sorted(dd.keys()):
        count = dd[depth]
        pct = count / total_s * 100
        label = f"{depth}+" if depth >= 5 else str(depth)
        bar = "▓" * int(pct / 2)
        print(f"   Depth {label:<3} {bar} {pct:.1f}% ({count} sessions)")

    # 4. Abandonment Analysis
    print(f"\n{C_BOLD}4. ABANDONMENT ANALYSIS{C_RESET}")
    tta = metrics.get("time_to_abandon", {})
    avg_tta = tta.get("avg_seconds", 0)
    print(f"   Avg Time-to-Abandon: {avg_tta:.1f}s")
    print(f"   Max Time-to-Abandon: {tta.get('max_seconds', 0):.1f}s")

    if avg_tta > 0 and avg_tta < 10:
        print(f"   {C_RED}⚠ Users give up almost instantly. System may be hostile.{C_RESET}")
    elif avg_tta > 60:
        print(f"   {C_GREEN}✓ Users persist. System friction is tolerable.{C_RESET}")

    # 5. Verdict
    print(f"\n{C_BOLD}5. SYSTEM VERDICT{C_RESET}")
    if cr >= 0.6 and kur >= 0.5:
        print(f"   {C_GREEN}{'━' * 50}")
        print(f"   VERDICT: System DISCIPLINES thought without killing it.")
        print(f"   → Ready for Advisory Mode.")
        print(f"   {'━' * 50}{C_RESET}")
    elif cr >= 0.4:
        print(f"   {C_YELLOW}{'━' * 50}")
        print(f"   VERDICT: System is PARTIALLY effective.")
        print(f"   → Atomic tasks work. Complex tasks need workflow budgeting.")
        print(f"   {'━' * 50}{C_RESET}")
    else:
        print(f"   {C_RED}{'━' * 50}")
        print(f"   VERDICT: System is HOSTILE to productive thinking.")
        print(f"   → Economic unit (request) is wrong for this traffic mix.")
        print(f"   {'━' * 50}{C_RESET}")


# ─── Mock Data Generator ────────────────────────────────────────

def generate_mock_receipts(n: int = 200) -> List[Dict]:
    """Generate realistic receipt sequences with retries and abandons."""
    import random
    import uuid
    import time

    receipts = []
    base_time = time.time() - 3600  # Start 1 hour ago

    tenants = [f"tenant_{i}" for i in range(5)]

    for tenant in tenants:
        t = base_time + random.uniform(0, 60)
        attempts_left = n // len(tenants)

        while attempts_left > 0:
            budget = random.uniform(0.001, 0.010)
            model = random.choice(["gpt-4o", "gpt-4o-mini", "gemini-2.5-flash"])

            # Simulate a "logical task" with possible retries
            task_attempts = random.randint(1, 4)
            succeeded = False

            for attempt in range(task_attempts):
                if attempts_left <= 0:
                    break

                tokens = int(random.uniform(10, 600))
                cost = tokens * 0.0000006
                duration = random.uniform(0.5, 5.0)

                # Decide outcome based on attempt number
                roll = random.random()
                if roll < 0.55:
                    status = "COMPLETED"
                    succeeded = True
                elif roll < 0.80:
                    status = "KILLED"
                    cost = min(cost, budget * random.uniform(0.7, 1.0))
                elif roll < 0.90:
                    status = "KILLED"
                    cost = budget * random.uniform(0.01, 0.25)  # instant death
                    tokens = random.randint(1, 8)
                else:
                    status = "FAILED"
                    tokens = 0
                    cost = 0.0

                receipts.append({
                    "transaction_id": str(uuid.uuid4()),
                    "tenant_id": tenant,
                    "timestamp_start": t,
                    "timestamp_end": t + duration,
                    "requested_model": model,
                    "executed_model": model,
                    "tokens_prompt": random.randint(5, 50),
                    "tokens_completion": tokens,
                    "reserved_usd": budget,
                    "actual_cost_usd": cost,
                    "refunded_usd": max(0, budget - cost),
                    "status": status,
                    "kill_reason": "BUDGET_EXHAUSTED" if status == "KILLED" else None,
                    "retry_classification": "POLICY_INDUCED" if status == "KILLED" else None,
                })

                t += duration + random.uniform(1, 15)  # Small gap between retries
                attempts_left -= 1

                if succeeded:
                    break

            # Gap between logical tasks
            t += random.uniform(30, 300)

    return receipts


# ─── CLI Entry Point ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Kernell Pay — Behavioral Intelligence Analyzer"
    )
    parser.add_argument("--mock", action="store_true", help="Run with generated mock data")
    parser.add_argument("--gap", type=float, default=120.0,
                        help="Session gap threshold in seconds (default: 120)")
    args = parser.parse_args()

    if args.mock:
        print(f"{C_YELLOW}Generating mock behavioral data...{C_RESET}")
        receipts = generate_mock_receipts(200)
    elif not sys.stdin.isatty():
        print(f"{C_CYAN}Reading receipts from stdin...{C_RESET}")
        receipts = []
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                receipts.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    else:
        print("Pipe a JSONL file of ExecutionReceipts, or use --mock.")
        sys.exit(0)

    if not receipts:
        print("No receipts to analyze.")
        sys.exit(0)

    sessions = reconstruct_sessions(receipts, session_gap_seconds=args.gap)
    metrics = analyze_sessions(sessions)
    print_report(metrics)


if __name__ == "__main__":
    main()
