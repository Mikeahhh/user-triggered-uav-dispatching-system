from __future__ import annotations

import copy
import unittest
from datetime import timezone

from rescue_event_manager import (
    BOOKED_EVENTS,
    CLOSED_SAFE,
    CONTACT_EMERGENCY,
    CONTACT_USER,
    CONVERTED,
    EVENT_BOOKING_TIMEOUT,
    NOT_AVAILABLE,
    PENDING,
    QUICK_START_INACTIVITY,
    QUICK_START_SESSIONS,
    RESCUE_REQUESTS,
    RESOLUTION,
    SAFE_CONFIRMED,
    SAFETY_UNCONFIRMED,
    RescueAlertTransitionError,
    RescueEventValidationError,
    convert_alert_to_rescue_event,
    detect_event_booking_timeout,
    detect_pending_sos,
    detect_quick_start_inactivity,
    event_booking_alert_id,
    parse_legacy_expected_end_ms,
    quick_start_alert_id,
    quick_start_inactivity_state,
    record_emergency_contact,
    record_user_contact,
    scan_user_records,
    valid_gps_points,
)


NOW = 2_000_000
D_MOVE = 50.0
T_INACTIVE = 100_000


def booking(expected_end):
    return {
        "title": "Synthetic booking",
        "date": "2026-12-31",
        "startTime": "22:00",
        "endTime": "23:00",
        "waypoints": [],
        "createdAt": "2026-01-01T00:00:00.000Z",
        "expectedEndAtMs": expected_end,
    }


def point(latitude, longitude, timestamp):
    return {
        "latitude": latitude,
        "longitude": longitude,
        "timestamp": timestamp,
        "timestampISO": "2099-01-01T00:00:00.000Z",
    }


def base_alert():
    return {
        "alert_id": "event_timeout__event_1",
        "user_id": "USER_A",
        "trigger_type": EVENT_BOOKING_TIMEOUT,
        "primary_record_type": BOOKED_EVENTS,
        "primary_record_id": "event_1",
        "abnormal_since_ms": NOW - 500_000,
        "created_at_ms": NOW - 100,
        "stage": CONTACT_USER,
        "user_contact_result": None,
        "emergency_contact_result": None,
        "status": PENDING,
    }


class EventBookingTimeoutTests(unittest.TestCase):
    def test_before_end_time_creates_no_alert(self):
        self.assertIsNone(
            detect_event_booking_timeout("USER_A", "event_1", booking(NOW + 1), NOW)
        )

    def test_exact_end_time_creates_alert(self):
        alert = detect_event_booking_timeout(
            "USER_A", "event_1", booking(NOW), NOW
        )
        self.assertEqual(alert["trigger_type"], EVENT_BOOKING_TIMEOUT)
        self.assertEqual(alert["abnormal_since_ms"], NOW)
        self.assertEqual(alert["stage"], CONTACT_USER)

    def test_repeated_refresh_does_not_duplicate_alert(self):
        alert = detect_event_booking_timeout(
            "USER_A", "event_1", booking(NOW - 1), NOW
        )
        self.assertIsNone(
            detect_event_booking_timeout(
                "USER_A",
                "event_1",
                booking(NOW - 1),
                NOW + 10,
                existing_alerts={alert["alert_id"]: alert},
            )
        )

    def test_legacy_booking_requires_and_uses_explicit_timezone(self):
        legacy = {"date": "1970-01-02", "endTime": "00:00"}
        self.assertEqual(
            parse_legacy_expected_end_ms(legacy, timezone.utc), 86_400_000
        )


