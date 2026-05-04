import logging
import math
import time
from typing import Any, AsyncGenerator, Dict, Generator, Optional
from dataclasses import dataclass, field
from kernell_sdk.tracer import Span

logger = logging.getLogger("kernell.budget_killer")

class BudgetExceededError(Exception):
    """Raised when the maximum token cap is breached during execution."""
    pass

class KillSignal(Exception):
    """Internal signal used to sever the connection instantly mid-stream."""
    pass

class ExecutionTimeout(Exception):
    """Raised when wall-clock execution time exceeds the hard limit."""
    pass

class MaxDepthExceeded(Exception):
    """Raised when agent recursion depth exceeds the hard limit."""
    pass

@dataclass
class ExecutionContext:
    """Global execution context for multi-call and router limits."""
    total_budget_tokens: int
    max_depth: int
    remaining_tokens: int = field(init=False)
    depth: int = 0
    kill_events: int = 0
    fallback_activated: bool = False

    def __post_init__(self):
        self.remaining_tokens = self.total_budget_tokens

    def consume(self, tokens: int):
        if self.remaining_tokens < tokens:
            raise BudgetExceededError(f"Cannot consume {tokens}. Only {self.remaining_tokens} remaining.")
        self.remaining_tokens -= tokens

    def refund(self, tokens: int):
        """Refund unspent reserved tokens."""
        self.remaining_tokens += tokens
        if self.remaining_tokens > self.total_budget_tokens:
            self.remaining_tokens = self.total_budget_tokens


class BudgetKiller:
    """
    The Guillotine.
    Lives exactly at the socket boundary. Disconnects physically when limits are reached.
    Two kill axes: token count AND wall-clock time.
    """
    def __init__(self, max_tokens: int, span: Span, max_execution_time: float = 3.0):
        self.max_tokens = max_tokens
        self.max_execution_time = max_execution_time
        self.used_tokens = 0
        self._killed = False
        self._kill_reason = None
        self._start_time = time.monotonic()
        self.span = span

    @property
    def elapsed_ms(self) -> float:
        return (time.monotonic() - self._start_time) * 1000

    def on_token(self, tokens_in_chunk: int = 1):
        if self._killed:
            raise KillSignal(f"Stream already killed: {self._kill_reason}")

        # Check wall-clock timeout FIRST (catches slow-drip attacks)
        elapsed = time.monotonic() - self._start_time
        if elapsed >= self.max_execution_time:
            self._killed = True
            self._kill_reason = "EXECUTION_TIMEOUT"
            logger.critical(
                f"[KILL] TIMEOUT: {elapsed:.2f}s >= {self.max_execution_time}s. Severing socket."
            )
            self.span.emit("KILL_EVENT", reason="EXECUTION_TIMEOUT", severity="CRITICAL",
                           tokens_used=self.used_tokens, elapsed_s=round(elapsed, 3), socket_closed=True)
            raise KillSignal()

        self.used_tokens += tokens_in_chunk

        if self.used_tokens >= self.max_tokens:
            self._killed = True
            self._kill_reason = "TOKEN_LIMIT_BREACH"
            logger.critical(
                f"[KILL] TRIGGERED: Breached max token limit ({self.max_tokens}). Severing socket."
            )
            self.span.emit("KILL_EVENT", reason="TOKEN_LIMIT_BREACH", severity="CRITICAL",
                           tokens_used=self.used_tokens, tokens_limit=self.max_tokens, socket_closed=True)
            raise KillSignal()


class ExecutionStreamWrapper:
    """
    Wraps any model generation stream to force chunk-by-chunk counting.
    If the killer triggers, the generator is destroyed and the socket is conceptually severed.
    """
    def __init__(self, raw_stream: Generator, killer: BudgetKiller):
        self.raw_stream = raw_stream
        self.killer = killer

    def __iter__(self):
        try:
            for chunk in self.raw_stream:
                token_len = 1 if isinstance(chunk, str) else getattr(chunk, 'token_count', 1)
                self.killer.on_token(token_len)
                yield chunk
        except KillSignal:
            self._sever_connection()
            # Let caller handle it

    def _sever_connection(self):
        """Physically drops the connection to the upstream API."""
        if hasattr(self.raw_stream, "close"):
            self.raw_stream.close()
        logger.info("[SOCKET] Connection forcefully severed by BudgetKiller.")


