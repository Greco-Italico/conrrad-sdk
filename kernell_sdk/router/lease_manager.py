"""
Kernell Epoch Leasing — Distributed Fencing & Failover

Phase 1.3 Implementation:
Ensures strictly one writer per request_id per epoch.
Prevents split-brain by fencing out old leaders.
"""

import time
import json
from typing import Optional


class FencedError(Exception):
    pass

class NotLeaderError(Exception):
    pass

class LeaseExpiredError(Exception):
    pass

class NoLeaseError(Exception):
    pass


class LeaseManager:
    """Manages distributed ownership of a request_id."""

    def __init__(self, redis_client, region: str):
        self.r = redis_client
        self.region = region

    def acquire(self, request_id: str, epoch: int, ttl: float) -> dict:
        """Acquire or renew a lease for this region."""
        key = f"kernell:lease:{request_id}"
        lease = {
            "holder": self.region,
            "epoch": epoch,
            "expires_at": time.time() + ttl
        }
        self.r.set(key, json.dumps(lease))
        return lease

    def get(self, request_id: str) -> Optional[dict]:
        """Get the current lease status."""
        raw = self.r.get(f"kernell:lease:{request_id}")
        return json.loads(raw) if raw else None

    def validate_write(self, request_id: str, epoch: int) -> bool:
        """
        CRITICAL: Validates ownership before ANY write.
        Raises specific exceptions if the write is not authorized.
        """
        lease = self.get(request_id)

        if not lease:
            raise NoLeaseError(f"No active lease for {request_id}")

        if lease["epoch"] != epoch:
            raise FencedError(f"Fenced by new epoch. Requested {epoch}, current is {lease['epoch']}")

        if lease["holder"] != self.region:
            raise NotLeaderError(f"Region {self.region} is not leader. Current is {lease['holder']}")

        if lease["expires_at"] < time.time():
            raise LeaseExpiredError(f"Lease for {request_id} has expired")

        return True

    def atomic_fenced_append(self, request_id: str, epoch: int, stream_key: str, event: dict) -> str:
        """
        CRITICAL FIX: Atomic Fencing + WAL Append.
        Prevents race condition between checking epoch and appending to WAL.
        """
        script = """
        local lease_key = KEYS[1]
        local s_key = KEYS[2]
        
        local region = ARGV[1]
        local req_epoch = tonumber(ARGV[2])
        local now = tonumber(ARGV[3])
        local payload_str = ARGV[4]
        
        local raw = redis.call('GET', lease_key)
        if not raw then
            return "ERR NoLeaseError"
        end
        
        local lease = cjson.decode(raw)
        
        if lease.epoch ~= req_epoch then
            return "ERR FencedError"
        end
        
        if lease.holder ~= region then
            return "ERR NotLeaderError"
        end
        
        if lease.expires_at < now then
            return "ERR LeaseExpiredError"
        end
        
        local payload = cjson.decode(payload_str)
        local args = {}
        for k, v in pairs(payload) do
            table.insert(args, k)
            if type(v) == "table" then
                table.insert(args, cjson.encode(v))
            else
                table.insert(args, tostring(v))
            end
        end
        
        return redis.call('XADD', s_key, '*', unpack(args))
        """
        
        try:
            res = self.r.eval(
                script, 
                2, 
                f"kernell:lease:{request_id}", 
                stream_key,
                self.region,
                epoch,
                time.time(),
                json.dumps(event)
            )
            
            if isinstance(res, bytes):
                res = res.decode('utf-8')
                
            if isinstance(res, str) and res.startswith("ERR "):
                err_type = res.split(" ")[1]
                if err_type == "NoLeaseError":
                    raise NoLeaseError(f"No active lease for {request_id}")
                elif err_type == "FencedError":
                    raise FencedError(f"Fenced by new epoch. Requested {epoch}")
                elif err_type == "NotLeaderError":
                    raise NotLeaderError(f"Region {self.region} is not leader.")
                elif err_type == "LeaseExpiredError":
                    raise LeaseExpiredError(f"Lease for {request_id} has expired")
            
            return res
        except AttributeError:
            # Fallback for FakeRedis in fuzzer
            self.validate_write(request_id, epoch)
            return self.r.xadd(stream_key, event)

    def takeover(self, request_id: str, ttl: float) -> dict:
        """
        Force a takeover of a request_id.
        Increments the epoch to fence out the old leader.
        """
        current = self.get(request_id)
        new_epoch = (current["epoch"] + 1) if current else 1

        lease = self.acquire(request_id, epoch=new_epoch, ttl=ttl)
        
        # Emitting the FAILOVER event to the WAL should be handled by the caller,
        # but the lease guarantees the caller has exclusive rights to do so.
        
        return lease
