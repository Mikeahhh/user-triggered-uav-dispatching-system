#!/usr/bin/env python3


from __future__ import annotations

import argparse
import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping


CODE_ROOT = Path(__file__).resolve().parents[1]
GROUND_STATION = CODE_ROOT / "FYP_alin1_SmartUAVRescueSystem_Ground_Station-main"
if str(GROUND_STATION) not in sys.path:
    sys.path.insert(0, str(GROUND_STATION))

from priority_scheduler import order_pending_events, schedule_metrics


TEST_NOTICE = "TEST-ONLY SYNTHETIC SCHEDULER SOFTWARE TEST"
PARAMETER_NOTICE = "TEST-ONLY VALUE; NOT AN OPERATIONAL OR VALIDATED THRESHOLD"
BOOKING_EVENT_ID = "rescue__event_timeout__controlled_booking_001"
SOS_EVENT_ID = "sos__controlled_sos_001"
QUICK_EVENT_ID = (
    "rescue__quick_inactivity__controlled_session_001__1788580440000"
)
BASE_CREATED_AT_MS = 1_788_580_800_000
BOOKING_ABNORMAL_SINCE_MS = 1_788_580_500_000
QUICK_ABNORMAL_SINCE_MS = 1_788_580_440_000

EVIDENCE_BOUNDARIES = {
    "synthetic_test_data": True,
    "local_scheduler_software_only": True,
    "firebase": False,
    "mqtt": False,
    "real_mqtt_publish": False,
    "operator_contact_performed": False,
    "police_contact_performed": False,
    "ros_or_flight_control": False,
    "physical_uav": False,
    "physical_dispatch": False,
    "physical_landing": False,
}


def validate_test_only_wait_threshold_ms(value: Any) -> int:


    if isinstance(value, bool):
        raise ValueError("test-only wait threshold must be integer milliseconds")
    try:
        threshold = int(value)
    except (TypeError, ValueError):
        raise ValueError(
            "test-only wait threshold must be integer milliseconds"
        ) from None
    if str(value).strip() != str(threshold) or threshold < 3:
        raise ValueError(
            "test-only wait threshold must be an integer of at least 3 ms"
        )
    return threshold


