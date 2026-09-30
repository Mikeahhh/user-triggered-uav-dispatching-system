from __future__ import annotations

import copy
import math
import re
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:
    ZoneInfo = None
    ZoneInfoNotFoundError = Exception


EVENT_BOOKING_TIMEOUT = "EVENT_BOOKING_TIMEOUT"
QUICK_START_INACTIVITY = "QUICK_START_INACTIVITY"
QUICK_START_LOCATION_TIMEOUT = "QUICK_START_LOCATION_TIMEOUT"
SOS = "SOS"

BOOKED_EVENTS = "booked_events"
QUICK_START_SESSIONS = "QuickStartSessions"
RESCUE_REQUESTS = "rescue_requests"

CONTACT_USER = "CONTACT_USER"
CONTACT_EMERGENCY = "CONTACT_EMERGENCY"
RESOLUTION = "RESOLUTION"

PENDING = "PENDING"
CLOSED_SAFE = "CLOSED_SAFE"
CONVERTED = "CONVERTED"
DISPATCHED = "DISPATCHED"
CLOSED = "CLOSED"

SAFE_CONFIRMED = "SAFE_CONFIRMED"
SAFETY_UNCONFIRMED = "SAFETY_UNCONFIRMED"
NOT_AVAILABLE = "NOT_AVAILABLE"

_INVALID_FIREBASE_KEY = re.compile(r"[.#$\[\]/\x00-\x1f\x7f]")
_FIXED_OFFSET = re.compile(r"^([+-])(\d{2}):(\d{2})$")
_EARTH_RADIUS_METRES = 6_371_008.8


class RescueEventError(ValueError):
    pass


class RescueEventValidationError(RescueEventError):
    pass


class RescueAlertTransitionError(RescueEventError):
    pass


