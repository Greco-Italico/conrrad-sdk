import time
import uuid
import json
import logging
import threading
import os
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, Callable, List
from collections import defaultdict, deque
from contextlib import contextmanager

try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

from kernell_sdk.budget_killer import ExecutionContext, BudgetExceededError
from kernell_sdk.resilience import CircuitBreakerRegistry

logger = logging.getLogger("kernell.runtime.protocol")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. Distributed ELP + WAL + Fencing Tokens + Backpressure (Redis LUA)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

LUA_START_RECLAIM = """
local exec_key = KEYS[1]
local wal_key = KEYS[2]

local wal_len = tonumber(redis.call("XLEN", wal_key) or "0")
if wal_len > 5000000 then
    return { "ERROR", "WAL_BACKPRESSURE" }
end

local ttl = tonumber(ARGV[1])
local owner = ARGV[2]
local lease_id = ARGV[3]

local time_arr = redis.call("TIME")
local now = tonumber(time_arr[1]) + (tonumber(time_arr[2]) / 1000000)

local exists = redis.call("EXISTS", exec_key)

if exists == 0 then
    redis.call("HMSET", exec_key,
        "state", "IN_PROGRESS",
        "epoch", 1,
        "lease_id", lease_id,
        "lease_expires_at", now + ttl,
        "owner", owner,
        "updated_at", now
    )
    
    redis.call("XADD", wal_key, "*",
        "event", "START",
        "request_id", exec_key,
        "epoch", "1",
        "lease_id", lease_id,
        "owner", owner,
        "ts", tostring(now),
        "state_after", "IN_PROGRESS"
    )

    return { "NEW", 1 }
end

local state = redis.call("HGET", exec_key, "state")

if state == "COMPLETED" then
    local result_ptr = redis.call("HGET", exec_key, "result_ptr")
    return { "COMPLETED", result_ptr }
end

local lease_expires_at = tonumber(redis.call("HGET", exec_key, "lease_expires_at") or "0")

if lease_expires_at > now then
    return { "IN_PROGRESS", 0 }
end

local epoch = tonumber(redis.call("HINCRBY", exec_key, "epoch", 1))

redis.call("HMSET", exec_key,
    "state", "IN_PROGRESS",
    "lease_id", lease_id,
    "lease_expires_at", now + ttl,
    "owner", owner,
    "updated_at", now
)

redis.call("XADD", wal_key, "*",
    "event", "RECLAIM",
    "request_id", exec_key,
    "epoch", tostring(epoch),
    "lease_id", lease_id,
    "owner", owner,
    "ts", tostring(now),
    "state_after", "IN_PROGRESS"
)

return { "RECLAIMED", epoch }
"""

LUA_HEARTBEAT = """
local key = KEYS[1]
local lease_id = ARGV[1]
local ttl = tonumber(ARGV[2])

local time_arr = redis.call("TIME")
local now = tonumber(time_arr[1]) + (tonumber(time_arr[2]) / 1000000)

local current_lease = redis.call("HGET", key, "lease_id")

if current_lease ~= lease_id then
    return 0
end

redis.call("HSET", key, "lease_expires_at", now + ttl)
return 1
"""

