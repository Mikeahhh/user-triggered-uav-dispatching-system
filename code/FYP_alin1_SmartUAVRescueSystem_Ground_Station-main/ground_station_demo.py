from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Mapping

from priority_scheduler import order_pending_events, schedule_metrics


DEMO_BANNER = "LOCAL DEMO — NO FIREBASE / NO MQTT / NO PHYSICAL UAV"
DEMO_DATA_NOTICE = "SYNTHETIC DATA — NOT EXPERIMENTAL EVIDENCE"
DEMO_BROKER_LABEL = "Broker: disabled in local demo"
DEMO_DATABASE_STATUS = "Local synthetic records (Firebase disabled)"
DEMO_LOCATION_LABEL = "Location: hidden in demo screenshot"
DEMO_RAW_RECORDS_NOTICE = (
    "RAW RECORDS — READ-ONLY SOURCE/SUPPLEMENTARY DATA — NO DIRECT DISPATCH"
)
DEMO_CONTACT_LABEL = "SYNTHETIC CONTACT — NOT DIALABLE"
DEMO_EMERGENCY_CONTACT_LABEL = "NOT_AVAILABLE"


DEMO_NOW_MS = 1_788_580_860_000
DEMO_TEST_ONLY_WAIT_THRESHOLD_MS = 60_000
DEMO_TEST_ONLY_LOCATION_TIMEOUT_MS = 30_000
DEMO_TEST_PARAMETER_NOTICE = (
    "TEST-ONLY THRESHOLDS — NOT OPERATIONAL OR VALIDATED VALUES"
)

DEMO_BOOKING_EVENT_ID = "rescue__event_timeout__demo_event_001"
DEMO_SOS_EVENT_ID = "sos__demo_sos_001"
DEMO_QUICK_EVENT_ID = (
    "rescue__quick_location_timeout__demo_session_001__1788580710000"
)
DEMO_EXPECTED_ORDER = (
    DEMO_BOOKING_EVENT_ID,
    DEMO_QUICK_EVENT_ID,
    DEMO_SOS_EVENT_ID,
)


