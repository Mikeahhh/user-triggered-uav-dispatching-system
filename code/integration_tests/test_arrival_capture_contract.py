import copy
import http.client
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
GS = ROOT / 'ground_station'
BRIDGE = ROOT / 'search_uav/catkin_ws/src/rescue_bridge/src'
RECEIVER = ROOT / 'search_uav/drone_system/receiver'
sys.path[:0] = [str(GS), str(BRIDGE), str(RECEIVER)]
import ground_station as gs
from dispatch_journal import DispatchJournal
from execution_protocol import normalize_execution_payload
from execution_state import ExecutionManager
from mission_transfer_store import MissionTransferStore
from mission_transfer_protocol import decode
from capture_store_v2 import JournalCollectionContextProvider, NoCollectionContext
from phone_sos_receiver import RescueStore, RescueHttpServer, RescueDeliveryCoordinator
from rescue_record_protocol import GroundRescueStore, process_onboard_envelope, ACK_PUBLISHED, RECEIVED_STORED
import test_flight_execution_contract as flight_fixture
from execution_test_support import arrive, complete_hover


def source_request():
    return dict(schema_version=1, user_id='SYNTHETIC', request_id='request',
                mission_id='SYNTHETIC/request', latitude=22.0, longitude=114.0, accuracy=3.0,
                captured_at='2026-09-09T00:00:00.000Z', client_timestamp_ms=1788912000000,
                status='PENDING', device='synthetic-source', gps_points=[], test_mode=True)


def memory_database(data):
    lock = threading.RLock()
    class Reference:
        def __init__(self, path=()): self.path = path
        def child(self, segment): return Reference(self.path + tuple(segment.split('/')))
        def get(self):
            with lock:
                current = data
                for segment in self.path:
                    current = current.get(segment)
                return copy.deepcopy(current)
        def transaction(self, transform):
            with lock:
                updated = transform(self.get())
                current = data
                for segment in self.path[:-1]: current = current[segment]
                current[self.path[-1]] = copy.deepcopy(updated)
                return copy.deepcopy(updated)
    return Reference()


