import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_ground_station_rescue_flow import (
    gs, _FirebaseReference, _MqttClient, _PublishInfo, _event, NOW_MS,
)
import test_ground_station_rescue_flow as flow
from dispatch_journal import DispatchJournal, JournalConflict
from receiver_ready_protocol import ReceiverReadyGate, validate_receiver_ready
from mission_execution_protocol import task_fingerprint


def ready(identity="0" * 32):
    return json.dumps({"schema_version": 1, "message_type": "RECEIVER_READY",
                       "receiver_id": "uav_phone_rescue_receiver", "ready_id": identity}).encode()


class AtomicReference(_FirebaseReference):
    def __init__(self, data, path=(), history=None, fail_updates=None, lock=None):
        super().__init__(data, path, history, fail_updates)
        self.lock = lock or threading.RLock()

    def child(self, segment):
        return AtomicReference(self.data, self.path + tuple(str(segment).split("/")),
                               self.history, self.fail_updates, self.lock)

    def transaction(self, transform):
        with self.lock:
            return super().transaction(transform)


class DispatchFaultTests(unittest.TestCase):
    setUp = flow.RescueEventDispatchWrapperTests.setUp
    tearDown = flow.RescueEventDispatchWrapperTests.tearDown
    call = flow.RescueEventDispatchWrapperTests.call
    def test_two_stations_authorize_same_event_only_one_claim_publishes(self):
        self.root = AtomicReference(self.root.data)
        second = DispatchJournal(Path(self.directory.name) / "other.sqlite3", "https://synthetic.firebaseio.com")
        barrier = threading.Barrier(2)
        outcomes = []
        def run(journal):
            barrier.wait(timeout=3)
            try:
                gs.select_rescue_event("USER_A", "sos__request_1", self.root, journal=journal)
                gs.prepare_active_rescue_event("USER_A", "sos__request_1", self.root, journal=journal)
                result = gs.dispatch_rescue_event("USER_A", "sos__request_1", self.root, journal=journal,
                    confirm_callback=lambda *_: True)
            except ValueError as exc:
                result = {"status": gs.DISPATCH_INVALID_PARAMETERS, "message": str(exc)}
            outcomes.append(result)
        workers = [threading.Thread(target=run, args=(journal,)) for journal in (self.journal, second)]
        try:
            for worker in workers: worker.start()
            for worker in workers: worker.join(timeout=5)
            self.assertTrue(all(not worker.is_alive() for worker in workers))
            self.assertEqual(len(self.client.calls), 3)
            self.assertEqual(sorted(item["status"] for item in outcomes),
                             sorted([gs.DISPATCH_PUBLISHED, gs.DISPATCH_INVALID_PARAMETERS]))
            cloud_id = self.root.data["users"]["USER_A"]["rescue_events"]["sos__request_1"]["execution"]["execution_id"]
            self.assertEqual(json.loads(self.client.calls[0][1])["execution_id"], cloud_id)
        finally:
            second.close()

    def test_corrupt_saved_payload_never_publishes(self):
        user = self.root.data["users"]["USER_A"]
        payload = gs.build_execution_payload(gs.prepare_mission_for_rescue_event(user, user["rescue_events"]["sos__request_1"]), "exec-corrupt")
        self.journal.prepare(("USER_A", "sos__request_1"), payload)
        payload["return_to_launch"] = False
        self.journal.connection.execute("UPDATE execution_intents SET payload=?", (json.dumps(payload),))
        self.journal.connection.commit()
        result = gs.dispatch_rescue_event("USER_A", "sos__request_1", self.root, journal=self.journal, resume_execution=True)
        self.assertEqual(result["status"], gs.DISPATCH_INVALID_PARAMETERS)
        self.assertEqual(self.client.calls, [])

    def test_confirmed_recovery_works_after_primary_removed_and_config_unavailable(self):
        self.call()
        with patch.object(gs, "_commit_execution", side_effect=RuntimeError("offline write")):
            with self.assertRaisesRegex(RuntimeError, "offline write"):
                flow._admit(self.root, self.journal)
        del self.root.data["users"]["USER_A"]["rescue_requests"]
        with patch.object(gs, "rescue_runtime_config", {"ready": False}):
            recovered = self.call()
        self.assertEqual(recovered["firebase_status"], "DISPATCHED")
        self.assertEqual(len(self.client.calls), 3)

    def test_idempotent_commit_preserves_original_publication_time(self):
        self.call()
        flow._admit(self.root, self.journal)
        original = self.root.data["users"]["USER_A"]["rescue_events"]["sos__request_1"]["dispatch_published_at_ms"]
        with patch.object(gs, "_epoch_now_ms", return_value=original + 60_000):
            self.call()
        self.assertEqual(self.root.data["users"]["USER_A"]["rescue_events"]["sos__request_1"]["dispatch_published_at_ms"], original)
        self.assertEqual(len(self.client.calls), 3)

    def test_ledger_write_failure_prevents_publication(self):
        with patch.object(self.journal, "prepare", side_effect=OSError("synthetic disk full")):
            self.assertEqual(self.call()["status"], gs.DISPATCH_UNKNOWN)
        self.assertEqual(self.client.calls, [])
        self.assertIn("execution", self.root.data["users"]["USER_A"]["rescue_events"]["sos__request_1"])
        self.assertEqual(self.root.data["users"]["USER_A"]["active_events"]["sos__request_1"]["phase"], "AUTHORIZED")

    def test_rtl_cannot_differ_from_reviewed_value(self):
        payload = {"schema_version": 2, "execution_id": "exec-rtl", "mission_id": "USER_A/x", "mission_type": "event",
                   "waypoints": [{"latitude": 1.0, "longitude": 2.0}], "return_to_launch": False}
        before = unittest.mock.Mock()
        result = gs.dispatch_mission([(1, 2)], "USER_A", "x", return_to_launch=True,
                                     execution=payload, before_publish=before, confirm_callback=lambda *_: True)
        self.assertEqual(result["status"], gs.DISPATCH_PUBLISH_FAILED)
        before.assert_not_called()
        self.assertEqual(self.client.calls, [])

    def test_renderer_recovery_callback_uses_saved_id_without_current_primary(self):
        self.client.info = _PublishInfo(wait_result=False)
        first = self.call()
        del self.root.data["users"]["USER_A"]["rescue_requests"]
        captured = []
        card = SimpleNamespace(bind=lambda *_: None)
        with patch.object(gs, "get_dispatch_journal", side_effect=lambda: DispatchJournal(self.journal_path, self.journal.scope)), \
             patch.object(gs, "rescue_runtime_config", {"ready": True, "wait_threshold_ms": 1_000}), \
             patch.object(gs, "_section_heading"), patch.object(gs, "create_mission_card", return_value=card), \
             patch.object(gs, "_add_card_button", side_effect=lambda _, text, cb, **kwargs: captured.append((text, cb, kwargs))):
            gs._render_active_events(self.root.data["users"], NOW_MS)
        retry_button = next(item for item in captured if "Same Saved Execution" in item[0])
        self.assertEqual(retry_button[2]["state"], "normal")
        with patch.object(gs, "dispatch_rescue_event") as dispatch:
            retry_button[1]()
            dispatch.assert_called_once_with("USER_A", "sos__request_1", resume_execution=True)
        self.assertEqual(self.journal.get(("USER_A", "sos__request_1"))["execution_id"], first["execution_id"])


