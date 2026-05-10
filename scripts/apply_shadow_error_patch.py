#!/usr/bin/env python3
"""Add error capture to shadow interceptor hook in agent_base.py"""
import sys

path = "/home/anny/kernell-os/agents/core/agent_base.py"

with open(path, "r") as f:
    content = f.read()

# PATCH: Add error receipt in except block
old = '''        except Exception as e:
            # Even on error, if tokens were partially used or to track failure frequency
            if self.state:
                self.state.incrby(f"kernell:agent:{self.agent_name}:usage_count", 1)
            # Re-raise to trigger fallback logic in generate()
            raise e'''

new = '''        except Exception as e:
            # Even on error, if tokens were partially used or to track failure frequency
            if self.state:
                self.state.incrby(f"kernell:agent:{self.agent_name}:usage_count", 1)

            # --- SHADOW INTERCEPTOR: ERROR CAPTURE ---
            if os.environ.get("KERNELL_SHADOW_INTERCEPT") == "1":
                try:
                    import importlib.util
                    _si_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
                        os.path.abspath(__file__)))),
                        "kernell-sdk", "kernell_sdk", "runtime", "shadow_interceptor.py")
                    if os.path.exists(_si_path):
                        _si_spec = importlib.util.spec_from_file_location("shadow_interceptor", _si_path)
                        _si_mod = importlib.util.module_from_spec(_si_spec)
                        _si_spec.loader.exec_module(_si_mod)
                        _si_mod.record_llm_call(
                            agent_id=getattr(self, "agent_name", type(self).__name__),
                            provider=provider,
                            model=target_model,
                            prompt_tokens=prompt_tokens,
                            completion_tokens=completion_tokens,
                            total_tokens=total_tokens,
                            latency_ms=(time.time() - _shadow_ts_start) * 1000,
                            success=False,
                            error_msg=str(e)[:200],
                            prompt_len_chars=len(prompt) if prompt else 0,
                            response_len_chars=0,
                        )
                except Exception:
                    pass
            # --- END ERROR CAPTURE ---

            # Re-raise to trigger fallback logic in generate()
            raise e'''

if old not in content:
    print("ERROR: Target not found")
    sys.exit(1)

content = content.replace(old, new, 1)

with open(path, "w") as f:
    f.write(content)

print("OK: Error capture patch applied")
