from __future__ import annotations

import copy
import capture_record_v2 as capture_v2
import hashlib
import hmac
import json
import math
import os
import re
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional


SCHEMA_VERSION = 1
DELIVERY_STATE_FORWARDED = "FORWARDED_TO_GS"
STORAGE_STATE_STORED = "STORED"
ACK_STATUS = "ACKNOWLEDGED_BY_GS"
RECEIVED_STORED = "RECEIVED_STORED"
ACK_PUBLISHED = "ACK_PUBLISHED"
GROUND_RESCUE_DATA_ENV = "GS_RESCUE_DATA_DIR"
MQTT_USERNAME_ENV = "MQTT_USERNAME"
MQTT_PASSWORD_ENV = "MQTT_PASSWORD"
DEFAULT_ACK_PUBLISH_TIMEOUT_SECONDS = 5.0

TRIGGER_SEMANTICS = {
    "MISSION_QUEUE_COMPLETE_LAND_REQUESTED": (
        "Mission queue complete and LAND command requested; physical touchdown "
        "is not confirmed."
    ),
    "GROUND_STATION_SYNC_REQUEST": (
        "Recovery replay requested by Ground Station; this is not landing evidence."
    ),
}
FORWARD_TRIGGERS = frozenset(TRIGGER_SEMANTICS)

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MISSION_ID_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}/[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
)
HASH_RE = re.compile(r"^[0-9a-f]{64}$")

PAYLOAD_FIELDS = (
    "schema_version",
    "request_id",
    "mission_id",
    "user_id",
    "latitude",
    "longitude",
    "accuracy",
    "captured_at",
    "client_timestamp_ms",
    "status",
    "device",
    "gps_points",
    "test_mode",
)
STORED_RECORD_FIELDS = frozenset(
    PAYLOAD_FIELDS
    + (
        "uav_received_at",
        "payload_sha256",
        "storage_state",
    )
)
ENVELOPE_FIELDS = frozenset(
    {
        "schema_version",
        "delivery_state",
        "forward_trigger",
        "trigger_semantics",
        "forwarded_at",
        "record",
        "envelope_sha256",
    }
)
ACK_FIELDS = frozenset(
    {
        "schema_version",
        "request_id",
        "mission_id",
        "payload_sha256",
        "envelope_sha256",
        "status",
        "acknowledged_at",
    }
)


class RescueRecordError(ValueError):
    pass


class RescueRecordPersistenceError(RuntimeError):
    pass


class AckPublishError(RuntimeError):
    pass


class MqttConfigurationError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _fsync_directory(path: Path) -> None:

    if os.name == "nt":
        return
    fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _mkdir_durable(path: Path) -> None:
    missing = []
    ancestor = path
    while not ancestor.exists():
        missing.append(ancestor)
        ancestor = ancestor.parent
    path.mkdir(parents=True, exist_ok=True)
    for directory in reversed(missing):
        _fsync_directory(directory)
        _fsync_directory(directory.parent)


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _json_object(payload: Any, name: str) -> Dict[str, Any]:
    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RescueRecordError("invalid UTF-8") from exc
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise RescueRecordError("invalid JSON") from exc
    if not isinstance(payload, dict):
        raise RescueRecordError(f"{name} must be an object")
    return payload


def _require_exact_fields(
    value: Mapping[str, Any], expected: frozenset[str], name: str
) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        details = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if extra:
            details.append("extra=" + ",".join(extra))
        raise RescueRecordError(f"invalid {name} fields ({'; '.join(details)})")


def _require_schema_version(value: Any, name: str) -> None:
    if type(value) is not int or value != SCHEMA_VERSION:
        raise RescueRecordError(f"unsupported {name} schema version")


