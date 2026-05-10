#!/usr/bin/env python3
"""Apply shadow interceptor patch to agent_base.py — v2 (self-contained import)"""
import sys

path = "/home/anny/kernell-os/agents/core/agent_base.py"

with open(path, "r") as f:
    content = f.read()

# PATCH 1: Add timestamp capture after variable init
old1 = '        import requests\n        \n        prompt_tokens = 0\n        completion_tokens = 0\n        total_tokens = 0\n        content = ""'

new1 = '        import requests\n        \n        prompt_tokens = 0\n        completion_tokens = 0\n        total_tokens = 0\n        content = ""\n        _shadow_ts_start = time.time()  # SHADOW_INTERCEPT_HOOK'

# PATCH 2: Add receipt logging before return — self-contained import
old2 = '            # Export to Overseer log with token info\n            self._export_interaction(prompt, content, total_tokens, target_model)\n\n            return content'

new2 = '''            # Export to Overseer log with token info
            self._export_interaction(prompt, content, total_tokens, target_model)

            # --- SHADOW INTERCEPTOR HOOK (Phase 1: Passive) ---
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
                            success=True,
                            prompt_len_chars=len(prompt) if prompt else 0,
                            response_len_chars=len(content) if content else 0,
                        )
                except Exception:
                    pass  # NEVER block agent execution
            # --- END SHADOW INTERCEPTOR HOOK ---

            return content'''

if old1 not in content:
    print("ERROR: Patch 1 target not found")
    sys.exit(1)
if old2 not in content:
    print("ERROR: Patch 2 target not found")
    sys.exit(1)

content = content.replace(old1, new1, 1)
content = content.replace(old2, new2, 1)

with open(path, "w") as f:
    f.write(content)

print("OK: Both patches applied successfully (v2 — self-contained import)")
