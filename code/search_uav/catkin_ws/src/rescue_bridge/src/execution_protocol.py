import hashlib
import json
import re

from mission_protocol import MissionValidationError, normalize_multi_payload, normalize_legacy_payload

EXECUTION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def positive_zero(value):
    number = float(value)
    return 0.0 if number == 0 else number


def normalized_content(mission):
    return {
        "mission_id": mission["mission_id"],
        "mission_type": mission["mission_type"],
        "waypoints": [[positive_zero(lat), positive_zero(lon)] for lat, lon in mission["waypoints"]],
        "return_to_launch": mission["return_to_launch"],
        "altitude": None if mission["altitude"] is None else positive_zero(mission["altitude"]),
        "hover_seconds": None if mission["hover_seconds"] is None else positive_zero(mission["hover_seconds"]),
    }


def content_fingerprint(mission):
    content = json.dumps(normalized_content(mission), sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def normalize_execution_payload(data, *, legacy_topic=False):
    if not isinstance(data, dict):
        raise MissionValidationError("mission payload must be an object")
    version = data.get("schema_version", 1)
    if type(version) is not int or version not in (1, 2):
        raise MissionValidationError("unsupported task schema_version")
    if version == 2:
        for field in ("mission_id", "mission_type", "execution_id"):
            if not isinstance(data.get(field), str) or not data[field]:
                raise MissionValidationError("missing {}".format(field))
        if not EXECUTION_ID_RE.fullmatch(data["execution_id"]):
            raise MissionValidationError("invalid execution_id")
        if legacy_topic:
            raise MissionValidationError("v2 requires the multi-waypoint topic")
    mission = normalize_legacy_payload(data) if legacy_topic else normalize_multi_payload(data, default_rtl=True)
    mission["content_fingerprint"] = content_fingerprint(mission)
    mission["execution_id"] = data["execution_id"] if version == 2 else "legacy-" + mission["content_fingerprint"]
    mission["legacy"] = version == 1
    mission["schema_version"] = 2
    advertised = data.get("content_fingerprint")
    if advertised is not None and advertised != mission["content_fingerprint"]:
        raise MissionValidationError("content_fingerprint mismatch")
    return mission