def _require_id(value: Any, pattern: re.Pattern[str], name: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise RescueRecordError(f"invalid {name}")
    return value


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise RescueRecordError(f"invalid {name}")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise RescueRecordError(f"invalid {name}") from exc
    if not math.isfinite(number):
        raise RescueRecordError(f"invalid {name}")
    return number


def _utc_timestamp(value: Any, name: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise RescueRecordError(f"invalid {name}")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise RescueRecordError(f"invalid {name}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise RescueRecordError(f"invalid {name}")
    return parsed


def _validate_gps_points(value: Any) -> None:
    if not isinstance(value, list) or len(value) > 1000:
        raise RescueRecordError("invalid gps_points")
    expected = frozenset({"latitude", "longitude", "captured_at"})
    for index, point in enumerate(value):
        if not isinstance(point, dict):
            raise RescueRecordError(f"invalid gps_points[{index}]")
        _require_exact_fields(point, expected, f"gps_points[{index}]")
        latitude = _finite_number(point["latitude"], f"gps_points[{index}].latitude")
        longitude = _finite_number(
            point["longitude"], f"gps_points[{index}].longitude"
        )
        if not -90.0 <= latitude <= 90.0 or not -180.0 <= longitude <= 180.0:
            raise RescueRecordError(f"gps_points[{index}] out of range")
        _utc_timestamp(point["captured_at"], f"gps_points[{index}].captured_at")


def calculate_envelope_sha256(envelope: Mapping[str, Any]) -> str:

    unsigned = {key: value for key, value in envelope.items() if key != "envelope_sha256"}
    return hashlib.sha256(_canonical_json(unsigned)).hexdigest()


def parse_onboard_envelope(payload: Any) -> Dict[str, Any]:

    envelope = _json_object(payload, "envelope")
    if type(envelope.get("schema_version")) is int and envelope["schema_version"] == 2:
        try:
            return capture_v2.parse_envelope(envelope)
        except capture_v2.CaptureValidationError as exc:
            raise RescueRecordError(str(exc)) from exc
    _require_exact_fields(envelope, ENVELOPE_FIELDS, "envelope")

    envelope_hash = envelope["envelope_sha256"]
    if not isinstance(envelope_hash, str) or not HASH_RE.fullmatch(envelope_hash):
        raise RescueRecordError("invalid envelope hash")
    calculated_envelope_hash = calculate_envelope_sha256(envelope)
    if not hmac.compare_digest(calculated_envelope_hash, envelope_hash):
        raise RescueRecordError("envelope hash mismatch")

    _require_schema_version(envelope["schema_version"], "envelope")
    if envelope["delivery_state"] != DELIVERY_STATE_FORWARDED:
        raise RescueRecordError("invalid delivery state")
    if envelope["forward_trigger"] not in FORWARD_TRIGGERS:
        raise RescueRecordError("invalid forward_trigger")
    expected_semantics = TRIGGER_SEMANTICS[envelope["forward_trigger"]]
    if envelope["trigger_semantics"] != expected_semantics:
        raise RescueRecordError("trigger_semantics does not match forward_trigger")
    forwarded_at = _utc_timestamp(envelope["forwarded_at"], "forwarded_at")

    record = envelope["record"]
    if not isinstance(record, dict):
        raise RescueRecordError("missing record")
    _require_exact_fields(record, STORED_RECORD_FIELDS, "record")
    _require_schema_version(record["schema_version"], "record")

    request_id = _require_id(record["request_id"], ID_RE, "request_id")
    mission_id = _require_id(record["mission_id"], MISSION_ID_RE, "mission_id")
    user_id = _require_id(record["user_id"], ID_RE, "user_id")
    if mission_id != f"{user_id}/{request_id}":
        raise RescueRecordError("mission/user/request mismatch")

    latitude = _finite_number(record["latitude"], "latitude")
    longitude = _finite_number(record["longitude"], "longitude")
    if not -90.0 <= latitude <= 90.0 or not -180.0 <= longitude <= 180.0:
        raise RescueRecordError("GPS out of range")

    accuracy = record["accuracy"]
    if accuracy is not None and _finite_number(accuracy, "accuracy") < 0:
        raise RescueRecordError("accuracy out of range")
    _utc_timestamp(record["captured_at"], "captured_at")
    if (
        isinstance(record["client_timestamp_ms"], bool)
        or not isinstance(record["client_timestamp_ms"], int)
        or record["client_timestamp_ms"] <= 0
    ):
        raise RescueRecordError("invalid client_timestamp_ms")
    if record["status"] not in ("PENDING", "ACCEPTED"):
        raise RescueRecordError("invalid record status")
    if (
        not isinstance(record["device"], str)
        or not record["device"]
        or len(record["device"]) > 32
    ):
        raise RescueRecordError("invalid device")
    if not isinstance(record["test_mode"], bool):
        raise RescueRecordError("invalid test_mode")
    _validate_gps_points(record["gps_points"])

    if record["storage_state"] != STORAGE_STATE_STORED:
        raise RescueRecordError("invalid storage_state")
    uav_received_at = _utc_timestamp(record["uav_received_at"], "uav_received_at")
    if forwarded_at < uav_received_at:
        raise RescueRecordError("forwarded_at precedes uav_received_at")

    payload_hash = record["payload_sha256"]
    if not isinstance(payload_hash, str) or not HASH_RE.fullmatch(payload_hash):
        raise RescueRecordError("invalid payload hash")
    normalized_payload = {field: record[field] for field in PAYLOAD_FIELDS}
    calculated_payload_hash = hashlib.sha256(
        _canonical_json(normalized_payload)
    ).hexdigest()
    if not hmac.compare_digest(calculated_payload_hash, payload_hash):
        raise RescueRecordError("payload hash mismatch")

    return copy.deepcopy(envelope)


def parse_onboard_record(payload: Any) -> Dict[str, Any]:

    envelope = parse_onboard_envelope(payload)
    result = copy.deepcopy(envelope["record"])
    result.update(
        {
            "delivery_state": envelope["delivery_state"],
            "forward_trigger": envelope["forward_trigger"],
            "trigger_semantics": envelope["trigger_semantics"],
            "forwarded_at": envelope["forwarded_at"],
            "envelope_sha256": envelope["envelope_sha256"],
        }
    )
    return result


def validate_ground_ack(ack: Any) -> Dict[str, Any]:
    ack = _json_object(ack, "ack")
    if type(ack.get("schema_version")) is int and ack["schema_version"] == 2:
        try:
            return capture_v2.validate_ack(ack)
        except capture_v2.CaptureValidationError as exc:
            raise RescueRecordError(str(exc)) from exc
    _require_exact_fields(ack, ACK_FIELDS, "ack")
    _require_schema_version(ack["schema_version"], "ack")
    request_id = _require_id(ack["request_id"], ID_RE, "ack request_id")
    mission_id = _require_id(ack["mission_id"], MISSION_ID_RE, "ack mission_id")
    if mission_id.rsplit("/", 1)[-1] != request_id:
        raise RescueRecordError("ack mission/request mismatch")
    for field in ("payload_sha256", "envelope_sha256"):
        if not isinstance(ack[field], str) or not HASH_RE.fullmatch(ack[field]):
            raise RescueRecordError(f"invalid ack {field}")
    if ack["status"] != ACK_STATUS:
        raise RescueRecordError("invalid ack status")
    _utc_timestamp(ack["acknowledged_at"], "acknowledged_at")
    return copy.deepcopy(ack)


def build_ground_ack(envelope: Dict[str, Any]) -> Dict[str, Any]:
    verified = parse_onboard_envelope(envelope)
    if verified["schema_version"] == 2:
        return capture_v2.build_ack(verified)
    record = verified["record"]
    ack = {
        "schema_version": SCHEMA_VERSION,
        "request_id": record["request_id"],
        "mission_id": record["mission_id"],
        "payload_sha256": record["payload_sha256"],
        "envelope_sha256": verified["envelope_sha256"],
        "status": ACK_STATUS,
        "acknowledged_at": utc_now(),
    }
    return validate_ground_ack(ack)


def get_ground_rescue_data_dir(
    environ: Optional[Mapping[str, str]] = None,
    platform: Optional[str] = None,
    home: Optional[Path] = None,
) -> Path:

    env = os.environ if environ is None else environ
    configured = env.get(GROUND_RESCUE_DATA_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()

    platform = sys.platform if platform is None else platform
    home = Path.home() if home is None else Path(home)
    if platform.startswith("win"):
        base = env.get("LOCALAPPDATA", "").strip() or env.get("APPDATA", "").strip()
        if base:
            return Path(base) / "MASS26GroundStation" / "onboard_rescue_records"
    if platform == "darwin":
        return home / "Library" / "Application Support" / "MASS26GroundStation" / "onboard_rescue_records"
    xdg_data_home = env.get("XDG_DATA_HOME", "").strip()
    base = Path(xdg_data_home).expanduser() if xdg_data_home else home / ".local" / "share"
    return base / "MASS26GroundStation" / "onboard_rescue_records"


def configure_mqtt_client_auth(
    client: Any, environ: Optional[Mapping[str, str]] = None
) -> bool:

    env = os.environ if environ is None else environ
    username = env.get(MQTT_USERNAME_ENV, "")
    password = env.get(MQTT_PASSWORD_ENV, "")
    username = username if isinstance(username, str) else ""
    password = password if isinstance(password, str) else ""
    if password and not username:
        raise MqttConfigurationError(
            f"{MQTT_PASSWORD_ENV} requires {MQTT_USERNAME_ENV}"
        )
    if not username:
        return False
    client.username_pw_set(username, password or None)
    return True


class GroundRescueStore:


    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root is not None else get_ground_rescue_data_dir()
        self.records_dir = self.root / "records"
        self.envelopes_dir = self.root / "envelopes"
        self._lock = threading.RLock()

    @staticmethod
    def _write_json_atomic(path: Path, value: Dict[str, Any]) -> None:
        try:
            _mkdir_durable(path.parent)
        except OSError as exc:
            raise RescueRecordPersistenceError("cannot persist evidence directory") from exc
        expected = _canonical_json(value)
        if path.exists():
            try:
                with path.open("r", encoding="utf-8") as handle:
                    existing = json.load(handle)
            except (OSError, json.JSONDecodeError) as exc:
                raise RescueRecordPersistenceError(
                    f"cannot verify existing evidence file: {path.name}"
                ) from exc
            if not hmac.compare_digest(_canonical_json(existing), expected):
                raise RescueRecordPersistenceError(
                    f"immutable evidence conflict: {path.name}"
                )
            try:
                _fsync_directory(path.parent)
            except OSError as exc:
                raise RescueRecordPersistenceError("cannot synchronize existing evidence") from exc
            return

        try:
            fd, temp_name = tempfile.mkstemp(
                prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
            )
        except OSError as exc:
            raise RescueRecordPersistenceError("cannot create evidence temporary file") from exc
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, 0o600)
            try:
                os.link(temp_name, path)
            except FileExistsError:


                GroundRescueStore._write_json_atomic(path, value)
            _fsync_directory(path.parent)
        except OSError as exc:
            raise RescueRecordPersistenceError(
                f"cannot persist evidence file: {path.name}"
            ) from exc
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass

    def persist(self, envelope: Dict[str, Any]) -> Dict[str, Any]:
        verified = parse_onboard_envelope(envelope)
        record = verified["record"]
        if verified["schema_version"] == 2:
            capture_id = record["capture_id"]
            record_path = self.root / "captures_v2" / f"{capture_id}.json"
            envelope_path = self.root / "capture_envelopes_v2" / f"{capture_id}.{verified['envelope_sha256']}.json"
            with self._lock:

                self._write_json_atomic(record_path, record)
                self._write_json_atomic(envelope_path, verified)
            return dict(record_path=record_path,envelope_path=envelope_path,record=copy.deepcopy(record),envelope=verified)
        request_id = record["request_id"]
        record_path = self.records_dir / (
            f"{request_id}.{record['payload_sha256']}.json"
        )
        envelope_path = self.envelopes_dir / (
            f"{request_id}.{verified['envelope_sha256']}.json"
        )
        with self._lock:
            self._write_json_atomic(record_path, record)
            self._write_json_atomic(envelope_path, verified)
        return {
            "record_path": record_path,
            "envelope_path": envelope_path,
            "record": copy.deepcopy(record),
            "envelope": verified,
        }


def publish_ground_ack(
    client: Any,
    topic: str,
    ack: Dict[str, Any],
    timeout: float = DEFAULT_ACK_PUBLISH_TIMEOUT_SECONDS,
) -> None:

    verified_ack = validate_ground_ack(ack)
    try:
        info = client.publish(
            topic,
            _canonical_json(verified_ack).decode("utf-8"),
            qos=1,
        )
    except Exception as exc:
        raise AckPublishError("ACK publish raised an exception") from exc

    rc = getattr(info, "rc", None)
    if rc != 0:
        raise AckPublishError(f"ACK publish failed rc={rc}")
    waiter = getattr(info, "wait_for_publish", None)
    if not callable(waiter):
        raise AckPublishError("ACK publish result cannot be awaited")
    try:
        waited = waiter(timeout=timeout)
    except Exception as exc:
        raise AckPublishError("ACK publish confirmation failed") from exc
    if waited is False:
        raise AckPublishError("ACK publish confirmation timed out")
    is_published = getattr(info, "is_published", None)
    if callable(is_published) and not is_published():
        raise AckPublishError("ACK was not confirmed published")


def process_onboard_envelope(
    payload: Any,
    store: GroundRescueStore,
    publish_ack: Callable[[Dict[str, Any]], None],
    on_state: Optional[Callable[[str, Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:


    envelope = parse_onboard_envelope(payload)
    persisted = store.persist(envelope)
    record = parse_onboard_record(envelope)
    if on_state is not None:
        on_state(RECEIVED_STORED, copy.deepcopy(record))

    ack = build_ground_ack(envelope)
    publish_ack(ack)
    if on_state is not None:
        on_state(ACK_PUBLISHED, copy.deepcopy(record))

    return {
        **persisted,
        "record": record,
        "ack": ack,
        "state": ACK_PUBLISHED,
    }


def format_record_summary(record: Dict[str, Any], delivery_state: str) -> str:
    if record.get("schema_version") == 2:
        source = record["source_request"]
        return (
            f"Capture: {record['capture_id']}\n"
            f"Source SOS: {source['mission_id']} (request {record['request_id']})\n"
            f"Carrier mission: {record['carrier_mission_id']}\n"
            f"Carrier execution: {record['carrier_execution_id']}\n"
            f"Original SOS GPS: {source['latitude']:.6f}, {source['longitude']:.6f}\n"
            f"New GPS: {record['latitude']:.6f}, {record['longitude']:.6f}\n"
            f"New GPS captured: {record['captured_at']}\n"
            f"UAV stored: {record['uav_received_at']}\n"
            f"Forward trigger: {record['forward_trigger']}\n"
            f"Trigger semantics: {record['trigger_semantics']}\n"
            f"Payload hash: {record['payload_sha256'][:12]}...\n"
            f"Delivery: {delivery_state}"
        )
    trigger = record["forward_trigger"]
    if trigger == "MISSION_QUEUE_COMPLETE_LAND_REQUESTED":
        trigger_note = "LAND_REQUESTED != physical touchdown"
    elif trigger == "GROUND_STATION_SYNC_REQUEST":
        trigger_note = "SYNC may occur before LANDING"
    else:
        trigger_note = "Unknown trigger"
    return (
        "Request: {request_id}\n"
        "Mission: {mission_id}\n"
        "GPS: {latitude:.6f}, {longitude:.6f}\n"
        "UAV stored: {uav_received_at}\n"
        "Forward trigger: {forward_trigger}\n"
        "Trigger semantics: {trigger_semantics}\n"
        "Meaning: {trigger_note}\n"
        "Payload hash: {payload_hash}...\n"
        "Envelope hash: {envelope_hash}...\n"
        "Delivery: {delivery_state}"
    ).format(
        request_id=record["request_id"],
        mission_id=record["mission_id"],
        latitude=float(record["latitude"]),
        longitude=float(record["longitude"]),
        uav_received_at=record["uav_received_at"],
        forward_trigger=trigger,
        trigger_semantics=record["trigger_semantics"],
        trigger_note=trigger_note,
        payload_hash=record["payload_sha256"][:12],
        envelope_hash=record["envelope_sha256"][:12],
        delivery_state=delivery_state,
    )