class ReadyTests(unittest.TestCase):
    def test_exact_shape_retained_duplicates_and_size_fail_closed(self):
        validate_receiver_ready(ready())
        bad = [b"[]", b"x" * 513, ready().replace(b'"schema_version": 1', b'"schema_version": true'),
               ready().replace(b'{', b'{"ready_id":"1",', 1),
               ready().replace(b'"RECEIVER_READY"', b'"OTHER"')]
        for payload in bad:
            with self.subTest(payload=payload[:20]), self.assertRaises(ValueError):
                validate_receiver_ready(payload)
        with self.assertRaises(ValueError): validate_receiver_ready(ready(), True)

    def test_bounded_success_dedup_and_failed_sync_can_retry_same_ready(self):
        gate = ReceiverReadyGate(limit=2)
        key = gate.begin(ready())
        self.assertIsNone(gate.begin(ready()))
        gate.finish(key, False)
        self.assertEqual(gate.begin(ready()), key)
        gate.finish(key, True)
        self.assertIsNone(gate.begin(ready()))
        for char in "12":
            other = gate.begin(ready(char * 32)); gate.finish(other, True)
        self.assertEqual(len(gate.successes), 2)
        self.assertIsNotNone(gate.begin(ready()))

    def test_real_reply_waits_for_confirmation_before_success_cache(self):
        gate = ReceiverReadyGate()
        class Client:
            def __init__(self): self.calls = []; self.info = _PublishInfo(wait_result=False)
            def publish(self, topic, payload, **kwargs):
                self.calls.append((topic, json.loads(payload), kwargs)); return self.info
        client = Client()
        with patch.object(gs, "receiver_ready_gate", gate):
            key = gate.begin(ready()); gs._respond_to_receiver_ready(client, key)
            self.assertIsNotNone(gate.begin(ready()))
            client.info = _PublishInfo()
            gs._respond_to_receiver_ready(client, key)
            self.assertIsNone(gate.begin(ready()))
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.calls[1][0], gs.MQTT_TOPIC_RESCUE_SYNC)
        self.assertEqual(client.calls[1][2], {"qos": 1, "retain": False})

    def test_callback_spawns_worker_once_and_rejects_retained_ready(self):
        with patch.object(gs, "receiver_ready_gate", ReceiverReadyGate()), patch.object(gs.threading, "Thread") as thread:
            msg = SimpleNamespace(topic=gs.MQTT_TOPIC_RECEIVER_READY, payload=ready(), retain=True)
            gs._on_mqtt_message(None, None, msg)
            thread.assert_not_called()
            msg.retain = False
            gs._on_mqtt_message(None, None, msg); gs._on_mqtt_message(None, None, msg)
            thread.assert_called_once()
            thread.return_value.start.assert_called_once()

    def test_connect_subscribes_ready_and_preserves_own_sync(self):
        client = unittest.mock.Mock()
        with patch.object(gs, "_schedule_ui"), patch.object(gs, "mqtt_connected", False), \
             patch.object(gs.threading, "Thread") as worker:
            gs._on_mqtt_connect(client, None, None, 0)
        client.subscribe.assert_any_call(gs.MQTT_TOPIC_RECEIVER_READY, qos=1)
        sync = next(call for call in worker.call_args_list if call.kwargs["target"] is gs._respond_to_receiver_ready)
        self.assertIsNone(sync.kwargs["args"][1])
        self.assertTrue(any(call.kwargs["target"] is gs.query_saved_executions for call in worker.call_args_list))
        self.assertEqual(worker.return_value.start.call_count, 2)

    def test_sync_failure_retries_without_second_receiver_ready(self):
        gate = ReceiverReadyGate()
        client = unittest.mock.Mock()
        client.publish.side_effect = [_PublishInfo(rc=7), _PublishInfo()]
        with patch.object(gs, "receiver_ready_gate", gate), patch.object(gs, "mqtt_client", client), \
             patch.object(gs, "mqtt_connected", True), patch.object(gs, "_wait_for_sync_retry") as wait:
            key = gate.begin(ready())
            gs._respond_to_receiver_ready(client, key)
            self.assertIsNone(gate.begin(ready()))
        self.assertEqual(client.publish.call_count, 2)
        wait.assert_called_once_with(1.0)

    def test_sync_retry_stops_when_connection_generation_changes(self):
        gate = ReceiverReadyGate()
        client = unittest.mock.Mock()
        client.publish.return_value = _PublishInfo(rc=7)
        def disconnect(_delay): gs.mqtt_connection_generation += 1
        with patch.object(gs, "receiver_ready_gate", gate), patch.object(gs, "mqtt_client", client), \
             patch.object(gs, "mqtt_connected", True), patch.object(gs, "mqtt_connection_generation", 50), \
             patch.object(gs, "_wait_for_sync_retry", side_effect=disconnect):
            gs._respond_to_receiver_ready(client, gate.begin(ready()))
            self.assertIsNotNone(gate.begin(ready()))
        client.publish.assert_called_once()