# --- ROUTER INTEGRATION & WORKER PIPELINE ---

def execute_with_context(
    req: Dict[str, Any], 
    context: ExecutionContext, 
    parent_span: Span,
    SYSTEM_MAX_TOKENS_PER_CALL: int = 1000
) -> Dict[str, Any]:
    """
    Router integrado (Budget-aware).
    """
    if context.depth >= context.max_depth:
        logger.critical(f"DEPTH KILLER TRIGGERED: Max recursion depth ({context.max_depth}) reached.")
        parent_span.emit("MAX_DEPTH_EXCEEDED", depth=context.depth, limit=context.max_depth)
        raise MaxDepthExceeded()

    context.depth += 1
    router_span = parent_span.child()
    router_span.emit("ROUTER_DECISION_START")

    # Route subordinado al presupuesto (Router Amplification Risk mitigation)
    MIN_SAFE_THRESHOLD = 200
    FALLBACK_COST = 500

    if context.remaining_tokens <= MIN_SAFE_THRESHOLD:
        model_name = "cheap_model"
    else:
        model_name = "primary_model"

    router_span.emit("ROUTER_DECISION", model=model_name)

    max_tokens = min(context.remaining_tokens, SYSTEM_MAX_TOKENS_PER_CALL)
    
    # RESERVA GLOBAL DE ESTA LLAMADA
    remaining_before = context.remaining_tokens
    context.consume(max_tokens)
    router_span.emit("BUDGET_RESERVED", tokens=max_tokens, remaining_before=remaining_before, remaining_after=context.remaining_tokens)
    logger.info(f"[BUDGET] Reserved: {max_tokens} tokens for model {model_name}")

    try:
        return run_in_worker(req, model_name, max_tokens, context, router_span)
    except Exception as e:
        logger.warning(f"Primary model failed: {e}. Trying fallback...")
        context.fallback_activated = True
        router_span.emit("FALLBACK_TRIGGERED", error=str(e))
        
        # Router amplification safeguard
        if context.remaining_tokens > FALLBACK_COST:
            fallback_max = min(context.remaining_tokens, FALLBACK_COST)
            r_before = context.remaining_tokens
            context.consume(fallback_max)
            router_span.emit("BUDGET_RESERVED", tokens=fallback_max, remaining_before=r_before, remaining_after=context.remaining_tokens, fallback=True)
            return run_in_worker(req, "fallback_model", fallback_max, context, router_span)
        else:
            router_span.emit("BUDGET_EXCEEDED", msg="Not enough budget for fallback.")
            raise BudgetExceededError("Not enough budget for fallback.")


def run_in_worker(req: Dict[str, Any], model_name: str, max_tokens: int, context: ExecutionContext, parent_span: Span) -> Dict[str, Any]:
    worker_span = parent_span.child()
    worker_span.emit("WORKER_ASSIGNED", model=model_name)
    worker_span.emit("MODEL_CALL_START", limit=max_tokens)
    
    killer = BudgetKiller(max_tokens, worker_span)

    # In real execution, we enforce 'max_output_tokens' parameter to mitigate "silent partial streaming cost drift"
    # Example: model.stream(req, max_output_tokens=max_tokens)
    
    # Mocking stream generation
    def mock_stream():
        for i in range(max_tokens + 10): # Tries to exceed
            yield "token "
            
    stream = mock_stream()
    wrapped = ExecutionStreamWrapper(stream, killer)

    output = []
    try:
        logger.info(f"[ROUTER] selected: {model_name}")
        for chunk in wrapped:
            output.append(chunk)
    except KillSignal:
        context.kill_events += 1

    used_tokens = killer.used_tokens
    worker_span.emit("MODEL_STREAM", tokens_used=used_tokens)

    # Ajuste fino (Reconciliación post-call)
    refund_amount = max_tokens - used_tokens
    if refund_amount > 0:
        r_before = context.remaining_tokens
        context.refund(refund_amount)
        worker_span.emit("BUDGET_REFUND", tokens=refund_amount, remaining_before=r_before, remaining_after=context.remaining_tokens)

    logger.info(f"[STREAM] execution ended. Tokens used: {used_tokens}/{max_tokens}")
    worker_span.emit("MODEL_CALL_END", status="killed" if context.kill_events > 0 else "success")

    return {
        "response": "".join(output),
        "tokens_used": used_tokens,
        "model": model_name
    }
