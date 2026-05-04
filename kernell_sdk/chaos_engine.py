"""
Kernell OS — Chaos Engine
══════════════════════════
Simulates real-world provider failures (network drops, stream corruption, timeouts)
to validate that the system degrades gracefully under high entropy.
"""
import time
import random
import logging
from typing import Generator

logger = logging.getLogger("kernell.chaos")

class ProviderTimeoutError(Exception):
    pass

class ConnectionDroppedError(Exception):
    pass

class Provider500Error(Exception):
    pass

class ChaosEngine:
    """Injects failure into the model execution stream."""

    def __init__(self, enable_chaos: bool = True):
        self.enable_chaos = enable_chaos
        
        # Chaos probabilities
        self.p_timeout = 0.05       # total hang (tests TimeoutKiller)
        self.p_latency_spike = 0.10 # slow start
        self.p_connection_drop = 0.05 # stream cuts off early
        self.p_500_error = 0.05     # total failure immediately
        self.p_slow_drip = 0.05     # 1 token every X ms (slow attack)

    def inject_chaos(self, stream: Generator, max_tokens: int) -> Generator:
        """Wraps a token generator with real-world failure patterns."""
        if not self.enable_chaos:
            yield from stream
            return

        r = random.random()

        if r < self.p_500_error:
            logger.warning("[CHAOS] Injecting 500 Internal Server Error.")
            raise Provider500Error("Provider returned 500 Internal Server Error.")

        if r < self.p_timeout + self.p_500_error:
            logger.warning("[CHAOS] Injecting complete network timeout (hanging).")
            time.sleep(10) # Simulating a 10s hang
            raise ProviderTimeoutError("Network timeout before first byte.")

        if r < self.p_latency_spike + self.p_timeout + self.p_500_error:
            delay = random.uniform(2.0, 5.0)
            logger.warning(f"[CHAOS] Injecting TTFB latency spike ({delay:.2f}s).")
            time.sleep(delay)

        is_slow_drip = random.random() < self.p_slow_drip
        is_connection_drop = random.random() < self.p_connection_drop
        drop_at_token = random.randint(1, max(2, max_tokens // 2)) if is_connection_drop else -1

        token_count = 0
        for chunk in stream:
            token_count += 1

            if is_connection_drop and token_count >= drop_at_token:
                logger.warning(f"[CHAOS] Injecting mid-stream connection drop at token {token_count}.")
                raise ConnectionDroppedError("Connection reset by peer.")

            if is_slow_drip:
                # Sleep a small amount to accumulate to a timeout
                time.sleep(0.2)

            yield chunk
