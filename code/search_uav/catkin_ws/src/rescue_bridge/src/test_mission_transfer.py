import copy
import json
import tempfile
import unittest
from pathlib import Path

from execution_protocol import normalize_execution_payload
from execution_state import ExecutionManager
from mission_transfer_protocol import messages, query, canonical, MAX_MESSAGE_BYTES, decode
from mission_transfer_store import MissionTransferStore


def payload(count=1001, eid='exec-long'):
    return dict(schema_version=2, execution_id=eid, mission_id='user/event', mission_type='quick_start',
                waypoints=[dict(latitude=22.0 + (i % 2) / 100000, longitude=114.0) for i in range(count)],
                return_to_launch=True, altitude=5, hover_seconds=5)


class MissionTransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.engine = ExecutionManager(str(self.root / 'engine.json')); self.addCleanup(self.engine.close)
        self.store = MissionTransferStore(self.root / 'transfer')

    def send(self, value, store=None):
        return (store or self.store).handle(value, self.engine, (22.0, 114.0))

    def sequence(self, task, attempt=1):
        fp = normalize_execution_payload(task)['content_fingerprint']
        return list(messages(task, fp, attempt))

    def test_long_route_is_one_complete_ordered_execution_and_one_rtl(self):
        task = payload(); seq = self.sequence(task)
        self.assertEqual(len(seq), 6)
        for message in seq[:-1]:
            self.send(message)
            self.assertEqual(self.engine.active_id, '')
        decision, result, state = self.send(seq[-1])
        self.assertTrue(decision['accepted']); self.assertEqual(result, 'NEW')
        self.assertEqual(self.engine.queue[:-1], [(p['latitude'], p['longitude']) for p in task['waypoints']])
        self.assertEqual(len(self.engine.queue), 1002)
        self.assertEqual(state['source_waypoint_total'], 1001)
        self.assertEqual(self.send(seq[-1])[1], 'DUPLICATE')
        self.assertEqual(len(self.engine.queue), 1002)

    def test_missing_chunk_reopen_and_out_of_order_recovery(self):
        seq = self.sequence(payload()); self.send(seq[0]); self.send(seq[3]); self.send(seq[1])
        missing = self.send(seq[-1])[0]
        self.assertEqual(missing['missing_chunks'], [1, 3]); self.assertEqual(self.engine.active_id, '')
        reopened = MissionTransferStore(self.root / 'transfer')
        self.send(seq[4], reopened); self.send(seq[2], reopened)
        self.assertTrue(self.send(seq[-1], reopened)[0]['accepted'])

    def test_same_attempt_rejection_cached_next_attempt_can_be_accepted(self):
        occupied = normalize_execution_payload(payload(1, 'occupied'))
        self.engine.admit(occupied, (22.,114.))
        seq = self.sequence(payload(1))
        for item in seq: result = self.send(item)
        self.assertFalse(result[0]['accepted']); self.assertEqual(result[0]['decision_seq'], 1)
        self.engine.land_result(True, aborted=True)
        self.engine.operator_reset('occupied', confirmed=True, reason='test release')
        self.assertFalse(self.send(seq[-1])[0]['accepted'])
        next_commit = dict(seq[-1], attempt=2)
        accepted = self.send(next_commit)[0]
        self.assertTrue(accepted['accepted']); self.assertEqual(accepted['decision_seq'], 2)
        self.assertTrue(self.send(seq[-1])[0]['accepted'])

    def test_wrong_chunk_and_full_hash_do_not_start(self):
        seq = self.sequence(payload())
        self.send(seq[0]); corrupt = copy.deepcopy(seq[1]); corrupt['waypoints'][0]['latitude'] = 0
        with self.assertRaisesRegex(ValueError, 'chunk'): self.send(corrupt)
        bad = dict(seq[0], content_fingerprint='0'*64)
        with self.assertRaisesRegex(ValueError, 'CONFLICT'): self.send(bad)
        self.assertEqual(self.engine.active_id, '')

    def test_accepted_record_recovers_lost_decision_and_restart_does_not_replay(self):
        task=payload(2); seq=self.sequence(task)
        for item in seq: self.send(item)
        (self.root/'transfer'/'exec-long'/'decision.json').unlink()
        self.engine.close(); self.engine=ExecutionManager(str(self.root/'engine.json')); self.addCleanup(self.engine.close)
        response, _, state=self.send(query(task, seq[0]['content_fingerprint']))
        self.assertTrue(response['accepted']); self.assertEqual(state['phase'], 'RECOVERY_REQUIRED')
        self.assertIsNone(self.engine.command())

    def test_capacity_and_wire_size_boundaries(self):
        for count in (1000,1001,17280,17281,100000):
            task=payload(count)
            seq=self.sequence(task)
            self.assertTrue(all(len(canonical(part)) <= MAX_MESSAGE_BYTES for part in seq))
            self.assertEqual(sum(len(part.get('waypoints', [])) for part in seq), count)
        with self.assertRaises(ValueError): self.sequence(payload(100001))
        with self.assertRaises(ValueError): decode(b' '* (MAX_MESSAGE_BYTES+1))

    def test_maximum_route_is_accepted_without_truncation(self):
        for count in (1000, 1001, 17281, 100000):
            with self.subTest(count=count):
                task = payload(count)
                engine = ExecutionManager(str(self.root / str(count) / 'engine.json'))
                self.addCleanup(engine.close)
                store = MissionTransferStore(self.root / str(count) / 'transfer')
                for item in self.sequence(task):
                    decision, result, state = store.handle(item, engine, (22.0, 114.0))
                self.assertTrue(decision['accepted'])
                self.assertEqual(state['source_waypoint_total'], count)
                self.assertEqual(len(engine.queue), count + 1)
                self.assertEqual(engine.queue[:-1], [(p['latitude'], p['longitude']) for p in task['waypoints']])
                self.assertEqual(engine.queue[-1], (22.0, 114.0))

    def test_full_fingerprint_mismatch_never_accepts_even_with_valid_chunk_hashes(self):
        seq=self.sequence(payload(2))
        for item in seq: item['content_fingerprint']='0'*64
        for item in seq[:-1]: self.send(item)
        with self.assertRaisesRegex(ValueError,'fingerprint'): self.send(seq[-1])
        self.assertEqual(self.engine.active_id,'')

    def test_same_identifier_different_complete_content_never_overwrites(self):
        first=payload(1)
        for item in self.sequence(first): self.send(item)
        other=payload(2)
        with self.assertRaisesRegex(ValueError,'CONFLICT'): self.send(self.sequence(other)[0])
        self.assertEqual(len(self.engine.queue),2)


if __name__ == '__main__': unittest.main()
