import copy
import hashlib
import json
import re
from collections.abc import Mapping

from rescue_event_manager import (
    CONTACT_USER, PENDING, QUICK_START_LOCATION_TIMEOUT,
    QUICK_START_SESSIONS, RescueEventValidationError, _epoch_ms, _firebase_key,
    _parse_datetime_ms, valid_gps_points,
)


MONITORING_GROUP = "quick_start_monitoring"
MONITORING_VERSION = 1
TIMEOUT_EPISODE_VERSION = 1
_HASH = re.compile(r"^[0-9a-f]{64}$")
MAX_SAFE_INTEGER = 9_007_199_254_740_991
ALERT_DETECTION_FIELDS = frozenset({
    "sample_at_ms", "timeout_ms", "abnormal_since_ms", "location_status",
    "location_latest_sample_at_ms", "location_recovered_at_ms",
    "location_episode_count", "location_episode_started_at_ms",
    "ignored_gps_point_count", "valid_gps_point_count", "policy_epoch",
})


def location_timeout_alert_id(record_id, first_onset_ms):
    record = _firebase_key(record_id, "primary_record_id")
    onset = _epoch_ms(first_onset_ms, "episode_started_at_ms")
    return f"quick_location_timeout__{record}__{onset}"


def _positive_ms(value, field):
    parsed = _epoch_ms(value, field)
    if not 0 < parsed <= MAX_SAFE_INTEGER:
        raise RescueEventValidationError(f"{field} must be positive safe-integer milliseconds")
    return parsed