LUA_COMMIT = """
local exec_key = KEYS[1]
local wal_key = KEYS[2]
local outbox_key = KEYS[3]

local wal_len = tonumber(redis.call("XLEN", wal_key) or "0")
if wal_len > 5000000 then
    return { "ERROR", "WAL_BACKPRESSURE" }
end

local epoch = tonumber(ARGV[1])
local result_hash = ARGV[2]
local result_ptr = ARGV[3]
local execution_fp = ARGV[4]

local time_arr = redis.call("TIME")
local now = tonumber(time_arr[1]) + (tonumber(time_arr[2]) / 1000000)

local exists = redis.call("EXISTS", exec_key)
if exists == 0 then
    return { "ERROR", "NO_RECORD" }
end

local state = redis.call("HGET", exec_key, "state")

if state == "COMPLETED" then
    return { "ERROR", "ALREADY_COMPLETED" }
end

local current_epoch = tonumber(redis.call("HGET", exec_key, "epoch"))

if current_epoch ~= epoch then
    return { "ERROR", "ZOMBIE_EPOCH" }
end

local lease_expires_at = tonumber(redis.call("HGET", exec_key, "lease_expires_at") or "0")

if lease_expires_at < now then
    return { "ERROR", "LEASE_EXPIRED" }
end

redis.call("HMSET", exec_key,
    "state", "COMPLETED",
    "result_hash", result_hash,
    "result_ptr", result_ptr,
    "execution_fp", execution_fp,
    "updated_at", now
)

redis.call("XADD", wal_key, "*",
    "event", "COMMIT",
    "request_id", exec_key,
    "epoch", tostring(epoch),
    "result_hash", result_hash,
    "result_ptr", result_ptr,
    "execution_fp", execution_fp,
    "ts", tostring(now),
    "state_after", "COMPLETED"
)

-- Encolamos el evento observable en el OUTBOX
redis.call("XADD", outbox_key, "*",
    "request_id", exec_key,
    "result_ptr", result_ptr
)

return { "COMMITTED", 1 }
"""

class ExecutionState(str, Enum):
    NEW = "NEW"
    RESERVED = "RESERVED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"
    KILLED = "KILLED"
    ORPHANED = "ORPHANED"
    RECLAIMED = "RECLAIMED"

class DistributedELPStore:
    def __init__(self, redis_client=None, lease_ttl=5.0):
        if not redis_client:
            redis_client = redis.Redis(host="localhost", port=6379, decode_responses=True)
        self.r = redis_client
        self.lease_ttl = lease_ttl
        self.wal_key = "kernell:wal"
        self.outbox_key = "kernell:outbox"

        self.start_script = self.r.register_script(LUA_START_RECLAIM)
        self.heartbeat_script = self.r.register_script(LUA_HEARTBEAT)
        self.commit_script = self.r.register_script(LUA_COMMIT)

    def _key(self, request_id):
        return f"kernell:exec:{request_id}"

    def _save_payload_external(self, request_id: str, payload: Any) -> str:
        os.makedirs("/tmp/kernell_payloads", exist_ok=True)
        ptr = f"/tmp/kernell_payloads/{request_id}.json"
        with open(ptr, "w") as f:
            json.dump(payload, f)
        return f"file://{ptr}"

    def _load_payload_external(self, ptr: str) -> Any:
        if ptr.startswith("file://"):
            with open(ptr[7:], "r") as f:
                return json.load(f)
        return ptr

    def start_or_reclaim(self, request_id: str, owner: str):
        lease_id = str(uuid.uuid4())
        res = self.start_script(
            keys=[self._key(request_id), self.wal_key],
            args=[self.lease_ttl, owner, lease_id]
        )
        status = res[0]
        data = res[1]
        
        if status == "ERROR":
            raise Exception(f"Failed to start/reclaim: {data}")
            
        if status == "COMPLETED":
            return {"status": "COMPLETED", "result": self._load_payload_external(data)}
            
        return {
            "status": status,
            "epoch": data,
            "lease_id": lease_id
        }

    def heartbeat(self, request_id: str, lease_id: str) -> bool:
        return self.heartbeat_script(
            keys=[self._key(request_id)],
            args=[lease_id, self.lease_ttl]
        ) == 1

    def _compute_execution_fp(self, request_id: str, epoch: int, result_hash: str):
        import hashlib
        raw = f"{request_id}:{epoch}:{result_hash}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def try_commit(self, request_id: str, epoch: int, payload: Any) -> tuple:
        payload_json = json.dumps(payload)
        result_hash = str(hash(payload_json))
        result_ptr = self._save_payload_external(request_id, payload)
        execution_fp = self._compute_execution_fp(request_id, epoch, result_hash)
        
        res = self.commit_script(
            keys=[self._key(request_id), self.wal_key, self.outbox_key],
            args=[epoch, result_hash, result_ptr, execution_fp]
        )
        if res[0] == "ERROR":
            return False, res[1]
        return True, "COMMITTED"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. Snapshotting + Compaction + WAL Replay Engine
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

