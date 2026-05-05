"""
Kernell OS — Calibration Engine
════════════════════════════════
Bridges the audit log and adaptive budget policy.
Uses control theory (EMA) rather than ML to dynamically penalize or reward
budget allocation based on historical system stability in specific contexts.

"We do not learn confidence; we learn calibration."

Signals ingested from JSONL logs:
  - Prediction Error (actual_cost / estimated_cost)
  - Kill Rate (was execution killed?)
  - Drift Anomaly Rate (extreme drift triggered?)

Flow:
Confidence → CalibrationEngine → Adjusted Confidence → AdaptiveBudgetPolicy
"""
import json
import logging
import threading
import os
import time
import math
from dataclasses import dataclass, field, asdict
from typing import Dict, Tuple, Optional, Any

logger = logging.getLogger("kernell.runtime.calibration")


@dataclass
class CalibrationStats:
    """Tracks stability metrics using Exponential Moving Average (EMA)."""
    samples: int = 0
    avg_error: float = 1.0           # actual / estimated
    kill_rate: float = 0.0
    drift_anomaly_rate: float = 0.0
    last_updated: float = field(default_factory=time.time)
    
    def update(self, error: float, killed: bool, extreme_drift: bool, alpha: float = 0.1):
        self.samples += 1
        self.last_updated = time.time()
        # EMA: new_avg = old * (1 - alpha) + current * alpha
        self.avg_error = self.avg_error * (1 - alpha) + error * alpha
        self.kill_rate = self.kill_rate * (1 - alpha) + float(killed) * alpha
        self.drift_anomaly_rate = self.drift_anomaly_rate * (1 - alpha) + float(extreme_drift) * alpha


