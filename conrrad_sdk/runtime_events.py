"""
P1.3D — Emit SDK_* events to Observatory (runtime_event_v1 path).
Best-effort HTTP; never blocks SDK import.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

_OBSERVATORY = os.environ.get("CONRRAD_OBSERVATORY_URL", "http://127.0.0.1:23817").rstrip("/")


def emit_sdk_event(
    event_type: str,
    *,
    client: str = "conrrad-sdk",
    payload: Optional[Dict[str, Any]] = None,
) -> bool:
    """POST /api/sdk/event — returns True if accepted."""
    body = json.dumps(
        {
            "type": event_type,
            "client": client,
            "payload": payload or {},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{_OBSERVATORY}/api/sdk/event",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=2) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def on_client_initialized(client: str = "conrrad-sdk") -> bool:
    return emit_sdk_event("SDK_CLIENT_INITIALIZED", client=client)


def on_tool_executed(tool: str, **extra: Any) -> bool:
    return emit_sdk_event(
        "SDK_TOOL_EXECUTED",
        payload={"tool": tool, **extra},
    )