class QuickStartInactivityTests(unittest.TestCase):
    def test_completed_session_creates_no_alert(self):
        session = {
            "status": "COMPLETED",
            "points": {"point_1": point(0, 0, NOW - T_INACTIVE)},
        }
        self.assertIsNone(
            detect_quick_start_inactivity(
                "USER_A", "session_1", session, NOW, D_MOVE, T_INACTIVE
            )
        )

    def test_continuing_significant_movement_creates_no_alert(self):
        session = {
            "status": "ACTIVE",
            "points": {
                "point_1": point(0, 0, NOW - 300_000),
                "point_2": point(0, 0.001, NOW - 80_000),
                "point_3": point(0, 0.002, NOW - 1_000),
            },
        }
        self.assertIsNone(
            detect_quick_start_inactivity(
                "USER_A", "session_1", session, NOW, D_MOVE, T_INACTIVE
            )
        )

    def test_long_period_without_significant_movement_creates_alert(self):
        session = {
            "status": "ACTIVE",
            "points": {
                "point_1": point(0, 0, NOW - 200_000),
                "point_2": point(0, 0.00001, NOW - 10_000),
            },
        }
        alert = detect_quick_start_inactivity(
            "USER_A", "session_1", session, NOW, D_MOVE, T_INACTIVE
        )
        self.assertEqual(alert["trigger_type"], QUICK_START_INACTIVITY)
        self.assertEqual(alert["abnormal_since_ms"], NOW - 200_000)

    def test_continued_uploads_at_same_position_still_trigger(self):
        session = {
            "status": "ACTIVE",
            "points": {
                "point_1": point(22.3, 114.1, NOW - 200_000),
                "point_2": point(22.3, 114.1, NOW - 1_000),
            },
        }
        alert = detect_quick_start_inactivity(
            "USER_A", "session_1", session, NOW, D_MOVE, T_INACTIVE
        )
        self.assertIsNotNone(alert)
        self.assertEqual(alert["valid_gps_point_count"], 2)

    def test_no_points_leave_inactivity_timer_uninitialized(self):
        for points in (None, {}):
            with self.subTest(points=points):
                session = {
                    "status": "ACTIVE",
                    "startTime": "1970-01-01T00:00:00.000Z",
                    "points": points,
                }
                state = quick_start_inactivity_state(session, D_MOVE, NOW)
                self.assertIsNone(state["last_movement_at_ms"])
                self.assertTrue(state["missing_valid_gps_point"])
                self.assertEqual(state["valid_gps_point_count"], 0)
                self.assertIsNone(
                    detect_quick_start_inactivity(
                        "USER_A", "session_1", session, NOW, D_MOVE, T_INACTIVE
                    )
                )

    def test_invalid_only_points_do_not_start_timer_or_create_alert(self):
        session = {
            "status": "ACTIVE",
            "startTime": "1970-01-01T00:00:00.000Z",
            "points": {"bad": {"latitude": "bad"}},
        }
        state = quick_start_inactivity_state(session, D_MOVE, NOW)
        self.assertIsNone(state["last_movement_at_ms"])
        self.assertTrue(state["missing_valid_gps_point"])
        self.assertEqual(state["ignored_gps_point_count"], 1)
        self.assertIsNone(
            detect_quick_start_inactivity(
                "USER_A", "session_1", session, NOW, D_MOVE, T_INACTIVE
            )
        )

    def test_no_points_without_legacy_start_time_create_no_alert(self):
        self.assertIsNone(
            detect_quick_start_inactivity(
                "USER_A", "session_1", {"status": "ACTIVE"},
                NOW, D_MOVE, T_INACTIVE
            )
        )

    def test_late_first_valid_point_starts_timer_at_its_timestamp(self):
        first_fix_at = 3 * T_INACTIVE
        session = {
            "status": "ACTIVE",
            "startTime": "1970-01-01T00:00:00.000Z",
            "points": {
                "bad": {"latitude": "bad"},
                "point_1": point(22.3, 114.1, first_fix_at),
            },
        }
        for now in (first_fix_at, first_fix_at + T_INACTIVE - 1):
            with self.subTest(now=now):
                self.assertIsNone(
                    detect_quick_start_inactivity(
                        "USER_A", "session_1", session, now, D_MOVE, T_INACTIVE
                    )
                )
        alert = detect_quick_start_inactivity(
            "USER_A", "session_1", session,
            first_fix_at + T_INACTIVE, D_MOVE, T_INACTIVE
        )
        self.assertEqual(alert["abnormal_since_ms"], first_fix_at)
        self.assertFalse(alert["missing_valid_gps_point"])
        self.assertEqual(alert["valid_gps_point_count"], 1)

    def test_points_are_sorted_by_timestamp_not_lexical_key(self):
        session = {
            "points": {
                "point_10": point(0, 0.001, NOW - 1_000),
                "point_2": point(0, 0, NOW - 200_000),
            }
        }
        state = quick_start_inactivity_state(session, D_MOVE)
        self.assertEqual(state["last_movement_at_ms"], NOW - 1_000)

    def test_future_point_cannot_suppress_inactivity_alert(self):
        session = {
            "status": "ACTIVE",
            "startTime": "1970-01-01T00:00:00.000Z",
            "points": {
                "point_1": point(0, 0, NOW - 200_000),
                "point_2": point(0, 0.01, NOW + 9_999_999),
            },
        }
        alert = detect_quick_start_inactivity(
            "USER_A", "session_1", session, NOW, D_MOVE, T_INACTIVE
        )
        self.assertIsNotNone(alert)
        self.assertEqual(alert["abnormal_since_ms"], NOW - 200_000)
        self.assertEqual(alert["ignored_gps_point_count"], 1)

    def test_point_before_session_start_is_ignored(self):
        session_start = NOW - 150_000
        valid, ignored = valid_gps_points(
            {
                "before": point(0, 0, session_start - 1),
                "inside": point(0, 0.001, session_start),
            },
            min_timestamp_ms=session_start,
            max_timestamp_ms=NOW,
        )
        self.assertEqual([item["timestamp_ms"] for item in valid], [session_start])
        self.assertEqual(ignored, 1)

    def test_same_timestamp_mapping_points_use_stable_key_tie_break(self):
        timestamp = NOW - 1_000
        reverse_inserted = {
            "point_z": point(0, 0.002, timestamp),
            "point_a": point(0, 0.001, timestamp),
        }
        forward_inserted = {
            "point_a": point(0, 0.001, timestamp),
            "point_z": point(0, 0.002, timestamp),
        }
        reverse_valid, _ = valid_gps_points(reverse_inserted)
        forward_valid, _ = valid_gps_points(forward_inserted)
        self.assertEqual(reverse_valid, forward_valid)
        self.assertEqual(
            [item["longitude"] for item in reverse_valid],
            [0.001, 0.002],
        )

    def test_boolean_gps_values_are_ignored_not_coerced_to_zero_or_one(self):
        valid, ignored = valid_gps_points(
            {
                "boolean_coordinate": point(True, 0, NOW - 2),
                "boolean_timestamp": point(0, 0, True),
                "valid": point(0, 0, NOW - 1),
            }
        )
        self.assertEqual(len(valid), 1)
        self.assertEqual(valid[0]["timestamp_ms"], NOW - 1)
        self.assertEqual(ignored, 2)

    def test_later_inactivity_episode_in_same_session_gets_a_new_alert(self):
        session = {
            "status": "ACTIVE",
            "points": {
                "point_1": point(0, 0, NOW - 500_000),
                "point_2": point(0, 0.001, NOW - 200_000),
                "point_3": point(0, 0.00101, NOW - 1_000),
            },
        }
        old_source = "quick_inactivity__session_1__1000000"
        existing = {
            f"rescue__{old_source}": {
                "event_id": f"rescue__{old_source}",
                "primary_record_type": "QuickStartSessions",
                "primary_record_id": "session_1",
                "source_alert_id": old_source,
            }
        }
        alert = detect_quick_start_inactivity(
            "USER_A",
            "session_1",
            session,
            NOW,
            D_MOVE,
            T_INACTIVE,
            existing_events=existing,
        )
        self.assertIsNotNone(alert)
        self.assertEqual(alert["abnormal_since_ms"], NOW - 200_000)