class ArrivalCaptureContractTests(unittest.TestCase):
    def get_context(self, server, capture_id):
        conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        try:
            conn.request('GET', '/api/v2/mission-context?user_id=SYNTHETIC&request_id=request&capture_id=' + capture_id)
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    def mobile_capture(self, server, directory, mode='capture'):
        result = subprocess.run([shutil.which('node') or 'node', str(Path(__file__).with_name('mobile_capture_flow_probe.cjs'))],
            input=json.dumps(dict(baseUrl=f'http://127.0.0.1:{server.server_port}',
                                  storagePath=str(directory / 'mobile-durable.json'), mode=mode,
                                  source=source_request(), captureId='synthetic-capture',
                                  position=dict(latitude=22.351, longitude=114.181, accuracy=2.5,
                                      capture_started_at_ms=1788912060000, client_timestamp_ms=1788912060123))),
            capture_output=True, text=True, timeout=25)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def exercise(self, trigger, *, lost_response=False, fast_ack=False):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            now = [100.0]
            manager = ExecutionManager(str(directory / 'executions.json'), clock=lambda: now[0])
            self.addCleanup(manager.close)
            provider = JournalCollectionContextProvider(directory / 'executions.json')
            ledger = DispatchJournal(directory / 'dispatch.sqlite3', 'synthetic-scope')
            self.addCleanup(ledger.close)
            user, event = flight_fixture.FlightExecutionContractTests().fixture(trigger, count=1)
            user['rescue_events'] = {event['event_id']: event}
            data = {'users': {'SYNTHETIC': user}}
            reference = memory_database(data)
            commands = []
            transfers = MissionTransferStore(directory / 'transfers')
            class Info:
                rc = 0
                def wait_for_publish(self, timeout): pass
                def is_published(self): return True
            class Broker:
                def publish(self, _topic, body, qos, retain=False):
                    assert retain is False
                    payload = decode(body.encode())
                    commands.append(payload)
                    response, _, _ = transfers.handle(payload, manager, (22.0, 114.0))
                    if response['message_type'] == 'ADMISSION':
                        gs.process_admission_report(response, database_root=reference, journal=ledger)
                    return Info()
            with patch.object(gs, 'rescue_runtime_config', {'ready': True}), \
                 patch.object(gs, 'mqtt_connected', True), patch.object(gs, 'mqtt_client', Broker()), \
                 patch.object(gs, '_show_message_safely'), \
                 patch.object(gs, '_show_workflow_error'), patch.object(gs, 'refresh_data'), \
                 patch.object(gs, '_epoch_now_ms', return_value=flight_fixture.NOW):
                selected = gs.select_rescue_event('SYNTHETIC', event['event_id'], reference, journal=ledger)
                self.assertEqual(selected['phase'], 'SELECTED')
                prepared = gs.prepare_active_rescue_event('SYNTHETIC', event['event_id'], reference, journal=ledger)
                self.assertEqual(prepared['phase'], 'PREPARED')
                result = gs.dispatch_rescue_event('SYNTHETIC', event['event_id'], reference,
                    journal=ledger, confirm_callback=lambda *_: True)
            self.assertEqual(result['status'], gs.DISPATCH_PUBLISHED)
            self.assertEqual([command['kind'] for command in commands], ['MANIFEST', 'CHUNK', 'COMMIT'])
            self.assertEqual(data['users']['SYNTHETIC']['rescue_events'][event['event_id']]['status'], 'DISPATCHED')
            self.assertNotIn(event['event_id'], data['users']['SYNTHETIC'].get('active_events', {}))
            wire = commands[0]['task']
            store = RescueStore(directory / 'uav', execution_journal_path=manager.path)
            server = RescueHttpServer(('127.0.0.1', 0), store, collection_context_provider=provider)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:

                status, _ = self.get_context(server, 'too-early')
                self.assertNotEqual(status, 200)
                arrive(manager)
                context = provider('SYNTHETIC')
                self.assertEqual(context['mission_id'], wire['mission_id'])
                self.assertEqual(context['execution_id'], wire['execution_id'])
                report = self.mobile_capture(server, directory, 'lose-response' if lost_response else 'capture')
                self.assertEqual(report['gpsCalls'], 1)
                self.assertEqual(report['requestCount'], 1)
                self.assertEqual(report['postCount'], 2 if lost_response else 1)
                self.assertEqual(report['order'][:6], ['save-intent', 'context', 'save-context', 'gps', 'save-payload', 'post'])
                if lost_response:
                    self.assertEqual(report['bodies'][0], report['bodies'][1])
                    self.assertEqual(report['retry']['remaining'], 0)
                record = store.captures.read_record('synthetic-capture')
                self.assertEqual(record['request_id'], 'request')
                self.assertEqual(record['carrier_mission_id'], wire['mission_id'])
                self.assertEqual(record['carrier_execution_id'], wire['execution_id'])
                self.assertEqual(record['source_request'], source_request())
                self.assertEqual(record['latitude'], 22.351)
                self.assertNotEqual(record['latitude'], record['source_request']['latitude'])
                self.assertEqual(record['capture_started_at_ms'], 1788912060000)
                self.assertEqual(record['client_timestamp_ms'], 1788912060123)

                early_messages = []
                early_delivery = RescueDeliveryCoordinator(store, lambda topic, body: early_messages.append(body))
                self.assertEqual(early_delivery.handle_sync_request(), 0)
                self.assertEqual(early_delivery.handle_mission_status({**manager.snapshot(), 'phase': 'LAND_REQUESTED',
                    'all_waypoints_completed': True, 'land_command_requested': True, 'delivery_eligible': True}), 0)
                self.assertEqual(early_messages, [])
                complete_hover(manager, now)
                while manager.snapshot()['phase'] == 'WAITING_TARGET_ACCEPTANCE':
                    snapshot = manager.snapshot()
                    index = snapshot['waypoint_index']
                    self.assertEqual(manager.collection_context()['collection_ready'],
                                     index < snapshot['source_waypoint_total'])
                    arrive(manager)
                    complete_hover(manager, now)
                self.assertEqual(manager.snapshot()['phase'], 'LAND_REQUEST_PENDING')
                landed = manager.land_result(True)
                status, _ = self.get_context(server, 'after-land-request')
                self.assertNotEqual(status, 200)
                status, _ = self.get_context(server, 'synthetic-capture')
                self.assertNotEqual(status, 200)
                messages, acks, ground_states = [], [], []
                ground_store = GroundRescueStore(directory / 'gs-records')
                received = []
                def accept_on_ground(body):
                    accepted = process_onboard_envelope(body, store=ground_store,
                        publish_ack=lambda ack: (acks.append(ack), coordinator.handle_ground_ack(ack)),
                        on_state=lambda state, _record: ground_states.append(state))
                    received.append(accepted)
                def publish(_topic, body):
                    messages.append(body)
                    if fast_ack: accept_on_ground(body)
                coordinator = RescueDeliveryCoordinator(store, publish)
                status_message = {**landed, 'schema_version': 2, 'status': 'LANDING',
                                  'active_execution_id': wire['execution_id']}
                self.assertEqual(coordinator.handle_mission_status(status_message), 1)
                if not fast_ack: accept_on_ground(messages[0])
                self.assertEqual(ground_states, [RECEIVED_STORED, ACK_PUBLISHED])
                self.assertEqual(received[0]['record']['carrier_execution_id'], wire['execution_id'])
                self.assertTrue(received[0]['record_path'].is_file())
                self.assertTrue(received[0]['envelope_path'].is_file())
                self.assertEqual(store.captures.read_delivery('synthetic-capture')['state'], 'ACKNOWLEDGED_BY_GS')
                coordinator.handle_ground_ack(acks[0])
                self.assertEqual(store.captures.read_delivery('synthetic-capture')['state'], 'ACKNOWLEDGED_BY_GS')
                self.assertEqual(coordinator.retry_pending(), 0)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
                manager.close()
                ledger.close()

    def test_all_three_trigger_types_keep_original_sos_and_current_flight_identity(self):
        for trigger in ('SOS', 'EVENT_BOOKING_TIMEOUT', 'QUICK_START_INACTIVITY', 'QUICK_START_LOCATION_TIMEOUT'):
            with self.subTest(trigger=trigger): self.exercise(trigger)

    def test_lost_http_receipt_retries_exact_durable_payload_without_new_gps(self):
        self.exercise('EVENT_BOOKING_TIMEOUT', lost_response=True)

    def test_immediate_ground_ack_cannot_be_lost_or_rolled_back_by_publish_completion(self):
        self.exercise('QUICK_START_INACTIVITY', fast_ack=True)

    def test_live_owner_is_checked_across_real_process_exit_and_restart(self):
        prepared, wire = flight_fixture.FlightExecutionContractTests().produce('SOS')
        worker = """
import json,sys
sys.path.insert(0,sys.argv[1])
from execution_protocol import normalize_execution_payload
from execution_state import ExecutionManager
engine=ExecutionManager(sys.argv[2])
task=json.loads(sys.stdin.readline())
result,state=engine.admit(normalize_execution_payload(task),launch_fix=(22.0,114.0))
if result=='NEW':
 engine.feedback(task['mission_id'],task['execution_id'],0,'ACCEPTED')
 engine.feedback(task['mission_id'],task['execution_id'],0,'ARRIVED',position_valid=True,feedback_seq=1)
print(engine.snapshot()['phase'],flush=True)
sys.stdin.readline()
engine.close()
"""
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'execution.json'
            provider = JournalCollectionContextProvider(path)
            for phase in ('HOVERING', 'RECOVERY_REQUIRED'):
                process = subprocess.Popen([sys.executable, '-u', '-c', worker, str(BRIDGE), str(path)],
                                           stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    process.stdin.write(json.dumps(wire) + '\n'); process.stdin.flush()
                    self.assertEqual(process.stdout.readline().strip(), phase)
                    if phase == 'HOVERING':
                        self.assertEqual(provider('SYNTHETIC')['execution_id'], wire['execution_id'])
                    else:
                        with self.assertRaises(NoCollectionContext): provider('SYNTHETIC')
                    process.terminate()
                    process.wait(timeout=5)
                    with self.assertRaises(NoCollectionContext): provider('SYNTHETIC')
                finally:
                    if process.poll() is None: process.kill(); process.wait(timeout=5)
                    process.stdin.close(); process.stdout.close(); process.stderr.close()


if __name__ == '__main__':
    unittest.main()
