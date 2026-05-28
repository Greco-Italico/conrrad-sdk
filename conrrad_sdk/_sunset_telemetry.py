"""
CONRRAD Sunset Telemetry — L2 Migration Evidence
=================================================
Records legacy import events for evidence-based deprecation decisions.
Events are appended to a local JSONL file so the team can measure
when legacy usage reaches ~0 and it's safe to remove wrappers.

Policy: semantic purity is subordinate to causal continuity.
"""

import json
import os
import time
import traceback
from pathlib import Path

_SUNSET_DIR = os.environ.get(
    "CONRRAD_SUNSET_TELEMETRY_DIR",
    os.path.join(
        os.path.expanduser("~"),
        ".conrrad-evidence",
        "sunset",
    ),
)

_ENABLED = os.environ.get("CONRRAD_SUNSET_TELEMETRY", "1") == "1"


def _emit(asset: str, event: str, sunset_target: str) -> None:
    """Append a single sunset telemetry event to the JSONL log."""
    if not _ENABLED:
        return
    try:
        dirpath = Path(_SUNSET_DIR)
        dirpath.mkdir(parents=True, exist_ok=True)
        logfile = dirpath / "legacy_imports.jsonl"

        # Capture the call site (skip this function + the wrapper __init__)
        caller_frames = traceback.extract_stack(limit=4)
        caller = None
        for frame in reversed(caller_frames):
            if "conrrad_sdk" not in frame.filename and "__init__" not in frame.filename:
                caller = f"{frame.filename}:{frame.lineno}"
                break

        record = {
            "asset": asset,
            "event": event,
            "sunset_target": sunset_target,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "caller": caller,
            "pid": os.getpid(),
        }
        with open(logfile, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, separators=(",", ":")) + "\n")
    except Exception:
        # Telemetry must never break the runtime
        pass


def record_legacy_import(package_name: str) -> None:
    """Record that a legacy package was imported directly."""
    _emit(
        asset=package_name,
        event="legacy_import",
        sunset_target="conrrad_sdk",
    )
