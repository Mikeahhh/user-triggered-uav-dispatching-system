import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

from test_ground_station_rescue_flow import gs, _event, _FirebaseReference
from rescue_repository import apply_user_reconciliation
from rescue_event_manager import record_user_contact, SAFETY_UNCONFIRMED
from quick_start_observations import ObservationStore
from quick_start_freshness import evaluate_quick_start_freshness, sample_identity


class QuickStartGroundTests(unittest.TestCase):
    def fixture(self):
        session = {"status": "ACTIVE", "startTime": 100,
                   "points": [{"timestamp": 200, "latitude": 1, "longitude": 2}]}
        monitor = evaluate_quick_start_freshness("USER_A", "session_A", session, 250, 100)["monitoring"]
        return {"QuickStartSessions": {"session_A": session},
                "quick_start_monitoring": {"session_A": monitor}}

    def test_observation_uses_accepted_sample_and_survives_repeat_read(self):
        user = self.fixture()
        with tempfile.TemporaryDirectory() as directory:
            store = ObservationStore(Path(directory) / "local.sqlite3", "synthetic")
            self.addCleanup(store.close)
            first = gs.observe_committed_quick_start("USER_A", user, 250, store)["session_A"]
            second = gs.observe_committed_quick_start("USER_A", user, 290, store)["session_A"]
            self.assertEqual(first, second)
            self.assertEqual(first["sample_at_ms"], 200)
            self.assertEqual(first["first_seen_at_ms"], 250)
            user["QuickStartSessions"]["session_A"]["points"] = []
            self.assertEqual(gs.observe_committed_quick_start("USER_A", user, 300, store), {})

    def test_refresh_records_b_after_commit_and_never_after_failed_transaction(self):
        user = self.fixture()
        for fail in (False, True):
            database = _FirebaseReference({"users": {"USER_A": copy.deepcopy(user)}})
            times = iter([100, 300, 400, 500, 600, 700])
            def reconcile(_db, _uid, _now, _config, *, clock_ms):
                self.assertEqual(clock_ms(), 300)
                if fail:
                    raise OSError("synthetic failed commit")
                return copy.deepcopy(user), {"issues": [], "alerts": {}, "events": {}}
            with tempfile.TemporaryDirectory() as directory:
                store = ObservationStore(Path(directory) / "local.sqlite3", "synthetic")
                with patch.object(gs, "initialize_firebase", return_value=database), \
                     patch.object(gs, "_epoch_now_ms", side_effect=lambda: next(times)), \
                     patch.object(gs, "reconcile_user", side_effect=reconcile), \
                     patch.object(gs, "get_observation_store", return_value=store) as factory, \
                     patch.object(gs, "_migrate_unqueued_executions", return_value=[]), \
                     patch.object(gs, "scroll_frame", Mock(winfo_children=Mock(return_value=[]))), patch.object(gs, "root", Mock()), \
                     patch.object(gs.ctk, "CTkLabel", Mock()), patch.object(gs, "safe_configure"), \
                     patch.object(gs, "_render_pending_alerts"), patch.object(gs, "_render_pending_events"), \
                     patch.object(gs, "_render_active_events"), patch.object(gs, "_render_raw_records_read_only"):
                    gs.refresh_data()
                    if fail:
                        factory.assert_not_called()
                        self.assertEqual(gs.quick_start_observations, {})
                        store.close()
                    else:
                        self.assertEqual(gs.quick_start_observations["USER_A"]["session_A"]["first_seen_at_ms"], 400)

    def test_ui_labels_recovery_without_claiming_safe_or_replacing_timestamps(self):
        user = self.fixture()
        monitor = user["quick_start_monitoring"]["session_A"]
        monitor["ignored_gps_point_count"] = 2
        text = gs._quick_start_gps_summary(user["QuickStartSessions"]["session_A"], 250,
                    monitor, {"sample_at_ms": 200, "first_seen_at_ms": 250})
        self.assertIn("last accepted sample: 200", text)
        self.assertIn("workstation: 250", text)
        self.assertIn("not cloud receipt", text)
        self.assertIn("ignored samples: 2", text)
        alert = {"trigger_type": "QUICK_START_LOCATION_TIMEOUT", "location_status": "RECOVERED",
                 "status": "PENDING", "stage": "CONTACT_USER"}
        original = copy.deepcopy(alert)
        self.assertIn("manual safety verification still required", gs._location_alert_summary(alert, 250))
        self.assertEqual(alert, original)

    def pending_fixture(self):
        user, result = apply_user_reconciliation(self.fixture(), "USER_A", 300,
                                                {"quick_start_timeout_ms": 100})
        alert_id = next(iter(result["alerts"]))
        user["rescue_alerts"][alert_id] = record_user_contact(
            user["rescue_alerts"][alert_id], SAFETY_UNCONFIRMED, False)
        return user, alert_id

    def test_conversion_reconciles_live_recovery_with_historical_anomaly_snapshot(self):
        user, alert_id = self.pending_fixture()
        user["QuickStartSessions"]["session_A"]["points"].append(
            {"timestamp": 350, "latitude": 1, "longitude": 2})
        database = _FirebaseReference({"users": {"USER_A": user}})
        with patch.object(gs.messagebox, "askyesno", return_value=True), \
             patch.object(gs, "_epoch_now_ms", return_value=400), \
             patch.object(gs, "rescue_runtime_config", {"quick_start_timeout_ms": 100}):
            gs._convert_alert_atomically(database, "USER_A", alert_id, user, user["rescue_alerts"][alert_id])
        saved = database.data["users"]["USER_A"]
        event = next(iter(saved["rescue_events"].values()))
        self.assertEqual(event["created_at_ms"], 400)
        self.assertEqual(event["abnormal_since_ms"], 300)
        self.assertEqual(event["location_status"], "RECOVERED")
        self.assertEqual(event["location_latest_sample_at_ms"], 350)

    def test_conversion_blocks_missing_or_changed_session_source(self):
        for changed in (None, 150):
            user, alert_id = self.pending_fixture()
            if changed is None:
                user["QuickStartSessions"].clear()
            else:
                user["QuickStartSessions"]["session_A"]["startTime"] = changed
            database = _FirebaseReference({"users": {"USER_A": user}})
            with patch.object(gs.messagebox, "askyesno", return_value=True), \
                 patch.object(gs, "_epoch_now_ms", return_value=400), \
                 patch.object(gs, "rescue_runtime_config", {"quick_start_timeout_ms": 100}), \
                 self.assertRaises(ValueError):
                gs._convert_alert_atomically(database, "USER_A", alert_id, user, user["rescue_alerts"][alert_id])
            self.assertFalse(database.data["users"]["USER_A"]["rescue_events"])

    def test_quarantined_future_point_cannot_age_into_mission_route(self):
        user = self.fixture()
        monitor = user["quick_start_monitoring"]["session_A"]
        monitor["rejected_future_samples"] = [sample_identity(monitor["latest_sample"])]
        event = _event("new_timeout", trigger_type="QUICK_START_LOCATION_TIMEOUT",
                       primary_record_type="QuickStartSessions", primary_record_id="session_A",
                       abnormal_since_ms=300)
        for trigger in ("QUICK_START_LOCATION_TIMEOUT", "QUICK_START_INACTIVITY"):
            event["trigger_type"] = trigger
            with self.subTest(trigger=trigger), self.assertRaisesRegex(ValueError, "quarantined"):
                gs.prepare_mission_for_rescue_event(user, event, 400)


if __name__ == "__main__":
    unittest.main()
