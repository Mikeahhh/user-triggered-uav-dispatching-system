#!/usr/bin/env python3


from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple


UAV_SOFTWARE_VERSION = "1.2.0"
SYSTEM_RELEASE_ID = "MASS26-20260806"
MAX_WAYPOINTS = 100000
ID_TOKEN = r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}"
MISSION_ID_RE = re.compile(r"^{}(?:/{})?$".format(ID_TOKEN, ID_TOKEN))
MISSION_TYPE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,63}$")

PHASE_LAND_REQUESTED = "LAND_REQUESTED"
LAND_REQUEST_SEMANTICS = (
    "Mission queue complete and a LAND command request was published; "
    "physical touchdown is not confirmed."
)


class MissionValidationError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise MissionValidationError("{} must be numeric".format(field))
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise MissionValidationError("{} must be numeric".format(field)) from exc
    if not math.isfinite(result):
        raise MissionValidationError("{} must be finite".format(field))
    return result


def _optional_number(
    value: Any,
    field: str,
    minimum: float,
    maximum: float,
) -> Optional[float]:
    if value is None:
        return None
    result = _finite_number(value, field)
    if not minimum <= result <= maximum:
        raise MissionValidationError(
            "{} must be between {} and {}".format(field, minimum, maximum)
        )
    return result


def _strict_bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise MissionValidationError("{} must be a JSON boolean".format(field))
    return value


def normalize_mission_id(value: Any, fallback: str) -> str:
    if value is None:
        value = fallback
    if not isinstance(value, str) or not MISSION_ID_RE.fullmatch(value):
        raise MissionValidationError("invalid mission_id")
    return value


def normalize_mission_type(value: Any, fallback: str) -> str:
    if value is None:
        value = fallback
    if not isinstance(value, str) or not MISSION_TYPE_RE.fullmatch(value):
        raise MissionValidationError("invalid mission_type")
    return value


def _coordinate(raw: Dict[str, Any], long_name: str, short_name: str) -> Any:

    if long_name in raw:
        return raw[long_name]
    return raw.get(short_name)


def normalize_waypoints(raw_waypoints: Any) -> List[Tuple[float, float]]:
    if not isinstance(raw_waypoints, list) or not raw_waypoints:
        raise MissionValidationError("missing waypoints")
    if len(raw_waypoints) > MAX_WAYPOINTS:
        raise MissionValidationError("too many waypoints")

    normalized: List[Tuple[float, float]] = []
    for index, raw in enumerate(raw_waypoints):
        if not isinstance(raw, dict):
            raise MissionValidationError("waypoint {} must be an object".format(index + 1))
        latitude = _finite_number(
            _coordinate(raw, "latitude", "lat"),
            "waypoint {} latitude".format(index + 1),
        )
        longitude = _finite_number(
            _coordinate(raw, "longitude", "lon"),
            "waypoint {} longitude".format(index + 1),
        )
        if not -90.0 <= latitude <= 90.0:
            raise MissionValidationError("waypoint {} latitude out of range".format(index + 1))
        if not -180.0 <= longitude <= 180.0:
            raise MissionValidationError("waypoint {} longitude out of range".format(index + 1))
        normalized.append((latitude, longitude))
    return normalized


def normalize_multi_payload(data: Any, default_rtl: bool = True) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise MissionValidationError("mission payload must be an object")
    rtl_raw = data.get("return_to_launch", default_rtl)
    return {
        "mission_id": normalize_mission_id(data.get("mission_id"), "multi"),
        "mission_type": normalize_mission_type(data.get("mission_type"), "event"),
        "waypoints": normalize_waypoints(data.get("waypoints")),
        "return_to_launch": _strict_bool(rtl_raw, "return_to_launch"),
        "altitude": _optional_number(data.get("altitude"), "altitude", 0.5, 120.0),
        "hover_seconds": _optional_number(
            data.get("hover_seconds"), "hover_seconds", 0.0, 600.0
        ),
    }


def normalize_legacy_payload(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise MissionValidationError("mission payload must be an object")
    latitude = _finite_number(data.get("latitude"), "latitude")
    longitude = _finite_number(data.get("longitude"), "longitude")
    if not -90.0 <= latitude <= 90.0:
        raise MissionValidationError("latitude out of range")
    if not -180.0 <= longitude <= 180.0:
        raise MissionValidationError("longitude out of range")
    return {
        "mission_id": normalize_mission_id(data.get("mission_id"), "legacy"),
        "mission_type": normalize_mission_type(data.get("mission_type"), "rescue"),
        "waypoints": [(latitude, longitude)],
        "return_to_launch": False,
        "altitude": _optional_number(data.get("altitude"), "altitude", 0.5, 120.0),
        "hover_seconds": _optional_number(
            data.get("hover_seconds"), "hover_seconds", 0.0, 600.0
        ),
    }


def append_rtl_waypoint(
    waypoints: Sequence[Tuple[float, float]],
    return_to_launch: bool,
    launch_fix: Optional[Tuple[float, float]],
) -> Tuple[List[Tuple[float, float]], bool]:
    queue = list(waypoints)
    if not return_to_launch:
        return queue, False
    if launch_fix is None:
        return queue, False
    queue.append((float(launch_fix[0]), float(launch_fix[1])))
    return queue, True


def build_status_payload(
    status: str,
    message: str = "",
    mission_id: str = "",
    waypoint_index: int = 0,
    waypoint_total: int = 0,
    queue_remaining: int = 0,
    phase: str = "",
    land_command_requested: bool = False,
) -> Dict[str, Any]:


    payload = {
        "status": status,
        "message": message,
        "mission_id": mission_id,
        "waypoint_index": int(waypoint_index),
        "waypoint_total": int(waypoint_total),
        "queue_remaining": max(0, int(queue_remaining)),
        "phase": phase or status,
        "land_command_requested": bool(land_command_requested),
        "touchdown_confirmed": False,
        "uav_software_version": UAV_SOFTWARE_VERSION,
        "system_release_id": SYSTEM_RELEASE_ID,
        "published_at": utc_now(),
    }
    if phase == PHASE_LAND_REQUESTED:
        payload["semantics"] = LAND_REQUEST_SEMANTICS
    return payload
