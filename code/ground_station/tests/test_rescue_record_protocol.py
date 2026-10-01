import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rescue_record_protocol import (
    ACK_FIELDS,
    ACK_PUBLISHED,
    RECEIVED_STORED,
    AckPublishError,
    GroundRescueStore,
    MqttConfigurationError,
    PAYLOAD_FIELDS,
    RescueRecordError,
    RescueRecordPersistenceError,
    TRIGGER_SEMANTICS,
    build_ground_ack,
    calculate_envelope_sha256,
    configure_mqtt_client_auth,
    format_record_summary,
    parse_onboard_envelope,
    parse_onboard_record,
    process_onboard_envelope,
    publish_ground_ack,
)


def canonical_json(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def signed_envelope(trigger="MISSION_QUEUE_COMPLETE_LAND_REQUESTED"):
    payload = {
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
        "device": "synthetic-bench-client",
        "gps_points": [
            {
                "latitude": 22.3519,
                "longitude": 114.1829,
                "captured_at": "2026-08-01T12:00:00.001Z",
            }
        ],
        "test_mode": True,
    }
    digest = hashlib.sha256(canonical_json(payload)).hexdigest()
    record = dict(payload)
    record.update(
        {
            "uav_received_at": "2026-08-01T12:00:01Z",
            "payload_sha256": digest,
            "storage_state": "STORED",
        }
    )
    envelope = {
        "schema_version": 1,
        "delivery_state": "FORWARDED_TO_GS",
        "forward_trigger": trigger,
        "trigger_semantics": TRIGGER_SEMANTICS[trigger],
        "forwarded_at": "2026-08-01T12:01:00Z",
        "record": record,
    }
    envelope["envelope_sha256"] = calculate_envelope_sha256(envelope)
    return envelope


def resign(envelope):
    envelope["envelope_sha256"] = calculate_envelope_sha256(envelope)
    return envelope


class RescueRecordProtocolTests(unittest.TestCase):
    def test_parses_both_hashes_and_builds_strict_matching_ack(self):
        envelope = signed_envelope()
        verified = parse_onboard_envelope(json.dumps(envelope).encode("utf-8"))
        record = parse_onboard_record(verified)
        ack = build_ground_ack(verified)

        self.assertEqual(record["forward_trigger"], envelope["forward_trigger"])
        self.assertEqual(record["envelope_sha256"], envelope["envelope_sha256"])
        self.assertEqual(set(ack), set(ACK_FIELDS))
        self.assertEqual(ack["request_id"], "bench_001")
        self.assertEqual(ack["payload_sha256"], record["payload_sha256"])
        self.assertEqual(ack["envelope_sha256"], record["envelope_sha256"])
        self.assertEqual(ack["status"], "ACKNOWLEDGED_BY_GS")

    def test_rejects_tampered_envelope_metadata(self):
        value = signed_envelope()
        value["forwarded_at"] = "2026-08-01T12:02:00Z"
        with self.assertRaisesRegex(RescueRecordError, "envelope hash mismatch"):
            parse_onboard_envelope(value)

    def test_rejects_tampered_payload_even_if_envelope_is_resigned(self):
        value = signed_envelope()
        value["record"]["latitude"] = 22.5
        resign(value)
        with self.assertRaisesRegex(RescueRecordError, "payload hash mismatch"):
            parse_onboard_envelope(value)

    def test_rejects_resigned_invalid_forward_trigger(self):
        value = signed_envelope()
        value["forward_trigger"] = "PHYSICAL_TOUCHDOWN_CONFIRMED"
        resign(value)
        with self.assertRaisesRegex(RescueRecordError, "invalid forward_trigger"):
            parse_onboard_envelope(value)

    def test_rejects_resigned_semantics_that_do_not_match_trigger(self):
        value = signed_envelope()
        value["trigger_semantics"] = "Physical touchdown confirmed."
        resign(value)
        with self.assertRaisesRegex(RescueRecordError, "trigger_semantics"):
            parse_onboard_envelope(value)

    def test_rejects_resigned_invalid_storage_state(self):
        value = signed_envelope()
        value["record"]["storage_state"] = "NOT_STORED"
        resign(value)
        with self.assertRaisesRegex(RescueRecordError, "invalid storage_state"):
            parse_onboard_envelope(value)

    def test_rejects_forwarding_timestamp_before_uav_receipt(self):
        value = signed_envelope()
        value["forwarded_at"] = "2026-08-01T11:59:59Z"
        resign(value)
        with self.assertRaisesRegex(RescueRecordError, "precedes"):
            parse_onboard_envelope(value)

    def test_summary_preserves_real_trigger_and_semantics(self):
        landing = format_record_summary(
            parse_onboard_record(signed_envelope()), RECEIVED_STORED
        )
        sync = format_record_summary(
            parse_onboard_record(signed_envelope("GROUND_STATION_SYNC_REQUEST")),
            ACK_PUBLISHED,
        )
        self.assertIn("MISSION_QUEUE_COMPLETE_LAND_REQUESTED", landing)
        self.assertIn(TRIGGER_SEMANTICS["MISSION_QUEUE_COMPLETE_LAND_REQUESTED"], landing)
        self.assertIn("LAND_REQUESTED != physical touchdown", landing)
        self.assertIn("Delivery: RECEIVED_STORED", landing)
        self.assertIn("GROUND_STATION_SYNC_REQUEST", sync)
        self.assertIn(TRIGGER_SEMANTICS["GROUND_STATION_SYNC_REQUEST"], sync)
        self.assertIn("SYNC may occur before LANDING", sync)
        self.assertIn("Delivery: ACK_PUBLISHED", sync)


class PersistenceAndAckTests(unittest.TestCase):
    def test_temporary_file_failure_is_reported_as_persistence_failure_without_ack(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            published = []
            with patch('rescue_record_protocol.tempfile.mkstemp', side_effect=OSError('disk full')):
                with self.assertRaises(RescueRecordPersistenceError):
                    process_onboard_envelope(signed_envelope(), store=GroundRescueStore(Path(temp_dir)), publish_ack=published.append)
            self.assertEqual(published, [])

    def test_envelope_sync_failure_after_record_write_is_recoverable_without_false_ack(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / 'records').mkdir()
            (root / 'envelopes').mkdir()
            store = GroundRescueStore(root)
            published = []

            def fail_envelope_sync(path):
                if path.name == 'envelopes':
                    raise OSError('envelope directory synchronization failed')

            with patch('rescue_record_protocol._fsync_directory', side_effect=fail_envelope_sync):
                with self.assertRaises(RescueRecordPersistenceError):
                    process_onboard_envelope(signed_envelope(), store=store, publish_ack=published.append)
            self.assertEqual(published, [])
            self.assertEqual(len(list((root / 'records').glob('*.json'))), 1)
            self.assertEqual(len(list((root / 'envelopes').glob('*.json'))), 1)
            process_onboard_envelope(signed_envelope(), store=store, publish_ack=published.append)
            self.assertEqual(len(published), 1)

    def test_directory_sync_failure_does_not_publish_ack(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            published = []
            states = []
            with patch("rescue_record_protocol._fsync_directory", side_effect=OSError("disk error")):
                with self.assertRaises(RescueRecordPersistenceError):
                    process_onboard_envelope(
                        signed_envelope(), store=GroundRescueStore(Path(temp_dir)),
                        publish_ack=published.append,
                        on_state=lambda state, record: states.append(state),
                    )
            self.assertEqual(published, [])
            self.assertEqual(states, [])

    def test_conflicting_existing_evidence_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = GroundRescueStore(Path(temp_dir))
            path = Path(temp_dir) / "records" / "evidence.json"
            store._write_json_atomic(path, {"value": "original"})
            with self.assertRaises(RescueRecordPersistenceError):
                store._write_json_atomic(path, {"value": "conflict"})
            self.assertEqual(json.loads(path.read_text()), {"value": "original"})

    def test_create_race_verifies_winner_without_overwriting_it(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = GroundRescueStore(Path(temp_dir))
            path = Path(temp_dir) / "records" / "evidence.json"

            def competing_writer(source, destination):
                Path(destination).write_text('{"value":"winner"}', encoding="utf-8")
                raise FileExistsError("another writer won")

            with patch("rescue_record_protocol.os.link", side_effect=competing_writer):
                with self.assertRaises(RescueRecordPersistenceError):
                    store._write_json_atomic(path, {"value": "loser"})
            self.assertEqual(json.loads(path.read_text()), {"value": "winner"})

    def test_persists_complete_record_and_envelope_before_ack(self):
        envelope = signed_envelope()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = GroundRescueStore(root)
            events = []
            published = []

            def on_state(state, record):
                events.append(state)
                if state == RECEIVED_STORED:
                    self.assertEqual(len(list((root / "records").glob("*.json"))), 1)
                    self.assertEqual(len(list((root / "envelopes").glob("*.json"))), 1)

            def publish(ack):
                events.append("PUBLISH_CALLED")
                self.assertEqual(len(list((root / "records").glob("*.json"))), 1)
                self.assertEqual(len(list((root / "envelopes").glob("*.json"))), 1)
                published.append(ack)

            result = process_onboard_envelope(
                envelope,
                store=store,
                publish_ack=publish,
                on_state=on_state,
            )

            self.assertEqual(
                events,
                [RECEIVED_STORED, "PUBLISH_CALLED", ACK_PUBLISHED],
            )
            self.assertEqual(result["state"], ACK_PUBLISHED)
            self.assertEqual(published[0], result["ack"])
            with result["record_path"].open(encoding="utf-8") as handle:
                self.assertEqual(json.load(handle), envelope["record"])
            with result["envelope_path"].open(encoding="utf-8") as handle:
                self.assertEqual(json.load(handle), envelope)

    def test_publish_failure_keeps_stored_evidence_without_false_ack_state(self):
        envelope = signed_envelope()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            states = []

            def fail_publish(ack):
                raise AckPublishError("broker rejected ACK")

            with self.assertRaises(AckPublishError):
                process_onboard_envelope(
                    envelope,
                    store=GroundRescueStore(root),
                    publish_ack=fail_publish,
                    on_state=lambda state, record: states.append(state),
                )

            self.assertEqual(states, [RECEIVED_STORED])
            self.assertEqual(len(list((root / "records").glob("*.json"))), 1)
            self.assertEqual(len(list((root / "envelopes").glob("*.json"))), 1)


class FakePublishInfo:
    def __init__(self, rc=0, waited=True, published=True):
        self.rc = rc
        self.waited = waited
        self.published = published
        self.wait_timeout = None

    def wait_for_publish(self, timeout):
        self.wait_timeout = timeout
        return self.waited

    def is_published(self):
        return self.published


class FakeClient:
    def __init__(self, info):
        self.info = info
        self.calls = []

    def publish(self, topic, payload, qos):
        self.calls.append((topic, json.loads(payload), qos))
        return self.info


class PublishResultTests(unittest.TestCase):
    def setUp(self):
        self.ack = build_ground_ack(signed_envelope())

    def test_publish_checks_rc_waits_bounded_and_requires_published(self):
        info = FakePublishInfo()
        client = FakeClient(info)
        publish_ground_ack(client, "alin1/rescue/ground_ack", self.ack, timeout=0.25)
        self.assertEqual(info.wait_timeout, 0.25)
        self.assertEqual(client.calls[0][2], 1)
        self.assertEqual(set(client.calls[0][1]), set(ACK_FIELDS))

    def test_publish_rc_failure_is_not_acknowledged(self):
        client = FakeClient(FakePublishInfo(rc=4))
        with self.assertRaisesRegex(AckPublishError, "rc=4"):
            publish_ground_ack(client, "alin1/rescue/ground_ack", self.ack)

    def test_publish_timeout_is_not_acknowledged(self):
        client = FakeClient(FakePublishInfo(waited=False, published=False))
        with self.assertRaisesRegex(AckPublishError, "timed out"):
            publish_ground_ack(client, "alin1/rescue/ground_ack", self.ack)


class FakeAuthClient:
    def __init__(self):
        self.auth_calls = []

    def username_pw_set(self, username, password):
        self.auth_calls.append((username, password))


class RuntimeMqttAuthTests(unittest.TestCase):
    def test_applies_optional_runtime_credentials(self):
        client = FakeAuthClient()
        configured = configure_mqtt_client_auth(
            client,
            {"MQTT_USERNAME": "bench-user", "MQTT_PASSWORD": "bench-password"},
        )
        self.assertTrue(configured)
        self.assertEqual(client.auth_calls, [("bench-user", "bench-password")])

    def test_no_credentials_leaves_client_unchanged(self):
        client = FakeAuthClient()
        self.assertFalse(configure_mqtt_client_auth(client, {}))
        self.assertEqual(client.auth_calls, [])

    def test_password_without_username_is_rejected(self):
        client = FakeAuthClient()
        with self.assertRaises(MqttConfigurationError):
            configure_mqtt_client_auth(client, {"MQTT_PASSWORD": "secret"})
        self.assertEqual(client.auth_calls, [])


if __name__ == "__main__":
    unittest.main()