class ContactAndConversionTests(unittest.TestCase):
    def test_user_safe_closes_alert_and_cannot_convert(self):
        closed = record_user_contact(base_alert(), SAFE_CONFIRMED, True)
        self.assertEqual(closed["status"], CLOSED_SAFE)
        with self.assertRaises(RescueAlertTransitionError):
            convert_alert_to_rescue_event(closed, NOW, True)

    def test_emergency_result_cannot_skip_user_contact_stage(self):
        with self.assertRaises(RescueAlertTransitionError):
            record_emergency_contact(base_alert(), SAFETY_UNCONFIRMED, True)

    def test_unconfirmed_user_advances_to_emergency_contact(self):
        updated = record_user_contact(base_alert(), SAFETY_UNCONFIRMED, True)
        self.assertEqual(updated["stage"], CONTACT_EMERGENCY)
        self.assertEqual(updated["user_contact_result"], SAFETY_UNCONFIRMED)

    def test_missing_emergency_contact_is_recorded_not_invented(self):
        updated = record_user_contact(base_alert(), SAFETY_UNCONFIRMED, False)
        self.assertEqual(updated["stage"], RESOLUTION)
        self.assertEqual(updated["emergency_contact_result"], NOT_AVAILABLE)

    def test_contact_removed_during_emergency_stage_records_not_available(self):
        awaiting_emergency = record_user_contact(
            base_alert(), SAFETY_UNCONFIRMED, True
        )
        updated = record_emergency_contact(
            awaiting_emergency, NOT_AVAILABLE, False
        )
        self.assertEqual(updated["stage"], RESOLUTION)
        self.assertEqual(updated["status"], PENDING)
        self.assertEqual(updated["emergency_contact_result"], NOT_AVAILABLE)

    def test_emergency_contact_availability_and_result_must_agree(self):
        awaiting_emergency = record_user_contact(
            base_alert(), SAFETY_UNCONFIRMED, True
        )
        for invented_result in (SAFE_CONFIRMED, SAFETY_UNCONFIRMED):
            with self.subTest(invented_result=invented_result):
                with self.assertRaisesRegex(
                    RescueEventValidationError, "result must be NOT_AVAILABLE"
                ):
                    record_emergency_contact(
                        awaiting_emergency, invented_result, False
                    )
        with self.assertRaisesRegex(
            RescueEventValidationError, "actual contact result"
        ):
            record_emergency_contact(awaiting_emergency, NOT_AVAILABLE, True)

    def test_still_unconfirmed_creates_rescue_event(self):
        after_user = record_user_contact(base_alert(), SAFETY_UNCONFIRMED, True)
        after_emergency = record_emergency_contact(
            after_user, SAFETY_UNCONFIRMED, True
        )
        updated_alert, rescue_event = convert_alert_to_rescue_event(
            after_emergency, NOW, True
        )
        self.assertEqual(updated_alert["status"], CONVERTED)
        self.assertEqual(rescue_event["status"], PENDING)
        self.assertEqual(rescue_event["source_alert_id"], base_alert()["alert_id"])
        self.assertEqual(rescue_event["created_at_ms"], NOW)
        self.assertEqual(rescue_event["search_confirmed_at_ms"], NOW)

    def test_conversion_returns_one_atomic_pair_without_mutating_input(self):
        alert = record_user_contact(base_alert(), SAFETY_UNCONFIRMED, False)
        original = copy.deepcopy(alert)
        updated_alert, event = convert_alert_to_rescue_event(alert, NOW, True)
        self.assertEqual(alert, original)
        self.assertEqual(updated_alert["alert_id"], original["alert_id"])
        self.assertEqual(event["event_id"], f"rescue__{original['alert_id']}")

    def test_rtdb_omitted_null_contact_fields_remain_compatible(self):
        alert = base_alert()
        alert.pop("user_contact_result")
        alert.pop("emergency_contact_result")
        resolved = record_user_contact(alert, SAFETY_UNCONFIRMED, False)
        updated, event = convert_alert_to_rescue_event(resolved, NOW, True)
        self.assertEqual(updated["emergency_contact_result"], NOT_AVAILABLE)
        self.assertEqual(event["source_alert_id"], alert["alert_id"])

    def test_conversion_rejects_duplicate_source_alert_under_another_key(self):
        resolved = record_user_contact(base_alert(), SAFETY_UNCONFIRMED, False)
        existing = {
            "legacy_or_malformed_event_key": {
                "source_alert_id": resolved["alert_id"],
            }
        }
        with self.assertRaisesRegex(
            RescueAlertTransitionError,
            "already has a rescue event",
        ):
            convert_alert_to_rescue_event(
                resolved,
                NOW,
                True,
                existing_events=existing,
            )

    def test_conversion_rejects_noncanonical_alert_id(self):
        alert = base_alert()
        alert["alert_id"] = "event_timeout__different_record"
        resolved = record_user_contact(alert, SAFETY_UNCONFIRMED, False)
        with self.assertRaisesRegex(RescueEventValidationError, "not canonical"):
            convert_alert_to_rescue_event(resolved, NOW, True)

    def test_conversion_rejects_trigger_primary_type_mismatch(self):
        cases = (
            (EVENT_BOOKING_TIMEOUT, QUICK_START_SESSIONS),
            (QUICK_START_INACTIVITY, BOOKED_EVENTS),
        )
        for trigger_type, primary_record_type in cases:
            with self.subTest(
                trigger_type=trigger_type,
                primary_record_type=primary_record_type,
            ):
                alert = base_alert()
                alert["trigger_type"] = trigger_type
                alert["primary_record_type"] = primary_record_type
                if trigger_type == QUICK_START_INACTIVITY:
                    alert["primary_record_id"] = "session_1"
                    alert["alert_id"] = quick_start_alert_id(
                        "session_1", alert["abnormal_since_ms"]
                    )
                resolved = record_user_contact(
                    alert,
                    SAFETY_UNCONFIRMED,
                    False,
                )
                with self.assertRaisesRegex(
                    RescueEventValidationError,
                    "does not match primary_record_type",
                ):
                    convert_alert_to_rescue_event(resolved, NOW, True)

    def test_quick_start_conversion_accepts_its_canonical_alert_id(self):
        alert = base_alert()
        alert["trigger_type"] = QUICK_START_INACTIVITY
        alert["primary_record_type"] = QUICK_START_SESSIONS
        alert["primary_record_id"] = "session_1"
        alert["alert_id"] = quick_start_alert_id(
            "session_1", alert["abnormal_since_ms"]
        )
        resolved = record_user_contact(alert, SAFETY_UNCONFIRMED, False)
        _, event = convert_alert_to_rescue_event(resolved, NOW, True)
        self.assertEqual(event["trigger_type"], QUICK_START_INACTIVITY)
        self.assertEqual(event["primary_record_type"], QUICK_START_SESSIONS)

    def test_conversion_rejects_future_abnormal_start(self):
        alert = base_alert()
        alert["abnormal_since_ms"] = NOW + 1
        resolved = record_user_contact(alert, SAFETY_UNCONFIRMED, False)
        with self.assertRaisesRegex(
            RescueEventValidationError,
            "later than conversion now_ms",
        ):
            convert_alert_to_rescue_event(resolved, NOW, True)