def build_synthetic_events(wait_threshold_ms: Any) -> List[Dict[str, Any]]:


    threshold = validate_test_only_wait_threshold_ms(wait_threshold_ms)
    sos_offset = max(1, threshold // 6)
    quick_offset = max(sos_offset + 1, threshold // 3)
    if quick_offset >= threshold:
        raise ValueError("test-only threshold is too small for the fixture")

    booking = {
        "event_id": BOOKING_EVENT_ID,
        "user_id": "CONTROLLED_USER",
        "trigger_type": "EVENT_BOOKING_TIMEOUT",
        "primary_record_type": "booked_events",
        "primary_record_id": "controlled_booking_001",
        "created_at_ms": BASE_CREATED_AT_MS,
        "abnormal_since_ms": BOOKING_ABNORMAL_SINCE_MS,
        "status": "PENDING",
        "source_alert_id": "event_timeout__controlled_booking_001",
        "search_confirmed_at_ms": BASE_CREATED_AT_MS,
        "test_only": True,
    }
    sos = {
        "event_id": SOS_EVENT_ID,
        "user_id": "CONTROLLED_USER",
        "trigger_type": "SOS",
        "primary_record_type": "rescue_requests",
        "primary_record_id": "controlled_sos_001",
        "created_at_ms": BASE_CREATED_AT_MS + sos_offset,
        "abnormal_since_ms": None,
        "status": "PENDING",
        "source_alert_id": "sos_review__controlled_sos_001",
        "search_confirmed_at_ms": BASE_CREATED_AT_MS + sos_offset,
        "user_contact_result": "SAFETY_UNCONFIRMED",
        "emergency_contact_result": "NOT_AVAILABLE",
        "test_only": True,
    }
    quick = {
        "event_id": QUICK_EVENT_ID,
        "user_id": "CONTROLLED_USER",
        "trigger_type": "QUICK_START_INACTIVITY",
        "primary_record_type": "QuickStartSessions",
        "primary_record_id": "controlled_session_001",
        "created_at_ms": BASE_CREATED_AT_MS + quick_offset,
        "abnormal_since_ms": QUICK_ABNORMAL_SINCE_MS,
        "status": "PENDING",
        "source_alert_id": (
            "quick_inactivity__controlled_session_001__1788580440000"
        ),
        "search_confirmed_at_ms": BASE_CREATED_AT_MS + quick_offset,
        "test_only": True,
    }
    return [quick, sos, booking]


def ordered_snapshot(
    events: Iterable[Mapping[str, Any]],
    *,
    now_ms: int,
    wait_threshold_ms: int,
) -> Dict[str, Any]:


    ordered = order_pending_events(events, now_ms, wait_threshold_ms)
    rows = []
    for position, event in enumerate(ordered, start=1):
        metrics = schedule_metrics(event, now_ms, wait_threshold_ms)
        rows.append(
            {
                "position": position,
                "event_id": event["event_id"],
                "user_id": event["user_id"],
                "trigger_type": event["trigger_type"],
                "created_at_ms": event["created_at_ms"],
                "abnormal_since_ms": event.get("abnormal_since_ms"),
                **metrics,
            }
        )
    return {
        "now_ms": now_ms,
        "wait_threshold_ms": wait_threshold_ms,
        "ordered_event_ids": [row["event_id"] for row in rows],
        "ordered_events": rows,
    }


def simulate_operator_confirmed_stub_dispatch(
    ordered_at_threshold: Mapping[str, Any],
    *,
    operator_confirmed: bool,
) -> Dict[str, Any]:


    ordered_ids = list(ordered_at_threshold.get("ordered_event_ids", []))
    if not ordered_ids:
        raise AssertionError("controlled queue is empty")
    selected_event_id = ordered_ids[0]
    if not operator_confirmed:
        return {
            "selected_rescue_event_id": selected_event_id,
            "operator_confirmation_result": "CANCELLED_TEST_STUB",
            "publish_transport": "IN_MEMORY_STUB",
            "publish_request_result": "NOT_ATTEMPTED",
            "event_status_before": "PENDING",
            "event_status_after": "PENDING",
            "dispatched_mission_id": None,
            "physical_dispatch": False,
        }
    return {
        "selected_rescue_event_id": selected_event_id,
        "operator_confirmation_result": "CONFIRMED_TEST_STUB",
        "publish_transport": "IN_MEMORY_STUB",
        "publish_request_result": "SUCCESS_TEST_STUB",
        "event_status_before": "PENDING",
        "event_status_after": "DISPATCHED",
        "dispatched_mission_id": f"TEST_ONLY/{selected_event_id}",
        "physical_dispatch": False,
    }


def build_controlled_artifacts(
    wait_threshold_ms: Any,
    *,
    operator_confirmed: bool,
) -> Dict[str, Dict[str, Any]]:


    threshold = validate_test_only_wait_threshold_ms(wait_threshold_ms)
    now_before = BASE_CREATED_AT_MS + threshold - 1
    now_at = BASE_CREATED_AT_MS + threshold
    events = build_synthetic_events(threshold)
    events_before = deepcopy(events)

    before = ordered_snapshot(
        events,
        now_ms=now_before,
        wait_threshold_ms=threshold,
    )
    at = ordered_snapshot(
        events,
        now_ms=now_at,
        wait_threshold_ms=threshold,
    )
    if events != events_before:
        raise AssertionError("scheduler mutated the synthetic input")

    expected_before = [QUICK_EVENT_ID, BOOKING_EVENT_ID, SOS_EVENT_ID]
    expected_at = [BOOKING_EVENT_ID, QUICK_EVENT_ID, SOS_EVENT_ID]
    if before["ordered_event_ids"] != expected_before:
        raise AssertionError("unexpected order one millisecond before upgrade")
    if at["ordered_event_ids"] != expected_at:
        raise AssertionError("unexpected order exactly at upgrade threshold")
    if [row["effective_priority"] for row in before["ordered_events"]] != [
        "NORMAL",
        "NORMAL",
        "NORMAL",
    ]:
        raise AssertionError("unexpected effective priorities before threshold")
    if [row["effective_priority"] for row in at["ordered_events"]] != [
        "HIGH",
        "NORMAL",
        "NORMAL",
    ]:
        raise AssertionError("unexpected effective priorities at threshold")

    dispatch = simulate_operator_confirmed_stub_dispatch(
        at,
        operator_confirmed=operator_confirmed,
    )
    if operator_confirmed and dispatch["event_status_after"] != "DISPATCHED":
        raise AssertionError("confirmed stub dispatch did not reach DISPATCHED")

    configuration = {
        "test_name": "controlled_three_trigger_scheduler_upgrade",
        "test_notice": TEST_NOTICE,
        "parameter_notice": PARAMETER_NOTICE,
        "test_only_wait_threshold_ms": threshold,
        "now_before_threshold_ms": now_before,
        "now_at_threshold_ms": now_at,
        "operator_confirmation_mode": "IN_MEMORY_TEST_STUB",
        "D_move": "NOT_EXERCISED_BY_SCHEDULER_TEST",
        "T_inactive": "NOT_EXERCISED_BY_SCHEDULER_TEST",
        "boundaries": dict(EVIDENCE_BOUNDARIES),
    }
    inputs = {
        "test_notice": TEST_NOTICE,
        "input_order_is_deliberately_unsorted": True,
        "events": deepcopy(events),
    }
    summary = {
        "result": "PASS",
        "test_name": configuration["test_name"],
        "test_notice": TEST_NOTICE,
        "verified": [
            "SOS is high before ordinary events upgrade",
            "ordinary trigger types share the same initial priority",
            "ordinary events use longest abnormal duration first",
            "the booking remains normal one millisecond before T_wait",
            "the booking upgrades exactly when waiting time equals T_wait",
            "the upgraded booking and SOS share created-at ordering",
            "the synthetic input was not mutated",
            "the operator-confirmed dispatch branch used an in-memory stub only",
        ],
        "ordered_event_ids_before_threshold": before["ordered_event_ids"],
        "ordered_event_ids_at_threshold": at["ordered_event_ids"],
        "operator_confirmation_result": dispatch["operator_confirmation_result"],
        "dispatched_mission_id": dispatch["dispatched_mission_id"],
        "boundaries": dict(EVIDENCE_BOUNDARIES),
        "claim_boundary": (
            "Local deterministic scheduler software evidence only; not a fourth "
            "mission simulation and not Firebase, MQTT, operator-contact, police, "
            "flight-control, physical-UAV, dispatch, or landing evidence."
        ),
    }
    return {
        "test_configuration.json": configuration,
        "synthetic_input_events.json": inputs,
        "before_threshold_order.json": before,
        "at_threshold_order.json": at,
        "dispatch_result.json": dispatch,
        "run_summary.json": summary,
    }


def write_controlled_artifacts(
    output_dir: Path,
    artifacts: Mapping[str, Mapping[str, Any]],
) -> None:


    if output_dir.exists():
        raise FileExistsError(f"output directory already exists: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=False)
    for filename, payload in artifacts.items():
        (output_dir / filename).write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

    checksum_lines = []
    for path in sorted(output_dir.iterdir()):
        if not path.is_file() or path.name == "SHA256SUMS.txt":
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        checksum_lines.append(f"{digest}  {path.name}")
    (output_dir / "SHA256SUMS.txt").write_text(
        "\n".join(checksum_lines) + "\n",
        encoding="ascii",
    )


def _test_only_threshold_argument(text: str) -> int:
    try:
        return validate_test_only_wait_threshold_ms(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--test-only-wait-threshold-ms",
        required=True,
        type=_test_only_threshold_argument,
        help="Explicit synthetic T_wait; never treated as an operational value.",
    )
    parser.add_argument(
        "--test-only-confirm-dispatch",
        required=True,
        action="store_true",
        help="Record a confirmed in-memory dispatch stub; no MQTT is used.",
    )
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    artifacts = build_controlled_artifacts(
        args.test_only_wait_threshold_ms,
        operator_confirmed=args.test_only_confirm_dispatch,
    )
    write_controlled_artifacts(args.output, artifacts)
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
