class ReconciliationResult:
    def __init__(self, winner: str, action: str, reason: str, metadata: dict = None):
        self.winner = winner  # "A" | "B" | "NONE"
        self.action = action  # "ACCEPT" | "FORCE_SYNC_A" | "FORCE_SYNC_B" | "ROLLBACK" | "MANUAL_REVIEW"
        self.reason = reason
        self.metadata = metadata or {}

    def to_dict(self):
        return {
            "winner": self.winner,
            "action": self.action,
            "reason": self.reason,
            "metadata": self.metadata
        }

class ReconciliationContext:
    def __init__(self, exec_a: dict, exec_b: dict):
        self.a = exec_a
        self.b = exec_b

    @property
    def a_completed(self) -> bool:
        return self.a.get("state") == "COMPLETED"

    @property
    def b_completed(self) -> bool:
        return self.b.get("state") == "COMPLETED"

    @property
    def a_fp(self) -> str:
        return self.a.get("execution_fp") or self.a.get("fingerprint")

    @property
    def b_fp(self) -> str:
        return self.b.get("execution_fp") or self.b.get("fingerprint")

    @property
    def same_fp(self) -> bool:
        return self.a_fp is not None and self.a_fp == self.b_fp


class ReconciliationPolicy:
    def evaluate(self, ctx: ReconciliationContext) -> ReconciliationResult:
        raise NotImplementedError


class ExactMatchPolicy(ReconciliationPolicy):
    def evaluate(self, ctx: ReconciliationContext):
        if ctx.same_fp and (ctx.a_completed or ctx.b_completed):
            return ReconciliationResult(
                winner="NONE",
                action="ACCEPT",
                reason="Fingerprints match (100% Deterministic Consensus)"
            )
        return None


class SingleCompletionPolicy(ReconciliationPolicy):
    def evaluate(self, ctx: ReconciliationContext):
        if ctx.a_completed and not ctx.b_completed:
            return ReconciliationResult(
                winner="A",
                action="FORCE_SYNC_B",
                reason="A completed, B is lagging/incomplete"
            )

        if ctx.b_completed and not ctx.a_completed:
            return ReconciliationResult(
                winner="B",
                action="FORCE_SYNC_A",
                reason="B completed, A is lagging/incomplete"
            )
        return None


class BothCompletedPolicy(ReconciliationPolicy):
    def evaluate(self, ctx: ReconciliationContext):
        if ctx.a_completed and ctx.b_completed and not ctx.same_fp:
            a_epoch = ctx.a.get("epoch", 0)
            b_epoch = ctx.b.get("epoch", 0)

            if a_epoch < b_epoch:
                winner = "A"
                action = "FORCE_SYNC_B"
            elif b_epoch < a_epoch:
                winner = "B"
                action = "FORCE_SYNC_A"
            else:
                return ReconciliationResult(
                    winner="NONE",
                    action="MANUAL_REVIEW",
                    reason="Same epoch, different fingerprints (Irresolvable Collision)"
                )

            return ReconciliationResult(
                winner=winner,
                action=action,
                reason="Both completed, resolved by earliest epoch precedence",
                metadata={
                    "a_epoch": a_epoch,
                    "b_epoch": b_epoch
                }
            )
        return None


class InvalidStatePolicy(ReconciliationPolicy):
    def evaluate(self, ctx: ReconciliationContext):
        a_failed = ctx.a.get("state") == "FAILED"
        b_failed = ctx.b.get("state") == "FAILED"

        if a_failed and ctx.b_completed:
            return ReconciliationResult(winner="B", action="FORCE_SYNC_A", reason="B succeeded while A failed")
        if b_failed and ctx.a_completed:
            return ReconciliationResult(winner="A", action="FORCE_SYNC_B", reason="A succeeded while B failed")
        
        return None


class FallbackPolicy(ReconciliationPolicy):
    def evaluate(self, ctx: ReconciliationContext):
        return ReconciliationResult(
            winner="NONE",
            action="MANUAL_REVIEW",
            reason="Exhausted all deterministic policies. Requires human forensic analysis."
        )


class ReconciliationEngine:
    def __init__(self, policies=None):
        self.policies = policies or [
            ExactMatchPolicy(),
            InvalidStatePolicy(),
            SingleCompletionPolicy(),
            BothCompletedPolicy(),
            FallbackPolicy()
        ]

    def reconcile(self, exec_a: dict, exec_b: dict) -> ReconciliationResult:
        ctx = ReconciliationContext(exec_a, exec_b)

        for policy in self.policies:
            result = policy.evaluate(ctx)
            if result:
                return result

        return ReconciliationResult(
            winner="NONE",
            action="MANUAL_REVIEW",
            reason="Exhausted policies"
        )


def reconcile_from_timelines(timeline_a: list, timeline_b: list) -> ReconciliationResult:
    if not timeline_a and not timeline_b:
        return ReconciliationResult("NONE", "MANUAL_REVIEW", "Both timelines empty")
    
    exec_a = timeline_a[-1].get("state_snapshot", {}) if timeline_a else {}
    exec_b = timeline_b[-1].get("state_snapshot", {}) if timeline_b else {}

    # Extract critical metadata from the final frames
    if timeline_a:
        exec_a["execution_fp"] = timeline_a[-1].get("fingerprint")
        exec_a["epoch"] = timeline_a[-1].get("epoch")
        exec_a["state"] = timeline_a[-1].get("state")
    if timeline_b:
        exec_b["execution_fp"] = timeline_b[-1].get("fingerprint")
        exec_b["epoch"] = timeline_b[-1].get("epoch")
        exec_b["state"] = timeline_b[-1].get("state")

    engine = ReconciliationEngine()
    return engine.reconcile(exec_a, exec_b)
