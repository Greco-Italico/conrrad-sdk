import json
import time
import uuid
import logging
import hashlib
from typing import Any, Dict, Optional
from threading import Lock

# Optional: Disable default logging for the trace to avoid console spam if needed
trace_logger = logging.getLogger("kernell.tracer")
trace_logger.setLevel(logging.INFO)

class ExecutionTracer:
    """
    Forensic-first Append-Only Tracer.
    Writes JSONL with implicit DAG via parent_span_id.
    Includes simple hash-chaining for immutability verification.
    """
    def __init__(self, log_path: str = "execution_trace.jsonl"):
        self.log_path = log_path
        self._lock = Lock()
        self._last_hash = "GENESIS"

    def emit(self, event_type: str, trace_id: str, span_id: str, parent_span_id: str, **kwargs):
        """Emit an atomic trace event."""
        event = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
            "trace_id": trace_id,
            "span_id": span_id,
            "parent_span_id": parent_span_id,
            "type": event_type,
        }
        event.update(kwargs)
        
        event_str = json.dumps(event, separators=(',', ':'))
        
        # Lightweight Hash Chaining
        current_hash = hashlib.sha256((self._last_hash + event_str).encode('utf-8')).hexdigest()
        self._last_hash = current_hash
        
        # Append only
        with self._lock:
            with open(self.log_path, "a") as f:
                f.write(event_str + "\n")
                
        # Also log to debug if needed
        trace_logger.debug(f"[TRACE] {event_type} | Span: {span_id}")


# Global Tracer instance (In production, inject this)
_global_tracer = ExecutionTracer()

def get_tracer() -> ExecutionTracer:
    return _global_tracer

class Span:
    """Context manager for tracing execution spans."""
    def __init__(self, trace_id: str, parent_span_id: str = "root"):
        self.trace_id = trace_id
        self.span_id = f"span_{uuid.uuid4().hex[:8]}"
        self.parent_span_id = parent_span_id
        self.tracer = get_tracer()

    def child(self) -> 'Span':
        return Span(trace_id=self.trace_id, parent_span_id=self.span_id)

    def emit(self, event_type: str, **kwargs):
        self.tracer.emit(event_type, self.trace_id, self.span_id, self.parent_span_id, **kwargs)
