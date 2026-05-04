import time
import json

class ReconciliationDecision:
    def __init__(self, action: str, winner: str, loser: str, reason: str):
        self.action = action  # "NOOP" | "MANUAL_REVIEW" | "FORCE_SYNC_A" | "FORCE_SYNC_B" | "COMPENSATE"
        self.winner = winner  # "A" | "B" | "NONE"
        self.loser = loser
        self.reason = reason


class ReconciliationExecutor:
    def __init__(self, redis_a, redis_b, wal_stream="kernell:wal"):
        self.ra = redis_a
        self.rb = redis_b
        self.wal = wal_stream

    def execute(self, request_id: str, decision: dict):
        action = decision.get("action")
        winner = decision.get("winner")
        reason = decision.get("reason")
        
        if action == "NOOP" or action == "ACCEPT":
            return {"status": "success", "message": "No action needed"}

        if action == "MANUAL_REVIEW":
            self._freeze(request_id, reason)
            return {"status": "frozen", "message": "Execution frozen for manual review"}

        elif action.startswith("FORCE_SYNC"):
            # FORCE_SYNC_A means A won, B must be synced to A's state
            self._force_sync(request_id, winner, reason)
            return {"status": "success", "message": f"Force sync applied from {winner}"}

        elif action == "COMPENSATE":
            self._compensate(request_id, winner, reason)
            return {"status": "success", "message": f"Compensation event emitted from {winner}"}

        else:
            raise Exception(f"Unknown action {action}")

    def _freeze(self, request_id: str, reason: str):
        state_a = self._get_state("A", request_id)
        state_b = self._get_state("B", request_id)
        current_epoch = max(state_a["epoch"], state_b["epoch"])
        
        key = f"kernell:exec:{request_id}"
        
        for r in [self.ra, self.rb]:
            if r:
                r.hset(key, "state", "FROZEN")
        
        self._emit_wal(request_id, "FREEZE", {
            "reason": reason,
            "epoch": current_epoch
        })

    def _get_state(self, region: str, request_id: str):
        r = self.ra if region == "A" else self.rb
        if not r:
            return {"epoch": 0, "state": "UNKNOWN", "result_ptr": None}
            
        key = f"kernell:exec:{request_id}"
        data = r.hgetall(key)
        
        return {
            "epoch": int(data.get("epoch", 0)),
            "state": data.get("state", "UNKNOWN"),
            "result_ptr": data.get("result_ptr")
        }

    def _force_sync(self, request_id: str, winner: str, reason: str):
        winner_state = self._get_state(winner, request_id)
        
        sync_event = {
            "event": "FORCE_SYNC",
            "epoch": winner_state["epoch"] + 1,
            "state_after": winner_state["state"],
            "result_ptr": winner_state.get("result_ptr", ""),
            "source_region": winner,
            "reason": reason
        }
        
        self._emit_wal(request_id, "FORCE_SYNC", sync_event)
        
        for r in [self.ra, self.rb]:
            if r:
                self._apply_state(r, request_id, sync_event)

    def _compensate(self, request_id: str, winner: str, reason: str):
        state = self._get_state(winner, request_id)
        
        compensation_event = {
            "event": "COMPENSATE",
            "epoch": state["epoch"] + 1,
            "target_epoch": state["epoch"],
            "reason": reason
        }
        
        self._emit_wal(request_id, "COMPENSATE", compensation_event)
        self._trigger_outbox_compensation(request_id, state)

    def _emit_wal(self, request_id: str, event_type: str, payload: dict):
        event = {
            "request_id": request_id,
            "event": event_type,
            "ts": str(time.time()),
            "payload": json.dumps(payload)
        }
        
        for r in [self.ra, self.rb]:
            if r:
                r.xadd(self.wal, event)

    def _apply_state(self, redis_client, request_id: str, event: dict):
        key = f"kernell:exec:{request_id}"
        mapping = {
            "epoch": event["epoch"],
            "state": event["state_after"]
        }
        if event.get("result_ptr"):
            mapping["result_ptr"] = event["result_ptr"]
            
        redis_client.hset(key, mapping=mapping)

    def _trigger_outbox_compensation(self, request_id: str, state: dict):
        # Inverse logic for side-effects: refund Stripe, reverse Webhooks, etc.
        pass
