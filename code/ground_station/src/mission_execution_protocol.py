import hashlib
import json
import math
import re

TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
TYPE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,63}$")
MAX_SOURCE_WAYPOINTS = 100000


def number(value):
    if isinstance(value, bool):
        raise ValueError("boolean is not a mission number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("mission numbers must be finite")
    return 0.0 if result == 0 else result


def normalized_task(payload):
    if not isinstance(payload, dict):
        raise ValueError("task must be an object")
    if type(payload.get("schema_version")) is not int or payload["schema_version"] != 2:
        raise ValueError("task schema_version must be 2")
    execution = payload.get("execution_id")
    if not isinstance(execution, str) or not TOKEN.fullmatch(execution):
        raise ValueError("invalid execution_id")
    mission = payload.get("mission_id")
    if (not isinstance(mission, str) or len(mission.split("/")) not in (1, 2)
            or not all(TOKEN.fullmatch(part) for part in mission.split("/"))):
        raise ValueError("invalid mission_id")
    mission_type = payload.get("mission_type")
    if not isinstance(mission_type, str) or not TYPE.fullmatch(mission_type):
        raise ValueError("invalid mission_type")
    raw = payload.get("waypoints")
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_SOURCE_WAYPOINTS:
        raise ValueError("task requires 1 to 100000 source waypoints (RTL is additional)")
    points = []
    for point in raw:
        if isinstance(point, dict):
            lat = point["latitude"] if "latitude" in point else point.get("lat")
            lon = point["longitude"] if "longitude" in point else point.get("lon")
        else:
            raise ValueError("invalid waypoint")
        lat, lon = number(lat), number(lon)
        if not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise ValueError("waypoint outside coordinate range")
        points.append([lat, lon])
    rtl = payload.get("return_to_launch", True)
    if not isinstance(rtl, bool):
        raise ValueError("return_to_launch must be boolean")
    altitude = None if payload.get("altitude") is None else number(payload["altitude"])
    hover = None if payload.get("hover_seconds") is None else number(payload["hover_seconds"])
    if altitude is not None and not 0.5 <= altitude <= 120.0:
        raise ValueError("altitude must be within 0.5..120 metres")
    if hover is not None and not 0 <= hover <= 600.0:
        raise ValueError("hover_seconds must be within 0..600")
    return {"mission_id": mission, "mission_type": mission_type, "waypoints": points,
            "return_to_launch": rtl,
            "altitude": altitude, "hover_seconds": hover}


def task_fingerprint(payload):
    if len(json.dumps(payload, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")) > 16 * 1024 * 1024:
        raise ValueError("task exceeds 16 MiB")
    canonical = json.dumps(normalized_task(payload), sort_keys=True,
                           separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
