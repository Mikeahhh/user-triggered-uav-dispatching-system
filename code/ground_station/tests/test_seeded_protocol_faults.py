import json
import os
from pathlib import Path
import random
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

import test_ground_station_rescue_flow as flow
from dispatch_journal import DispatchJournal
from mission_execution_protocol import task_fingerprint
from mission_transfer_protocol import messages, query

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'code/search_uav/catkin_ws/src/rescue_bridge/src'))
from execution_protocol import normalize_execution_payload
from execution_state import ExecutionManager
from mission_transfer_store import MissionTransferStore

gs = flow.gs
BASE_SEED = int(os.environ.get('SAR_PROTOCOL_FAULT_SEED', '20260930'))
CASES = int(os.environ.get('SAR_PROTOCOL_FAULT_CASES', '32'))


def task(points, eid='seeded-execution'):
    return dict(schema_version=2, execution_id=eid, mission_id='synthetic/event',
                mission_type='session', waypoints=points, return_to_launch=True,
                altitude=5, hover_seconds=5)


class SeededTransferFaultTests(unittest.TestCase):
    def test_arbitrary_order_duplicates_restarts_preserve_one_whole_route(self):
        for seed in range(BASE_SEED, BASE_SEED + CASES):
            with self.subTest(seed=seed), tempfile.TemporaryDirectory() as folder:
                rng = random.Random(seed)
                count = rng.randint(1001, 2300)
                points = [dict(latitude=22.0 + rng.randrange(4) / 10000,
                               longitude=114.0 + rng.randrange(3) / 10000) for _ in range(count)]
                payload = task(points)
                fingerprint = task_fingerprint(payload)
                sequence = list(messages(payload, fingerprint))
                engine_path = Path(folder) / 'engine.json'
                engine = ExecutionManager(str(engine_path))
                store_path = Path(folder) / 'transfers'
                store = MissionTransferStore(store_path)
                chunks = {part['index'] for part in sequence if part['kind'] == 'CHUNK'}
                received = set()
                manifest_seen = False
                new_admissions = 0
                schedule = sequence + rng.choices(sequence, k=24)
                rng.shuffle(schedule)
                try:
                    for message in schedule:
                        if not engine.active_id and rng.random() < .3:
                            engine.close()
                            engine = ExecutionManager(str(engine_path))
                            store = MissionTransferStore(store_path)
                        try:
                            response, result, _ = store.handle(message, engine, (21.5, 113.5))
                        except ValueError as error:
                            self.assertEqual(message['kind'], 'CHUNK')
                            self.assertFalse(manifest_seen)
                            self.assertIn('manifest required', str(error))
                            continue
                        if message['kind'] == 'MANIFEST': manifest_seen = True
                        if message['kind'] == 'CHUNK': received.add(message['index'])
                        if result == 'NEW':
                            new_admissions += 1
                            self.assertEqual(received, chunks)
                            self.assertTrue(manifest_seen)
                            self.assertEqual(message['kind'], 'COMMIT')
                        if not manifest_seen or received != chunks:
                            self.assertFalse(engine.active_id)
                            self.assertNotEqual(response['message_type'], 'ADMISSION')
                    if not engine.active_id:
                        report, _, _ = store.handle(query(payload, fingerprint), engine)
                        missing = None if report['manifest_required'] else report['missing_chunks']
                        for message in messages(payload, fingerprint, missing=missing):
                            response, result, _ = store.handle(message, engine, (21.5, 113.5))
                            new_admissions += result == 'NEW'
                    self.assertEqual(new_admissions, 1)
                    self.assertEqual(engine.queue,
                                     [(p['latitude'], p['longitude']) for p in points] + [(21.5, 113.5)])
                    self.assertEqual(len(engine.queue), count + 1)
                    self.assertFalse(engine.snapshot()['delivery_eligible'])
                    engine.close()
                    engine = ExecutionManager(str(engine_path))
                    for message in rng.choices(sequence, k=8) + [query(payload, fingerprint)]:
                        response, result, state = MissionTransferStore(store_path).handle(message, engine)
                        self.assertTrue(response['accepted'])
                        self.assertEqual(result, 'DUPLICATE')
                        self.assertEqual(state['phase'], 'RECOVERY_REQUIRED')
                        self.assertIsNone(engine.command())
                        self.assertFalse(state['delivery_eligible'])
                finally:
                    engine.close()

    def test_unknown_queries_and_invalid_messages_do_not_reserve_transfer_capacity(self):
        with tempfile.TemporaryDirectory() as folder:
            store = MissionTransferStore(Path(folder) / 'transfers')
            engine = ExecutionManager()
            self.addCleanup(engine.close)
            for index in range(100):
                payload = task([dict(latitude=22, longitude=114)], 'unknown-%d' % index)
                sequence = list(messages(payload, task_fingerprint(payload)))
                response, _, _ = store.handle(query(payload, task_fingerprint(payload)), engine)
                self.assertEqual(response['status'], 'NOT_RECEIVED')
                self.assertEqual(store.handle(sequence[-1], engine)[0]['status'], 'INCOMPLETE')
                with self.assertRaisesRegex(ValueError, 'manifest required'):
                    store.handle(sequence[1], engine)
                with self.assertRaisesRegex(ValueError, 'invalid task manifest'):
                    store.handle(dict(sequence[0], waypoint_count=0), engine)
            self.assertEqual(list(store.root.iterdir()), [])
            for message in messages(payload, task_fingerprint(payload)):
                response, result, _ = store.handle(message, engine, (22, 114))
            self.assertEqual(result, 'NEW')
            self.assertTrue(response['accepted'])

    def test_incomplete_capacity_is_bounded_without_blocking_existing_transfer(self):
        with tempfile.TemporaryDirectory() as folder:
            store = MissionTransferStore(Path(folder) / 'transfers')
            engine = ExecutionManager()
            self.addCleanup(engine.close)
            sequences = []
            for index in range(65):
                payload = task([dict(latitude=22, longitude=114)], 'capacity-%d' % index)
                sequences.append(list(messages(payload, task_fingerprint(payload))))
            for sequence in sequences[:64]: store.handle(sequence[0], engine)
            with self.assertRaisesRegex(ValueError, 'capacity'):
                store.handle(sequences[64][0], engine)
            self.assertEqual(len(list(store.root.iterdir())), 64)
            for message in sequences[0]: response, _, _ = store.handle(message, engine, (22, 114))
            self.assertTrue(response['accepted'])
            store.handle(sequences[64][0], engine)
            self.assertEqual(len(list(store.root.iterdir())), 65)

    def test_crash_after_engine_acceptance_before_decision_recovers_without_goal_replay(self):
        with tempfile.TemporaryDirectory() as folder:
            engine_path = Path(folder) / 'engine.json'
            store_path = Path(folder) / 'transfers'
            engine = ExecutionManager(str(engine_path))
            store = MissionTransferStore(store_path)
            payload = task([dict(latitude=22, longitude=114)] * 1001)
            fingerprint = task_fingerprint(payload)
            sequence = list(messages(payload, fingerprint))
            original_write = store._write
            def fail_decision(path, value):
                if path.name == 'decision.json': raise OSError('injected decision persistence failure')
                return original_write(path, value)
            try:
                with patch.object(store, '_write', side_effect=fail_decision):
                    for message in sequence[:-1]: store.handle(message, engine, (22, 114))
                    with self.assertRaisesRegex(OSError, 'injected'):
                        store.handle(sequence[-1], engine, (22, 114))
                self.assertEqual(engine.snapshot()['source_waypoint_total'], 1001)
                engine.close()
                engine = ExecutionManager(str(engine_path))
                response, result, state = MissionTransferStore(store_path).handle(query(payload, fingerprint), engine)
                self.assertTrue(response['accepted'])
                self.assertEqual(result, 'DUPLICATE')
                self.assertEqual(state['phase'], 'RECOVERY_REQUIRED')
                self.assertFalse(state['delivery_eligible'])
                self.assertIsNone(engine.command())
            finally:
                engine.close()

    def test_legacy_query_directories_do_not_count_and_corrupt_manifests_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            store = MissionTransferStore(Path(folder) / 'transfers')
            engine = ExecutionManager()
            self.addCleanup(engine.close)
            for index in range(80):
                directory = store.root / ('legacy-query-%d' % index)
                directory.mkdir()
                (directory / 'binding.json').write_text(json.dumps(dict(content_fingerprint='0' * 64)))
            payload = task([dict(latitude=22, longitude=114)])
            first = next(messages(payload, task_fingerprint(payload)))
            store.handle(first, engine)
            self.assertEqual(len(list(store.root.glob('*/manifest.json'))), 1)
            for raw in ('[1,2,3]', '{bad json', '{"waypoint_count":1}', 'null'):
                corrupt = store.root / 'corrupt'
                corrupt.mkdir(exist_ok=True)
                path = corrupt / 'manifest.json'
                path.write_text(raw)
                another = task([dict(latitude=22, longitude=114)], 'another')
                with self.assertRaisesRegex(ValueError, 'invalid stored manifest for corrupt'):
                    store.handle(next(messages(another, task_fingerprint(another))), engine)
                self.assertEqual(path.read_text(), raw)
                self.assertFalse((store.root / 'another').exists())
                self.assertFalse(engine.active_id)

    def test_unknown_query_is_read_only_and_completed_execution_query_preserves_active_mission(self):
        with tempfile.TemporaryDirectory() as folder:
            engine_path = Path(folder) / 'engine.json'
            engine = ExecutionManager(str(engine_path))
            self.addCleanup(engine.close)
            store = MissionTransferStore(Path(folder) / 'transfers')
            payload = dict(task([dict(latitude=22, longitude=114)], 'completed'),
                           return_to_launch=False, hover_seconds=0)
            fingerprint = task_fingerprint(payload)
            for message in messages(payload, fingerprint): store.handle(message, engine)
            engine.feedback(payload['mission_id'], 'completed', 0, 'ACCEPTED')
            engine.feedback(payload['mission_id'], 'completed', 0, 'ARRIVED', position_valid=True, feedback_seq=1)
            self.assertEqual(engine.tick()['phase'], 'LAND_REQUEST_PENDING')
            self.assertTrue(engine.land_result(True)['delivery_eligible'])
            engine.operator_reset('completed', confirmed=True, reason='synthetic completion confirmed')
            active = task([dict(latitude=23, longitude=115)], 'active')
            engine.admit(normalize_execution_payload(active), (22, 114))
            before = engine_path.read_bytes()
            before_command = engine.command()
            unknown = task([dict(latitude=24, longitude=116)], 'unknown')
            response, result, state = store.handle(query(unknown, task_fingerprint(unknown)), engine)
            self.assertEqual(response['status'], 'NOT_RECEIVED')
            self.assertIsNone(result)
            self.assertIsNone(state)
            self.assertFalse((store.root / 'unknown').exists())
            response, result, state = store.handle(query(payload, fingerprint), engine)
            self.assertTrue(response['accepted'])
            self.assertEqual(result, 'DUPLICATE')
            self.assertEqual(state['phase'], 'OPERATOR_RELEASED')
            self.assertTrue(state['all_waypoints_completed'])
            self.assertTrue(state['delivery_eligible'])
            self.assertEqual(engine.active_id, 'active')
            self.assertEqual(engine.command(), before_command)
            self.assertEqual(engine_path.read_bytes(), before)


