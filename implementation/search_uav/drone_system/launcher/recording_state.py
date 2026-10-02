#!/usr/bin/env python3


from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


RECORDING_SCHEMA_VERSION = 1
UAV_SOFTWARE_VERSION = "1.2.0"
SYSTEM_RELEASE_ID = "MASS26-20260806"

ALLOWED_TRANSITIONS = {
    None: {"STARTING"},
    "STARTING": {"RECORDING", "STOP_REQUESTED", "FAILED"},
    "RECORDING": {"PAUSED", "STOP_REQUESTED", "COMPLETED", "FAILED"},
    "PAUSED": {"RECORDING", "STOP_REQUESTED", "COMPLETED", "FAILED"},
    "STOP_REQUESTED": {"COMPLETED", "FAILED"},
    "COMPLETED": set(),
    "FAILED": set(),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def matches_recording_status(mission_id: str, execution_id: str, data: Dict[str, Any]) -> bool:

    if not mission_id or mission_id == "manual" or not isinstance(data, dict):
        return False
    if data.get("mission_id") != mission_id:
        return False
    if execution_id:
        return data.get("execution_id") == execution_id

    return not data.get("execution_id") and data.get("schema_version", 1) != 2


class RecordingStateError(RuntimeError):
    pass


def recording_result_error(output_file, frames_written, return_code):
    if return_code != 0:
        return "PROCESS_EXIT_{}".format(return_code)
    if type(frames_written) is not int or frames_written <= 0:
        return "NO_CONFIRMED_FRAMES"
    try:
        if not output_file or not Path(output_file).is_file() or Path(output_file).stat().st_size <= 0:
            return "OUTPUT_FILE_MISSING_OR_EMPTY"
    except OSError:
        return "OUTPUT_FILE_UNREADABLE"
    return ""


class RecordingJournal:


    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()

    @staticmethod
    def _safe_label(value: Any, fallback: str) -> str:
        if not isinstance(value, str):
            return fallback
        value = value.strip()
        if not value or len(value) > 256 or any(ord(char) < 32 for char in value):
            return fallback
        return value

    def _write_atomic(self, state: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temp_name = tempfile.mkstemp(
            prefix="." + self.path.name + ".",
            suffix=".tmp",
            dir=str(self.path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, self.path)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass

    def load(self) -> Dict[str, Any]:
        with self._lock:
            with self.path.open("r", encoding="utf-8") as handle:
                return json.load(handle)

    def start(
        self,
        session_id: str,
        mission_id: str,
        output_file: str,
        trigger: str,
        execution_id: str = "",
    ) -> Dict[str, Any]:
        with self._lock:
            if self.path.exists():
                raise RecordingStateError("recording journal already exists")
            now = utc_now()
            state = {
                "schema_version": RECORDING_SCHEMA_VERSION,
                "uav_software_version": UAV_SOFTWARE_VERSION,
                "system_release_id": SYSTEM_RELEASE_ID,
                "session_id": self._safe_label(session_id, "unknown"),
                "mission_id": self._safe_label(mission_id, "manual"),
                "execution_id": self._safe_label(execution_id, ""),
                "output_file": Path(output_file).name,
                "trigger": self._safe_label(trigger, "MANUAL"),
                "status": "STARTING",
                "started_at": now,
                "updated_at": now,
                "events": [{"status": "STARTING", "at": now}],
            }
            self._write_atomic(state)
            return state

    def transition(
        self,
        new_status: str,
        *,
        reason: str = "",
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if new_status not in ALLOWED_TRANSITIONS:
            raise RecordingStateError("unknown recording status")
        with self._lock:
            state = self.load()
            current = state.get("status")
            if new_status not in ALLOWED_TRANSITIONS.get(current, set()):
                raise RecordingStateError(
                    "invalid recording transition {} -> {}".format(current, new_status)
                )
            now = utc_now()
            event: Dict[str, Any] = {"status": new_status, "at": now}
            if reason:
                event["reason"] = self._safe_label(reason, "unspecified")
            if details:
                safe_details = {
                    key: value
                    for key, value in details.items()
                    if key in {
                        "width",
                        "height",
                        "fps",
                        "frames_written",
                        "opencv_version",
                        "error_code",
                        "incomplete_file",
                    }
                    and isinstance(value, (str, int, float, bool))
                }
                event.update(safe_details)
                state.update(safe_details)
            state["status"] = new_status
            state["updated_at"] = now
            state.setdefault("events", []).append(event)
            self._write_atomic(state)
            return state
