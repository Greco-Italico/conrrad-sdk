"""
Kernell OS — Shadow Interceptor (Phase 1: Passive)
═══════════════════════════════════════════════════
Captures every LLM call passing through agent_base._call_api()
and writes a receipt to /var/log/kernell/receipts.jsonl.

Activation: KERNELL_SHADOW_INTERCEPT=1 environment variable.
Behavior: OBSERVE ONLY. Never blocks, never modifies, never raises.

This is the bridge between the simulated economic layer and
real behavioral data. Every receipt enables:
  - Waste analysis (loops, retries, overthinking)
  - Economic pressure metrics (cost/solution, cost/agent)
  - Adaptive routing training (confidence → outcome)
  - Behavioral analytics (convergence, compression)
"""
import os
import json
import time
import uuid
import hashlib
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger("kernell.shadow_interceptor")

# ─── Configuration ──────────────────────────────────────────
RECEIPTS_PATH = os.environ.get(
    "KERNELL_RECEIPTS_PATH",
    "/var/log/kernell/receipts.jsonl"
)

# Pricing per output token (USD) — conservative estimates
PRICING = {
    # Groq (Llama models)
    "llama-3.3-70b-versatile":  0.0000006,
    "llama-3.1-8b-instant":     0.0000001,
    # Gemini
    "gemini-2.0-flash":         0.0000004,
    "gemini-2.5-flash":         0.0000003,
    "gemini-2.5-pro":           0.0000025,
    # OpenAI
    "gpt-4o":                   0.0000150,
    "gpt-4o-mini":              0.0000006,
    # Mistral
    "mistral-tiny":             0.0000002,
    # Fallback
    "unknown":                  0.0000010,
}

# ─── State ──────────────────────────────────────────────────
_prev_hash = "genesis"
_seq = 0
_initialized = False
_file_handle = None


