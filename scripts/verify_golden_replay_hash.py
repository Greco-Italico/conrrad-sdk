#!/usr/bin/env python3
"""Verify golden documentation fixtures match replay_hash semantics."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "docs" / "golden" / "runtime_event_v1"


def stable_stringify(obj: dict) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def compute_replay_intent_hash(intent: dict) -> str:
    normalized = {
        "operation_type": str(intent.get("operation_type", "")).upper(),
        "normalized_target": str(intent.get("normalized_target", "")).lower(),
        "normalized_intent": str(intent.get("normalized_intent", "")).lower(),
        "execution_semantics": str(intent.get("execution_semantics", "deterministic")).lower(),
    }
    return hashlib.sha256(stable_stringify(normalized).encode()).hexdigest()[:32]


def intent_from_event(event: dict) -> dict:
    """Match extractIntentFromLegacy() in runtimeEventV1.js."""
    payload = event.get("payload") or {}
    entity = event.get("entity") or {}
    if payload.get("llm_invoked") or event.get("llm_invoked") or event.get("model"):
        sem = "llm"
    elif payload.get("tool"):
        sem = "tool"
    else:
        sem = "deterministic"
    target = (
        entity.get("id")
        or payload.get("path")
        or payload.get("command")
        or payload.get("target")
        or "none"
    )
    intent = (
        payload.get("intent")
        or payload.get("opType")
        or payload.get("message")
        or event.get("type")
        or "none"
    )
    return {
        "operation_type": event.get("type") or "UNKNOWN",
        "normalized_target": target,
        "normalized_intent": intent,
        "execution_semantics": sem,
    }


def check_file(path: Path) -> list[str]:
    errors: list[str] = []
    data = json.loads(path.read_text(encoding="utf-8"))
    expected = data.get("_replay_hash_expected")
    if not expected:
        return errors
    computed = compute_replay_intent_hash(intent_from_event(data))
    if computed != expected:
        errors.append(f"{path.name}: hash {computed} != expected {expected}")
    if data.get("replay_hash") != expected:
        errors.append(f"{path.name}: envelope replay_hash mismatch")
    return errors


def main() -> int:
    if not GOLDEN.is_dir():
        print(f"Missing golden dir: {GOLDEN}", file=sys.stderr)
        return 1
    all_errors: list[str] = []
    for name in ("accepted_tool.json", "accepted_llm_chat.json"):
        p = GOLDEN / name
        if p.exists():
            all_errors.extend(check_file(p))
    if all_errors:
        for e in all_errors:
            print("FAIL:", e, file=sys.stderr)
        return 1
    print("Golden replay_hash verification OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
