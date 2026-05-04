"""
Kernell OS — SLO Report Generator
══════════════════════════════════
Human-readable + JSONL-exportable SLO compliance reports.
"""
from kernell_sdk.slo_engine import MetricsWindow, SLOEvaluator, Severity

def generate_report(window: MetricsWindow, evaluator=None) -> str:
    ev = evaluator or SLOEvaluator()
    alerts = ev.evaluate(window)
    m = window.computed
    L = []
    L.append("=" * 64)
    L.append("  KERNELL OS — SLO COMPLIANCE REPORT")
    L.append("=" * 64)
    L.append("")
    L.append(f"  ⚡ LATENCY")
    L.append(f"     p50:  {m['p50_latency_ms']:>8.1f} ms")
    L.append(f"     p95:  {m['p95_latency_ms']:>8.1f} ms")
    L.append(f"     p99:  {m['p99_latency_ms']:>8.1f} ms")
    L.append("")
    L.append(f"  💰 ECONOMY")
    L.append(f"     Avg tokens used:     {m['avg_tokens_used']:>8.1f}")
    L.append(f"     Avg tokens reserved: {m['avg_tokens_reserved']:>8.1f}")
    L.append(f"     Cost ratio:          {m['avg_cost_ratio']:>8.4f}")
    L.append(f"     Refund ratio:        {m['avg_refund_ratio']:>8.4f}")
    L.append("")
    L.append(f"  🔪 CONTROL")
    L.append(f"     Kill rate:        {m['kill_rate']:>8.4f}  ({int(m['total_kills'])}/{int(m['total_requests'])})")
    L.append(f"     Fallback rate:    {m['fallback_rate']:>8.4f}  ({int(m['total_fallbacks'])}/{int(m['total_requests'])})")
    L.append(f"     Timeout rate:     {m['timeout_rate']:>8.4f}  ({int(m['total_timeouts'])}/{int(m['total_requests'])})")
    L.append(f"     Depth violations: {m['depth_violation_rate']:>8.4f}")
    L.append("")
    L.append(f"  💣 ERRORS")
    L.append(f"     Hard error rate:   {m['hard_error_rate']:>8.4f}")
    L.append(f"     Wallet violations: {m['wallet_violation_rate']:>8.4f}")
    L.append("")
    L.append("  📊 SLO VERDICTS")
    for a in alerts:
        L.append(f"     {a}")
    L.append("")
    crits = [a for a in alerts if a.severity == Severity.CRITICAL]
    warns = [a for a in alerts if a.severity == Severity.WARNING]
    if crits:
        L.append(f"  🔴 VERDICT: {len(crits)} CRITICAL — NOT PRODUCTION READY")
    elif warns:
        L.append(f"  🟡 VERDICT: {len(warns)} WARNING(s) — DEGRADED")
    else:
        L.append("  🟢 VERDICT: ALL SLOs COMPLIANT — PRODUCTION READY")
    L.append("=" * 64)
    return "\n".join(L)
