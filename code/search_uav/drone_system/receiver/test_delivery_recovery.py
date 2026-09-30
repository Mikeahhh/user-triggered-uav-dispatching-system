import json
from receiver_test_support import completed_store, context_for, completed_status
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import phone_sos_receiver as receiver
from test_phone_sos_receiver import matching_ack, sample_payload


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = completed_store(Path(self.temp.name))
        self.now = 1000.0
        self.published = []
        self.coordinator = self.make_coordinator()

    def tearDown(self):
        self.temp.cleanup()

    def make_coordinator(self, publish=None):
        return receiver.RescueDeliveryCoordinator(
            self.store, publish or (lambda topic, body: self.published.append(json.loads(body))),
            clock=lambda: self.now,
        )

    def test_late_record_replays_after_land_marker_and_restart(self):
        self.assertEqual(self.coordinator.handle_mission_status({
            'status': 'LAND_REQUESTED', 'mission_id': 'TEST_USER/bench_001',
        }), 0)
        self.store = completed_store(Path(self.temp.name))
        record, _ = self.store.store(sample_payload(), carrier_context=context_for())
        self.coordinator = self.make_coordinator()
        self.assertEqual(self.coordinator.retry_pending(), 1)
        self.assertEqual(self.published[0]['forward_trigger'], receiver.TRIGGER_LAND_REQUESTED)
        self.assertEqual(self.published[0]['record'], record)
        self.assertIn('physical touchdown is not confirmed', self.published[0]['trigger_semantics'])

    def test_unrelated_land_or_no_trigger_does_not_authorize_new_record(self):
        self.store.store(sample_payload())
        self.assertEqual(self.coordinator.retry_pending(), 0)
        self.coordinator.handle_mission_status({
            'status': 'LAND_REQUESTED', 'mission_id': 'TEST_USER/another_request',
        })
        self.assertEqual(self.coordinator.retry_pending(), 0)
        self.assertEqual(self.published, [])

    def test_ack_loss_retries_same_envelope_then_stops_on_matching_ack(self):
        record, _ = self.store.store(sample_payload(), carrier_context=context_for())
        self.assertEqual(self.coordinator.handle_sync_request(), 1)
        self.assertEqual(self.coordinator.handle_sync_request(), 0)
        self.now += 2
        self.assertEqual(self.coordinator.retry_pending(), 1)
        self.assertEqual(self.published[0], self.published[1])
        self.coordinator.handle_ground_ack(matching_ack(record, self.published[0]['envelope_sha256']))
        self.now += 1000
        self.assertEqual(self.coordinator.retry_pending(), 0)
        self.store = completed_store(Path(self.temp.name))
        self.assertEqual(self.make_coordinator().retry_pending(), 0)

    def test_failed_publish_recovers_from_disk_with_same_envelope(self):
        self.store.store(sample_payload(), carrier_context=context_for())
        attempted = []

        def fail(topic, body):
            attempted.append(json.loads(body))
            raise RuntimeError('offline')

        self.assertEqual(self.make_coordinator(fail).handle_sync_request(), 0)
        self.store = completed_store(Path(self.temp.name))
        self.now += 2
        self.assertEqual(self.make_coordinator().retry_pending(), 1)
        self.assertEqual(attempted[0], self.published[0])
        self.assertEqual(self.published[0]['forward_trigger'], receiver.TRIGGER_GS_SYNC)

    def test_prepared_envelope_survives_crash_before_attempt_state(self):
        record, _ = self.store.store(sample_payload(), carrier_context=context_for())
        prepared = self.store.prepare_delivery(record, receiver.TRIGGER_GS_SYNC)
        self.store = completed_store(Path(self.temp.name))
        self.assertEqual(self.make_coordinator().retry_pending(), 1)
        self.assertEqual(self.published[0], prepared)

    def test_old_v1_forwarded_outbox_reconstructs_identical_envelope(self):
        record, _ = self.store.store(sample_payload(), carrier_context=context_for())
        self.coordinator.handle_sync_request()
        state = self.store.read_delivery(record['request_id'])
        del state['delivery_envelope']
        state.pop('retry_after_epoch')
        self.store._write_json_atomic(self.store._outbox_path(record['request_id']), state)
        self.store = completed_store(Path(self.temp.name))
        self.assertEqual(self.make_coordinator().retry_pending(), 1)
        self.assertEqual(self.published[0], self.published[1])

    def test_wrong_ack_preserves_retry_and_strict_schema_type(self):
        record, _ = self.store.store(sample_payload(), carrier_context=context_for())
        self.coordinator.handle_sync_request()
        for field, value in [('envelope_sha256', '0' * 64), ('schema_version', True)]:
            ack = matching_ack(record, self.published[0]['envelope_sha256'])
            ack[field] = value
            with self.assertRaises(receiver.PayloadValidationError):
                self.coordinator.handle_ground_ack(ack)
        self.now += 2
        self.assertEqual(self.coordinator.retry_pending(), 1)

    def test_backoff_interval_is_capped_and_persisted(self):
        record, _ = self.store.store(sample_payload(), carrier_context=context_for())
        self.coordinator.handle_sync_request()
        for attempt in range(1, 12):
            state = self.store.read_delivery(record['request_id'])
            delay = state['retry_after_epoch'] - self.now
            self.assertGreater(delay, 0)
            self.assertLessEqual(delay, receiver.RETRY_MAX_SECONDS)
            self.now = state['retry_after_epoch']
            self.assertEqual(self.coordinator.retry_pending(), 1)
        self.assertEqual(self.store.read_delivery(record['request_id'])['delivery_attempts'], 12)
        self.assertTrue(all(body == self.published[0] for body in self.published))


