import fcntl
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from execution_state import ExecutionManager
from test_execution_state import mission


class CollectionContextTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'executions.json'
        self.now = 100.0
        self.engine = ExecutionManager(str(self.path), clock=lambda: self.now)
        self.addCleanup(self.engine.close)

    def arrive(self, index):
        self.engine.feedback('AUDIT/a', 'exec-a', index, 'ACCEPTED')
        self.engine.feedback('AUDIT/a', 'exec-a', index, 'ARRIVED', position_valid=True, feedback_seq=1)

    def saved(self):
        return json.loads(self.path.read_text())

    def test_owner_gate_and_context_are_persisted_before_capture_can_be_created(self):
        self.engine.admit(mission())
        owner = json.loads(Path(str(self.path)+'.lock').read_text())
        self.assertEqual(owner, self.saved()['owner'])
        self.assertEqual(owner['instance_id'], self.saved()['collection_context']['context_epoch'])
        self.assertFalse(self.saved()['collection_context']['collection_ready'])
        with open(str(self.path)+'.lock', 'r') as lock:
            with self.assertRaises(BlockingIOError):
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_actual_arrival_enables_collection_during_source_legs_and_rtl_closes_it(self):
        self.engine.admit(mission(return_to_launch=True), launch_fix=(22.,114.))
        self.engine.feedback('AUDIT/a', 'old-execution', 0, 'ARRIVED', position_valid=True, feedback_seq=1)
        self.assertFalse(self.engine.collection_context()['collection_ready'])
        self.arrive(0)
        self.assertTrue(self.saved()['collection_context']['collection_ready'])
        for sequence in range(2, 12):
            self.now += .5
            self.engine.feedback('AUDIT/a', 'exec-a', self.engine.snapshot()['waypoint_index'], 'HOLDING',
                                 position_valid=True, feedback_seq=sequence)
        self.engine.tick()
        self.assertTrue(self.engine.collection_context()['collection_ready'])
        self.arrive(1)
        for sequence in range(2, 12):
            self.now += .5
            self.engine.feedback('AUDIT/a', 'exec-a', self.engine.snapshot()['waypoint_index'], 'HOLDING',
                                 position_valid=True, feedback_seq=sequence)
        self.engine.tick()
        self.assertEqual(self.engine.snapshot()['waypoint_index'], 2)
        self.assertFalse(self.saved()['collection_context']['collection_ready'])

    def test_landing_abort_unknown_and_controller_change_cannot_start_new_capture(self):
        for terminal in ('LAND_REQUESTED', 'ABORT_LAND_REQUESTED', 'RECOVERY_REQUIRED',
                         'TARGET_REJECTED', 'NAVIGATION_FEEDBACK_TIMEOUT', 'OPERATOR_RESET_PENDING'):
            with self.subTest(phase=terminal):
                engine = ExecutionManager()
                self.addCleanup(engine.close)
                engine.admit(mission()); engine.feedback('AUDIT/a','exec-a',0,'ACCEPTED')
                engine.feedback('AUDIT/a','exec-a',0,'ARRIVED', position_valid=True, feedback_seq=1)
                engine._commit('exec-a', phase=terminal)
                self.assertFalse(engine.collection_context()['collection_ready'])
        self.engine.admit(mission()); self.arrive(0)
        self.engine.invalidate_collection_controller()
        self.assertTrue(self.saved()['collection_context']['arrival_observed'])
        self.assertFalse(self.saved()['collection_context']['collection_ready'])

    def test_restart_changes_owner_epoch_and_never_inherits_ready_context(self):
        self.engine.admit(mission()); self.arrive(0)
        old_owner = self.saved()['owner']
        self.engine.close()
        restarted = ExecutionManager(str(self.path))
        self.addCleanup(restarted.close)
        self.assertNotEqual(old_owner['instance_id'], self.saved()['owner']['instance_id'])
        self.assertEqual(restarted.snapshot()['phase'], 'RECOVERY_REQUIRED')
        self.assertFalse(self.saved()['collection_context']['collection_ready'])

    def test_storage_failure_releases_liveness_gate_even_if_previous_snapshot_was_ready(self):
        self.engine.admit(mission()); self.arrive(0)
        with patch('execution_state.tempfile.mkstemp', side_effect=OSError('synthetic disk failure')):
            with self.assertRaises(OSError): self.engine.invalidate_collection_controller()
        self.assertFalse(self.engine.collection_context()['collection_ready'])
        with open(str(self.path)+'.lock', 'r') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(lock, fcntl.LOCK_UN)
        self.assertIsNone(self.engine.command(include_navigating=True))


if __name__ == '__main__':
    unittest.main()
