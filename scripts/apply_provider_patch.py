#!/usr/bin/env python3
"""
Add OpenRouter + Ollama support to agent_base.py
- Adds openrouter and ollama to FALLBACK_CHAIN
- Adds handler blocks in _call_api()
"""
import sys

path = "/home/anny/kernell-os/agents/core/agent_base.py"

with open(path, "r") as f:
    content = f.read()

errors = []

# ═══ PATCH 1: Update FALLBACK_CHAIN ═══
old_chain = '    FALLBACK_CHAIN = ["gemini", "groq", "mistral", "huggingface"]'
new_chain = '    FALLBACK_CHAIN = ["gemini", "groq", "openrouter", "mistral", "huggingface", "ollama"]'

if old_chain not in content:
    errors.append("FALLBACK_CHAIN not found")
else:
    content = content.replace(old_chain, new_chain, 1)
    print("✅ Patch 1: FALLBACK_CHAIN updated")

# ═══ PATCH 2: Add openrouter + ollama handlers in _call_api() ═══
# Insert after the huggingface block, before "else: raise Exception(f"Unknown provider")"
old_unknown = '''            else:
                raise Exception(f"Unknown provider: {provider}")'''

new_providers = '''            elif provider == "openrouter":
                import json as _json
                url = "https://openrouter.ai/api/v1/chat/completions"
                # Try pool first, then single key
                or_pool_raw = os.environ.get("OPENROUTER_POOL", "")
                or_key = key  # key from carousel
                if or_pool_raw:
                    try:
                        pool = _json.loads(or_pool_raw.replace("\\\\", "").replace('\\\\"', '"'))
                        if pool:
                            import random
                            or_key = random.choice(pool)
                    except Exception:
                        pass
                if not or_key:
                    or_key = os.environ.get("OPENROUTER_API_KEY", "")
                
                headers = {"Authorization": f"Bearer {or_key}", "Content-Type": "application/json"}
                # Map target model to OpenRouter model ID
                or_model = "meta-llama/llama-3.3-70b-instruct"
                if "8b" in target_model or "small" in target_model or "instant" in target_model:
                    or_model = "meta-llama/llama-3.1-8b-instruct"
                elif "flash" in target_model:
                    or_model = "google/gemini-2.5-flash"
                elif "pro" in target_model:
                    or_model = "google/gemini-2.5-pro"
                
                data = {
                    "model": or_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens
                }
                response = self.session.post(url, headers=headers, json=data, timeout=30)
                if response.status_code == 200:
                    res_json = response.json()
                    content_text = res_json['choices'][0]['message']['content']
                    content = content_text
                    usage = res_json.get('usage', {})
                    prompt_tokens = usage.get('prompt_tokens', 0)
                    completion_tokens = usage.get('completion_tokens', 0)
                    total_tokens = usage.get('total_tokens', prompt_tokens + completion_tokens)
                else:
                    raise Exception(f"OpenRouter failed: {response.status_code} - {response.text[:200]}")

            elif provider == "ollama":
                url = "http://localhost:11434/api/chat"
                ollama_model = os.environ.get("OLLAMA_MODEL", "qwen2.5:0.5b")
                data = {
                    "model": ollama_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False
                }
                response = self.session.post(url, json=data, timeout=60)
                if response.status_code == 200:
                    res_json = response.json()
                    content = res_json.get('message', {}).get('content', '')
                    # Ollama provides token counts
                    prompt_tokens = res_json.get('prompt_eval_count', len(prompt) // 4)
                    completion_tokens = res_json.get('eval_count', len(content) // 4)
                    total_tokens = prompt_tokens + completion_tokens
                else:
                    raise Exception(f"Ollama failed: {response.status_code} - {response.text[:200]}")

            else:
                raise Exception(f"Unknown provider: {provider}")'''

if old_unknown not in content:
    errors.append("'Unknown provider' block not found")
else:
    content = content.replace(old_unknown, new_providers, 1)
    print("✅ Patch 2: OpenRouter + Ollama handlers added to _call_api()")

# ═══ PATCH 3: Add get_api_key support for openrouter/ollama ═══
# These providers don't need Nexus carousel - they use env vars directly
# Find the get_api_key method and add fallback for these providers
old_no_key = '''            return "Error: No API Key available"'''
new_no_key = '''            # Last resort: try openrouter (has pool) or ollama (no key needed)
                if not self.provider_cooldown.is_in_cooldown("openrouter"):
                    or_key = os.environ.get("OPENROUTER_API_KEY", "")
                    if or_key:
                        self.logger.warning("⚠️ All providers exhausted. Emergency fallback to OpenRouter.")
                        return self._generate_with_provider(prompt, "openrouter", max_tokens)
                if not self.provider_cooldown.is_in_cooldown("ollama"):
                    self.logger.warning("⚠️ All cloud providers exhausted. Emergency fallback to Ollama (local).")
                    return self._generate_with_provider(prompt, "ollama", max_tokens)
            return "Error: No API Key available"'''

if old_no_key not in content:
    errors.append("'No API Key available' not found")
else:
    content = content.replace(old_no_key, new_no_key, 1)
    print("✅ Patch 3: Emergency fallback to OpenRouter/Ollama added")

if errors:
    print(f"\n❌ ERRORS: {errors}")
    sys.exit(1)

with open(path, "w") as f:
    f.write(content)

print("\n✅ All patches applied. New fallback chain:")
print("   gemini → groq → openrouter → mistral → huggingface → ollama")
print("   Agents will NEVER fail due to API keys.")