def _non_empty(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RescueEventValidationError(f"{field_name} must be a non-empty string")
    return value.strip()


def _firebase_key(value: Any, field_name: str) -> str:
    key = _non_empty(value, field_name)
    if _INVALID_FIREBASE_KEY.search(key):
        raise RescueEventValidationError(
            f"{field_name} contains a character forbidden in a Firebase key"
        )
    return key


def _epoch_ms(value: Any, field_name: str, allow_none: bool = False) -> Optional[int]:
    if value is None and allow_none:
        return None
    if isinstance(value, bool):
        raise RescueEventValidationError(f"{field_name} must be epoch milliseconds")
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        raise RescueEventValidationError(
            f"{field_name} must be epoch milliseconds"
        ) from None
    if not math.isfinite(numeric) or numeric < 0 or not numeric.is_integer():
        raise RescueEventValidationError(f"{field_name} must be a non-negative integer")
    return int(numeric)


def _positive_number(value: Any, field_name: str) -> float:
    if isinstance(value, bool):
        raise RescueEventValidationError(f"{field_name} must be greater than zero")
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        raise RescueEventValidationError(f"{field_name} must be greater than zero") from None
    if not math.isfinite(numeric) or numeric <= 0:
        raise RescueEventValidationError(f"{field_name} must be greater than zero")
    return numeric


def _parse_datetime_ms(value: Any, field_name: str) -> int:
    numeric = None
    if not isinstance(value, bool):
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            numeric = None
    if numeric is not None and math.isfinite(numeric) and numeric >= 0:
        if not numeric.is_integer():
            raise RescueEventValidationError(f"{field_name} must resolve to whole milliseconds")
        return int(numeric)
    if not isinstance(value, str) or not value.strip():
        raise RescueEventValidationError(f"{field_name} is missing or invalid")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise RescueEventValidationError(f"{field_name} is missing or invalid") from None
    if parsed.tzinfo is None:
        raise RescueEventValidationError(f"{field_name} must include a timezone offset")
    return int(parsed.timestamp() * 1000)


def timezone_from_config(value: Any) -> tzinfo:


    if isinstance(value, tzinfo):
        return value
    name = _non_empty(value, "legacy_timezone")
    match = _FIXED_OFFSET.fullmatch(name)
    if match:
        hours = int(match.group(2))
        minutes = int(match.group(3))
        if hours > 23 or minutes > 59:
            raise RescueEventValidationError("legacy_timezone offset is invalid")
        total = timedelta(hours=hours, minutes=minutes)
        if match.group(1) == "-":
            total = -total
        return timezone(total)
    if ZoneInfo is None:
        raise RescueEventValidationError("IANA timezone support is unavailable")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise RescueEventValidationError("legacy_timezone is not recognised") from None


def parse_legacy_expected_end_ms(
    booking: Mapping[str, Any], legacy_timezone: Any
) -> int:


    if not isinstance(booking, Mapping):
        raise RescueEventValidationError("booking must be a mapping")
    date_text = _non_empty(booking.get("date"), "date")
    end_text = _non_empty(booking.get("endTime"), "endTime")
    tz = timezone_from_config(legacy_timezone)
    parsed = None
    for time_format in ("%H:%M", "%H:%M:%S"):
        try:
            parsed = datetime.strptime(
                f"{date_text} {end_text}", f"%Y-%m-%d {time_format}"
            )
            break
        except ValueError:
            continue
    if parsed is None:
        raise RescueEventValidationError("date + endTime cannot be parsed")
    return int(parsed.replace(tzinfo=tz).timestamp() * 1000)


def expected_end_at_ms(
    booking: Mapping[str, Any], legacy_timezone: Any = None
) -> int:
    if not isinstance(booking, Mapping):
        raise RescueEventValidationError("booking must be a mapping")
    if booking.get("expectedEndAtMs") is not None:
        value = _epoch_ms(booking.get("expectedEndAtMs"), "expectedEndAtMs")
        assert value is not None
        return value
    if legacy_timezone is None:
        raise RescueEventValidationError(
            "expectedEndAtMs is missing and GS_EVENT_TIMEZONE is not configured"
        )
    return parse_legacy_expected_end_ms(booking, legacy_timezone)


def event_booking_alert_id(record_id: Any, expected_end_ms: Any = None) -> str:
    legacy = f"event_timeout__{_firebase_key(record_id, 'primary_record_id')}"
    if expected_end_ms is None:
        return legacy
    return f"{legacy}__{_epoch_ms(expected_end_ms, 'expected_end_ms')}"


def _has_booking_episode(records, record_id, expected_end_ms):
    return any(
        isinstance(item, Mapping)
        and item.get("primary_record_type") == BOOKED_EVENTS
        and item.get("primary_record_id") == record_id
        and item.get("abnormal_since_ms") == expected_end_ms
        for item in records.values()
    )


def quick_start_alert_id(record_id: Any, abnormal_since_ms: Any) -> str:
    since = _epoch_ms(abnormal_since_ms, "abnormal_since_ms")
    return f"quick_inactivity__{_firebase_key(record_id, 'primary_record_id')}__{since}"


def sos_event_id(request_id: Any) -> str:
    return f"sos__{_firebase_key(request_id, 'primary_record_id')}"


def converted_event_id(alert_id: Any) -> str:
    return f"rescue__{_firebase_key(alert_id, 'alert_id')}"


def _has_primary_event(
    events: Mapping[str, Any], primary_record_type: str, primary_record_id: str
) -> bool:
    for candidate in events.values():
        if not isinstance(candidate, Mapping):
            continue
        if (
            candidate.get("primary_record_type") == primary_record_type
            and candidate.get("primary_record_id") == primary_record_id
        ):
            return True
    return False


def _has_source_alert(events: Mapping[str, Any], alert_id: str) -> bool:
    return any(
        isinstance(candidate, Mapping)
        and candidate.get("source_alert_id") == alert_id
        for candidate in events.values()
    )


def detect_event_booking_timeout(
    user_id: Any,
    record_id: Any,
    booking: Mapping[str, Any],
    now_ms: Any,
    existing_alerts: Optional[Mapping[str, Any]] = None,
    existing_events: Optional[Mapping[str, Any]] = None,
    legacy_timezone: Any = None,
) -> Optional[Dict[str, Any]]:
    user = _firebase_key(user_id, "user_id")
    record = _firebase_key(record_id, "primary_record_id")
    now = _epoch_ms(now_ms, "now_ms")
    assert now is not None
    if isinstance(booking, Mapping) and booking.get("_deleted") is True:
        return None
    end_at = expected_end_at_ms(booking, legacy_timezone)
    if now < end_at:
        return None
    alerts = existing_alerts if isinstance(existing_alerts, Mapping) else {}
    events = existing_events if isinstance(existing_events, Mapping) else {}
    alert_id = event_booking_alert_id(record, end_at)
    if (alert_id in alerts or _has_booking_episode(alerts, record, end_at)
            or _has_booking_episode(events, record, end_at)):
        return None
    return {
        "alert_id": alert_id,
        "user_id": user,
        "trigger_type": EVENT_BOOKING_TIMEOUT,
        "primary_record_type": BOOKED_EVENTS,
        "primary_record_id": record,
        "abnormal_since_ms": end_at,
        "episode_version": 2,
        "expected_end_at_ms": end_at,
        "created_at_ms": now,
        "stage": CONTACT_USER,
        "user_contact_result": None,
        "emergency_contact_result": None,
        "status": PENDING,
    }


def haversine_distance_m(
    latitude_a: Any, longitude_a: Any, latitude_b: Any, longitude_b: Any
) -> float:
    if any(
        isinstance(value, bool)
        for value in (latitude_a, longitude_a, latitude_b, longitude_b)
    ):
        raise RescueEventValidationError("GPS coordinates must be numeric")
    try:
        lat_a = float(latitude_a)
        lon_a = float(longitude_a)
        lat_b = float(latitude_b)
        lon_b = float(longitude_b)
    except (TypeError, ValueError):
        raise RescueEventValidationError("GPS coordinates must be numeric") from None
    values = (lat_a, lon_a, lat_b, lon_b)
    if not all(math.isfinite(value) for value in values):
        raise RescueEventValidationError("GPS coordinates must be finite")
    if not (-90 <= lat_a <= 90 and -90 <= lat_b <= 90):
        raise RescueEventValidationError("latitude is outside [-90, 90]")
    if not (-180 <= lon_a <= 180 and -180 <= lon_b <= 180):
        raise RescueEventValidationError("longitude is outside [-180, 180]")
    lat1 = math.radians(lat_a)
    lat2 = math.radians(lat_b)
    delta_lat = lat2 - lat1
    delta_lon = math.radians(lon_b - lon_a)
    hav = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_METRES * math.asin(min(1.0, math.sqrt(hav)))


def _point_timestamp_ms(point: Mapping[str, Any]) -> int:
    if point.get("timestamp") is not None:
        value = _epoch_ms(point.get("timestamp"), "point.timestamp")
        assert value is not None
        return value
    return _parse_datetime_ms(point.get("timestampISO"), "point.timestampISO")


def valid_gps_points(
    points: Any,
    min_timestamp_ms: Any = None,
    max_timestamp_ms: Any = None,
) -> Tuple[List[Dict[str, Any]], int]:


    minimum = (
        _epoch_ms(min_timestamp_ms, "min_timestamp_ms")
        if min_timestamp_ms is not None
        else None
    )
    maximum = (
        _epoch_ms(max_timestamp_ms, "max_timestamp_ms")
        if max_timestamp_ms is not None
        else None
    )
    if minimum is not None and maximum is not None and minimum > maximum:
        raise RescueEventValidationError("session start time cannot be later than now_ms")
    if isinstance(points, Mapping):
        source: Iterable[Tuple[str, Any]] = (
            (str(key), value) for key, value in points.items()
        )
    elif isinstance(points, list):
        source = ((f"{index:020d}", value) for index, value in enumerate(points))
    elif points is None:
        source = []
    else:
        raise RescueEventValidationError("points must be a mapping or list")
    valid_with_order: List[Tuple[int, str, Dict[str, Any]]] = []
    invalid_count = 0
    for order_key, point in source:
        if not isinstance(point, Mapping):
            invalid_count += 1
            continue
        try:
            raw_latitude = point.get("latitude")
            raw_longitude = point.get("longitude")


            haversine_distance_m(
                raw_latitude,
                raw_longitude,
                raw_latitude,
                raw_longitude,
            )
            latitude = float(raw_latitude)
            longitude = float(raw_longitude)
            point_time = _point_timestamp_ms(point)
            if minimum is not None and point_time < minimum:
                raise RescueEventValidationError("point timestamp predates session start")
            if maximum is not None and point_time > maximum:
                raise RescueEventValidationError("point timestamp is later than now_ms")
        except (RescueEventValidationError, TypeError, ValueError):
            invalid_count += 1
            continue
        valid_with_order.append(
            (
                point_time,
                order_key,
                {
                    "latitude": latitude,
                    "longitude": longitude,
                    "timestamp_ms": point_time,
                },
            )
        )
    valid_with_order.sort(key=lambda item: (item[0], item[1]))
    valid = [item[2] for item in valid_with_order]
    return valid, invalid_count


def session_gps_points(session: Mapping[str, Any], now_ms: Any):

    if not isinstance(session, Mapping):
        raise RescueEventValidationError("session must be a mapping")
    start = None
    if session.get("startTime") is not None:
        start = _parse_datetime_ms(session["startTime"], "session.startTime")
    return valid_gps_points(
        session.get("points"), min_timestamp_ms=start, max_timestamp_ms=now_ms
    )


def quick_start_inactivity_state(
    session: Mapping[str, Any], movement_threshold_m: Any, now_ms: Any = None
) -> Dict[str, Any]:


    if not isinstance(session, Mapping):
        raise RescueEventValidationError("session must be a mapping")
    movement_threshold = _positive_number(
        movement_threshold_m, "movement_threshold_m"
    )
    now = _epoch_ms(now_ms, "now_ms") if now_ms is not None else None
    points, ignored_count = session_gps_points(session, now)
    if not points:
        return {
            "last_movement_at_ms": None,
            "valid_gps_point_count": 0,
            "ignored_gps_point_count": ignored_count,
            "missing_valid_gps_point": True,
        }
    anchor = points[0]
    last_movement_at = anchor["timestamp_ms"]
    for point in points[1:]:
        distance = haversine_distance_m(
            anchor["latitude"],
            anchor["longitude"],
            point["latitude"],
            point["longitude"],
        )
        if distance >= movement_threshold:
            anchor = point
            last_movement_at = point["timestamp_ms"]
    return {
        "last_movement_at_ms": last_movement_at,
        "valid_gps_point_count": len(points),
        "ignored_gps_point_count": ignored_count,
        "missing_valid_gps_point": False,
    }


def detect_quick_start_inactivity(
    user_id: Any,
    record_id: Any,
    session: Mapping[str, Any],
    now_ms: Any,
    movement_threshold_m: Any,
    inactivity_threshold_ms: Any,
    existing_alerts: Optional[Mapping[str, Any]] = None,
    existing_events: Optional[Mapping[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    if not isinstance(session, Mapping):
        raise RescueEventValidationError("session must be a mapping")
    if str(session.get("status", "")).upper() != "ACTIVE":
        return None
    user = _firebase_key(user_id, "user_id")
    record = _firebase_key(record_id, "primary_record_id")
    now = _epoch_ms(now_ms, "now_ms")
    threshold = _positive_number(inactivity_threshold_ms, "inactivity_threshold_ms")
    assert now is not None
    state = quick_start_inactivity_state(session, movement_threshold_m, now)
    abnormal_since = state["last_movement_at_ms"]
    if abnormal_since is None:
        return None
    if now - abnormal_since < threshold:
        return None
    alert_id = quick_start_alert_id(record, abnormal_since)
    alerts = existing_alerts if isinstance(existing_alerts, Mapping) else {}
    events = existing_events if isinstance(existing_events, Mapping) else {}


    if alert_id in alerts or _has_source_alert(events, alert_id):
        return None
    alert = {
        "alert_id": alert_id,
        "user_id": user,
        "trigger_type": QUICK_START_INACTIVITY,
        "primary_record_type": QUICK_START_SESSIONS,
        "primary_record_id": record,
        "abnormal_since_ms": abnormal_since,
        "created_at_ms": now,
        "stage": CONTACT_USER,
        "user_contact_result": None,
        "emergency_contact_result": None,
        "status": PENDING,
        "missing_valid_gps_point": state["missing_valid_gps_point"],
        "valid_gps_point_count": state["valid_gps_point_count"],
        "ignored_gps_point_count": state["ignored_gps_point_count"],
    }
    return alert


def sos_alert_id(request_id: Any) -> str:
    return f"sos_review__{_firebase_key(request_id, 'primary_record_id')}"


def require_sos_verification(event: Mapping[str, Any]) -> None:
    if event.get("trigger_type") != SOS:
        return
    record_id = _firebase_key(event.get("primary_record_id"), "primary_record_id")
    confirmed = _epoch_ms(event.get("search_confirmed_at_ms"), "search_confirmed_at_ms")
    created = _epoch_ms(event.get("created_at_ms"), "created_at_ms")
    if (event.get("source_alert_id") != sos_alert_id(record_id)
            or confirmed != created
            or event.get("user_contact_result") != SAFETY_UNCONFIRMED
            or event.get("emergency_contact_result") not in {SAFETY_UNCONFIRMED, NOT_AVAILABLE}):
        raise RescueAlertTransitionError("SOS requires completed contact checks and operator search confirmation")


def detect_pending_sos(
    user_id: Any,
    request_id: Any,
    request: Mapping[str, Any],
    now_ms: Any,
    existing_events: Optional[Mapping[str, Any]] = None,
    existing_alerts: Optional[Mapping[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    if not isinstance(request, Mapping):
        raise RescueEventValidationError("SOS request must be a mapping")
    if str(request.get("status", "")).upper() != PENDING:
        return None
    user = _firebase_key(user_id, "user_id")
    record = _firebase_key(request_id, "primary_record_id")
    now = _epoch_ms(now_ms, "now_ms")
    alerts = existing_alerts if isinstance(existing_alerts, Mapping) else {}
    events = existing_events if isinstance(existing_events, Mapping) else {}
    alert_id = sos_alert_id(record)
    if alert_id in alerts or _has_source_alert(events, alert_id):
        return None
    for event in events.values():
        if (isinstance(event, Mapping) and event.get("primary_record_type") == RESCUE_REQUESTS
                and event.get("primary_record_id") == record
                and (event.get("status") != PENDING or event.get("execution"))):
            return None
    return {
        "alert_id": alert_id,
        "user_id": user,
        "trigger_type": SOS,
        "primary_record_type": RESCUE_REQUESTS,
        "primary_record_id": record,
        "created_at_ms": now,
        "abnormal_since_ms": now,
        "stage": CONTACT_USER,
        "user_contact_result": None,
        "emergency_contact_result": None,
        "status": PENDING,
    }


def _pending_alert(alert: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(alert, Mapping):
        raise RescueEventValidationError("alert must be a mapping")
    result = copy.deepcopy(dict(alert))
    _firebase_key(result.get("alert_id"), "alert_id")
    _firebase_key(result.get("user_id"), "user_id")
    if result.get("status") != PENDING:
        raise RescueAlertTransitionError("only a PENDING alert can be handled")
    return result


def record_user_contact(
    alert: Mapping[str, Any], result: str, emergency_contact_available: bool
) -> Dict[str, Any]:
    updated = _pending_alert(alert)
    if updated.get("stage") != CONTACT_USER:
        raise RescueAlertTransitionError("user contact must be recorded first")
    if result not in {SAFE_CONFIRMED, SAFETY_UNCONFIRMED}:
        raise RescueEventValidationError("unsupported user contact result")
    updated["user_contact_result"] = result
    if result == SAFE_CONFIRMED:
        updated["status"] = CLOSED_SAFE
        updated["stage"] = RESOLUTION
        return updated
    if not isinstance(emergency_contact_available, bool):
        raise RescueEventValidationError("emergency_contact_available must be boolean")
    if emergency_contact_available:
        updated["stage"] = CONTACT_EMERGENCY
    else:
        updated["emergency_contact_result"] = NOT_AVAILABLE
        updated["stage"] = RESOLUTION
    return updated


def record_emergency_contact(
    alert: Mapping[str, Any], result: str, emergency_contact_available: bool
) -> Dict[str, Any]:
    updated = _pending_alert(alert)
    if updated.get("stage") != CONTACT_EMERGENCY:
        raise RescueAlertTransitionError(
            "emergency contact result cannot be recorded before user contact"
        )
    if updated.get("user_contact_result") != SAFETY_UNCONFIRMED:
        raise RescueAlertTransitionError("user safety must first be unconfirmed")
    if not isinstance(emergency_contact_available, bool):
        raise RescueEventValidationError("emergency_contact_available must be boolean")
    if emergency_contact_available:
        if result not in {SAFE_CONFIRMED, SAFETY_UNCONFIRMED}:
            raise RescueEventValidationError(
                "an available emergency contact requires an actual contact result"
            )
    elif result != NOT_AVAILABLE:
        raise RescueEventValidationError(
            "no emergency contact is available; result must be NOT_AVAILABLE"
        )
    updated["emergency_contact_result"] = result
    updated["stage"] = RESOLUTION
    if result == SAFE_CONFIRMED:
        updated["status"] = CLOSED_SAFE
    return updated


def convert_alert_to_rescue_event(
    alert: Mapping[str, Any],
    now_ms: Any,
    search_confirmed: bool,
    existing_events: Optional[Mapping[str, Any]] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:


    updated = _pending_alert(alert)
    if search_confirmed is not True:
        raise RescueAlertTransitionError(
            "search must be explicitly confirmed by the operator"
        )
    if updated.get("stage") != RESOLUTION:
        raise RescueAlertTransitionError("required contact stages are incomplete")
    if updated.get("user_contact_result") != SAFETY_UNCONFIRMED:
        raise RescueAlertTransitionError("user safety has not remained unconfirmed")
    if updated.get("emergency_contact_result") not in {
        SAFETY_UNCONFIRMED,
        NOT_AVAILABLE,
    }:
        raise RescueAlertTransitionError(
            "emergency-contact outcome does not permit conversion"
        )
    alert_id = _firebase_key(updated.get("alert_id"), "alert_id")
    trigger_type = updated.get("trigger_type")
    if trigger_type not in {EVENT_BOOKING_TIMEOUT, QUICK_START_INACTIVITY,
                            QUICK_START_LOCATION_TIMEOUT, SOS}:
        raise RescueEventValidationError("unsupported alert trigger_type")
    primary_record_type = _non_empty(
        updated.get("primary_record_type"), "primary_record_type"
    )
    primary_record_id = _firebase_key(
        updated.get("primary_record_id"), "primary_record_id"
    )
    expected_primary_type = {
        EVENT_BOOKING_TIMEOUT: BOOKED_EVENTS,
        QUICK_START_INACTIVITY: QUICK_START_SESSIONS,
        QUICK_START_LOCATION_TIMEOUT: QUICK_START_SESSIONS,
        SOS: RESCUE_REQUESTS,
    }[trigger_type]
    if primary_record_type != expected_primary_type:
        raise RescueEventValidationError(
            "alert trigger_type does not match primary_record_type"
        )
    abnormal_since = _epoch_ms(
        updated.get("abnormal_since_ms"), "abnormal_since_ms"
    )
    assert abnormal_since is not None
    timeout_snapshot = None
    if trigger_type == SOS:
        canonical_alert_ids = {sos_alert_id(primary_record_id)}
    elif trigger_type == QUICK_START_LOCATION_TIMEOUT:
        from quick_start_freshness import validate_timeout_alert
        timeout_snapshot = validate_timeout_alert(updated, now_ms)
        canonical_alert_ids = {updated["alert_id"]}
    else:
        canonical_alert_ids = (
            {event_booking_alert_id(primary_record_id),
             event_booking_alert_id(primary_record_id, abnormal_since)}
            if trigger_type == EVENT_BOOKING_TIMEOUT
            else {quick_start_alert_id(primary_record_id, abnormal_since)}
        )
    if alert_id not in canonical_alert_ids:
        raise RescueEventValidationError(
            "alert_id is not canonical for its trigger and primary record"
        )
    now = _epoch_ms(now_ms, "now_ms")
    assert now is not None
    if abnormal_since > now:
        raise RescueEventValidationError(
            "abnormal_since_ms cannot be later than conversion now_ms"
        )
    event_id = sos_event_id(primary_record_id) if trigger_type == SOS else converted_event_id(alert_id)
    events = existing_events if isinstance(existing_events, Mapping) else {}
    old_event = events.get(event_id)
    replace_unverified_sos = (
        trigger_type == SOS and isinstance(old_event, Mapping)
        and old_event.get("status") == PENDING and not old_event.get("execution")
        and not old_event.get("source_alert_id")
        and not old_event.get("search_confirmed_at_ms")
    )
    if (event_id in events and not replace_unverified_sos) or _has_source_alert(events, alert_id):
        raise RescueAlertTransitionError("the alert already has a rescue event")
    if trigger_type == QUICK_START_LOCATION_TIMEOUT and any(
        isinstance(event, Mapping)
        and event.get("primary_record_type") == QUICK_START_SESSIONS
        and event.get("primary_record_id") == primary_record_id
        and event.get("status") != CLOSED
        for event in events.values()
    ):
        raise RescueAlertTransitionError("this Quick Start session already has an unfinished rescue event")
    updated["status"] = CONVERTED
    updated["stage"] = RESOLUTION
    event = {
        "event_id": event_id,
        "user_id": _firebase_key(updated.get("user_id"), "user_id"),
        "trigger_type": trigger_type,
        "primary_record_type": primary_record_type,
        "primary_record_id": primary_record_id,
        "created_at_ms": now,
        "abnormal_since_ms": abnormal_since,
        "status": PENDING,
        "source_alert_id": alert_id,
        "search_confirmed_at_ms": now,
        "user_contact_result": updated["user_contact_result"],
        "emergency_contact_result": updated["emergency_contact_result"],
    }
    if trigger_type == EVENT_BOOKING_TIMEOUT and updated.get("episode_version") == 2:
        event["primary_expected_end_at_ms"] = abnormal_since
    if timeout_snapshot is not None:
        event.update(timeout_snapshot)
    return updated, event


def scan_user_records(
    user_id: Any,
    user_info: Mapping[str, Any],
    now_ms: Any,
    movement_threshold_m: Any = None,
    inactivity_threshold_ms: Any = None,
    legacy_timezone: Any = None,
    quick_start_timeout_ms: Any = None,
) -> Dict[str, Any]:


    user = _firebase_key(user_id, "user_id")
    if not isinstance(user_info, Mapping):
        raise RescueEventValidationError("user_info must be a mapping")
    alerts: Dict[str, Any] = copy.deepcopy(
        dict(user_info.get("rescue_alerts", {}))
        if isinstance(user_info.get("rescue_alerts"), Mapping)
        else {}
    )
    events: Dict[str, Any] = copy.deepcopy(
        dict(user_info.get("rescue_events", {}))
        if isinstance(user_info.get("rescue_events"), Mapping)
        else {}
    )
    new_alerts: Dict[str, Dict[str, Any]] = {}
    new_events: Dict[str, Dict[str, Any]] = {}
    monitoring_updates: Dict[str, Dict[str, Any]] = {}
    alert_updates: Dict[str, Dict[str, Any]] = {}
    issues: List[Dict[str, str]] = []

    def report(record_type: str, record_id: Any, exc: Exception) -> None:
        issues.append(
            {
                "user_id": user,
                "record_type": record_type,
                "record_id": str(record_id),
                "error": str(exc),
            }
        )

    bookings = user_info.get(BOOKED_EVENTS, {})
    if isinstance(bookings, Mapping):
        for record_id, booking in sorted(bookings.items(), key=lambda item: str(item[0])):
            try:
                alert = detect_event_booking_timeout(
                    user,
                    record_id,
                    booking,
                    now_ms,
                    alerts,
                    events,
                    legacy_timezone,
                )
                if alert is not None:
                    alerts[alert["alert_id"]] = alert
                    new_alerts[alert["alert_id"]] = alert
            except RescueEventError as exc:
                report(BOOKED_EVENTS, record_id, exc)

    sessions = user_info.get(QUICK_START_SESSIONS, {})
    monitoring = user_info.get("quick_start_monitoring", {})
    if isinstance(sessions, Mapping):
        for record_id, session in sorted(sessions.items(), key=lambda item: str(item[0])):
            if not isinstance(session, Mapping):
                continue
            active = str(session.get("status", "")).upper() == "ACTIVE"
            if not active and (not isinstance(monitoring, Mapping) or record_id not in monitoring):
                continue
            if quick_start_timeout_ms is None:
                report(
                    QUICK_START_SESSIONS,
                    record_id,
                    RescueEventValidationError(
                        "GS_T_LOCATION_UPDATE_SECONDS is required; legacy inactivity settings are not used"
                    ),
                )
                continue
            try:
                from quick_start_freshness import evaluate_quick_start_freshness
                if not isinstance(monitoring, Mapping):
                    raise RescueEventValidationError("quick_start_monitoring must be an object")
                result = evaluate_quick_start_freshness(
                    user, record_id, session, now_ms, quick_start_timeout_ms,
                    monitoring.get(record_id), alerts, events,
                )
                monitoring_updates[record_id] = result["monitoring"]
                alert_updates.update(result["alert_updates"])
                alert = result["alert"]
                if alert is not None:
                    alerts[alert["alert_id"]] = alert
                    new_alerts[alert["alert_id"]] = alert
            except RescueEventError as exc:
                report(QUICK_START_SESSIONS, record_id, exc)

    requests = user_info.get(RESCUE_REQUESTS, {})
    if isinstance(requests, Mapping):
        for record_id, request in sorted(requests.items(), key=lambda item: str(item[0])):
            try:
                alert = detect_pending_sos(user, record_id, request, now_ms, events, alerts)
                if alert is not None:
                    alerts[alert["alert_id"]] = alert
                    new_alerts[alert["alert_id"]] = alert
            except RescueEventError as exc:
                report(RESCUE_REQUESTS, record_id, exc)

    return {"alerts": new_alerts, "events": new_events, "issues": issues,
            "monitoring": monitoring_updates, "alert_updates": alert_updates}