import requests

class AlertManager:
    def __init__(self, webhook_url=None):
        self.webhook = webhook_url

    def emit(self, type_, message, payload=None):
        logger.error(f"[ALERT] {type_}: {message}")

        if self.webhook:
            try:
                requests.post(self.webhook, json={
                    "type": type_,
                    "message": message,
                    "payload": payload
                }, timeout=2)
            except Exception:
                pass

# Global default alert manager
alert_manager = AlertManager()

class CorruptionError(Exception):
    pass

class WALReplayEngine:
    def __init__(self, redis_client=None):
        if not redis_client:
            redis_client = redis.Redis(host="localhost", port=6379, decode_responses=True)
        self.r = redis_client
        self.stream = "kernell:wal"
        self.meta_key = "kernell:snapshot:meta"
        self.state_prefix = "kernell:snapshot:state:"

    def build_snapshot(self):
        """Creates a consistent snapshot up to the current high-watermark."""
        # 1. High-watermark
        latest = self.r.xrevrange(self.stream, count=1)
        if not latest:
            return
        end_id = latest[0][0]
        
        # 2. Get last snapshot ID
        start_id = self.r.get(self.meta_key) or "0-0"
        if start_id == end_id:
            return
            
        # 3. Read up to high-watermark (Exclusive start if not 0-0)
        min_id = f"({start_id}" if start_id != "0-0" else "0-0"
        entries = self.r.xrange(self.stream, min=min_id, max=end_id)
        
        pipe = self.r.pipeline()
        for entry_id, fields in entries:
            req = fields["request_id"]
            event = fields["event"]
            epoch = int(fields.get("epoch", 0))
            
            state_key = f"{self.state_prefix}{req}"
            
            # Hostile Replay Validation
            current_state_json = self.r.get(state_key)
            current_state = json.loads(current_state_json) if current_state_json else {"epoch": 0, "state": None}
            
            if event in ("START", "RECLAIM"):
                if epoch < current_state["epoch"]:
                    err_msg = f"Epoch regression detected for {req}: {epoch} < {current_state['epoch']}"
                    alert_manager.emit("CORRUPTION", err_msg, {"event": fields})
                    raise CorruptionError(err_msg)
                current_state["epoch"] = epoch
                current_state["state"] = "IN_PROGRESS"
                pipe.set(state_key, json.dumps(current_state))
                
            elif event == "COMMIT":
                if epoch != current_state["epoch"]:
                    err_msg = f"Zombie commit detected in WAL for {req}! epoch mismatch."
                    alert_manager.emit("CORRUPTION", err_msg, {"event": fields})
                    raise CorruptionError(err_msg)
                current_state["state"] = "COMPLETED"
                pipe.set(state_key, json.dumps(current_state))

        pipe.set(self.meta_key, end_id)
        # WAL Compaction (Trim)
        pipe.xtrim(self.stream, minid=end_id)
        pipe.execute()

    def load_full_state(self):
        keys = self.r.keys(f"{self.state_prefix}*")
        state = {}

        for key in keys:
            rid = key.split(":")[-1]
            state[rid] = json.loads(self.r.get(key))

        return state

