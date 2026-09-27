#!/usr/bin/env python3


from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import signal
import sys
import tempfile
import threading
import time
import uuid
from urllib.parse import urlsplit, parse_qs
import capture_record_v2 as capture_v2
from capture_store_v2 import CaptureStoreV2, CaptureDeliveryCoordinator, CaptureConflictError, NoCollectionContext, JournalCollectionContextProvider
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

try:
    import paho.mqtt.client as mqtt
    try:
        from paho.mqtt.client import CallbackAPIVersion
        _PAHO_V2 = True
    except ImportError:
        _PAHO_V2 = False
except ImportError:
    mqtt = None
    CallbackAPIVersion = None
    _PAHO_V2 = False


SCHEMA_VERSION = 1
RECEIVER_VERSION = "1.2.0"
UAV_SOFTWARE_VERSION = "1.2.0"
SYSTEM_RELEASE_ID = "MASS26-20260806"
MAX_BODY_BYTES = 64 * 1024
MAX_GPS_POINTS = 1000
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MISSION_ID_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}/[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
)
HASH_RE = re.compile(r"^[0-9a-f]{64}$")

TRIGGER_LAND_REQUESTED = "MISSION_QUEUE_COMPLETE_LAND_REQUESTED"
TRIGGER_GS_SYNC = "GROUND_STATION_SYNC_REQUEST"
TRIGGER_SEMANTICS = {
    TRIGGER_LAND_REQUESTED: (
        "Mission queue complete and LAND command requested; physical touchdown "
        "is not confirmed."
    ),
    TRIGGER_GS_SYNC: (
        "Recovery replay requested by Ground Station; this is not landing evidence."
    ),
}

