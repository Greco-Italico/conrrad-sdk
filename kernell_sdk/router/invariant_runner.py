import logging
from typing import List, Dict, Any

try:
    import redis
except ImportError:
    pass

from kernell_sdk.router.simulation_engine import IncidentReplayer, StateValidator

logger = logging.getLogger("kernell.runtime.invariants")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. Invariant Registry
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class Invariant:
    def check(self, request_id: str, state: Dict[str, Any]):
        raise NotImplementedError


class NoEpochRegression(Invariant):
    def check(self, request_id: str, state: Dict[str, Any]):
        if state.get("epoch", 0) < 0:
            raise Exception(f"[{request_id}] Invalid negative epoch")


class TerminalStateConsistency(Invariant):
    def check(self, request_id: str, state: Dict[str, Any]):
        if state.get("state") == "COMPLETED" and "result_ptr" not in state:
            raise Exception(f"[{request_id}] COMPLETED without result_ptr")


class NoZombieExecution(Invariant):
    def check(self, request_id: str, state: Dict[str, Any]):
        if state.get("state") == "COMPLETED" and state.get("epoch", 0) == 0:
            raise Exception(f"[{request_id}] Zombie commit detected (epoch=0 on COMPLETED)")

class SingleCommitInvariant(Invariant):
    def check(self, request_id: str, state: Dict[str, Any]):
        if state.get("committed") and state["history"].count(
            next((h for h in state["history"] if h["type"] == "COMMIT"), None)
        ) > 1:
            raise Exception(f"[{request_id}] Multiple COMMIT events detected")

class MustStartInvariant(Invariant):
    def check(self, request_id: str, state: Dict[str, Any]):
        if not any(h["type"] == "START" for h in state.get("history", [])):
            raise Exception(f"[{request_id}] Execution never STARTED")

class CausalOrderInvariant(Invariant):
    def check(self, request_id: str, state: Dict[str, Any]):
        seen_start = False
        for h in state.get("history", []):
            if h["type"] == "START":
                seen_start = True
            elif h["type"] in ("RECLAIM", "COMMIT") and not seen_start:
                raise Exception(f"[{request_id}] Causal violation: event before START")

class MonotonicTimeInvariant(Invariant):
    def check(self, request_id: str, state: Dict[str, Any]):
        prev = None
        for h in state.get("history", []):
            if prev and h["ts"] < prev:
                raise Exception(f"[{request_id}] Timestamp regression detected")
            prev = h["ts"]

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. Invariant Runner
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class InvariantRunner:
    def __init__(self, invariants: List[Invariant]):
        self.invariants = invariants

    def run(self, request_id: str, state: Dict[str, Any]):
        for inv in self.invariants:
            inv.check(request_id, state)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. Batch Runner (CI/CD Enabler)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class BatchInvariantTester:
    def __init__(self, replayer: IncidentReplayer, validator: StateValidator, runner: InvariantRunner):
        self.replayer = replayer
        self.validator = validator
        self.runner = runner

    def run_batch(self, request_ids: List[str]) -> Dict[str, List]:
        results = {
            "passed": [],
            "failed": []
        }

        for rid in request_ids:
            try:
                simulated = self.replayer.replay_request(rid)

                if not simulated:
                    continue

                # 🔴 1. Validación contra estado real de Redis
                self.validator.validate(rid, simulated)

                # 🔴 2. Validación formal matemática (Invariantes)
                self.runner.run(rid, simulated)

                results["passed"].append(rid)

            except Exception as e:
                logger.error(f"Invariant check failed for {rid}: {e}")
                results["failed"].append((rid, str(e)))

        return results

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. Auto-discovery de request_ids desde el WAL
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class RequestDiscovery:
    def __init__(self, redis_client):
        self.r = redis_client

    def sample(self, limit=1000) -> List[str]:
        ids = set()
        entries = self.r.xrevrange("kernell:wal", count=limit)

        for _, data in entries:
            rid = data.get("request_id")
            if not rid:
                continue
            if rid.startswith("kernell:exec:"):
                rid = rid.split("kernell:exec:")[1]
            ids.add(rid)

        return list(ids)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Helper de CI
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def run_ci_pipeline(redis_client=None, limit=500):
    if not redis_client:
        redis_client = redis.Redis(host="localhost", port=6379, decode_responses=True)
        
    from kernell_sdk.router.simulation_engine import WALEventAdapter
    
    adapter = WALEventAdapter(redis_client)
    replayer = IncidentReplayer(adapter)
    validator = StateValidator(redis_client)

    invariants = [
        NoEpochRegression(),
        TerminalStateConsistency(),
        NoZombieExecution(),
        SingleCommitInvariant(),
        MustStartInvariant(),
        CausalOrderInvariant(),
        MonotonicTimeInvariant()
    ]

    runner = InvariantRunner(invariants)
    tester = BatchInvariantTester(replayer, validator, runner)
    discovery = RequestDiscovery(redis_client)
    
    request_ids = discovery.sample(limit=limit)
    if not request_ids:
        logger.info("No requests found in WAL to test.")
        return {"passed": [], "failed": []}
        
    logger.info(f"Running Invariant CI Pipeline against {len(request_ids)} historical requests...")
    results = tester.run_batch(request_ids)
    return results

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    res = run_ci_pipeline()
    print("PASSED:", len(res["passed"]))
    print("FAILED:", len(res["failed"]))

    if res["failed"]:
        raise SystemExit(1)
