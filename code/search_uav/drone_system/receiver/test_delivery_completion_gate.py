import json
import tempfile
import unittest
from pathlib import Path

from receiver_test_support import ExecutionManager, task, completed_status
from phone_sos_receiver import RescueStore, RescueDeliveryCoordinator, TRIGGER_GS_SYNC
from test_phone_sos_receiver import sample_payload, matching_ack
from test_capture_v2 import capture


class DeliveryCompletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'execution.json'
        self.engine = ExecutionManager(str(self.path))
        self.addCleanup(self.engine.close)
        self.engine.admit(task())
        self.engine.feedback('TEST_USER/bench_001', 'exec-a', 0, 'ACCEPTED')
        self.engine.feedback('TEST_USER/bench_001', 'exec-a', 0, 'ARRIVED', position_valid=True, feedback_seq=1)
        self.store = RescueStore(Path(self.temp.name) / 'receiver', self.path)
        self.sent = []
        self.coordinator = RescueDeliveryCoordinator(self.store, lambda topic, body: self.sent.append(json.loads(body)))
        self.context = self.engine.collection_context()
        self.v1, _ = self.store.store(sample_payload(), carrier_context=self.context)
        context = self.store.captures.issue_context('TEST_USER', 'bench_001', 'capture-a', lambda user: self.context)
        self.v2, _ = self.store.captures.store(capture(context))

    def test_sync_retry_and_forged_status_cannot_bypass_final_hold(self):
        self.store.prepare_delivery(self.v1, TRIGGER_GS_SYNC)
        self.store.captures.prepare_delivery(self.v2, TRIGGER_GS_SYNC)
        self.store.remember_land_request('TEST_USER/bench_001')
        self.assertEqual(self.coordinator.handle_sync_request(), 0)
        self.assertEqual(self.coordinator.retry_pending(), 0)
        self.assertEqual(self.coordinator.handle_mission_status(completed_status()), 0)
        self.assertEqual(self.sent, [])

    def test_complete_then_failed_land_still_blocks_both_versions(self):
        self.engine.tick()
        self.engine.land_result(False)
        self.assertEqual(self.coordinator.handle_sync_request(), 0)
        self.engine.land_result(True)
        self.assertEqual(self.coordinator.handle_sync_request(), 2)
        self.assertEqual(len(self.sent), 2)

    def test_abort_never_grants_delivery_even_after_queue_complete(self):
        self.engine.tick()
        self.engine.land_result(True, aborted=True)
        self.assertEqual(self.coordinator.handle_sync_request(), 0)
        self.assertEqual(self.sent, [])

        self.engine.land_result(True)
        self.assertFalse(self.engine.snapshot()['delivery_eligible'])

    def test_legacy_unbound_v1_is_preserved_not_reassigned_on_duplicate(self):
        data = sample_payload()
        data.update(request_id='legacy', mission_id='TEST_USER/legacy')
        old, _ = self.store.store(data)
        self.store.store(data, carrier_context=self.context)
        self.engine.tick()
        self.engine.land_result(True)
        self.assertFalse(self.store.can_deliver(old))
        self.assertTrue(self.store._record_path('legacy').exists())
        self.assertEqual(self.coordinator.handle_sync_request(), 2)

    def test_completion_survives_bridge_exit_but_changed_fingerprint_blocks(self):
        self.engine.tick()
        self.engine.land_result(True)
        self.engine.close()
        reopened = RescueStore(self.store.root, self.path)
        self.assertTrue(reopened.can_deliver(self.v1))
        self.assertTrue(reopened.captures.can_deliver(self.v2))
        saved = json.loads(self.path.read_text())
        saved['executions']['exec-a']['content_fingerprint'] = '0'*64
        self.path.write_text(json.dumps(saved))
        self.assertFalse(reopened.can_deliver(self.v1))
        self.assertFalse(reopened.captures.can_deliver(self.v2))
    def test_synchronous_ack_then_publish_error_keeps_acknowledged(self):
        self.engine.tick()
        self.engine.land_result(True)
        def publish(topic, body):
            envelope = json.loads(body)
            if envelope['schema_version'] == 1:
                self.coordinator.handle_ground_ack(matching_ack(self.v1, envelope['envelope_sha256']))
                raise RuntimeError('completion callback raced ACK')
        self.coordinator.publish = publish
        self.coordinator.handle_sync_request()
        self.assertEqual(self.store.read_delivery('bench_001')['state'], 'ACKNOWLEDGED_BY_GS')


if __name__ == '__main__':
    unittest.main()
