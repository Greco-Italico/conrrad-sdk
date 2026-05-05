"""
Kernell OS — Watchdog (Nivel 3: External Process Monitor)
═════════════════════════════════════════════════════════════
Lives OUTSIDE the worker AND outside the agent.
This is a separate thread/process that monitors all executions
and kills anything that violates invariants.

Kills:
  - Recursion loops (depth > N)
  - Retry storms (requests > M)
  - Budget drain (spent >= budget)
  - Time bombs (elapsed > T seconds)
  - Hallucinated tool calls (rapid-fire pattern)

This is the LAST line of defense. If both the agent and the proxy
fail to enforce limits, the watchdog kills the process.

Usage:
    supervisor = ExecutionSupervisor(proxy)
    watchdog = Watchdog(supervisor, tick_interval=1.0)
    watchdog.start()   # Runs in background thread
    # ... later ...
    watchdog.stop()
"""
from __future__ import annotations
import logging, time, threading, signal, os
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("kernell.runtime.watchdog")


class Watchdog:
    """
    External process watchdog. Runs in a daemon thread.
    Ticks every N seconds and checks ALL active executions.

    If any invariant is violated:
      1. Calls supervisor.kill() (severs network + rolls back funds)
      2. Optionally kills the OS process (SIGKILL for VMs)
      3. Logs everything for forensics

    Anti-loop detection:
      Tracks request velocity. If requests_per_second > threshold,
      triggers kill even if budget isn't exhausted yet (cost-of-delay attack).
    """

    def __init__(
        self,
        supervisor,                          # ExecutionSupervisor
        tick_interval: float = 1.0,          # Check every N seconds
        velocity_threshold: float = 5.0,     # Max requests/second before alarm
        stall_timeout: float = 10.0,         # Kill if no progress for N seconds
        process_killer: Optional[Callable[[str], None]] = None,  # pid/vm killer
    ):
        self.supervisor = supervisor
        self.tick_interval = tick_interval
        self.velocity_threshold = velocity_threshold
        self.stall_timeout = stall_timeout
        self._process_killer = process_killer

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._velocity_snapshots: Dict[str, List[tuple]] = {}  # exec_id → [(ts, count)]
        self._ticks = 0
        self._kills = 0

    def start(self):
        """Start the watchdog daemon thread."""
        if self._thread and self._thread.is_alive():
            logger.warning("[WATCHDOG] Already running")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="kernell-watchdog")
        self._thread.start()
        logger.info(f"[WATCHDOG] Started (tick={self.tick_interval}s, velocity_limit={self.velocity_threshold} req/s)")

    def stop(self):
        """Stop the watchdog."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5.0)
        logger.info(f"[WATCHDOG] Stopped after {self._ticks} ticks, {self._kills} kills")

    def _run(self):
        """Main watchdog loop."""
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as e:
                logger.error(f"[WATCHDOG] Tick error: {e}")
            self._stop_event.wait(timeout=self.tick_interval)

    def _tick(self):
        """Single watchdog tick. Checks all active executions."""
        self._ticks += 1
        active = self.supervisor.list_active()

        for exec_info in active:
            exec_id = exec_info["execution_id"]
            result = self.supervisor.check(exec_id)

            if result.get("status") == "KILLED":
                self._kills += 1
                logger.critical(
                    f"[WATCHDOG] KILL via supervisor: {exec_id} "
                    f"violations={result.get('violations')}"
                )
                self._kill_process(exec_id)
                continue

            # Velocity check (anti-loop: rapid-fire requests)
            if result.get("status") == "OK":
                self._check_velocity(exec_id, exec_info.get("requests", 0))

    def _check_velocity(self, exec_id: str, request_count: int):
        """Detect request velocity anomalies (loops, retry storms)."""
        now = time.time()

        if exec_id not in self._velocity_snapshots:
            self._velocity_snapshots[exec_id] = []

        history = self._velocity_snapshots[exec_id]
        history.append((now, request_count))

        # Keep only last 10 seconds of history
        cutoff = now - 10.0
        history[:] = [(t, c) for t, c in history if t > cutoff]

        if len(history) >= 2:
            dt = history[-1][0] - history[0][0]
            dreq = history[-1][1] - history[0][1]
            if dt > 0:
                velocity = dreq / dt
                if velocity > self.velocity_threshold:
                    reason = f"VELOCITY_ANOMALY: {velocity:.1f} req/s > {self.velocity_threshold} req/s"
                    logger.critical(f"[WATCHDOG] {reason} for {exec_id}")
                    self.supervisor.kill(exec_id, reason)
                    self._kills += 1
                    self._kill_process(exec_id)

    def _kill_process(self, exec_id: str):
        """Kill the actual OS process/VM if process_killer is configured."""
        if self._process_killer:
            try:
                self._process_killer(exec_id)
                logger.info(f"[WATCHDOG] Process killed for {exec_id}")
            except Exception as e:
                logger.error(f"[WATCHDOG] Process kill failed for {exec_id}: {e}")

    @property
    def stats(self) -> Dict[str, Any]:
        return {"ticks": self._ticks, "kills": self._kills,
                "running": self._thread.is_alive() if self._thread else False,
                "tick_interval": self.tick_interval}


def kill_process_by_pid(pid: int):
    """Helper: kill a process by PID (for real VM/container kills)."""
    try:
        os.kill(pid, signal.SIGKILL)
        logger.info(f"[WATCHDOG] SIGKILL sent to PID {pid}")
    except ProcessLookupError:
        logger.warning(f"[WATCHDOG] PID {pid} already dead")
    except PermissionError:
        logger.error(f"[WATCHDOG] Permission denied killing PID {pid}")