_DEMO_USERS: Dict[str, Dict[str, Any]] = {
    "DEMO_USER": {
        "profile": {
            "phone": DEMO_CONTACT_LABEL,
            "emergency_contacts": [],
            "emergency_contact_status": DEMO_EMERGENCY_CONTACT_LABEL,
            "demo_only": True,
        },
        "QuickStartSessions": {
            "demo_session_001": {
                "startTime": "2026-09-05T03:53:00.000Z",
                "endTime": None,
                "status": "ACTIVE",
                "demo_read_only": True,
                "direct_dispatch_allowed": False,
                "points": {
                    "point_1": {
                        "latitude": 0.0010,
                        "longitude": 0.0010,
                        "timestamp": 1_788_580_380_000,
                        "timestampISO": "2026-09-05T03:53:00.000Z",
                    },
                    "point_2": {
                        "latitude": 0.0016,
                        "longitude": 0.0018,
                        "timestamp": 1_788_580_440_000,
                        "timestampISO": "2026-09-05T03:54:00.000Z",
                    },


                    "point_3": {
                        "latitude": 0.0016,
                        "longitude": 0.0018,
                        "timestamp": 1_788_580_620_000,
                        "timestampISO": "2026-09-05T03:57:00.000Z",
                    },
                    "point_4": {
                        "latitude": 0.0016,
                        "longitude": 0.0018,
                        "timestamp": 1_788_580_680_000,
                        "timestampISO": "2026-09-05T03:58:00.000Z",
                    },
                },
            }
        },
        "booked_events": {
            "demo_event_001": {
                "date": "2026-09-05",
                "startTime": "11:50",
                "endTime": "11:55",
                "expectedEndAtMs": 1_788_580_500_000,
                "title": "Synthetic Event Booking",
                "demo_read_only": True,
                "direct_dispatch_allowed": False,
                "waypoints": [
                    {"latitude": 0.0010, "longitude": 0.0010},
                    {"latitude": 0.0015, "longitude": 0.0021},
                    {"latitude": 0.0022, "longitude": 0.0014},
                ],
            },
            "demo_event_pending_002": {
                "date": "2026-09-05",
                "startTime": "11:52",
                "endTime": "11:56",
                "expectedEndAtMs": 1_788_580_560_000,
                "title": "Synthetic Pending Alert Source",
                "demo_read_only": True,
                "direct_dispatch_allowed": False,
                "waypoints": [
                    {"latitude": 0.0011, "longitude": 0.0011},
                    {"latitude": 0.0014, "longitude": 0.0017},
                ],
            },
        },
        "rescue_requests": {
            "demo_sos_001": {
                "latitude": 0.0017,
                "longitude": 0.0016,
                "status": "PENDING",
                "timestamp": 1_788_580_805_000,
                "demo_search_points": 19,
                "demo_read_only": True,
                "direct_dispatch_allowed": False,
            }
        },
        "rescue_alerts": {
            "event_timeout__demo_event_001": {
                "alert_id": "event_timeout__demo_event_001",
                "user_id": "DEMO_USER",
                "trigger_type": "EVENT_BOOKING_TIMEOUT",
                "primary_record_type": "booked_events",
                "primary_record_id": "demo_event_001",
                "abnormal_since_ms": 1_788_580_500_000,
                "created_at_ms": 1_788_580_550_000,
                "stage": "RESOLUTION",
                "user_contact_result": "SAFETY_UNCONFIRMED",
                "emergency_contact_result": "NOT_AVAILABLE",
                "status": "CONVERTED",
                "demo_only": True,
            },
            "quick_location_timeout__demo_session_001__1788580710000": {
                "alert_id": (
                    "quick_location_timeout__demo_session_001__1788580710000"
                ),
                "user_id": "DEMO_USER",
                "trigger_type": "QUICK_START_LOCATION_TIMEOUT",
                "primary_record_type": "QuickStartSessions",
                "primary_record_id": "demo_session_001",
                "abnormal_since_ms": 1_788_580_710_000,
                "sample_at_ms": 1_788_580_680_000,
                "timeout_ms": DEMO_TEST_ONLY_LOCATION_TIMEOUT_MS,
                "episode_version": 1,
                "episode_started_at_ms": 1_788_580_710_000,
                "location_episode_started_at_ms": 1_788_580_710_000,
                "policy_epoch": 1,
                "location_status": "TIMED_OUT",
                "created_at_ms": 1_788_580_715_000,
                "stage": "RESOLUTION",
                "user_contact_result": "SAFETY_UNCONFIRMED",
                "emergency_contact_result": "NOT_AVAILABLE",
                "status": "CONVERTED",
                "demo_only": True,
            },
            "event_timeout__demo_event_pending_002": {
                "alert_id": "event_timeout__demo_event_pending_002",
                "user_id": "DEMO_USER",
                "trigger_type": "EVENT_BOOKING_TIMEOUT",
                "primary_record_type": "booked_events",
                "primary_record_id": "demo_event_pending_002",
                "abnormal_since_ms": 1_788_580_560_000,
                "created_at_ms": 1_788_580_565_000,
                "stage": "RESOLUTION",
                "user_contact_result": "SAFETY_UNCONFIRMED",
                "emergency_contact_result": "NOT_AVAILABLE",
                "status": "PENDING",
                "demo_only": True,
            },
        },
        "rescue_events": {
            DEMO_BOOKING_EVENT_ID: {
                "event_id": DEMO_BOOKING_EVENT_ID,
                "user_id": "DEMO_USER",
                "trigger_type": "EVENT_BOOKING_TIMEOUT",
                "primary_record_type": "booked_events",
                "primary_record_id": "demo_event_001",
                "created_at_ms": 1_788_580_800_000,
                "abnormal_since_ms": 1_788_580_500_000,
                "status": "PENDING",
                "source_alert_id": "event_timeout__demo_event_001",
                "search_confirmed_at_ms": 1_788_580_800_000,
                "demo_only": True,
            },
            DEMO_SOS_EVENT_ID: {
                "event_id": DEMO_SOS_EVENT_ID,
                "user_id": "DEMO_USER",
                "trigger_type": "SOS",
                "primary_record_type": "rescue_requests",
                "primary_record_id": "demo_sos_001",
                "created_at_ms": 1_788_580_810_000,
                "abnormal_since_ms": None,
                "status": "PENDING",
                "source_alert_id": "sos_review__demo_sos_001",
                "search_confirmed_at_ms": 1_788_580_810_000,
                "user_contact_result": "SAFETY_UNCONFIRMED",
                "emergency_contact_result": "NOT_AVAILABLE",
                "demo_only": True,
            },
            DEMO_QUICK_EVENT_ID: {
                "event_id": DEMO_QUICK_EVENT_ID,
                "user_id": "DEMO_USER",
                "trigger_type": "QUICK_START_LOCATION_TIMEOUT",
                "primary_record_type": "QuickStartSessions",
                "primary_record_id": "demo_session_001",
                "created_at_ms": 1_788_580_820_000,
                "abnormal_since_ms": 1_788_580_710_000,
                "sample_at_ms": 1_788_580_680_000,
                "timeout_ms": DEMO_TEST_ONLY_LOCATION_TIMEOUT_MS,
                "episode_version": 1,
                "episode_started_at_ms": 1_788_580_710_000,
                "location_episode_started_at_ms": 1_788_580_710_000,
                "policy_epoch": 1,
                "location_status": "TIMED_OUT",
                "status": "PENDING",
                "source_alert_id": (
                    "quick_location_timeout__demo_session_001__1788580710000"
                ),
                "search_confirmed_at_ms": 1_788_580_820_000,
                "demo_only": True,
            },
        },
    }
}


