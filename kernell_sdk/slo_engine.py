"""
Kernell OS — SLO Engine V1
═══════════════════════════
Three-axis SLOs: Latency / Economy / Control
Streaming-first. No batch parsing. Alert-aware.
"""
import time
import math
import logging
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("kernell.slo")

class Severity(Enum):
    OK = "OK"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"

@dataclass(frozen=True)
class SLOThreshold:
    name: str
    metric: str
    direction: str  # "lte" = lower is good
    warn: float
    critical: float
    unit: str = ""

    def evaluate(self, value: float) -> "Severity":
        if self.direction == "lte":
            if value > self.critical: return Severity.CRITICAL
            if value > self.warn: return Severity.WARNING
            return Severity.OK
        else:
            if value < self.critical: return Severity.CRITICAL
            if value < self.warn: return Severity.WARNING
            return Severity.OK

SLO_REGISTRY: List[SLOThreshold] = [
    SLOThreshold("p50_latency", "p50_latency_ms", "lte", 800, 1500, "ms"),
    SLOThreshold("p95_latency", "p95_latency_ms", "lte", 2500, 5000, "ms"),
    SLOThreshold("p99_latency", "p99_latency_ms", "lte", 5000, 8000, "ms"),
    SLOThreshold("avg_cost_ratio", "avg_cost_ratio", "lte", 0.60, 0.85, "ratio"),
    SLOThreshold("kill_rate", "kill_rate", "lte", 0.15, 0.40, "ratio"),
    SLOThreshold("fallback_rate", "fallback_rate", "lte", 0.10, 0.25, "ratio"),
    SLOThreshold("depth_violation_rate", "depth_violation_rate", "lte", 0.05, 0.15, "ratio"),
    SLOThreshold("hard_error_rate", "hard_error_rate", "lte", 0.01, 0.05, "ratio"),
    SLOThreshold("wallet_violations", "wallet_violation_rate", "lte", 0.0, 0.0, "ratio"),
    SLOThreshold("timeout_rate", "timeout_rate", "lte", 0.05, 0.15, "ratio"),
]

@dataclass
class RequestMetrics:
    request_id: str
    timestamp: float
    latency_ms: float
    tokens_reserved: int
    tokens_used: int
    tokens_refunded: int
    kill_triggered: bool
    fallback_used: bool
    max_depth_reached: bool
    model_used: str
    hard_error: bool
    wallet_violation: bool
    timeout_triggered: bool = False

    @property
    def cost_ratio(self) -> float:
        if self.tokens_reserved == 0: return 0.0
        return self.tokens_used / self.tokens_reserved

class MetricsWindow:
    """Streaming aggregation window. No batch parsing."""
    def __init__(self, window_size: int = 1000):
        self.window_size = window_size
        self._latencies: List[float] = []
        self._total = 0
        self._kills = 0
        self._fallbacks = 0
        self._depth_violations = 0
        self._hard_errors = 0
        self._wallet_violations = 0
        self._timeouts = 0
        self._tokens_used_total = 0
        self._tokens_reserved_total = 0
        self._model_counts: Dict[str, int] = {}

    def ingest(self, m: RequestMetrics):
        self._latencies.append(m.latency_ms)
        self._total += 1
        self._kills += int(m.kill_triggered)
        self._fallbacks += int(m.fallback_used)
        self._depth_violations += int(m.max_depth_reached)
        self._hard_errors += int(m.hard_error)
        self._wallet_violations += int(m.wallet_violation)
        self._timeouts += int(m.timeout_triggered)
        self._tokens_used_total += m.tokens_used
        self._tokens_reserved_total += m.tokens_reserved
        self._model_counts[m.model_used] = self._model_counts.get(m.model_used, 0) + 1
        if len(self._latencies) > self.window_size * 2:
            self._latencies = self._latencies[self.window_size:]

    def _pct(self, data: List[float], p: float) -> float:
        if not data: return 0.0
        s = sorted(data)
        i = max(0, int(math.ceil(p / 100.0 * len(s))) - 1)
        return s[i]

    def _rate(self, c: int) -> float:
        return c / self._total if self._total else 0.0

    @property
    def computed(self) -> Dict[str, float]:
        tr = self._tokens_reserved_total
        return {
            "p50_latency_ms": self._pct(self._latencies, 50),
            "p95_latency_ms": self._pct(self._latencies, 95),
            "p99_latency_ms": self._pct(self._latencies, 99),
            "avg_cost_ratio": self._tokens_used_total / tr if tr else 0.0,
            "avg_tokens_used": self._tokens_used_total / self._total if self._total else 0.0,
            "avg_tokens_reserved": tr / self._total if self._total else 0.0,
            "avg_refund_ratio": 1.0 - (self._tokens_used_total / tr) if tr else 0.0,
            "kill_rate": self._rate(self._kills),
            "fallback_rate": self._rate(self._fallbacks),
            "depth_violation_rate": self._rate(self._depth_violations),
            "hard_error_rate": self._rate(self._hard_errors),
            "wallet_violation_rate": self._rate(self._wallet_violations),
            "timeout_rate": self._rate(self._timeouts),
            "total_requests": float(self._total),
            "total_kills": float(self._kills),
            "total_fallbacks": float(self._fallbacks),
            "total_timeouts": float(self._timeouts),
        }

@dataclass
class SLOAlert:
    slo_name: str
    severity: Severity
    metric_name: str
    actual_value: float
    warn_threshold: float
    critical_threshold: float
    unit: str
    timestamp: float = field(default_factory=time.time)

    def __str__(self) -> str:
        icon = {"OK": "🟢", "WARNING": "🟡", "CRITICAL": "🔴"}[self.severity.value]
        return f"{icon} [{self.severity.value}] {self.slo_name}: {self.actual_value:.4f}{self.unit} (warn={self.warn_threshold}, crit={self.critical_threshold})"

class SLOEvaluator:
    def __init__(self, slos=None, alert_cb=None):
        self.slos = slos or SLO_REGISTRY
        self.alert_cb = alert_cb or self._default_cb

    def evaluate(self, window: MetricsWindow) -> List[SLOAlert]:
        metrics = window.computed
        alerts = []
        for slo in self.slos:
            v = metrics.get(slo.metric, 0.0)
            sev = slo.evaluate(v)
            a = SLOAlert(slo.name, sev, slo.metric, v, slo.warn, slo.critical, slo.unit)
            alerts.append(a)
            if sev != Severity.OK: self.alert_cb(a)
        return alerts

    def violations(self, window: MetricsWindow) -> List[SLOAlert]:
        return [a for a in self.evaluate(window) if a.severity != Severity.OK]

    @staticmethod
    def _default_cb(a: SLOAlert):
        if a.severity == Severity.CRITICAL: logger.critical(str(a))
        else: logger.warning(str(a))