DEFAULT_TOPIC_STATUS = "alin1/mission/status"
DEFAULT_TOPIC_RECORD = "alin1/rescue/onboard_record"
DEFAULT_TOPIC_ACK = "alin1/rescue/ground_ack"
DEFAULT_TOPIC_SYNC = "alin1/rescue/sync_request"
DEFAULT_TOPIC_READY = "alin1/rescue/receiver_ready"
RECEIVER_ID = "uav_phone_rescue_receiver"
RETRY_BASE_SECONDS = 2.0
RETRY_MAX_SECONDS = 60.0


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_json(value: Dict[str, Any]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


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
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    for directory in reversed(missing):
        _fsync_directory(directory)
        _fsync_directory(directory.parent)


class PayloadValidationError(ValueError):
    pass


class RecordConflictError(RuntimeError):
    pass


def _required_id(data: Dict[str, Any], field: str, pattern: re.Pattern) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise PayloadValidationError("invalid {}".format(field))
    return value


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise PayloadValidationError("invalid {}".format(field))
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise PayloadValidationError("invalid {}".format(field))
    if number != number or number in (float("inf"), float("-inf")):
        raise PayloadValidationError("invalid {}".format(field))
    return number


def _gps_point(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise PayloadValidationError("invalid gps_points entry")
    latitude = _finite_number(raw.get("latitude"), "gps_points.latitude")
    longitude = _finite_number(raw.get("longitude"), "gps_points.longitude")
    if not -90.0 <= latitude <= 90.0:
        raise PayloadValidationError("gps point latitude out of range")
    if not -180.0 <= longitude <= 180.0:
        raise PayloadValidationError("gps point longitude out of range")
    captured_at = raw.get("captured_at")
    _utc_timestamp(captured_at, "gps_points.captured_at")

    return {
        "latitude": latitude,
        "longitude": longitude,
        "captured_at": captured_at,
    }


def _utc_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise PayloadValidationError("invalid {}".format(field))
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise PayloadValidationError("invalid {}".format(field)) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise PayloadValidationError("invalid {}".format(field))
    return parsed


def validate_payload(data: Any) -> Dict[str, Any]:

    if not isinstance(data, dict):
        raise PayloadValidationError("JSON body must be an object")
    if type(data.get("schema_version")) is not int or data.get("schema_version") != SCHEMA_VERSION:
        raise PayloadValidationError("unsupported schema_version")

    request_id = _required_id(data, "request_id", ID_RE)
    mission_id = _required_id(data, "mission_id", MISSION_ID_RE)
    user_id = _required_id(data, "user_id", ID_RE)
    if not mission_id.startswith(user_id + "/"):
        raise PayloadValidationError("mission_id does not match user_id")
    if mission_id.rsplit("/", 1)[-1] != request_id:
        raise PayloadValidationError("mission_id does not match request_id")

    latitude = _finite_number(data.get("latitude"), "latitude")
    longitude = _finite_number(data.get("longitude"), "longitude")
    if not -90.0 <= latitude <= 90.0:
        raise PayloadValidationError("latitude out of range")
    if not -180.0 <= longitude <= 180.0:
        raise PayloadValidationError("longitude out of range")

    timestamp_ms = data.get("client_timestamp_ms")
    if isinstance(timestamp_ms, bool) or not isinstance(timestamp_ms, int) or timestamp_ms <= 0:
        raise PayloadValidationError("invalid client_timestamp_ms")
    captured_at = data.get("captured_at")
    _utc_timestamp(captured_at, "captured_at")

    accuracy_raw = data.get("accuracy")
    accuracy = None
    if accuracy_raw is not None:
        accuracy = _finite_number(accuracy_raw, "accuracy")
        if accuracy < 0:
            raise PayloadValidationError("accuracy out of range")

    gps_raw = data.get("gps_points", [])
    if not isinstance(gps_raw, list) or len(gps_raw) > MAX_GPS_POINTS:
        raise PayloadValidationError("invalid gps_points")
    gps_points = [_gps_point(point) for point in gps_raw]

    status = data.get("status", "PENDING")
    if status not in ("PENDING", "ACCEPTED"):
        raise PayloadValidationError("invalid status")
    device = data.get("device", "unknown")
    if not isinstance(device, str) or not device or len(device) > 32:
        raise PayloadValidationError("invalid device")
    try:
        device.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise PayloadValidationError("invalid device Unicode") from exc
    test_mode = data.get("test_mode", False)
    if not isinstance(test_mode, bool):
        raise PayloadValidationError("invalid test_mode")

    return {
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id,
        "mission_id": mission_id,
        "user_id": user_id,
        "latitude": latitude,
        "longitude": longitude,
        "accuracy": accuracy,
        "captured_at": captured_at,
        "client_timestamp_ms": timestamp_ms,
        "status": status,
        "device": device,
        "gps_points": gps_points,
        "test_mode": test_mode,
    }


class RescueStore:


    def __init__(self, root: Path):
        self.root = Path(root)
        self.records_dir = self.root / "records"
        self.outbox_dir = self.root / "outbox"
        self.triggers_dir = self.root / "land_requests"
        _mkdir_durable(self.records_dir)
        _mkdir_durable(self.outbox_dir)
        _mkdir_durable(self.triggers_dir)
        for directory in (self.root, self.records_dir, self.outbox_dir, self.triggers_dir):
            try:
                os.chmod(directory, 0o700)
            except OSError:
                pass
        self._lock = threading.RLock()
        self._bad_delivery_files = set()
        self._record_recovery_errors = set()
        self.recovered_outboxes = 0
        _fsync_directory(self.root)
        _fsync_directory(self.root.parent)
        self._recover_missing_outboxes()
        self.captures = CaptureStoreV2(self.root, self._write_json_atomic, self._write_json_exclusive,
                                       _mkdir_durable, self._read_verified_record)

    @staticmethod
    def _write_json_atomic(path: Path, data: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, sort_keys=True, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, path)
            _fsync_directory(path.parent)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass

    @staticmethod
    def _write_json_exclusive(path: Path, data: Dict[str, Any]) -> None:

        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, sort_keys=True, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, 0o600)


            os.link(temp_name, path)
            _fsync_directory(path.parent)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass

    def _record_path(self, request_id: str) -> Path:
        return self.records_dir / (request_id + ".json")

    def _outbox_path(self, request_id: str) -> Path:
        return self.outbox_dir / (request_id + ".json")

    @staticmethod
    def _initial_delivery_state(record: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "request_id": record["request_id"],
            "mission_id": record["mission_id"],
            "payload_sha256": record["payload_sha256"],
            "state": "STORED_ONBOARD",
            "delivery_attempts": 0,
            "updated_at": record["uav_received_at"],
        }

    def _ensure_outbox(self, record: Dict[str, Any]) -> bool:

        outbox_path = self._outbox_path(record["request_id"])
        if outbox_path.exists():
            return False
        try:
            self._write_json_exclusive(
                outbox_path,
                self._initial_delivery_state(record),
            )
            return True
        except FileExistsError:

            return False

    def _read_verified_record(self, request_id: str) -> Dict[str, Any]:
        if not ID_RE.fullmatch(request_id):
            raise PayloadValidationError("invalid stored request identifier")
        record = self.read_record(request_id)
        payload = validate_payload(record)
        if record.get("request_id") != request_id:
            raise PayloadValidationError("stored record filename mismatch")
        if set(record) != set(payload) | {"uav_received_at", "payload_sha256", "storage_state"}:
            raise PayloadValidationError("invalid stored record fields")
        if record.get("storage_state") != "STORED":
            raise PayloadValidationError("invalid stored record state")
        _utc_timestamp(record.get("uav_received_at"), "uav_received_at")
        digest = hashlib.sha256(canonical_json({key: record[key] for key in payload})).hexdigest()
        if record.get("payload_sha256") != digest:
            raise PayloadValidationError("stored record payload hash mismatch")
        return record

    def _recover_missing_outboxes(self) -> None:

        errors = set()
        with self._lock:
            for path in sorted(self.records_dir.glob("*.json")):
                if self._outbox_path(path.stem).exists():
                    continue
                try:
                    record = self._read_verified_record(path.stem)
                    if self._ensure_outbox(record):
                        self.recovered_outboxes += 1
                except (OSError, ValueError, TypeError, KeyError):
                    errors.add(path.name)
            self._record_recovery_errors = errors

    def _existing_duplicate(
        self, request_id: str, payload_sha256: str
    ) -> Tuple[Dict[str, Any], bool]:
        existing = self._read_verified_record(request_id)
        if existing.get("payload_sha256") != payload_sha256:
            raise RecordConflictError("request_id already has different content")
        self._ensure_outbox(existing)
        _fsync_directory(self.records_dir)
        _fsync_directory(self.outbox_dir)
        return existing, True

    def store(self, raw: Any) -> Tuple[Dict[str, Any], bool]:
        payload = validate_payload(raw)
        payload_sha256 = hashlib.sha256(canonical_json(payload)).hexdigest()
        record_path = self._record_path(payload["request_id"])

        with self._lock:
            if record_path.exists():
                return self._existing_duplicate(
                    payload["request_id"], payload_sha256
                )

            record = dict(payload)
            record.update({
                "uav_received_at": utc_now(),
                "payload_sha256": payload_sha256,
                "storage_state": "STORED",
            })
            try:
                self._write_json_exclusive(record_path, record)
            except FileExistsError:
                return self._existing_duplicate(
                    payload["request_id"], payload_sha256
                )
            self._ensure_outbox(record)
            return record, False

    def read_record(self, request_id: str) -> Dict[str, Any]:
        with self._record_path(request_id).open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def read_delivery(self, request_id: str) -> Dict[str, Any]:
        with self._outbox_path(request_id).open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def remember_land_request(self, mission_id: str) -> None:

        if not isinstance(mission_id, str) or not MISSION_ID_RE.fullmatch(mission_id):
            raise PayloadValidationError("invalid LAND request mission")
        path = self.triggers_dir / (hashlib.sha256(mission_id.encode("utf-8")).hexdigest() + ".json")
        with self._lock:
            self._write_json_atomic(path, {
                "schema_version": SCHEMA_VERSION,
                "mission_id": mission_id,
                "trigger": TRIGGER_LAND_REQUESTED,
                "observed_at": utc_now(),
            })

    def has_land_request(self, mission_id: str) -> bool:
        path = self.triggers_dir / (hashlib.sha256(mission_id.encode("utf-8")).hexdigest() + ".json")
        try:
            with path.open(encoding="utf-8") as handle:
                value = json.load(handle)
            return (isinstance(value, dict)
                    and type(value.get("schema_version")) is int
                    and value.get("schema_version") == SCHEMA_VERSION
                    and value.get("mission_id") == mission_id
                    and value.get("trigger") == TRIGGER_LAND_REQUESTED
                    and bool(_utc_timestamp(value.get("observed_at"), "observed_at")))
        except (OSError, ValueError, TypeError):
            return False

    def prepare_delivery(self, record: Dict[str, Any], trigger: str) -> Dict[str, Any]:

        with self._lock:
            state = self.read_delivery(record["request_id"])
            envelope = state.get("delivery_envelope")
            if envelope is None and state.get("state") == "FORWARDED_TO_GS":

                envelope = build_forward_envelope(record, state.get("forward_trigger"), state.get("forwarded_at"))
                if envelope["envelope_sha256"] != state.get("envelope_sha256"):
                    raise PayloadValidationError("stored forwarding metadata hash mismatch")
            if envelope is not None:
                if (not isinstance(envelope, dict)
                        or envelope.get("record") != record
                        or envelope.get("forward_trigger") not in TRIGGER_SEMANTICS
                        or envelope.get("envelope_sha256") != compute_envelope_sha256(envelope)):
                    raise PayloadValidationError("invalid saved delivery envelope")
                expected = build_forward_envelope(record, envelope["forward_trigger"], envelope.get("forwarded_at"))
                if canonical_json(expected) != canonical_json(envelope):
                    raise PayloadValidationError("invalid saved delivery envelope fields")
                forwarded_at = _utc_timestamp(envelope.get("forwarded_at"), "forwarded_at")
                if forwarded_at < _utc_timestamp(record["uav_received_at"], "uav_received_at"):
                    raise PayloadValidationError("forwarding time precedes receipt")
            else:
                envelope = build_forward_envelope(record, trigger)
            if state.get("delivery_envelope") is None:
                self._update_delivery(record["request_id"], delivery_envelope=envelope)
            return envelope

    def pending_records(self, mission_id: Optional[str] = None) -> Iterable[Dict[str, Any]]:
        with self._lock:
            self._recover_missing_outboxes()
            paths = sorted(self.outbox_dir.glob("*.json"))
            output: List[Dict[str, Any]] = []
            bad_files = set()
            for path in paths:
                try:
                    with path.open("r", encoding="utf-8") as handle:
                        state = json.load(handle)
                    if not isinstance(state, dict) or type(state.get("schema_version")) is not int or state.get("schema_version") != SCHEMA_VERSION:
                        raise PayloadValidationError("invalid delivery state schema")
                    if state.get("request_id") != path.stem or state.get("state") not in (
                        "STORED_ONBOARD", "FORWARDED_TO_GS", "ACKNOWLEDGED_BY_GS"
                    ):
                        raise PayloadValidationError("invalid delivery state identity")
                    record = self._read_verified_record(path.stem)
                    if state.get("mission_id") != record["mission_id"] or state.get("payload_sha256") != record["payload_sha256"]:
                        raise PayloadValidationError("delivery state does not match record")
                    if state["state"] == "ACKNOWLEDGED_BY_GS":
                        continue
                    if mission_id and state.get("mission_id") != mission_id:
                        continue
                    output.append(record)
                except (OSError, ValueError, TypeError, KeyError):
                    bad_files.add(path.name)
            if bad_files and bad_files != self._bad_delivery_files:
                print("[receiver] isolated {} unreadable delivery entries".format(len(bad_files)), file=sys.stderr)
            self._bad_delivery_files = bad_files
            return output

    def _update_delivery(self, request_id: str, **fields: Any) -> Dict[str, Any]:
        with self._lock:
            state = self.read_delivery(request_id)
            state.update(fields)
            state["updated_at"] = utc_now()
            self._write_json_atomic(self._outbox_path(request_id), state)
            return state

    def mark_delivery_attempt(
        self,
        request_id: str,
        trigger: str,
        attempted_at: str,
        retry_after_epoch: Optional[float] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            state = self.read_delivery(request_id)
            return self._update_delivery(
                request_id,
                delivery_attempts=int(state.get("delivery_attempts", 0)) + 1,
                last_attempt_at=attempted_at,
                last_attempt_trigger=trigger,
                last_attempt_result="IN_PROGRESS",
                retry_after_epoch=retry_after_epoch,
            )

    def mark_delivery_failed(
        self,
        request_id: str,
        error_type: str,
    ) -> Dict[str, Any]:
        return self._update_delivery(
            request_id,
            last_attempt_result="DEFERRED",
            last_delivery_error=error_type,
        )

    def mark_forwarded(
        self,
        request_id: str,
        trigger: str,
        forwarded_at: str,
        envelope_sha256: str,
    ) -> Dict[str, Any]:
        return self._update_delivery(
            request_id,
            state="FORWARDED_TO_GS",
            forward_trigger=trigger,
            forward_semantics=TRIGGER_SEMANTICS[trigger],
            forwarded_at=forwarded_at,
            envelope_sha256=envelope_sha256,
            last_attempt_result="PUBLISHED",
            last_delivery_error=None,
        )

    def mark_acknowledged(self, request_id: str, ack: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(ack, dict):
            raise PayloadValidationError("ground ack must be an object")
        if type(ack.get("schema_version")) is not int or ack.get("schema_version") != SCHEMA_VERSION:
            raise PayloadValidationError("ground ack schema mismatch")
        if ack.get("status") != "ACKNOWLEDGED_BY_GS":
            raise PayloadValidationError("ground ack status mismatch")
        ack_request_id = _required_id(ack, "request_id", ID_RE)
        if ack_request_id != request_id:
            raise PayloadValidationError("ground ack request mismatch")
        ack_mission_id = _required_id(ack, "mission_id", MISSION_ID_RE)
        ack_hash = ack.get("payload_sha256")
        if not isinstance(ack_hash, str) or not HASH_RE.fullmatch(ack_hash):
            raise PayloadValidationError("ground ack hash invalid")
        ack_envelope_hash = ack.get("envelope_sha256")
        if (
            not isinstance(ack_envelope_hash, str)
            or not HASH_RE.fullmatch(ack_envelope_hash)
        ):
            raise PayloadValidationError("ground ack envelope hash invalid")
        acknowledged_at = ack.get("acknowledged_at")
        _utc_timestamp(acknowledged_at, "ground ack timestamp")

        try:
            state = self.read_delivery(request_id)
        except FileNotFoundError as exc:
            raise PayloadValidationError("ground ack request is unknown") from exc
        if state.get("state") != "FORWARDED_TO_GS":
            raise PayloadValidationError("record is not awaiting Ground Station ack")
        if ack_mission_id != state.get("mission_id"):
            raise PayloadValidationError("ground ack mission mismatch")
        if ack_hash != state.get("payload_sha256"):
            raise PayloadValidationError("ground ack hash mismatch")
        if ack_envelope_hash != state.get("envelope_sha256"):
            raise PayloadValidationError("ground ack envelope hash mismatch")
        return self._update_delivery(
            request_id,
            state="ACKNOWLEDGED_BY_GS",
            acknowledged_at=acknowledged_at,
            ground_ack={
                "schema_version": SCHEMA_VERSION,
                "request_id": ack_request_id,
                "mission_id": ack_mission_id,
                "payload_sha256": ack_hash,
                "envelope_sha256": ack_envelope_hash,
                "status": "ACKNOWLEDGED_BY_GS",
                "acknowledged_at": acknowledged_at,
            },
        )

    def health_summary(self) -> Dict[str, Any]:

        states = {
            "records": 0,
            "stored_onboard": 0,
            "forwarded_waiting_ack": 0,
            "acknowledged_by_gs": 0,
            "unreadable_outbox_entries": 0,
            "record_recovery_errors": len(self._record_recovery_errors),
            "recovered_outbox_entries": self.recovered_outboxes,
        }
        with self._lock:
            states["records"] = len(list(self.records_dir.glob("*.json")))
            for path in self.outbox_dir.glob("*.json"):
                try:
                    with path.open("r", encoding="utf-8") as handle:
                        state = json.load(handle).get("state")
                except Exception:
                    states["unreadable_outbox_entries"] += 1
                    continue
                if state == "STORED_ONBOARD":
                    states["stored_onboard"] += 1
                elif state == "FORWARDED_TO_GS":
                    states["forwarded_waiting_ack"] += 1
                elif state == "ACKNOWLEDGED_BY_GS":
                    states["acknowledged_by_gs"] += 1
        states["persistence_ready"] = bool(
            self.records_dir.is_dir()
            and self.outbox_dir.is_dir()
            and os.access(self.records_dir, os.R_OK | os.W_OK)
            and os.access(self.outbox_dir, os.R_OK | os.W_OK)
        )
        states["unreadable_outbox_entries"] = max(
            states["unreadable_outbox_entries"], len(self._bad_delivery_files)
        )
        return states


def compute_envelope_sha256(envelope: Dict[str, Any]) -> str:

    value = dict(envelope)
    value.pop("envelope_sha256", None)
    return hashlib.sha256(canonical_json(value)).hexdigest()


def build_forward_envelope(
    record: Dict[str, Any], trigger: str, forwarded_at: Optional[str] = None
) -> Dict[str, Any]:
    if trigger not in TRIGGER_SEMANTICS:
        raise PayloadValidationError("unknown forward trigger")
    envelope = {
        "schema_version": SCHEMA_VERSION,
        "delivery_state": "FORWARDED_TO_GS",
        "forward_trigger": trigger,
        "trigger_semantics": TRIGGER_SEMANTICS[trigger],
        "forwarded_at": forwarded_at or utc_now(),
        "record": record,
    }
    envelope["envelope_sha256"] = compute_envelope_sha256(envelope)
    return envelope


class RescueDeliveryCoordinator:


    def __init__(
        self,
        store: RescueStore,
        publish: Callable[[str, str], Any],
        record_topic: str = DEFAULT_TOPIC_RECORD,
        clock: Callable[[], float] = time.time,
    ):
        self.store = store
        self.publish = publish
        self.record_topic = record_topic
        self.clock = clock
        self.capture_coordinator = CaptureDeliveryCoordinator(store.captures, publish, record_topic, clock)

    def _forward_record(self, record: Dict[str, Any], trigger: str) -> bool:
        request_id = record["request_id"]
        try:
            state = self.store.read_delivery(request_id)
            deadline = state.get("retry_after_epoch")
            if deadline is not None:
                deadline = _finite_number(deadline, "retry_after_epoch")
                if deadline > self.clock():
                    return False
            attempts = state.get("delivery_attempts", 0)
            if type(attempts) is not int or attempts < 0:
                raise PayloadValidationError("invalid delivery attempt count")
            envelope = self.store.prepare_delivery(record, trigger)
            retry_delay = min(RETRY_MAX_SECONDS, RETRY_BASE_SECONDS * (2 ** min(attempts, 8)))
            self.store.mark_delivery_attempt(
                request_id, envelope["forward_trigger"], utc_now(),
                retry_after_epoch=self.clock() + retry_delay,
            )
            self.publish(self.record_topic, canonical_json(envelope).decode("utf-8"))
            self.store.mark_forwarded(
                request_id, envelope["forward_trigger"], envelope["forwarded_at"],
                envelope["envelope_sha256"],
            )
            return True
        except Exception as exc:

            try:
                self.store.mark_delivery_failed(request_id, type(exc).__name__)
            except Exception:
                pass
            print("[receiver] delivery deferred: {}".format(type(exc).__name__), file=sys.stderr)
            return False

    def forward_pending(self, mission_id: Optional[str], trigger: str) -> int:
        return sum(self._forward_record(record, trigger)
                   for record in self.store.pending_records(mission_id))

    def retry_pending(self) -> int:
        return self._retry_v1_pending() + self.capture_coordinator.retry_pending()

    def _retry_v1_pending(self) -> int:

        count = 0
        for record in self.store.pending_records():
            try:
                state = self.store.read_delivery(record["request_id"])
            except (OSError, ValueError, TypeError):
                continue
            trigger = state.get("last_attempt_trigger") or state.get("forward_trigger")
            if trigger not in TRIGGER_SEMANTICS:
                saved = state.get("delivery_envelope")
                trigger = saved.get("forward_trigger") if isinstance(saved, dict) else None
            if trigger not in TRIGGER_SEMANTICS and self.store.has_land_request(record["mission_id"]):
                trigger = TRIGGER_LAND_REQUESTED
            if trigger in TRIGGER_SEMANTICS:
                count += self._forward_record(record, trigger)
        return count

    def handle_mission_status(self, data: Dict[str, Any]) -> int:
        return self._handle_v1_mission_status(data) + self.capture_coordinator.handle_status(data)

    def _handle_v1_mission_status(self, data: Dict[str, Any]) -> int:
        if not isinstance(data, dict):
            return 0
        status = str(data.get("status", "")).upper()
        phase = str(data.get("phase", "")).upper()
        if status not in ("LANDING", "LAND_REQUESTED"):
            return 0
        if phase and phase not in ("LANDING", "LAND_REQUESTED"):
            return 0
        if data.get("land_command_requested") is False:
            return 0
        mission_id = data.get("mission_id")
        if not isinstance(mission_id, str) or not MISSION_ID_RE.fullmatch(mission_id):
            return 0

        self.store.remember_land_request(mission_id)
        return self.forward_pending(mission_id, TRIGGER_LAND_REQUESTED)

    def handle_sync_request(self) -> int:

        return self.forward_pending(None, TRIGGER_GS_SYNC) + self.capture_coordinator.sync()

    def handle_ground_ack(self, data: Dict[str, Any]) -> None:
        if isinstance(data, dict) and type(data.get("schema_version")) is int and data["schema_version"] == 2:
            try:
                self.store.captures.acknowledge(data)
            except capture_v2.CaptureValidationError as exc:
                raise PayloadValidationError(str(exc)) from exc
            return
        request_id = _required_id(data, "request_id", ID_RE)
        self.store.mark_acknowledged(request_id, data)


class MqttRuntime:
    def __init__(
        self,
        store: RescueStore,
        broker: str,
        port: int,
        username: str = "",
        password: str = "",
    ):
        if mqtt is None:
            raise RuntimeError("paho-mqtt is required for MQTT forwarding")
        if _PAHO_V2:
            self.client = mqtt.Client(
                callback_api_version=CallbackAPIVersion.VERSION1,
                client_id=RECEIVER_ID,
            )
        else:
            self.client = mqtt.Client(client_id=RECEIVER_ID)
        self.broker = broker
        self.port = port
        self.connected = threading.Event()
        self._stop_event = threading.Event()
        self._work_queue: Queue = Queue()
        self._subscription_mids = set()
        self._subscription_failed = False
        self._pending_ready = None
        self._ready_retry_at = 0.0
        self._next_recovery_at = 0.0
        self._worker = threading.Thread(
            target=self._worker_loop,
            name="rescue-mqtt-delivery",
            daemon=True,
        )
        if password and not username:
            raise ValueError("MQTT password requires a username")
        if username:
            self.client.username_pw_set(username, password or None)
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        self.coordinator = RescueDeliveryCoordinator(store, self._publish)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.client.on_subscribe = self._on_subscribe

    def _publish(self, topic: str, payload: str) -> None:
        if not self.connected.is_set():
            raise RuntimeError("MQTT is not connected")
        info = self.client.publish(topic, payload, qos=1)
        success_code = getattr(mqtt, "MQTT_ERR_SUCCESS", 0)
        if getattr(info, "rc", success_code) != success_code:
            raise RuntimeError("MQTT publish failed rc={}".format(info.rc))
        if hasattr(info, "wait_for_publish"):
            info.wait_for_publish(timeout=5.0)
        if hasattr(info, "is_published") and not info.is_published():
            raise RuntimeError("MQTT QoS1 publish was not acknowledged")

    def _on_connect(self, client, userdata, flags, rc):
        self.connected.clear()
        self._subscription_mids.clear()
        self._subscription_failed = False
        self._pending_ready = None
        if rc != 0:
            print("[receiver] MQTT connect failed rc={}".format(rc), file=sys.stderr)
            return
        for topic in (DEFAULT_TOPIC_STATUS, DEFAULT_TOPIC_ACK, DEFAULT_TOPIC_SYNC):
            result = client.subscribe(topic, qos=1)
            if not isinstance(result, tuple) or result[0] != 0:
                print(
                    "[receiver] MQTT subscribe failed topic={} rc={}".format(
                        topic, result[0] if isinstance(result, tuple) else "unknown"
                    ),
                    file=sys.stderr,
                )
                self.connected.clear()
                self._subscription_failed = True
                return
            self._subscription_mids.add(result[1])

    def _on_subscribe(self, client, userdata, mid, granted_qos):
        if mid not in self._subscription_mids or self._subscription_failed:
            return
        self._subscription_mids.discard(mid)
        if not granted_qos or any(int(qos) >= 128 for qos in granted_qos):
            self._subscription_failed = True
            self.connected.clear()
            return
        if not self._subscription_mids:

            self._pending_ready = {
                "schema_version": 1,
                "message_type": "RECEIVER_READY",
                "receiver_id": RECEIVER_ID,
                "ready_id": uuid.uuid4().hex,
            }
            self._ready_retry_at = 0.0
            self.connected.set()

    def _on_disconnect(self, client, userdata, rc):
        self.connected.clear()
        self._pending_ready = None
        if rc != 0:
            print(
                "[receiver] MQTT disconnected rc={}; reconnecting in background".format(rc),
                file=sys.stderr,
            )

    def _on_message(self, client, userdata, msg):
        if msg.topic in (DEFAULT_TOPIC_STATUS, DEFAULT_TOPIC_SYNC) and getattr(msg, "retain", False):
            return
        try:
            data = json.loads(msg.payload.decode("utf-8"))
        except Exception as exc:
            print("[receiver] ignored MQTT message: {}".format(exc), file=sys.stderr)
            return
        self._work_queue.put((msg.topic, data))

    def _service_recovery(self) -> None:
        if not self.connected.is_set():
            return
        now = time.monotonic()
        if self._pending_ready is not None and now >= self._ready_retry_at:
            ready = self._pending_ready
            self._ready_retry_at = now + 5.0
            try:
                self._publish(DEFAULT_TOPIC_READY, canonical_json(ready).decode("utf-8"))
                if self._pending_ready is ready:
                    self._pending_ready = None
            except Exception as exc:
                print("[receiver] readiness deferred: {}".format(type(exc).__name__), file=sys.stderr)
        if now >= self._next_recovery_at:
            self._next_recovery_at = now + 0.5
            try:
                self.coordinator.retry_pending()
            except Exception as exc:
                print("[receiver] recovery deferred: {}".format(type(exc).__name__), file=sys.stderr)

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                topic, data = self._work_queue.get(timeout=0.2)
            except Empty:
                self._service_recovery()
                continue
            try:
                if topic == DEFAULT_TOPIC_STATUS:
                    self.coordinator.handle_mission_status(data)
                elif topic == DEFAULT_TOPIC_ACK:
                    self.coordinator.handle_ground_ack(data)
                elif topic == DEFAULT_TOPIC_SYNC:
                    self.coordinator.handle_sync_request()
            except Exception as exc:
                print("[receiver] ignored MQTT message: {}".format(exc), file=sys.stderr)
            finally:
                self._work_queue.task_done()
            self._service_recovery()

    def start(self) -> None:
        self._worker.start()
        self.client.connect_async(self.broker, self.port, keepalive=60)
        self.client.loop_start()

    def stop(self) -> None:
        self._stop_event.set()
        self.connected.clear()
        self.client.loop_stop()
        self.client.disconnect()
        if self._worker.is_alive():
            self._worker.join(timeout=2.0)

    def health_summary(self) -> Dict[str, Any]:
        return {
            "mode": "mqtt",
            "connected": self.connected.is_set(),
            "queued_messages": self._work_queue.qsize(),
        }


class RescueHttpServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address,
        store: RescueStore,
        auth_token: str = "",
        runtime_status: Optional[Callable[[], Dict[str, Any]]] = None,
        collection_context_provider: Optional[Callable[[str], Dict[str, Any]]] = None,
    ):
        super().__init__(address, RescueRequestHandler)
        self.store = store
        self.auth_token = auth_token
        self.collection_context_provider = collection_context_provider
        self.runtime_status = runtime_status or (
            lambda: {"mode": "disabled", "connected": False, "queued_messages": 0}
        )


class RescueRequestHandler(BaseHTTPRequestHandler):
    server_version = "TriggerSearchUAVReceiver/{}".format(RECEIVER_VERSION)

    def log_message(self, fmt, *args):
        print("[receiver-http] " + (fmt % args), file=sys.stderr)

    def _json_response(self, status: int, body: Dict[str, Any]) -> None:
        encoded = canonical_json(body)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def _authorized(self):
        expected=self.server.auth_token
        if expected and not hmac.compare_digest(expected,self.headers.get("X-Rescue-Token", "")):
            self._json_response(401,{"error":"unauthorized","error_code":"UNAUTHORIZED"})
            return False
        return True

    def do_GET(self):
        if urlsplit(self.path).path == "/api/v2/mission-context":
            if not self._authorized(): return
            try:
                query=parse_qs(urlsplit(self.path).query,keep_blank_values=True,strict_parsing=True)
                if set(query)!={"user_id","request_id","capture_id"} or any(len(v)!=1 for v in query.values()):
                    raise capture_v2.CaptureValidationError("user_id, request_id and capture_id required once")
                body=self.server.store.captures.issue_context(query["user_id"][0],query["request_id"][0],query["capture_id"][0],self.server.collection_context_provider)
                self._json_response(200,body)
            except NoCollectionContext as exc:
                self._json_response(409,{"error":str(exc),"error_code":"NO_COLLECTION_CONTEXT"})
            except CaptureConflictError as exc:
                self._json_response(409,{"error":str(exc),"error_code":"CONTEXT_IDENTITY_CONFLICT"})
            except (ValueError,TypeError) as exc:
                self._json_response(400,{"error":str(exc),"error_code":"INVALID_REQUEST"})
            except Exception:
                self._json_response(500,{"error":"context persistence failure","error_code":"STORAGE_FAILURE"})
            return
        if self.path != "/health":
            self._json_response(404, {"error": "not found"})
            return
        self._json_response(
            200,
            {
                "status": "ok",
                "schema_version": SCHEMA_VERSION,
                "receiver_version": RECEIVER_VERSION,
                "uav_software_version": UAV_SOFTWARE_VERSION,
                "system_release_id": SYSTEM_RELEASE_ID,
                "persistence": self.server.store.health_summary(),
                "delivery_transport": self.server.runtime_status(),
            },
        )

    def _post_capture(self):
        if not self._authorized(): return
        try:
            size=int(self.headers.get("Content-Length","0"))
        except ValueError: size=0
        if not 0 < size <= 256 * 1024:
            self._json_response(413,{"error":"invalid body size","error_code":"INVALID_REQUEST"}); return
        try:
            data=json.loads(self.rfile.read(size).decode("utf-8"))
            record,duplicate=self.server.store.captures.store(data)
        except NoCollectionContext as exc:
            self._json_response(409,{"error":str(exc),"error_code":"NO_COLLECTION_CONTEXT"}); return
        except CaptureConflictError as exc:
            self._json_response(409,{"error":str(exc),"error_code":str(exc)}); return
        except (capture_v2.CaptureValidationError,UnicodeDecodeError,json.JSONDecodeError) as exc:
            code="CURRENT_SOS_REQUIRED" if "source_request required" in str(exc) else "INVALID_REQUEST"
            self._json_response(400,{"error":str(exc),"error_code":code}); return
        except Exception:
            self._json_response(500,{"error":"capture storage failure","error_code":"STORAGE_FAILURE"}); return
        receipt={k:record[k] for k in ("capture_id","request_id","context_id","carrier_mission_id","carrier_execution_id","payload_sha256","uav_received_at")}
        receipt.update(schema_version=2,status="STORED",duplicate=duplicate,receiver_version=RECEIVER_VERSION,system_release_id=SYSTEM_RELEASE_ID)
        self._json_response(200 if duplicate else 201,receipt)

    def do_POST(self):
        if self.path == "/api/v2/rescue-captures":
            return self._post_capture()
        if self.path != "/api/v1/rescue-requests":
            self._json_response(404, {"error": "not found"})
            return
        expected_token = self.server.auth_token
        supplied_token = self.headers.get("X-Rescue-Token", "")
        if expected_token and not hmac.compare_digest(expected_token, supplied_token):
            self._json_response(401, {"error": "unauthorized"})
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = 0
        if content_length <= 0 or content_length > MAX_BODY_BYTES:
            self._json_response(413, {"error": "invalid body size"})
            return
        try:
            raw = self.rfile.read(content_length)
            data = json.loads(raw.decode("utf-8"))
            record, duplicate = self.server.store.store(data)
        except PayloadValidationError as exc:
            self._json_response(400, {"error": str(exc)})
            return
        except RecordConflictError as exc:
            self._json_response(409, {"error": str(exc)})
            return
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json_response(400, {"error": "invalid JSON"})
            return
        except Exception as exc:
            self._json_response(500, {"error": "storage failure", "detail": type(exc).__name__})
            return

        self._json_response(
            200 if duplicate else 201,
            {
                "schema_version": SCHEMA_VERSION,
                "status": "STORED",
                "request_id": record["request_id"],
                "mission_id": record["mission_id"],
                "uav_received_at": record["uav_received_at"],
                "payload_sha256": record["payload_sha256"],
                "duplicate": duplicate,
                "receiver_version": RECEIVER_VERSION,
                "system_release_id": SYSTEM_RELEASE_ID,
            },
        )


def _is_loopback(host: str) -> bool:
    return host in ("127.0.0.1", "localhost", "::1")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("UAV_RESCUE_BIND_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("UAV_RESCUE_PORT", "8080")))
    parser.add_argument(
        "--data-dir",
        default=os.environ.get("UAV_RESCUE_DATA_DIR", "/home/mike/drone_system/data/rescue_records"),
    )
    parser.add_argument("--mqtt-broker", default=os.environ.get("UAV_RESCUE_MQTT_BROKER", "localhost"))
    parser.add_argument("--mqtt-port", type=int, default=int(os.environ.get("UAV_RESCUE_MQTT_PORT", "1883")))
    parser.add_argument(
        "--mqtt-username",
        default=os.environ.get("UAV_RESCUE_MQTT_USERNAME", ""),
    )
    parser.add_argument(
        "--mqtt-password",
        default=os.environ.get("UAV_RESCUE_MQTT_PASSWORD", ""),
    )
    parser.add_argument("--execution-journal-path", default=os.environ.get("UAV_EXECUTION_JOURNAL_PATH", "/home/mike/drone_system/data/mission_state/executions.json"))
    parser.add_argument("--no-mqtt", action="store_true")
    parser.add_argument("--allow-unauthenticated-lan", action="store_true")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    token = os.environ.get("UAV_RESCUE_TOKEN", "")
    if not _is_loopback(args.host) and not token and not args.allow_unauthenticated_lan:
        print(
            "Refusing LAN bind without UAV_RESCUE_TOKEN. "
            "Set a runtime token or explicitly pass --allow-unauthenticated-lan.",
            file=sys.stderr,
        )
        return 2

    store = RescueStore(Path(args.data_dir))
    mqtt_runtime = None
    if not args.no_mqtt:
        try:
            mqtt_runtime = MqttRuntime(
                store,
                args.mqtt_broker,
                args.mqtt_port,
                username=args.mqtt_username,
                password=args.mqtt_password,
            )
            mqtt_runtime.start()
        except Exception as exc:

            if mqtt_runtime is not None:
                try:
                    mqtt_runtime.stop()
                except Exception:
                    pass
            print(
                "[receiver] MQTT unavailable at startup; HTTP storage remains active: {}".format(
                    type(exc).__name__
                ),
                file=sys.stderr,
            )
            mqtt_runtime = None

    runtime_status = (
        mqtt_runtime.health_summary
        if mqtt_runtime is not None
        else lambda: {"mode": "disabled", "connected": False, "queued_messages": 0}
    )
    server = RescueHttpServer(
        (args.host, args.port),
        store,
        token,
        runtime_status=runtime_status,
        collection_context_provider=JournalCollectionContextProvider(args.execution_journal_path),
    )

    def stop_server(_signum=None, _frame=None):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop_server)
    signal.signal(signal.SIGINT, stop_server)
    print("[receiver] listening on {}:{}".format(args.host, args.port))
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
        if mqtt_runtime is not None:
            mqtt_runtime.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