class SosAndScanTests(unittest.TestCase):
    def test_pending_sos_requires_contact_and_search_confirmation(self):
        request = {"status": "PENDING", "latitude": 22.3, "longitude": 114.1}
        alert = detect_pending_sos("USER_A", "request_1", request, NOW)
        self.assertNotIn("event_id", alert)
        self.assertEqual(alert["stage"], CONTACT_USER)
        with self.assertRaises(RescueAlertTransitionError):
            convert_alert_to_rescue_event(alert, NOW, True)
        resolved = record_user_contact(alert, SAFETY_UNCONFIRMED, False)
        with self.assertRaises(RescueAlertTransitionError):
            convert_alert_to_rescue_event(resolved, NOW, False)
        updated, event = convert_alert_to_rescue_event(resolved, NOW, True)
        self.assertEqual(event["event_id"], "sos__request_1")
        self.assertEqual(event["source_alert_id"], "sos_review__request_1")
        self.assertEqual(updated["status"], "CONVERTED")
        self.assertIsNone(detect_pending_sos("USER_A", "request_1", request, NOW + 1,
                                            {event["event_id"]: event}))

    def test_sos_safe_user_does_not_create_event(self):
        alert = detect_pending_sos("USER_A", "request_1", {"status": "PENDING"}, NOW)
        resolved = record_user_contact(alert, SAFE_CONFIRMED, True)
        with self.assertRaises(RescueAlertTransitionError):
            convert_alert_to_rescue_event(resolved, NOW, True)
        self.assertIsNone(detect_pending_sos("USER_A", "request_1", {"status": "PENDING"}, NOW,
                                           existing_alerts={alert["alert_id"]: resolved}))

    def test_sos_requires_available_emergency_contact_result(self):
        alert = detect_pending_sos("USER_A", "request_1", {"status": "PENDING"}, NOW)
        user = record_user_contact(alert, SAFETY_UNCONFIRMED, True)
        with self.assertRaises(RescueAlertTransitionError):
            convert_alert_to_rescue_event(user, NOW, True)
        safe = record_emergency_contact(user, SAFE_CONFIRMED, True)
        with self.assertRaises(RescueAlertTransitionError):
            convert_alert_to_rescue_event(safe, NOW, True)
        unresolved = record_emergency_contact(user, SAFETY_UNCONFIRMED, True)
        _, event = convert_alert_to_rescue_event(unresolved, NOW, True)
        self.assertEqual(event["emergency_contact_result"], SAFETY_UNCONFIRMED)
        with self.assertRaises(RescueAlertTransitionError):
            convert_alert_to_rescue_event(unresolved, NOW, True, {event["event_id"]: event})

    def test_repeated_sos_scan_preserves_existing_alert(self):
        user = {"rescue_requests": {"request_1": {"status": "PENDING"}}}
        first = scan_user_records("USER_A", user, NOW)
        self.assertFalse(first["events"])
        user["rescue_alerts"] = first["alerts"]
        second = scan_user_records("USER_A", user, NOW + 1)
        self.assertFalse(second["alerts"])
        self.assertFalse(second["events"])

    def test_scan_reports_bad_legacy_record_without_blocking_sos(self):
        result = scan_user_records(
            "USER_A",
            {
                "booked_events": {"bad": {"date": "not-a-date", "endTime": "x"}},
                "rescue_requests": {"request_1": {"status": "PENDING"}},
            },
            NOW,
            movement_threshold_m=D_MOVE,
            inactivity_threshold_ms=T_INACTIVE,
        )
        self.assertEqual(set(result["alerts"]), {"sos_review__request_1"})
        self.assertFalse(result["events"])
        self.assertEqual(len(result["issues"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