class FakeClient:
    def __init__(self, **kwargs):
        self.subscribed = []
        self.published = []

    def reconnect_delay_set(self, **kwargs):
        pass

    def subscribe(self, topic, qos):
        self.subscribed.append((topic, qos))
        return (0, len(self.subscribed))

    def publish(self, topic, body, qos, retain=False):
        self.published.append((topic, json.loads(body), qos, retain))
        return SimpleNamespace(rc=0, wait_for_publish=lambda timeout: None, is_published=lambda: True)


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = completed_store(Path(self.temp.name))
        with patch.object(receiver, 'mqtt', SimpleNamespace(Client=FakeClient, MQTT_ERR_SUCCESS=0)), patch.object(receiver, '_PAHO_V2', False):
            self.runtime = receiver.MqttRuntime(self.store, 'offline-test.invalid', 1883)
        self.client = self.runtime.client

    def tearDown(self):
        self.temp.cleanup()

    def connect_and_ack(self):
        self.runtime._on_connect(self.client, None, {}, 0)
        mids = sorted(self.runtime._subscription_mids)
        for mid in mids:
            self.runtime._on_subscribe(self.client, None, mid, [1])

    def test_ready_waits_for_all_subacks_and_publishes_nonretained_in_worker(self):
        self.runtime._on_connect(self.client, None, {}, 0)
        mids = sorted(self.runtime._subscription_mids)
        for mid in mids[:-1]:
            self.runtime._on_subscribe(self.client, None, mid, [1])
        self.runtime._service_recovery()
        self.assertFalse(self.runtime.connected.is_set())
        self.assertEqual(self.client.published, [])
        self.runtime._on_subscribe(self.client, None, mids[-1], [1])
        self.assertEqual(self.client.published, [])
        self.runtime._service_recovery()
        topic, ready, qos, retained = self.client.published[0]
        self.assertEqual(topic, receiver.DEFAULT_TOPIC_READY)
        self.assertEqual(set(ready), {'schema_version', 'message_type', 'receiver_id', 'ready_id'})
        self.assertEqual(ready['message_type'], 'RECEIVER_READY')
        self.assertRegex(ready['ready_id'], r'^[0-9a-f]{32}$')
        self.assertLessEqual(len(receiver.canonical_json(ready)), 512)
        self.assertEqual(qos, 1)
        self.assertFalse(retained)
        self.runtime._service_recovery()
        self.assertEqual(len(self.client.published), 1)

    def test_failed_suback_prevents_false_ready(self):
        self.runtime._on_connect(self.client, None, {}, 0)
        for mid in sorted(self.runtime._subscription_mids):
            self.runtime._on_subscribe(self.client, None, mid, [128])
        self.runtime._service_recovery()
        self.assertFalse(self.runtime.connected.is_set())
        self.assertEqual(self.client.published, [])

    def test_reconnect_creates_new_ready_identity(self):
        self.connect_and_ack()
        self.runtime._service_recovery()
        first = self.client.published[0][1]['ready_id']
        self.runtime._on_disconnect(self.client, None, 1)
        self.connect_and_ack()
        self.runtime._service_recovery()
        self.assertNotEqual(first, self.client.published[-1][1]['ready_id'])

    def test_retained_land_and_sync_are_not_new_trigger_events(self):
        for topic in (receiver.DEFAULT_TOPIC_STATUS, receiver.DEFAULT_TOPIC_SYNC):
            self.runtime._on_message(self.client, None, SimpleNamespace(topic=topic, payload=b'{}', retain=True))
        self.assertEqual(self.runtime._work_queue.qsize(), 0)

    def test_recovery_read_failure_does_not_kill_worker_service(self):
        self.connect_and_ack()
        with patch.object(self.runtime.coordinator, 'retry_pending', side_effect=OSError('disk unavailable')):
            self.runtime._service_recovery()
        self.assertTrue(self.runtime.connected.is_set())


if __name__ == '__main__':
    unittest.main()