class RuntimeBootstrap:
    def __init__(self, redis_client):
        self.r = redis_client
        self.replay = WALReplayEngine(redis_client)

    def bootstrap(self):
        snapshot_state = self.replay.load_full_state()
        last_id = self.r.get("kernell:snapshot:meta") or "0-0"

        entries = self.r.xrange("kernell:wal", min=f"({last_id}")

        for eid, data in entries:
            rid = data["request_id"].split(":")[-1]
            epoch = int(data.get("epoch", 0))
            state = data.get("state_after")

            snapshot_state[rid] = {
                "epoch": epoch,
                "state": state
            }

        return snapshot_state

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. Outbox Pattern Consumer (Exactly-Once Observable Effects)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class OutboxWorker:
    def __init__(self, worker_id: str, redis_client=None):
        if not redis_client:
            redis_client = redis.Redis(host="localhost", port=6379, decode_responses=True)
        self.r = redis_client
        self.worker_id = worker_id
        self.stream = "kernell:outbox"
        self.group = "workers"
        self._ensure_group()

    def _ensure_group(self):
        try:
            self.r.xgroup_create(self.stream, self.group, id="0-0", mkstream=True)
        except redis.exceptions.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

    def process_pending(self):
        """Processes exactly-once effects using Consumer Groups and idempotent keys."""
        while True:
            events = self.r.xreadgroup(
                groupname=self.group,
                consumername=self.worker_id,
                streams={self.stream: ">"},
                count=100,
                block=5000
            )
            if not events:
                continue
                
            for stream_name, messages in events:
                for entry_id, fields in messages:
                    req = fields["request_id"]
                    
                    # Idempotency check with 24h TTL
                    idem_key = f"kernell:outbox:processed:{req}"
                    if not self.r.set(idem_key, "1", ex=86400, nx=True):
                        # Ya procesado (duplicate delivery detectado)
                        self.r.xack(self.stream, self.group, entry_id)
                        continue
                        
                    try:
                        self._deliver_webhook(req, fields["result_ptr"])
                        self.r.xack(self.stream, self.group, entry_id)
                    except Exception as e:
                        logger.error(f"Outbox delivery failed for {req}: {e}")
                        self.r.delete(idem_key) # Allow retry

    def _deliver_webhook(self, req: str, result_ptr: str):
        logger.info(f"[OUTBOX] Delivering exactly-once webhook for {req}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. WalletQueueScheduler
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class WalletQueueScheduler:
    def __init__(self, workers_per_wallet: int = 5):
        self.queues = defaultdict(deque)
        self.active = defaultdict(int)
        self.max_workers = workers_per_wallet
        self.lock = threading.Lock()

    def submit(self, wallet_id: str, task: Callable, on_complete: Callable):
        with self.lock:
            self.queues[wallet_id].append((task, on_complete))
        self._maybe_dispatch(wallet_id)

    def _maybe_dispatch(self, wallet_id: str):
        with self.lock:
            while (
                self.active[wallet_id] < self.max_workers
                and self.queues[wallet_id]
            ):
                task, on_complete = self.queues[wallet_id].popleft()
                self.active[wallet_id] += 1
                
                threading.Thread(
                    target=self._run_task,
                    args=(wallet_id, task, on_complete),
                    daemon=True
                ).start()

    def _run_task(self, wallet_id: str, task: Callable, on_complete: Callable):
        try:
            result = task()
            on_complete(result, None)
        except Exception as e:
            on_complete(None, e)
        finally:
            with self.lock:
                self.active[wallet_id] -= 1
            self._maybe_dispatch(wallet_id)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. Cancellation Propagation Protocol (CPP)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class CancelledError(Exception):
    pass

class CancellationContext:
    def __init__(self, parent: Optional['CancellationContext'] = None):
        self.parent = parent
        self._cancelled = False
        self._reason = None
        self._lock = threading.Lock()
        self._children: List['CancellationContext'] = []
        if parent:
            parent._children.append(self)

    def cancel(self, reason: str = "timeout"):
        with self._lock:
            if self._cancelled:
                return
            self._cancelled = True
            self._reason = reason
            
        for child in self._children:
            child.cancel(reason)

    def is_cancelled(self) -> bool:
        with self._lock:
            if self._cancelled:
                return True
        if self.parent:
            return self.parent.is_cancelled()
        return False
        
    def check(self):
        if self.is_cancelled():
            raise CancelledError(f"Execution cancelled: {self._reason}")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PROTOCOL EXECUTOR
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

wallet_scheduler = WalletQueueScheduler(workers_per_wallet=5)

class RuntimeProtocolExecutor:
    def __init__(self, worker_id: str = "worker-default"):
        self.worker_id = worker_id
        self.lease_ttl = 5.0
        
        if REDIS_AVAILABLE:
            try:
                r_client = redis.Redis(host="localhost", port=6379, decode_responses=True)
                r_client.ping()
                self.elp_store = DistributedELPStore(r_client, self.lease_ttl)
                self.use_redis = True
            except Exception as e:
                logger.warning(f"Redis not available. {e}")
                self.use_redis = False
        else:
            self.use_redis = False
            
        if not self.use_redis:
            raise NotImplementedError("DistributedELPStore requires a working Redis instance for atomic WAL and fencing.")

    def execute_with_resilience_queued(
        self,
        request_id: str,
        wallet_id: str,
        provider_name: str,
        context: ExecutionContext,
        action: Callable[[CancellationContext], Any],
        fallback_action: Optional[Callable[[CancellationContext], Any]] = None,
        global_timeout: float = 30.0,
        fallback_min_cost: int = 500,
        max_attempts: int = 2
    ) -> Any:
        
        result_container = {}
        event = threading.Event()
        
        def task_wrapper():
            return self._execute_internal(
                request_id, wallet_id, provider_name, context, action, fallback_action, global_timeout, fallback_min_cost, max_attempts
            )
            
        def on_complete(res, err):
            if err:
                result_container['error'] = err
            else:
                result_container['result'] = res
            event.set()
            
        wallet_scheduler.submit(wallet_id, task_wrapper, on_complete)
        
        event.wait()
        
        if 'error' in result_container:
            raise result_container['error']
        return result_container['result']


    def _execute_internal(
        self,
        request_id: str,
        wallet_id: str,
        provider_name: str,
        context: ExecutionContext,
        action: Callable[[CancellationContext], Any],
        fallback_action: Optional[Callable[[CancellationContext], Any]] = None,
        global_timeout: float = 30.0,
        fallback_min_cost: int = 500,
        max_attempts: int = 2
    ) -> Any:
        
        res = self.elp_store.start_or_reclaim(request_id, self.worker_id)
        
        if res["status"] == "COMPLETED":
            logger.info(f"[ELP] Idempotency hit for {request_id}")
            return res["result"]
            
        epoch = res["epoch"]
        lease_id = res["lease_id"]

        heartbeat_event = threading.Event()
        def heartbeater():
            while not heartbeat_event.is_set():
                valid = self.elp_store.heartbeat(request_id, lease_id)
                if not valid:
                    break
                time.sleep(self.lease_ttl / 2)
        
        hb_thread = threading.Thread(target=heartbeater, daemon=True)
        hb_thread.start()
        
        root_ctx = CancellationContext()
        
        def global_timeout_trigger():
            time.sleep(global_timeout)
            if not heartbeat_event.is_set():
                logger.warning("[CPP] Global timeout reached. Cancelling root context and stopping heartbeat.")
                heartbeat_event.set()
                root_ctx.cancel("global_timeout")
        
        timeout_thread = threading.Thread(target=global_timeout_trigger, daemon=True)
        timeout_thread.start()

        try:
            primary_ctx = CancellationContext(parent=root_ctx)
            cb = CircuitBreakerRegistry.get(provider_name)
            
            payload = None
            success = False
            
            if cb.can_execute():
                try:
                    payload = action(primary_ctx)
                    cb.record_success()
                    success = True
                except CancelledError as ce:
                    pass
                except Exception as e:
                    cb.record_failure(str(e))
            
            if not success:
                logger.info("[RUNTIME] Cancelling primary context before fallback")
                primary_ctx.cancel("fallback_triggered")
                
                if fallback_action:
                    if context.remaining_tokens < fallback_min_cost:
                        raise BudgetExceededError("Fallback aborted: insufficient funds")
                        
                    fallback_ctx = CancellationContext(parent=root_ctx)
                    payload = fallback_action(fallback_ctx)
                else:
                    raise Exception("No fallback available and primary failed.")
            
            if root_ctx.is_cancelled():
                raise CancelledError(f"Root context was cancelled before commit: {root_ctx._reason}")
            
            success, reason = self.elp_store.try_commit(request_id, epoch, payload)
            
            if not success:
                raise Exception(f"Distributed CommitGate REJECTED execution for {request_id}: {reason} (Zombie execution epoch {epoch})")
                
            return payload

        except Exception as e:
            raise e
            
        finally:
            heartbeat_event.set()
            root_ctx.cancel("execution_completed")