def build_demo_users_data() -> Dict[str, Dict[str, Any]]:


    return deepcopy(_DEMO_USERS)


def _flatten_group(
    users_data: Mapping[str, Any], group_name: str
) -> List[Dict[str, Any]]:
    values: List[Dict[str, Any]] = []
    for user_id in sorted(users_data):
        user_data = users_data[user_id]
        if not isinstance(user_data, Mapping):
            continue
        group = user_data.get(group_name, {})
        if not isinstance(group, Mapping):
            continue
        for record_id in sorted(group):
            record = group[record_id]
            if not isinstance(record, Mapping):
                continue
            copied = deepcopy(dict(record))
            copied.setdefault("user_id", str(user_id))
            values.append(copied)
    return values


def build_demo_pending_alerts(
    users_data: Mapping[str, Any] | None = None,
) -> List[Dict[str, Any]]:


    source = build_demo_users_data() if users_data is None else users_data
    return [
        alert
        for alert in _flatten_group(source, "rescue_alerts")
        if str(alert.get("status", "")).upper() == "PENDING"
    ]


def build_demo_rescue_events(
    users_data: Mapping[str, Any] | None = None,
) -> List[Dict[str, Any]]:


    source = build_demo_users_data() if users_data is None else users_data
    return _flatten_group(source, "rescue_events")


def build_demo_ordered_rescue_events(
    users_data: Mapping[str, Any] | None = None,
    *,
    now_ms: int = DEMO_NOW_MS,
    wait_threshold_ms: int = DEMO_TEST_ONLY_WAIT_THRESHOLD_MS,
) -> List[Dict[str, Any]]:


    ordered = order_pending_events(
        build_demo_rescue_events(users_data),
        now_ms=now_ms,
        wait_threshold_ms=wait_threshold_ms,
    )
    output: List[Dict[str, Any]] = []
    for event in ordered:
        rendered = deepcopy(dict(event))
        rendered.update(
            schedule_metrics(
                event,
                now_ms=now_ms,
                wait_threshold_ms=wait_threshold_ms,
            )
        )
        output.append(rendered)
    return output


def build_demo_onboard_summary() -> str:


    return (
        f"{DEMO_DATA_NOTICE}\n"
        "Request: demo_sos_001\n"
        "Mission: DEMO_USER/demo_sos_001\n"
        "UAV storage: STORED (synthetic)\n"
        "Ground ACK: ACK_PUBLISHED (synthetic)\n"
        f"{DEMO_LOCATION_LABEL}"
    )


def build_demo_drone_status() -> str:


    queue = build_demo_ordered_rescue_events()
    leader = queue[0]["event_id"] if queue else "NONE"
    return (
        "LOCAL DEMO DISPATCH: DISABLED\n"
        f"Synthetic queue leader: {leader}\n"
        "Operator confirmation: preview only\n"
        "NO MQTT publication; no physical UAV or touchdown"
    )