def sample_identity(point):

    canonical = json.dumps(
        {key: point[key] for key in ("timestamp_ms", "latitude", "longitude")},
        sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _samples(session, now, rejected):
    start = (_parse_datetime_ms(session["startTime"], "session.startTime")
             if session.get("startTime") is not None else None)
    raw = session.get("points")
    if raw is None:
        source = []
    elif isinstance(raw, Mapping):
        source = list(raw.values())
    elif isinstance(raw, list):
        source = raw
    else:
        raise RescueEventValidationError("points must be a mapping or list")
    accepted, ignored = [], 0
    for value in source:

        valid, bad = valid_gps_points([value])
        if bad or not valid:
            ignored += 1
            continue
        point = valid[0]
        if not 0 < point["timestamp_ms"] <= MAX_SAFE_INTEGER:
            ignored += 1
            continue
        identity = sample_identity(point)
        if point["timestamp_ms"] > now:
            rejected.add(identity)
        if (identity in rejected or point["timestamp_ms"] <= 0
                or start is not None and point["timestamp_ms"] < start):
            ignored += 1
            continue
        accepted.append(point)
    accepted.sort(key=lambda item: (item["timestamp_ms"], sample_identity(item)))
    return accepted, ignored, start


def _matching_records(records, user_id, session_id, identity_field):
    matches = []
    for key, record in records.items():
        if (not isinstance(record, Mapping)
                or record.get("primary_record_type") != QUICK_START_SESSIONS
                or record.get("primary_record_id") != session_id):
            continue
        if record.get("user_id") != user_id or record.get(identity_field) != key:
            raise RescueEventValidationError("existing Quick Start workflow identity is inconsistent")
        matches.append(record)
    return sorted(matches, key=lambda item: str(item[identity_field]))


def _initial_state(user_id, session_id, now, timeout, start):
    return {
        "schema_version": MONITORING_VERSION,
        "trigger_type": QUICK_START_LOCATION_TIMEOUT,
        "user_id": user_id, "session_id": session_id,
        "session_start_at_ms": start,
        "configured_timeout_ms": timeout, "effective_timeout_ms": timeout,
        "policy_epoch": 1, "policy_started_at_ms": now,
        "configuration_pending": False,
        "monitoring_status": "NOT_READY", "episode_active": False,
        "episode_count": 0, "recovery_seen": False,
        "rejected_future_samples": [],
    }


def _validate_state(state, user_id, session_id, start, now):
    if (type(state.get("schema_version")) is not int
            or state.get("schema_version") != MONITORING_VERSION
            or state.get("trigger_type") != QUICK_START_LOCATION_TIMEOUT
            or state.get("user_id") != user_id or state.get("session_id") != session_id):
        raise RescueEventValidationError("monitoring identity/version is inconsistent")
    if state.get("session_start_at_ms") != start:
        raise RescueEventValidationError("session start changed; monitoring requires a new session identity")
    for key in ("last_evaluated_at_ms", "latest_sample_at_ms"):
        if state.get(key) is not None and _epoch_ms(state[key], key) > now:
            raise RescueEventValidationError("monitoring time moved backwards; reconcile the clock before continuing")
    if state.get("latest_sample_at_ms") is not None:
        sample = _positive_ms(state["latest_sample_at_ms"], "latest_sample_at_ms")
        if start is not None and sample < start:
            raise RescueEventValidationError("stored sample predates its session")
        stored = state.get("latest_sample")
        if not isinstance(stored, Mapping) or stored.get("timestamp_ms") != sample:
            raise RescueEventValidationError("stored latest sample/time is inconsistent")
        valid, ignored = valid_gps_points([{
            "latitude": stored.get("latitude"), "longitude": stored.get("longitude"),
            "timestamp": stored.get("timestamp_ms"),
        }], min_timestamp_ms=start, max_timestamp_ms=now)
        if ignored or len(valid) != 1:
            raise RescueEventValidationError("stored latest sample is invalid")
    _positive_ms(state.get("effective_timeout_ms"), "effective_timeout_ms")
    if type(state.get("policy_epoch")) is not int or state["policy_epoch"] < 1:
        raise RescueEventValidationError("monitoring policy epoch is invalid")
    if type(state.get("episode_count")) is not int or state["episode_count"] < 0:
        raise RescueEventValidationError("monitoring episode count is invalid")
    if type(state.get("episode_active")) is not bool:
        raise RescueEventValidationError("monitoring episode state is invalid")
    if state["episode_active"]:
        onset = _epoch_ms(state.get("episode_first_onset_ms"), "episode_first_onset_ms")
        duration = _positive_ms(state.get("episode_timeout_ms"), "episode_timeout_ms")
        if (onset > now or duration != state["effective_timeout_ms"]
                or state["episode_count"] < 1
                or state.get("episode_id") != location_timeout_alert_id(session_id, onset)):
            raise RescueEventValidationError("stored active episode is inconsistent")
    if state.get("monitoring_status") not in {"NOT_READY", "FRESH", "TIMED_OUT", "STOPPED"}:
        raise RescueEventValidationError("monitoring status is invalid")
    rejected = state.get("rejected_future_samples", [])
    if not isinstance(rejected, list) or any(not isinstance(x, str) or not _HASH.fullmatch(x) for x in rejected):
        raise RescueEventValidationError("stored future-sample rejection identities are invalid")
    started = _epoch_ms(state.get("policy_started_at_ms"), "policy_started_at_ms")
    history = state.get("policy_history", [])
    if started > now or not isinstance(history, list):
        raise RescueEventValidationError("stored policy history is inconsistent")


def evaluate_quick_start_freshness(user_id, session_id, session, now_ms,
                                   timeout_ms, monitoring=None,
                                   existing_alerts=None, existing_events=None):


    user_id = _firebase_key(user_id, "user_id")
    session_id = _firebase_key(session_id, "primary_record_id")
    now = _epoch_ms(now_ms, "now_ms")
    if now > MAX_SAFE_INTEGER:
        raise RescueEventValidationError("now_ms exceeds safe-integer milliseconds")
    timeout = _positive_ms(timeout_ms, "quick_start_timeout_ms")
    if not isinstance(session, Mapping):
        raise RescueEventValidationError("session must be a mapping")
    start = (_parse_datetime_ms(session["startTime"], "session.startTime")
             if session.get("startTime") is not None else None)
    if monitoring is not None and not isinstance(monitoring, Mapping):
        raise RescueEventValidationError("monitoring state must be an object")
    state = (copy.deepcopy(dict(monitoring)) if monitoring is not None
             else _initial_state(user_id, session_id, now, timeout, start))
    _validate_state(state, user_id, session_id, start, now)
    state["configured_timeout_ms"] = timeout
    state["configuration_pending"] = timeout != state["effective_timeout_ms"]
    state["last_evaluated_at_ms"] = now
    new_alert, patches = None, {}
    result = {"monitoring": state, "alert": None, "alert_updates": patches}
    if str(session.get("status", "")).upper() != "ACTIVE":
        state["monitoring_status"] = "STOPPED"
        state.setdefault("stopped_at_ms", now)
        return result

    rejected = set(state.get("rejected_future_samples", []))
    points, ignored, _ = _samples(session, now, rejected)
    state["rejected_future_samples"] = sorted(rejected)
    state["valid_gps_point_count"] = len(points)
    state["ignored_gps_point_count"] = ignored
    previous_sample = state.get("latest_sample_at_ms")
    newest = points[-1] if points else None
    advanced = newest is not None and (
        previous_sample is None or newest["timestamp_ms"] > previous_sample)
    if advanced:
        state["latest_sample_at_ms"] = newest["timestamp_ms"]
        state["latest_sample"] = copy.deepcopy(newest)
    sample_at = state.get("latest_sample_at_ms")
    effective = state["effective_timeout_ms"]
    if timeout != effective and (
            sample_at is None or advanced and now - sample_at < min(effective, timeout)):
        history = state.setdefault("policy_history", [])
        history.append({"policy_epoch": state["policy_epoch"], "timeout_ms": effective,
                        "started_at_ms": state["policy_started_at_ms"], "ended_at_ms": now})
        state.update(effective_timeout_ms=timeout, policy_epoch=state["policy_epoch"] + 1,
                     policy_started_at_ms=now)
        effective = timeout
    state["configuration_pending"] = timeout != effective
    if sample_at is None:
        state["monitoring_status"] = "NOT_READY"
        return result
    if sample_at + effective > MAX_SAFE_INTEGER:
        raise RescueEventValidationError("location timeout onset exceeds safe-integer milliseconds")
    state["timeout_since_ms"] = sample_at + effective

    alerts = _matching_records(existing_alerts or {}, user_id, session_id, "alert_id")
    events = _matching_records(existing_events or {}, user_id, session_id, "event_id")
    pending = [a for a in alerts if a.get("status") == PENDING]
    unfinished = [e for e in events if e.get("status") != "CLOSED"]
    new_pending = [a for a in pending if a.get("trigger_type") == QUICK_START_LOCATION_TIMEOUT]
    legacy_pending = [a for a in pending if a.get("trigger_type") != QUICK_START_LOCATION_TIMEOUT]
    orphan_converted = [a for a in alerts if a.get("status") == "CONVERTED"
                        and not any(e.get("source_alert_id") == a["alert_id"] for e in events)]
    recovery = now - sample_at < effective
    if recovery:
        was_timed_out = state["episode_active"]
        state["monitoring_status"] = "FRESH"
        state["recovery_seen"] = True
        if was_timed_out:
            state.update(episode_active=False, recovered_at_ms=now,
                         recovery_sample_at_ms=sample_at)
        for alert in new_pending:
            patch = {"location_status": "RECOVERED", "location_latest_sample_at_ms": sample_at,
                     "valid_gps_point_count": len(points), "ignored_gps_point_count": ignored}
            if was_timed_out or alert.get("location_status") != "RECOVERED":
                patch["location_recovered_at_ms"] = now
            patches[alert["alert_id"]] = patch
        return result

    new_episode = not state["episode_active"]
    if new_episode:
        state.update(episode_active=True, episode_count=state["episode_count"] + 1,
                     episode_first_onset_ms=sample_at + effective,
                     episode_timeout_ms=effective,
                     episode_id=location_timeout_alert_id(session_id, sample_at + effective))
    state["monitoring_status"] = "TIMED_OUT"


    if unfinished or legacy_pending or orphan_converted:
        state["suppression_reason"] = "EXISTING_UNRESOLVED_WORKFLOW"
        return result
    state.pop("suppression_reason", None)
    if new_pending:
        alert = new_pending[0]
        state["linked_alert_id"] = alert["alert_id"]
        count = alert.get("location_episode_count", 1)
        if type(count) is not int or count < 1:
            raise RescueEventValidationError("existing location episode count is invalid")

        if new_episode and monitoring is not None:
            count += 1
        patches[alert["alert_id"]] = {
            "sample_at_ms": sample_at, "timeout_ms": effective,
            "abnormal_since_ms": sample_at + effective,
            "location_status": "TIMED_OUT", "location_latest_sample_at_ms": sample_at,
            "location_episode_count": count,
            "location_episode_started_at_ms": state["episode_first_onset_ms"],
            "policy_epoch": state["policy_epoch"],
            "valid_gps_point_count": len(points), "ignored_gps_point_count": ignored,
        }
    elif new_episode and (not alerts or state.get("recovery_seen")):
        alert_id = state["episode_id"]

        if any(a["alert_id"] == alert_id for a in alerts):
            state["suppression_reason"] = "EXISTING_EPISODE_IDENTITY"
            return result
        new_alert = {
            "alert_id": alert_id, "user_id": user_id,
            "trigger_type": QUICK_START_LOCATION_TIMEOUT,
            "primary_record_type": QUICK_START_SESSIONS, "primary_record_id": session_id,
            "episode_version": TIMEOUT_EPISODE_VERSION,
            "episode_started_at_ms": state["episode_first_onset_ms"],
            "location_episode_started_at_ms": state["episode_first_onset_ms"],
            "sample_at_ms": sample_at, "timeout_ms": effective,
            "abnormal_since_ms": sample_at + effective, "created_at_ms": now,
            "policy_epoch": state["policy_epoch"], "location_status": "TIMED_OUT",
            "location_latest_sample_at_ms": sample_at, "location_episode_count": 1,
            "stage": CONTACT_USER, "status": PENDING,
            "user_contact_result": None, "emergency_contact_result": None,
            "valid_gps_point_count": len(points), "ignored_gps_point_count": ignored,
        }
        state["linked_alert_id"] = alert_id
    else:
        state["suppression_reason"] = "AWAITING_GENUINE_RECOVERY"
    result["alert"] = new_alert
    return result


def validate_timeout_alert(alert, now_ms):

    now = _epoch_ms(now_ms, "now_ms")
    if type(alert.get("episode_version")) is not int or alert["episode_version"] != TIMEOUT_EPISODE_VERSION:
        raise RescueEventValidationError("unsupported location timeout episode version")
    sample = _positive_ms(alert.get("sample_at_ms"), "sample_at_ms")
    timeout = _positive_ms(alert.get("timeout_ms"), "timeout_ms")
    onset = _epoch_ms(alert.get("abnormal_since_ms"), "abnormal_since_ms")
    first = _epoch_ms(alert.get("episode_started_at_ms"), "episode_started_at_ms")
    current_first = _epoch_ms(alert.get("location_episode_started_at_ms"), "location_episode_started_at_ms")
    created = _epoch_ms(alert.get("created_at_ms"), "created_at_ms")
    if onset != sample + timeout or not first <= current_first <= onset <= now or not first <= created <= now:
        raise RescueEventValidationError("location timeout A/T/onset snapshot is inconsistent")
    if alert.get("alert_id") != location_timeout_alert_id(alert.get("primary_record_id"), first):
        raise RescueEventValidationError("location timeout alert identity does not match its episode")
    if alert.get("location_status") not in {"TIMED_OUT", "RECOVERED"}:
        raise RescueEventValidationError("location timeout recovery state is invalid")
    if type(alert.get("policy_epoch")) is not int or alert["policy_epoch"] < 1:
        raise RescueEventValidationError("location timeout policy epoch is invalid")
    if type(alert.get("location_episode_count")) is not int or alert["location_episode_count"] < 1:
        raise RescueEventValidationError("location timeout episode count is invalid")
    latest = _positive_ms(alert.get("location_latest_sample_at_ms"), "location_latest_sample_at_ms")
    if not sample <= latest <= now:
        raise RescueEventValidationError("location timeout latest-sample diagnostic is inconsistent")
    recovery_at = alert.get("location_recovered_at_ms")
    if recovery_at is not None and _epoch_ms(recovery_at, "location_recovered_at_ms") > now:
        raise RescueEventValidationError("location recovery diagnostic is in the future")
    snapshot = {key: copy.deepcopy(alert[key]) for key in (
        "episode_version", "episode_started_at_ms", "location_episode_started_at_ms",
        "sample_at_ms", "timeout_ms", "policy_epoch", "location_status",
        "location_latest_sample_at_ms", "location_episode_count")}
    if recovery_at is not None:
        snapshot["location_recovered_at_ms"] = recovery_at
    return snapshot