class CalibrationEngine:
    """
    Stateful engine that calibrates agent confidence based on empirical evidence.
    Grouped by discrete context buckets: (model, confidence_bucket, density_bucket)
    """

    def __init__(self, min_samples: int = 20, ema_alpha: float = 0.1, decay_tau: float = 86400.0):
        self.min_samples = min_samples
        self.ema_alpha = ema_alpha
        self.decay_tau = decay_tau  # Time constant for forgetting (default 24h)
        
        # In-memory O(1) lookup table
        # Key: (model, conf_bucket, density_bucket)
        self._stats: Dict[Tuple[str, int, int], CalibrationStats] = {}
        self._lock = threading.Lock()

    def _get_context_key(self, model: str, confidence: float, density: float) -> Tuple[str, int, int]:
        """Discretize continuous space into buckets for stability."""
        # 0.0 -> 0, 0.2 -> 1, ..., 1.0 -> 5
        conf_bucket = int(max(0.0, min(1.0, confidence)) * 5)
        dens_bucket = int(max(0.0, min(1.0, density)) * 5)
        return (model, conf_bucket, dens_bucket)

    def process_log_entry(self, entry: Dict[str, Any]):
        """
        Ingest a single audit log entry and update EMA stats.
        Expected keys: model, confidence, density, estimated_cost, actual_cost, killed, kill_reason
        """
        model = entry.get("model", "unknown")
        confidence = float(entry.get("confidence", 0.5))
        density = float(entry.get("density", 0.5))
        
        estimated = float(entry.get("estimated_cost_usd", 0.0))
        actual = float(entry.get("cost_usd", 0.0))
        
        # Avoid division by zero
        error = (actual / estimated) if estimated > 0.000001 else 1.0
        
        killed = bool(entry.get("killed", False))
        kill_reason = str(entry.get("kill_reason") or "")
        extreme_drift = "EXTREME_DRIFT_ANOMALY" in kill_reason
        
        key = self._get_context_key(model, confidence, density)
        
        with self._lock:
            if key not in self._stats:
                self._stats[key] = CalibrationStats()
            self._stats[key].update(error, killed, extreme_drift, alpha=self.ema_alpha)

    def ingest_jsonl(self, filepath: str):
        """Batch load historical logs from disk to populate cold-start state."""
        if not os.path.exists(filepath):
            return
            
        with open(filepath, 'r') as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    self.process_log_entry(entry)
                except Exception as e:
                    logger.debug(f"Failed to parse audit log line: {e}")
                    
        logger.info(f"[CALIBRATION] Loaded {len(self._stats)} context buckets from {filepath}")

    def get_calibration_factor(self, model: str, confidence: float, density: float) -> Tuple[float, float]:
        """
        Calculate the penalty or reward factor for a given context.
        Returns (factor_multiplier, decay_weight).
        """
        key = self._get_context_key(model, confidence, density)
        
        with self._lock:
            stats = self._stats.get(key)
            if not stats:
                return 1.0, 0.0  # Cold start: trust base confidence
                
            # Time-based decay (forgetting curve)
            age = time.time() - stats.last_updated
            decay = math.exp(-age / self.decay_tau)
            effective_samples = stats.samples * decay
            
            if effective_samples < self.min_samples:
                return 1.0, decay
                
            # Copy to avoid holding lock during compute
            s_error = stats.avg_error
            s_kill = stats.kill_rate
            s_drift = stats.drift_anomaly_rate

        factor = 1.0
        
        # 1. Penalize systematic underestimation (financial risk)
        if s_error > 1.3:
            factor *= 0.70
        elif s_error > 1.1:
            factor *= 0.85
            
        # 2. Penalize high kill rates (system instability / loops)
        if s_kill > 0.20:
            factor *= 0.70
            
        # 3. Penalize extreme anomalies (Nuclear option)
        if s_drift > 0.05:
            factor *= 0.50
            
        # 4. Reward safe execution (Unlock potential)
        if s_error < 0.8 and s_kill < 0.05:
            factor *= 1.10
            
        # Bound the final factor to prevent wild swings
        return max(0.3, min(1.5, factor)), decay

    def calibrate(self, model: str, confidence: float, density: float) -> float:
        """
        Apply the calibration factor to the raw confidence score.
        Uses decay to prevent irreversible confidence collapse:
        If data is old (decay -> 0), it reverts naturally to raw confidence.
        """
        factor, decay = self.get_calibration_factor(model, confidence, density)
        
        # Memory floor: never forget completely to avoid catastrophic oscillation
        # But only maintain heavy scar tissue (0.2) if the zone was actually dangerous (drift > 5%)
        # Otherwise, let it heal gracefully (0.05)
        stats = None
        key = self._get_context_key(model, confidence, density)
        with self._lock:
            stats = self._stats.get(key)
            
        memory_floor = 0.2 if stats and stats.drift_anomaly_rate > 0.05 else 0.05
        effective_decay = max(memory_floor, decay)
        
        # Anti-collapse formula: mix calibrated and raw based on memory freshness
        adjusted = (confidence * factor * effective_decay) + (confidence * (1.0 - effective_decay))
        
        # Hard floor of 0.05 to avoid total paralysis, max 1.0
        return max(0.05, min(1.0, adjusted))

    def get_stats_snapshot(self) -> Dict[str, Any]:
        """Observability endpoint for the Command Center."""
        with self._lock:
            return {
                f"{k[0]}_C{k[1]}_D{k[2]}": {
                    "samples": v.samples,
                    "avg_error": round(v.avg_error, 3),
                    "kill_rate": round(v.kill_rate, 3),
                    "drift_rate": round(v.drift_anomaly_rate, 3),
                    "last_updated": round(v.last_updated, 2),
                    "age_s": round(time.time() - v.last_updated, 2)
                }
                for k, v in self._stats.items()
            }
            
    def save_state(self, filepath: str):
        """Snapshot incremental state to disk without reprocessing JSONL."""
        with self._lock:
            # Convert tuple keys to strings for JSON
            serializable = {
                f"{k[0]}|{k[1]}|{k[2]}": asdict(v)
                for k, v in self._stats.items()
            }
        
        with open(filepath, 'w') as f:
            json.dump(serializable, f, indent=2)
            
    def load_state(self, filepath: str):
        """Restore incremental state from a snapshot."""
        if not os.path.exists(filepath):
            return
            
        with open(filepath, 'r') as f:
            data = json.load(f)
            
        with self._lock:
            for k_str, v_dict in data.items():
                parts = k_str.split('|')
                if len(parts) == 3:
                    key = (parts[0], int(parts[1]), int(parts[2]))
                    self._stats[key] = CalibrationStats(**v_dict)
                    
        logger.info(f"[CALIBRATION] Restored {len(self._stats)} buckets from snapshot {filepath}")
