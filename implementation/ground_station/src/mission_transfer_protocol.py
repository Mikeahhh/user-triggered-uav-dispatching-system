import hashlib
import json
import math
import re

MAX_SOURCE_WAYPOINTS = 100000
MAX_TASK_BYTES = 16 * 1024 * 1024
CHUNK_POINTS = 256
MAX_MESSAGE_BYTES = 64 * 1024
TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
HASH = re.compile(r"^[0-9a-f]{64}$")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def validate_header(data):
    if not isinstance(data, dict) or data.get("transport_version") != 1 or type(data.get("transport_version")) is not int:
        raise ValueError("unsupported mission transport")
    if not isinstance(data.get("execution_id"), str) or not TOKEN.fullmatch(data["execution_id"]):
        raise ValueError("invalid execution_id")
    if not isinstance(data.get("content_fingerprint"), str) or not HASH.fullmatch(data["content_fingerprint"]):
        raise ValueError("invalid content_fingerprint")
    if type(data.get("attempt")) is not int or not 1 <= data["attempt"] <= 2147483647:
        raise ValueError("invalid admission attempt")
    if data.get("kind") not in {"MANIFEST", "CHUNK", "COMMIT", "QUERY"}:
        raise ValueError("invalid transfer kind")
    if len(canonical(data)) > MAX_MESSAGE_BYTES:
        raise ValueError("transfer message exceeds 64 KiB")
    return data


def decode(payload):
    if len(payload) > MAX_MESSAGE_BYTES:
        raise ValueError("transfer message exceeds 64 KiB")
    return validate_header(json.loads(payload.decode("utf-8")))


def messages(payload, fingerprint, attempt=1, missing=None):
    points = payload["waypoints"]
    if not 1 <= len(points) <= MAX_SOURCE_WAYPOINTS or len(canonical(payload)) > MAX_TASK_BYTES:
        raise ValueError("task exceeds 100000 points or 16 MiB")
    base = dict(transport_version=1, execution_id=payload["execution_id"],
                content_fingerprint=fingerprint, attempt=attempt)
    task = {key: value for key, value in payload.items() if key != "waypoints"}
    count = math.ceil(len(points) / CHUNK_POINTS)
    yield validate_header(dict(base, kind="MANIFEST", task=task, waypoint_count=len(points), chunk_count=count))
    indexes = range(count) if missing is None else missing
    for index in indexes:
        if type(index) is not int or not 0 <= index < count:
            raise ValueError("invalid missing chunk")
        chunk = points[index * CHUNK_POINTS:(index + 1) * CHUNK_POINTS]
        yield validate_header(dict(base, kind="CHUNK", index=index, start=index * CHUNK_POINTS,
                                   waypoints=chunk, chunk_sha256=digest(chunk)))
    yield validate_header(dict(base, kind="COMMIT"))


def query(payload, fingerprint, attempt=1):
    return validate_header(dict(transport_version=1, kind="QUERY", execution_id=payload["execution_id"],
                                content_fingerprint=fingerprint, attempt=attempt))