class SeededGroundStationFaultTests(unittest.TestCase):
    def fixture(self):
        fixture = flow.RescueEventDispatchWrapperTests()
        fixture.setUp()
        return fixture

    def test_actual_admissions_survive_delayed_rejections_restarts_and_progress_reordering(self):
        for seed in range(BASE_SEED, BASE_SEED + CASES):
            with self.subTest(seed=seed):
                fixture = self.fixture()
                engine = ExecutionManager()
                rng = random.Random(seed)
                try:
                    fixture.call()
                    store = MissionTransferStore(Path(fixture.directory.name) / 'transfer')
                    busy = normalize_execution_payload(task([dict(latitude=22, longitude=114)], 'busy'))
                    engine.admit(busy, (22, 114))
                    for _, body, _ in fixture.client.calls:
                        rejected, _, _ = store.handle(json.loads(body), engine, (22, 114))
                    self.assertFalse(rejected['accepted'])
                    with patch.object(gs, '_schedule_ui'):
                        gs.process_admission_report(rejected, fixture.root, fixture.journal)
                    engine.land_result(True, aborted=True)
                    engine.operator_reset('busy', confirmed=True, reason='synthetic operator recovery')
                    count = len(fixture.client.calls)
                    fixture.call(resume=True)
                    for _, body, _ in fixture.client.calls[count:]:
                        accepted, _, state = store.handle(json.loads(body), engine, (22, 114))
                    self.assertTrue(accepted['accepted'])
                    self.assertEqual(accepted['attempt'], 2)
                    schedule = [accepted, rejected] + rng.choices([accepted, rejected], k=12)
                    rng.shuffle(schedule)
                    seen_acceptance = False
                    publication_count = len(fixture.client.calls)
                    for report in schedule:
                        if rng.random() < .25:
                            fixture.journal.close()
                            fixture.journal = DispatchJournal(fixture.journal_path, 'https://synthetic.firebaseio.com')
                        with patch.object(gs, '_schedule_ui'):
                            gs.process_admission_report(report, fixture.root, fixture.journal)
                        seen_acceptance |= report['accepted']
                        event = fixture.root.data['users']['USER_A']['rescue_events']['sos__request_1']
                        self.assertEqual(event['status'], 'DISPATCHED' if seen_acceptance else 'PENDING')
                        self.assertEqual(fixture.journal.get(('USER_A', 'sos__request_1'))['state'] == 'ACCEPTED', seen_acceptance)
                    revisions = list(range(1, 24)) + rng.choices(list(range(1, 24)), k=30)
                    rng.shuffle(revisions)
                    maximum = 0
                    for revision in revisions:
                        report = dict(accepted, message_type='EXECUTION', state_revision=revision,
                                      phase='RECOVERY_REQUIRED', reason='COORDINATE_FRAME_CHANGED', status='REJECTED')
                        with patch.object(gs, '_schedule_ui'):
                            gs.process_execution_report(report, fixture.root, fixture.journal)
                        maximum = max(maximum, revision)
                        event = fixture.root.data['users']['USER_A']['rescue_events']['sos__request_1']
                        self.assertEqual(event['execution']['last_report']['state_revision'], maximum)
                        self.assertEqual(event['status'], 'DISPATCHED')
                    fixture.call(resume=True)
                    self.assertEqual(len(fixture.client.calls), publication_count)
                finally:
                    engine.close()
                    fixture.tearDown()

    def test_operator_gate_rejects_changed_source_or_confirmation_and_cancellation(self):
        for seed in range(BASE_SEED, BASE_SEED + CASES):
            with self.subTest(seed=seed):
                fixture = self.fixture()
                rng = random.Random(seed)
                try:
                    action = rng.choice(['cancel', 'source', 'confirmation', 'verification', 'status'])
                    def confirm(*_):
                        user = fixture.root.data['users']['USER_A']
                        if action == 'cancel': return False
                        if action == 'source': user['rescue_requests']['request_1']['longitude'] += .01
                        if action == 'confirmation': user['active_events']['sos__request_1']['confirmation_id'] = 'obsolete'
                        if action == 'verification': user['rescue_events']['sos__request_1'].pop('search_confirmed_at_ms', None)
                        if action == 'status': user['rescue_events']['sos__request_1']['status'] = 'CLOSED_SAFE'
                        return True
                    result = fixture.call(confirm)
                    self.assertNotEqual(result['status'], gs.DISPATCH_PUBLISHED)
                    self.assertEqual(fixture.client.calls, [])
                    self.assertIsNone(fixture.journal.get(('USER_A', 'sos__request_1')))
                finally:
                    fixture.tearDown()

    def test_mode2_preserves_time_order_ties_and_repeated_coordinates(self):
        for seed in range(BASE_SEED, BASE_SEED + CASES):
            with self.subTest(seed=seed):
                rng = random.Random(seed)
                points = [dict(timestamp=1000000 + rng.randrange(8),
                               latitude=22 + rng.randrange(3) / 1000,
                               longitude=114 + rng.randrange(2) / 1000) for _ in range(1001)]
                event = flow._event('quick', trigger_type='QUICK_START_LOCATION_TIMEOUT',
                                    primary_record_type='QuickStartSessions', primary_record_id='walk')
                for mapping in (False, True):
                    source = {'p%05d' % i: p for i, p in enumerate(points)} if mapping else points
                    if mapping:
                        items = list(source.items()); rng.shuffle(items); source = dict(items)
                    user = {'QuickStartSessions': {'walk': {'startTime': 1000000, 'points': source}}}
                    prepared = gs.prepare_mission_for_rescue_event(user, event, 2000000)
                    expected = [p for _, p in sorted(enumerate(points), key=lambda value: (value[1]['timestamp'], value[0]))]
                    self.assertEqual(prepared['waypoints'], [(p['latitude'], p['longitude']) for p in expected])
                    self.assertEqual(len(prepared['waypoints']), 1001)
                    self.assertTrue(prepared['return_to_launch'])

    @unittest.skipUnless(os.environ.get('MASS26_LOCAL_VERIFICATION') == '1', 'isolated runner required')
    def test_network_guard_blocks_external_address_and_dns_without_network(self):
        for operation in (lambda: socket.getaddrinfo('example.invalid', 443),
                          lambda: socket.create_connection(('192.0.2.1', 443), timeout=.01)):
            with self.assertRaisesRegex(PermissionError, 'non-loopback'):
                operation()


if __name__ == '__main__':
    unittest.main()
