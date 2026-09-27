import json
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.request
from pathlib import Path

from phone_sos_receiver import (
    PayloadValidationError,
    RecordConflictError,
    RescueDeliveryCoordinator,
    RescueHttpServer,
    RescueStore,
    RECEIVER_VERSION,
    SYSTEM_RELEASE_ID,
    TRIGGER_GS_SYNC,
    TRIGGER_LAND_REQUESTED,
    compute_envelope_sha256,
    validate_payload,
)


def sample_payload():
    return {
        "schema_version": 1,
        "request_id": "bench_001",
        "mission_id": "TEST_USER/bench_001",
        "user_id": "TEST_USER",
        "latitude": 22.352,
        "longitude": 114.183,
        "accuracy": 3.5,
        "captured_at": "2026-08-01T12:00:00.000Z",
        "client_timestamp_ms": 1785585600000,
        "status": "PENDING",
        "device": "android",
        "gps_points": [
            {
                "latitude": 22.3519,
                "longitude": 114.1829,
                "captured_at": "2026-08-01T11:59:55.000Z",
            }
        ],
        "test_mode": True,
    }


def matching_ack(record, envelope_sha256="e" * 64):
    return {
        "schema_version": 1,
        "request_id": record["request_id"],
        "mission_id": record["mission_id"],
        "payload_sha256": record["payload_sha256"],
        "envelope_sha256": envelope_sha256,
        "status": "ACKNOWLEDGED_BY_GS",
        "acknowledged_at": "2026-08-01T12:01:00Z",
    }


class RescueReceiverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = RescueStore(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def test_validates_and_stores_immutable_record(self):
        record, duplicate = self.store.store(sample_payload())
        self.assertFalse(duplicate)
        self.assertEqual(record["storage_state"], "STORED")
        self.assertEqual(len(record["payload_sha256"]), 64)
        on_disk = self.store.read_record("bench_001")
        self.assertEqual(on_disk["mission_id"], "TEST_USER/bench_001")

    def test_same_request_is_idempotent_but_conflict_is_rejected(self):
        first, _ = self.store.store(sample_payload())
        second, duplicate = self.store.store(sample_payload())
        self.assertTrue(duplicate)
        self.assertEqual(first["payload_sha256"], second["payload_sha256"])
        changed = sample_payload()
        changed["latitude"] = 22.4
        with self.assertRaises(RecordConflictError):
            self.store.store(changed)

    def test_duplicate_repairs_missing_outbox_after_interrupted_first_store(self):
        record, _ = self.store.store(sample_payload())
        self.store._outbox_path(record["request_id"]).unlink()

        duplicate_record, duplicate = self.store.store(sample_payload())

        self.assertTrue(duplicate)
        self.assertEqual(duplicate_record["payload_sha256"], record["payload_sha256"])
        self.assertTrue(self.store._outbox_path(record["request_id"]).is_file())
        self.assertEqual(
            self.store.read_delivery(record["request_id"])["state"],
            "STORED_ONBOARD",
        )

    def test_restart_recovers_record_without_waiting_for_phone_retransmission(self):
        with patch.object(self.store, "_ensure_outbox", side_effect=OSError("injected")):
            with self.assertRaises(OSError):
                self.store.store(sample_payload())
        restarted = RescueStore(Path(self.temp.name))
        self.assertEqual(restarted.recovered_outboxes, 1)
        self.assertEqual(len(list(restarted.pending_records())), 1)
        self.assertEqual(len(list(restarted.records_dir.glob("*.json"))), 1)

    def test_corrupt_outbox_is_preserved_without_blocking_healthy_record(self):
        self.store.store(sample_payload())
        other = sample_payload()
        other.update(request_id="bench_002", mission_id="TEST_USER/bench_002")
        self.store.store(other)
        corrupt_path = self.store._outbox_path("bench_001")
        corrupt_path.write_text("{", encoding="utf-8")
        published = []
        coordinator = RescueDeliveryCoordinator(self.store, lambda topic, body: published.append(body))
        self.assertEqual(coordinator.handle_sync_request(), 1)
        self.assertEqual(json.loads(published[0])["record"]["request_id"], "bench_002")
        self.assertEqual(corrupt_path.read_text(), "{")
        self.assertEqual(self.store.health_summary()["unreadable_outbox_entries"], 1)

    def test_tampered_record_is_not_forwarded_or_silently_repaired(self):
        self.store.store(sample_payload())
        path = self.store._record_path("bench_001")
        record = json.loads(path.read_text())
        record["latitude"] = 23.0
        path.write_text(json.dumps(record))
        self.assertEqual(list(self.store.pending_records()), [])
        self.assertEqual(json.loads(path.read_text())["latitude"], 23.0)

    def test_directory_sync_failure_cannot_return_a_storage_success(self):
        with patch("phone_sos_receiver._fsync_directory", side_effect=OSError("disk sync failed")):
            with self.assertRaises(OSError):
                self.store.store(sample_payload())
        record, duplicate = self.store.store(sample_payload())
        self.assertTrue(duplicate)
        self.assertEqual(record["storage_state"], "STORED")
        self.assertEqual(len(list(self.store.pending_records())), 1)

    def test_rejects_out_of_range_gps(self):
        payload = sample_payload()
        payload["latitude"] = 100.0
        with self.assertRaises(PayloadValidationError):
            validate_payload(payload)

    def test_test_mode_requires_a_json_boolean(self):
        for invalid in ("false", "true", 0, 1, None):
            payload = sample_payload()
            payload["test_mode"] = invalid
            with self.subTest(invalid=invalid):
                with self.assertRaises(PayloadValidationError):
                    validate_payload(payload)

    def test_receiver_rejects_fields_that_ground_station_cannot_accept(self):
        for field, value in (("schema_version", True), ("captured_at", "not-a-date"),
                             ("captured_at", "2026-08-01T12:00:00+00:00"),
                             ("device", ""), ("device", "x" * 33), ("device", "\ud800")):
            with self.subTest(field=field, value=repr(value)):
                payload = sample_payload()
                payload[field] = value
                with self.assertRaises(PayloadValidationError):
                    self.store.store(payload)
        payload = sample_payload()
        payload["gps_points"][0]["captured_at"] = "not-a-date"
        with self.assertRaises(PayloadValidationError):
            self.store.store(payload)
        self.assertEqual(len(list(self.store.records_dir.glob("*.json"))), 0)

    def test_landing_state_forwards_matching_record_then_ground_ack_closes_outbox(self):
        record, _ = self.store.store(sample_payload())
        published = []
        coordinator = RescueDeliveryCoordinator(
            self.store, lambda topic, body: published.append((topic, body))
        )
        count = coordinator.handle_mission_status(
            {"status": "LANDING", "mission_id": "TEST_USER/bench_001"}
        )
        self.assertEqual(count, 1)
        self.assertEqual(len(published), 1)
        envelope = json.loads(published[0][1])
        self.assertEqual(
            envelope["forward_trigger"],
            TRIGGER_LAND_REQUESTED,
        )
        self.assertIn("physical touchdown is not confirmed", envelope["trigger_semantics"])
        self.assertEqual(
            envelope["envelope_sha256"],
            compute_envelope_sha256(envelope),
        )
        tampered = dict(envelope)
        tampered["forwarded_at"] = "2026-08-01T12:02:00Z"
        self.assertNotEqual(
            envelope["envelope_sha256"],
            compute_envelope_sha256(tampered),
        )
        tampered_record = json.loads(json.dumps(envelope))
        tampered_record["record"]["uav_received_at"] = "2026-08-01T12:03:00Z"
        self.assertNotEqual(
            envelope["envelope_sha256"],
            compute_envelope_sha256(tampered_record),
        )

        delivery = self.store.read_delivery(record["request_id"])
        coordinator.handle_ground_ack(
            matching_ack(record, delivery["envelope_sha256"])
        )
        state = self.store.read_delivery("bench_001")
        self.assertEqual(state["state"], "ACKNOWLEDGED_BY_GS")
        self.assertEqual(state["delivery_attempts"], 1)
        self.assertEqual(state["last_attempt_result"], "PUBLISHED")
        self.assertEqual(state["ground_ack"]["status"], "ACKNOWLEDGED_BY_GS")
        self.assertEqual(
            state["ground_ack"]["envelope_sha256"],
            delivery["envelope_sha256"],
        )

    def test_ground_ack_requires_forwarded_state_and_all_matching_fields(self):
        record, _ = self.store.store(sample_payload())
        coordinator = RescueDeliveryCoordinator(self.store, lambda *_: None)

        with self.assertRaises(PayloadValidationError):
            coordinator.handle_ground_ack(matching_ack(record))

        coordinator.handle_mission_status(
            {"status": "LANDING", "mission_id": record["mission_id"]}
        )
        delivery = self.store.read_delivery(record["request_id"])
        valid = matching_ack(record, delivery["envelope_sha256"])
        invalid_acks = []
        for field, value in (
            ("schema_version", 2),
            ("status", "RECEIVED"),
            ("request_id", "other_request"),
            ("mission_id", "OTHER_USER/bench_001"),
            ("payload_sha256", "0" * 64),
            ("envelope_sha256", None),
            ("envelope_sha256", "0" * 64),
            ("acknowledged_at", ""),
        ):
            candidate = dict(valid)
            candidate[field] = value
            invalid_acks.append(candidate)

        for candidate in invalid_acks:
            with self.subTest(candidate=candidate):
                with self.assertRaises(PayloadValidationError):
                    coordinator.handle_ground_ack(candidate)
                self.assertEqual(
                    self.store.read_delivery(record["request_id"])["state"],
                    "FORWARDED_TO_GS",
                )

        coordinator.handle_ground_ack(valid)
        with self.assertRaises(PayloadValidationError):
            coordinator.handle_ground_ack(valid)

    def test_publish_failure_keeps_record_pending_for_retry(self):
        record, _ = self.store.store(sample_payload())

        def fail_publish(_topic, _body):
            raise RuntimeError("broker unavailable")

        coordinator = RescueDeliveryCoordinator(self.store, fail_publish)
        count = coordinator.handle_mission_status(
            {"status": "LANDING", "mission_id": record["mission_id"]}
        )

        self.assertEqual(count, 0)
        self.assertEqual(
            self.store.read_delivery(record["request_id"])["state"],
            "STORED_ONBOARD",
        )
        state = self.store.read_delivery(record["request_id"])
        self.assertEqual(state["delivery_attempts"], 1)
        self.assertEqual(state["last_attempt_result"], "DEFERRED")
        self.assertEqual(state["last_delivery_error"], "RuntimeError")
        self.assertEqual(len(list(self.store.pending_records())), 1)

    def test_sync_replay_is_explicitly_not_landing_evidence(self):
        self.store.store(sample_payload())
        published = []
        coordinator = RescueDeliveryCoordinator(
            self.store, lambda topic, body: published.append((topic, body))
        )

        self.assertEqual(coordinator.handle_sync_request(), 1)
        envelope = json.loads(published[0][1])
        self.assertEqual(envelope["forward_trigger"], TRIGGER_GS_SYNC)
        self.assertIn("not landing evidence", envelope["trigger_semantics"])

    def test_land_requested_phase_and_legacy_landing_are_both_supported(self):
        record, _ = self.store.store(sample_payload())
        published = []
        coordinator = RescueDeliveryCoordinator(
            self.store, lambda topic, body: published.append((topic, body))
        )

        count = coordinator.handle_mission_status({
            "status": "LANDING",
            "phase": "LAND_REQUESTED",
            "land_command_requested": True,
            "touchdown_confirmed": False,
            "mission_id": record["mission_id"],
        })
        self.assertEqual(count, 1)

        other = sample_payload()
        other["request_id"] = "bench_002"
        other["mission_id"] = "TEST_USER/bench_002"
        record2, _ = self.store.store(other)
        count = coordinator.handle_mission_status({
            "status": "LAND_REQUESTED",
            "mission_id": record2["mission_id"],
        })
        self.assertEqual(count, 1)

    def test_landing_with_explicit_false_command_request_is_not_forwarded(self):
        record, _ = self.store.store(sample_payload())
        coordinator = RescueDeliveryCoordinator(self.store, lambda *_: None)
        count = coordinator.handle_mission_status({
            "status": "LANDING",
            "phase": "LAND_REQUESTED",
            "land_command_requested": False,
            "mission_id": record["mission_id"],
        })
        self.assertEqual(count, 0)
        self.assertEqual(
            self.store.read_delivery(record["request_id"])["state"],
            "STORED_ONBOARD",
        )

    def test_health_reports_versions_and_aggregate_persistence_only(self):
        self.store.store(sample_payload())
        server = RescueHttpServer(("127.0.0.1", 0), self.store)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(
                "http://127.0.0.1:{}/health".format(server.server_port),
                timeout=2,
            ) as response:
                health = json.loads(response.read().decode("utf-8"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(health["receiver_version"], RECEIVER_VERSION)
        self.assertEqual(health["system_release_id"], SYSTEM_RELEASE_ID)
        self.assertEqual(health["persistence"]["records"], 1)
        self.assertEqual(health["persistence"]["stored_onboard"], 1)
        self.assertTrue(health["persistence"]["persistence_ready"])
        self.assertEqual(health["delivery_transport"]["mode"], "disabled")
        self.assertNotIn("data_dir", health)


if __name__ == "__main__":
    unittest.main()
