import sys
from kernell_sdk.router.simulation_engine import SimulationEngine, NormalizedEvent

events = [
    NormalizedEvent("r1", 1, "START", 10.0, {}),
    NormalizedEvent("r1", 2, "FAILOVER", 12.0, {}),
    NormalizedEvent("r1", 1, "COMMIT", 14.0, {}) # Delayed commit from old leader
]

try:
    SimulationEngine(events).build()
    print("FAIL: Simulation succeeded unexpectedly")
except Exception as e:
    print(f"PASS: Caught expected exception: {e}")