def _get_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Calculate estimated cost in USD."""
    # Input pricing is typically ~1/3 of output for most models
    output_price = PRICING.get(model, PRICING["unknown"])
    input_price = output_price * 0.25  # conservative input ratio
    return (prompt_tokens * input_price) + (completion_tokens * output_price)


def _compute_hash(data: dict) -> str:
    """Deterministic hash for receipt chain integrity."""
    canonical = json.dumps(data, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode()).hexdigest()[:32]

def _get_cognitive_tier(model: str, provider: str) -> int:
    """
    Derives the Cognitive Tier (0-5) from the model/provider.
    Tier 0: Frontier Cognition (pro, opus, gpt-4)
    Tier 1: Strong Cloud Reasoning (flash, 70b)
    Tier 2: Cheap Distributed Routing (openrouter pools, 8b)
    Tier 3: Cloud Orchestration (conrrad, fine-tunes)
    Tier 4: Local Degraded Cognition (derek-1.5b)
    Tier 5: Survival Substrate (qwen 0.5b, tiny models)
    """
    model_lower = model.lower()
    if provider == "ollama":
        if "0.5b" in model_lower or "tiny" in model_lower:
            return 5
        return 4
    if "pro" in model_lower or "opus" in model_lower or "gpt-4" in model_lower:
        return 0
    if "flash" in model_lower or "70b" in model_lower or "mixtral" in model_lower:
        return 1
    # Fallback to Tier 2 for general OpenRouter / Groq / etc.
    return 2


def _init_log():
    """Initialize the receipt log file (once)."""
    global _initialized, _seq, _prev_hash
    if _initialized:
        return

    try:
        os.makedirs(os.path.dirname(RECEIPTS_PATH), exist_ok=True)

        # Resume chain from last entry if file exists
        if os.path.exists(RECEIPTS_PATH):
            with open(RECEIPTS_PATH, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entry = json.loads(line)
                            _seq = entry.get("seq", _seq)
                            _prev_hash = entry.get("hash", _prev_hash)
                        except json.JSONDecodeError:
                            pass

        _initialized = True
        logger.info(
            f"Shadow interceptor initialized. "
            f"Resuming from seq={_seq}, path={RECEIPTS_PATH}"
        )
    except Exception as e:
        logger.error(f"Shadow interceptor init failed: {e}")
        _initialized = True  # Don't retry init on every call


def record_llm_call(
    agent_id: str,
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    latency_ms: float,
    success: bool,
    error_msg: Optional[str] = None,
    prompt_len_chars: int = 0,
    response_len_chars: int = 0,
) -> None:
    """
    Record a single LLM call as a receipt.
    
    CRITICAL CONTRACT:
    - This function MUST NEVER raise an exception
    - This function MUST NEVER block the caller
    - This function MUST NEVER modify any agent state
    """
    global _seq, _prev_hash

    try:
        _init_log()

        _seq += 1
        ts = time.time()

        cost_usd = _get_cost(model, prompt_tokens, completion_tokens)
        cog_tier = _get_cognitive_tier(model, provider)

        receipt = {
            "schema_version": 1,
            "seq": _seq,
            "ts": ts,
            "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts)),
            "prev_hash": _prev_hash,
            "agent_id": agent_id,
            "provider": provider,
            "model": model,
            "cognitive_tier": cog_tier,
            "tokens_in": prompt_tokens,
            "tokens_out": completion_tokens,
            "tokens_total": total_tokens,
            "cost_usd": f"{cost_usd:.8f}",
            "latency_ms": f"{latency_ms:.1f}",
            "success": success,
            "error": error_msg,
            "prompt_chars": prompt_len_chars,
            "response_chars": response_len_chars,
            "receipt_id": str(uuid.uuid4())[:12],
        }

        receipt_hash = _compute_hash(receipt)
        receipt["hash"] = receipt_hash
        _prev_hash = receipt_hash

        # Append-only write with fsync
        line = json.dumps(receipt, separators=(',', ':')) + "\n"
        with open(RECEIPTS_PATH, "a") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

    except Exception as e:
        # NEVER propagate — this is shadow mode
        logger.debug(f"Shadow receipt write failed (non-fatal): {e}")


# ─── Public API ─────────────────────────────────────────────

def is_active() -> bool:
    """Check if shadow interception is enabled."""
    return os.environ.get("KERNELL_SHADOW_INTERCEPT", "0") == "1"


def wrap_call_api(original_call_api):
    """
    Decorator that wraps agent_base._call_api to record receipts.
    
    Usage in agent_base.py:
        if shadow_interceptor.is_active():
            self._call_api = shadow_interceptor.wrap_call_api(self._call_api)
    """
    def wrapped(self_agent, provider, key, prompt, max_tokens, target_model="unknown"):
        start_ts = time.time()
        error_msg = None
        success = True

        try:
            result = original_call_api(self_agent, provider, key, prompt, max_tokens, target_model)
            return result
        except Exception as e:
            success = False
            error_msg = str(e)[:200]
            raise
        finally:
            try:
                elapsed_ms = (time.time() - start_ts) * 1000

                # Extract agent identity
                agent_id = getattr(self_agent, 'name', None) or \
                           getattr(self_agent, 'agent_id', None) or \
                           type(self_agent).__name__

                # Extract token counts from the agent's last call
                # (agent_base stores these as local vars, we read from return)
                pt = getattr(self_agent, '_last_prompt_tokens', 0)
                ct = getattr(self_agent, '_last_completion_tokens', 0)
                tt = getattr(self_agent, '_last_total_tokens', pt + ct)

                record_llm_call(
                    agent_id=str(agent_id),
                    provider=provider,
                    model=target_model,
                    prompt_tokens=pt,
                    completion_tokens=ct,
                    total_tokens=tt,
                    latency_ms=elapsed_ms,
                    success=success,
                    error_msg=error_msg,
                    prompt_len_chars=len(prompt) if prompt else 0,
                    response_len_chars=0,  # filled by hook below
                )
            except Exception:
                pass  # NEVER propagate

    return wrapped