class ConfigurationAndStatusTests(unittest.TestCase):
    def test_cached_or_named_firebase_app_cannot_use_another_database(self):
        cfg = {"GS_FIREBASE_DATABASE_URL": "https://new-synthetic.firebaseio.com"}
        with patch.dict(gs.os.environ, cfg), patch.object(gs, "DEMO_SCREENSHOT_MODE", False), \
             patch.object(gs, "rt_db", object()), patch.object(gs, "_firebase_active_target", "https://old-synthetic.firebaseio.com"):
            with self.assertRaisesRegex(ValueError, "target changed"): gs.initialize_firebase()
        app = SimpleNamespace(options={"databaseURL": "https://old-synthetic.firebaseio.com"})
        with patch.dict(gs.os.environ, cfg), patch.object(gs, "DEMO_SCREENSHOT_MODE", False), \
             patch.object(gs, "rt_db", None), patch.object(gs.firebase_admin, "get_app", return_value=app), \
             patch.object(gs.db, "reference") as ref:
            with self.assertRaisesRegex(ValueError, "different target"): gs.initialize_firebase()
            ref.assert_not_called()

    def test_explicit_missing_credential_path_does_not_fall_back(self):
        with patch.dict(gs.os.environ, {"GS_FIREBASE_CREDENTIALS": "/synthetic/missing-key.json"}), \
             patch.object(gs.Path, "is_file", return_value=False):
            with self.assertRaisesRegex(FileNotFoundError, "GS_FIREBASE_CREDENTIALS"):
                gs.get_firebase_credential_path()

    def test_mission_status_database_lookup_runs_off_network_callback(self):
        message = SimpleNamespace(topic=gs.MQTT_TOPIC_STATUS, payload=b'{"status":"ONLINE"}')
        with patch.object(gs.threading, "Thread") as worker:
            gs._on_mqtt_message(None, None, message)
        self.assertIs(worker.call_args.kwargs["target"], gs._process_drone_status_message)
        worker.return_value.start.assert_called_once()

    def test_task_optional_bounds_match_uav_and_wire_points_are_objects(self):
        base = {"schema_version": 2, "execution_id": "exec-x", "mission_id": "U/E", "mission_type": "event",
                "waypoints": [{"latitude": 0, "longitude": 0}]}
        for field, value in [("altitude", 0.49), ("altitude", 120.01), ("hover_seconds", -0.1),
                             ("hover_seconds", 600.1), ("waypoints", [[0, 0]])]:
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                task_fingerprint({**base, field: value})
        for altitude in [0.5, 120]:
            for hover in [0, 600]: task_fingerprint({**base, "altitude": altitude, "hover_seconds": hover})

    def test_v2_status_is_correlated_zero_based_and_does_not_commit_ledger(self):
        with tempfile.TemporaryDirectory() as folder:
            journal = DispatchJournal(Path(folder) / "status.sqlite3", "synthetic")
            payload = {"schema_version": 2, "execution_id": "exec-status", "mission_id": "U/E", "mission_type": "event",
                       "waypoints": [{"latitude": 0, "longitude": 0}], "return_to_launch": True}
            journal.prepare(("U", "E"), payload)
            report = {"schema_version": 2, "execution_id": "exec-status", "mission_id": "U/E", "status": "NAVIGATING", "waypoint_index": 0}
            self.assertIn("1/1", gs.correlated_status_display(report, journal))
            self.assertIsNone(gs.correlated_status_display({**report, "execution_id": "unknown"}, journal))
            self.assertIsNone(gs.correlated_status_display({**report, "mission_id": "OTHER"}, journal))
            landing = gs.correlated_status_display({**report, "status": "LANDING", "phase": "LAND_REQUESTED", "waypoint_index": None}, journal)
            self.assertIn("physical touchdown not confirmed", landing)
            self.assertEqual(journal.get(("U", "E"))["state"], "PREPARED")
            self.assertIn("Return-to-launch", gs.correlated_status_display({**report, "waypoint_index": 1}, journal))
            with self.assertRaises(ValueError): gs.correlated_status_display({**report, "waypoint_index": 2}, journal)
            journal.close()


if __name__ == "__main__":
    unittest.main()
