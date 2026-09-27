import copy
import unittest
from unittest.mock import patch

from test_ground_station_rescue_flow import gs, _FirebaseReference, _event
from rescue_event_manager import (
    detect_event_booking_timeout, record_user_contact, SAFE_CONFIRMED,
    SAFETY_UNCONFIRMED, session_gps_points,
)
from rescue_repository import reconcile_user, transition_alert
from firebase_runtime_config import load_firebase_runtime_config


class InputAndContactTests(unittest.TestCase):
    def test_firebase_target_is_explicit_and_normalized(self):
        self.assertFalse(load_firebase_runtime_config({})["ready"])
        cfg = load_firebase_runtime_config({"GS_FIREBASE_DATABASE_URL": "https://synthetic-default-rtdb.asia-southeast1.firebasedatabase.app:443/"})
        self.assertTrue(cfg["ready"])
        self.assertEqual(cfg["database_url"], "https://synthetic-default-rtdb.asia-southeast1.firebasedatabase.app")
        for bad in ("http://synthetic.firebaseio.com", "https://u:p@synthetic.firebaseio.com",
                    "https://synthetic.firebaseio.com:444", "https://synthetic.firebaseio.com/path",
                    "https://synthetic.firebaseio.com?auth=synthetic", "https://example.com",
                    "https://synthe\ntic.firebaseio.com", "https://a.b.firebaseio.com",
                    "https://a.b.c.firebasedatabase.app"):
            self.assertFalse(load_firebase_runtime_config({"GS_FIREBASE_DATABASE_URL": bad})["ready"])
    def quick_event(self):
        return _event("quick_event", trigger_type="QUICK_START_INACTIVITY",
                      primary_record_type="QuickStartSessions",
                      primary_record_id="session_A", abnormal_since_ms=1000000)

    def test_detection_and_preparation_share_session_time_bounds(self):
        session = {"status": "ACTIVE", "startTime": 1000000, "points": [
            {"timestamp": 500000, "latitude": 21, "longitude": 113},
            {"timestamp": 1100000, "latitude": 22, "longitude": 114},
            {"timestamp": 4000000, "latitude": 23, "longitude": 115},
        ]}
        points, ignored = session_gps_points(session, 2000000)
        self.assertEqual([p["timestamp_ms"] for p in points], [1100000])
        self.assertEqual(ignored, 2)
        with self.assertRaisesRegex(ValueError, "invalid GPS points"):
            gs.prepare_mission_for_rescue_event(
                {"QuickStartSessions": {"session_A": session}}, self.quick_event(), 2000000)

    def test_source_waypoint_limit_is_explicit_and_does_not_truncate(self):
        point = {"timestamp": 1100000, "latitude": 22, "longitude": 114}
        session = {"startTime": 1000000, "points": [copy.deepcopy(point) for _ in range(1000)]}
        user = {"QuickStartSessions": {"session_A": session}}
        prepared = gs.prepare_mission_for_rescue_event(user, self.quick_event(), 2000000)
        self.assertEqual(len(prepared["waypoints"]), 1000)
        self.assertTrue(prepared["return_to_launch"])
        session["points"].append(copy.deepcopy(point))
        with self.assertRaisesRegex(ValueError, "1000-waypoint limit"):
            gs.prepare_mission_for_rescue_event(user, self.quick_event(), 2000000)
        self.assertEqual(len(session["points"]), 1001)

    def test_null_or_nonstring_contact_is_unavailable(self):
        for value in (None, True, {}, [], 1234, "   "):
            with self.subTest(value=value):
                info = gs._profile_contact_data("USER_A", {
                    "profile": {"emergency_contacts": [{"name": "Synthetic", "phone": value}]}})
                self.assertFalse(info["emergency_contact_available"])

    def test_police_cancellation_preserves_completed_contact_result(self):
        alert = detect_event_booking_timeout("USER_A", "booking_A", {"expectedEndAtMs": 1000000}, 1500000)
        alert = record_user_contact(alert, SAFETY_UNCONFIRMED, True)
        user = {"profile": {"emergency_contacts": [{"phone": "SYNTHETIC"}]},
                "rescue_alerts": {alert["alert_id"]: alert}}
        database = _FirebaseReference({"users": {"USER_A": copy.deepcopy(user)}})
        with patch.object(gs, "_load_live_user", return_value=(database, copy.deepcopy(user))), \
             patch.object(gs.messagebox, "askyesno", return_value=False), \
             patch.object(gs, "refresh_data"), patch.object(gs, "_show_workflow_error") as error:
            gs._handle_emergency_contact("USER_A", alert["alert_id"], SAFETY_UNCONFIRMED)
        error.assert_not_called()
        saved = database.data["users"]["USER_A"]["rescue_alerts"][alert["alert_id"]]
        self.assertEqual(saved["stage"], "RESOLUTION")
        self.assertEqual(saved["emergency_contact_result"], SAFETY_UNCONFIRMED)
        self.assertEqual(saved["status"], "PENDING")
        self.assertFalse(database.data["users"]["USER_A"].get("rescue_events"))

    def test_booking_new_end_rearms_but_same_legacy_episode_deduplicates(self):
        old = detect_event_booking_timeout("USER_A", "booking_A", {"expectedEndAtMs": 100}, 101)
        old["alert_id"] = "event_timeout__booking_A"
        old = record_user_contact(old, SAFE_CONFIRMED, False)
        existing = {old["alert_id"]: old}
        self.assertIsNone(detect_event_booking_timeout(
            "USER_A", "booking_A", {"expectedEndAtMs": 100}, 301, existing))
        new = detect_event_booking_timeout(
            "USER_A", "booking_A", {"expectedEndAtMs": 300}, 301, existing)
        self.assertEqual(new["abnormal_since_ms"], 300)
        self.assertNotEqual(new["alert_id"], old["alert_id"])
        self.assertEqual(new["stage"], "CONTACT_USER")

    def test_booking_changed_or_missing_source_cannot_convert_old_episode(self):
        alert = detect_event_booking_timeout("USER_A", "booking_A", {"expectedEndAtMs": 100}, 101)
        alert = record_user_contact(alert, SAFETY_UNCONFIRMED, False)
        for booking in ({"expectedEndAtMs": 200}, None):
            user = {"rescue_alerts": {alert["alert_id"]: copy.deepcopy(alert)},
                    "booked_events": {"booking_A": booking}}
            database = _FirebaseReference({"users": {"USER_A": copy.deepcopy(user)}})
            with patch.object(gs.messagebox, "askyesno", return_value=True), self.assertRaises(ValueError):
                gs._convert_alert_atomically(database, "USER_A", alert["alert_id"], user, alert)
            self.assertFalse(database.data["users"]["USER_A"].get("rescue_events"))
            self.assertEqual(database.data["users"]["USER_A"]["rescue_alerts"][alert["alert_id"]]["status"], "PENDING")

    def test_legacy_booking_event_checks_original_episode_before_preparation(self):
        event = _event("rescue__event_timeout__booking_A", trigger_type="EVENT_BOOKING_TIMEOUT",
                       primary_record_type="booked_events", primary_record_id="booking_A", abnormal_since_ms=100)
        booking = {"expectedEndAtMs": 100, "waypoints": [{"latitude": 1, "longitude": 2}]}
        user = {"booked_events": {"booking_A": booking}}
        self.assertEqual(gs.prepare_mission_for_rescue_event(user, event)["waypoints"], [(1, 2)])
        booking["expectedEndAtMs"] = 200
        with self.assertRaisesRegex(ValueError, "source changed"):
            gs.prepare_mission_for_rescue_event(user, event)

    def test_gps_diagnostic_uses_first_valid_point_without_new_timer(self):
        session = {"startTime": 100, "points": []}
        self.assertIn("timeout monitoring has not started", gs._quick_start_gps_summary(session, 300))
        session["points"] = [{"timestamp": 200, "latitude": 1, "longitude": 2}]
        self.assertIn("last valid sample: 200", gs._quick_start_gps_summary(session, 300))

    def test_transaction_retry_cannot_restore_closed_alert_to_pending(self):
        database = _FirebaseReference({"users": {"USER_A": {
            "booked_events": {"booking_A": {"expectedEndAtMs": 100}}}}})
        config = {"quick_start_timeout_ms": 100}
        current, _ = reconcile_user(database, "USER_A", 101, config)
        alert_id = next(iter(current["rescue_alerts"]))
        closed = record_user_contact(current["rescue_alerts"][alert_id], SAFE_CONFIRMED, False)
        database.data["users"]["USER_A"]["rescue_alerts"][alert_id] = closed
        final, result = reconcile_user(database, "USER_A", 200, config)
        self.assertEqual(final["rescue_alerts"][alert_id]["status"], "CLOSED_SAFE")
        self.assertEqual(result["alerts"], {})
        with self.assertRaises(ValueError):
            transition_alert(database, "USER_A", alert_id,
                             lambda alert, user: record_user_contact(alert, SAFETY_UNCONFIRMED, False))
        self.assertEqual(database.data["users"]["USER_A"]["rescue_alerts"][alert_id]["status"], "CLOSED_SAFE")


if __name__ == "__main__":
    unittest.main()
