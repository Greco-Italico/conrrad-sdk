import copy
import random
import logging

try:
    import redis
except ImportError:
    pass

from kernell_sdk.router.simulation_engine import SimulationEngine, WALEventAdapter
from kernell_sdk.router.invariant_runner import (
    InvariantRunner,
    NoEpochRegression,
    TerminalStateConsistency,
    NoZombieExecution,
    SingleCommitInvariant,
    MustStartInvariant,
    CausalOrderInvariant,
    MonotonicTimeInvariant
)

logger = logging.getLogger("kernell.runtime.fuzzer")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. Mutaciones Dirigidas (Adversarial Physics)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class WALMutator:
    def mutate(self, events):
        raise NotImplementedError

class EpochRegressionMutator(WALMutator):
    def mutate(self, events):
        if not events:
            return events
        e = copy.deepcopy(events)
        target = e[len(e)//2]
        target.epoch = max(0, target.epoch - 2)
        return e

class DuplicateCommitMutator(WALMutator):
    def mutate(self, events):
        e = copy.deepcopy(events)
        commits = [ev for ev in e if ev.type == "COMMIT"]
        if not commits:
            return e
        # Re-inject the commit at the end
        e.append(copy.deepcopy(commits[0]))
        return e

class ReorderMutator(WALMutator):
    def mutate(self, events):
        if not events: return events
        e = copy.deepcopy(events)
        e.reverse()
        return e

class DropEventMutator(WALMutator):
    def mutate(self, events):
        e = copy.deepcopy(events)
        return [ev for ev in e if ev.type != "START"]

class TimestampChaosMutator(WALMutator):
    def mutate(self, events):
        if not events: return events
        e = copy.deepcopy(events)
        # Retrocede masivamente solo el último evento para causar una regresión obvia respecto al anterior
        e[-1].ts = e[-1].ts - 1000
        return e

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. Fuzzing Engine
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class WALFuzzer:
    def __init__(self, mutators):
        self.mutators = mutators

    def fuzz(self, events):
        results = []
        for mutator in self.mutators:
            mutated = mutator.mutate(events)
            results.append((mutator.__class__.__name__, mutated))
        return results

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. FuzzRunner (Adversarial Engine)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class FuzzRunner:
    def __init__(self, invariant_runner):
        self.runner = invariant_runner

    def run(self, request_id, base_events):
        fuzzer = WALFuzzer([
            EpochRegressionMutator(),
            DuplicateCommitMutator(),
            ReorderMutator(),
            DropEventMutator(),
            TimestampChaosMutator()
        ])

        mutated_sets = fuzzer.fuzz(base_events)
        report = []

        for name, events in mutated_sets:
            try:
                engine = SimulationEngine(events)
                engine.build()
                
                state = engine.state.executions.get(request_id)
                if state:
                    self.runner.run(request_id, state)
                    report.append((name, "❌ NOT DETECTED"))
                else:
                    report.append((name, "❌ NOT DETECTED (State Empty)"))

            except Exception as e:
                report.append((name, f"✅ DETECTED: {str(e)}"))

        return report

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. Integración y Ejecución
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def run_fuzzing():
    r = redis.Redis(host="localhost", port=6379, decode_responses=True)
    adapter = WALEventAdapter(r)
    events = adapter.fetch_range()

    # Buscamos un request_id que tenga eventos (ej. black-swan-test)
    rids = list(set([e.request_id for e in events if e.request_id]))
    if not rids:
        print("No events in WAL to fuzz.")
        return
        
    rid = "fuzz-test-commit"
    base = [e for e in events if e.request_id == rid]

    print(f"\n🔬 Inyectando Caos Determinista en {rid} ({len(base)} eventos base)...\n")

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
    fuzz_runner = FuzzRunner(runner)

    report = fuzz_runner.run(rid, base)

    for name, result in report:
        print(f"{name:<25} → {result}")

if __name__ == "__main__":
    run_fuzzing()
