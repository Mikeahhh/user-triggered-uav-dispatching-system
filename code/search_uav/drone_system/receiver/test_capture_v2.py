import copy
from receiver_test_support import completed_store, context_for
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
import capture_record_v2 as wire
from capture_store_v2 import CaptureConflictError, NoCollectionContext, JournalCollectionContextProvider
from phone_sos_receiver import RescueStore, RescueHttpServer, RescueDeliveryCoordinator
from test_phone_sos_receiver import sample_payload


def eligible(kind='event',eid='exec-a'):
    return context_for('TEST_USER/'+kind,eid,kind)


def capture(context,source=None):
    value={k:context[k] for k in ('capture_id','request_id','user_id','context_id','carrier_mission_id','carrier_execution_id')}
    epoch=int(datetime(2026,9,9,tzinfo=timezone.utc).timestamp()*1000)
    value.update(schema_version=2,source_request=source or sample_payload(),latitude=22.4,longitude=114.2,
                 accuracy=2.,captured_at='2026-09-09T00:00:01.000Z',client_timestamp_ms=epoch+1000,
                 capture_started_at_ms=epoch,device='android',test_mode=True)
    return value


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=completed_store(Path(self.temp.name),"TEST_USER/event"); self.active=eligible()
        self.provider=lambda user: copy.deepcopy(self.active)

    def context(self,cid='cap-a'):
        return self.store.captures.issue_context('TEST_USER','bench_001',cid,self.provider)

    def test_booking_quickstart_sos_carry_original_and_new_gps(self):
        original,_=self.store.store(sample_payload());before=self.store._record_path('bench_001').read_bytes()
        for kind in ('event','session','rescue'):
            self.active=eligible(kind,'exec-'+kind)
            record,_=self.store.captures.store(capture(self.context('cap-'+kind)))
            self.assertEqual(record['source_request']['latitude'],original['latitude'])
            self.assertEqual(record['latitude'],22.4)
            self.assertEqual(record['request_id'],'bench_001')
            self.assertEqual(record['carrier_execution_id'],'exec-'+kind)
        self.assertEqual(before,self.store._record_path('bench_001').read_bytes())
        self.assertEqual(len(self.store.captures.pending_records()),3)

    def test_context_reget_requires_live_same_execution_and_epoch(self):
        context=self.context();self.assertEqual(self.context(),context)
        for change in ({'collection_ready':False},{'execution_id':'new'},{'context_epoch':'b'*32}):
            self.active=eligible();self.active.update(change)
            with self.assertRaises(NoCollectionContext): self.context()
        with self.assertRaises(CaptureConflictError):
            self.store.captures.issue_context('TEST_USER','different','cap-a',self.provider)

    def test_no_context_or_wrong_user_cannot_create_capture(self):
        with self.assertRaises(NoCollectionContext): self.store.captures.issue_context('OTHER','bench_001','cap-a',self.provider)
        self.active['collection_ready']=False
        with self.assertRaises(NoCollectionContext): self.context()
        self.active=eligible();payload=capture(self.context());payload['capture_id']='cap-b'
        with self.assertRaises(NoCollectionContext): self.store.captures.store(payload)

    def test_issued_intent_late_first_post_and_duplicate_after_restart(self):
        payload=capture(self.context());self.active['collection_ready']=False
        record,duplicate=self.store.captures.store(payload);self.assertFalse(duplicate)
        reopened=RescueStore(Path(self.temp.name))
        again,duplicate=reopened.captures.store(payload)
        self.assertTrue(duplicate);self.assertEqual(record,again)
        changed=copy.deepcopy(payload);changed['latitude']+=.1
        with self.assertRaises(CaptureConflictError): reopened.captures.store(changed)

    def test_missing_source_and_carrier_substitution_rejected(self):
        payload=capture(self.context());bad=copy.deepcopy(payload);bad.pop('source_request')
        with self.assertRaises(wire.CaptureValidationError): self.store.captures.store(bad)
        bad=copy.deepcopy(payload);bad['carrier_execution_id']='another'
        with self.assertRaises(CaptureConflictError): self.store.captures.store(bad)
        bad=copy.deepcopy(payload);bad['source_request']['mission_id']='TEST_USER/other'
        with self.assertRaises(wire.CaptureValidationError): self.store.captures.store(bad)

    def test_negative_zero_v1_source_is_preserved_semantically(self):
        source=sample_payload();source['latitude']=-0.;source['gps_points'][0]['longitude']=-0.
        old,_=self.store.store(source);before=self.store._record_path('bench_001').read_bytes()
        record,_=self.store.captures.store(capture(self.context(),source))
        self.assertEqual(record['source_request']['latitude'],0.)
        self.assertEqual(before,self.store._record_path('bench_001').read_bytes())
        self.assertEqual(old['payload_sha256'],self.store.read_record('bench_001')['payload_sha256'])

    def test_old_source_cannot_be_overwritten_via_v2(self):
        self.store.store(sample_payload());payload=capture(self.context());payload['source_request']['latitude']=23.
        with self.assertRaises(CaptureConflictError): self.store.captures.store(payload)
        self.assertEqual(self.store.read_record('bench_001')['latitude'],22.352)

    def test_true_sample_time_contract_and_strict_capture_iso(self):
        payload=capture(self.context())
        for change in ({'capture_started_at_ms':payload['client_timestamp_ms']+1},
                       {'captured_at':'2026-09-09 00:00:01Z'}, {'captured_at':'20260909T00:00:01Z'},
                       {'captured_at':'2026-09-09T00:00:01.0000001Z'}, {'client_timestamp_ms':True}):
            bad=copy.deepcopy(payload);bad.update(change)
            with self.subTest(change=change),self.assertRaises(wire.CaptureValidationError): wire.validate_payload(bad)

    def test_failed_outbox_write_is_recovered_without_replacing_record(self):
        payload=capture(self.context())
        with patch.object(self.store.captures,'_ensure_outbox',side_effect=OSError('disk')):
            with self.assertRaises(OSError): self.store.captures.store(payload)
        record=self.store.captures.read_record('cap-a')
        reopened=RescueStore(Path(self.temp.name))
        self.assertEqual(reopened.captures.pending_records(),[record])

    def test_only_carrier_execution_land_forwards_and_late_capture_uses_saved_trigger(self):
        sent=[];coordinator=RescueDeliveryCoordinator(self.store,lambda t,p:sent.append(json.loads(p)))
        payload=capture(self.context())
        status=dict(schema_version=2,status='LANDING',phase='LAND_REQUESTED',mission_id='TEST_USER/event',execution_id='old',land_command_requested=True,all_waypoints_completed=True,delivery_eligible=True)
        record,_=self.store.captures.store(payload)
        self.assertEqual(coordinator.handle_mission_status(status),0)
        status['execution_id']='exec-a';self.assertEqual(coordinator.handle_mission_status(status),1)
        self.assertEqual(sent[0]['record']['capture_id'],'cap-a')
        self.context('cap-b');self.store.captures.store(capture(self.context('cap-b')))
        self.assertEqual(coordinator.retry_pending(),1)
        self.assertEqual(sent[-1]['record']['capture_id'],'cap-b')

    def test_synchronous_ack_and_late_publish_result_never_downgrade(self):
        record,_=self.store.captures.store(capture(self.context()));acks=[]
        def publish(topic,payload):
            envelope=json.loads(payload);ack=wire.build_ack(envelope);acks.append(ack)
            coordinator.handle_ground_ack(ack)
            coordinator.handle_ground_ack(ack)
            raise RuntimeError('late transport completion error')
        coordinator=RescueDeliveryCoordinator(self.store,publish)
        coordinator.handle_sync_request()
        state=self.store.captures.read_delivery('cap-a')
        self.assertEqual(state['state'],'ACKNOWLEDGED_BY_GS')
        bad=copy.deepcopy(acks[0]);bad['carrier_execution_id']='wrong'
        with self.assertRaises(ValueError): coordinator.handle_ground_ack(bad)
        self.assertEqual(self.store.captures.pending_records(),[])

    def test_ack_loss_replays_exact_envelope_and_hash(self):
        self.store.captures.store(capture(self.context()));sent=[];clock=[0.]
        coordinator=RescueDeliveryCoordinator(self.store,lambda t,p:sent.append(p),clock=lambda:clock[0])
        coordinator.handle_sync_request();clock[0]=3.;coordinator.retry_pending()
        self.assertEqual(sent[0],sent[1])
        coordinator.handle_ground_ack(wire.build_ack(json.loads(sent[1])))
        self.assertEqual(self.store.captures.pending_records(),[])


class HttpCaptureTests(unittest.TestCase):
    def test_loopback_context_capture_duplicate_and_missing_source(self):
        with tempfile.TemporaryDirectory() as directory:
            store=RescueStore(Path(directory));active=eligible()
            server=RescueHttpServer(('127.0.0.1',0),store,collection_context_provider=lambda user:dict(active))
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                url='http://127.0.0.1:'+str(server.server_address[1])
                def request(path,data=None):
                    req=urllib.request.Request(url+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json'})
                    try:
                        with urllib.request.urlopen(req,timeout=3) as response: return response.status,json.load(response)
                    except urllib.error.HTTPError as response:
                        with response: return response.code,json.load(response)
                code,context=request('/api/v2/mission-context?user_id=TEST_USER&request_id=bench_001&capture_id=cap-http')
                self.assertEqual(code,200);self.assertEqual(len(context),11)
                payload=capture(context);active['collection_ready']=False
                code,receipt=request('/api/v2/rescue-captures',payload)
                self.assertEqual(code,201);self.assertEqual(len(receipt),12)
                self.assertEqual(request('/api/v2/rescue-captures',payload)[0],200)
                self.assertEqual(request('/api/v2/mission-context?user_id=TEST_USER&request_id=bench_001&capture_id=cap-http')[0],409)
                bad=copy.deepcopy(payload);bad.pop('source_request')
                code,body=request('/api/v2/rescue-captures',bad)
                self.assertEqual((code,body['error_code']),(400,'CURRENT_SOS_REQUIRED'))
            finally:
                server.shutdown();server.server_close();thread.join(timeout=3)


class RealProviderTests(unittest.TestCase):
    def test_real_journal_liveness_and_facts(self):
        bridge_src=Path(__file__).resolve().parents[2]/'catkin_ws/src/rescue_bridge/src'
        sys.path.insert(0,str(bridge_src))
        from execution_state import ExecutionManager
        from execution_protocol import normalize_execution_payload
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'executions.json';engine=ExecutionManager(str(path))
            mission=normalize_execution_payload(dict(schema_version=2,mission_id='TEST_USER/event',execution_id='exec-a',mission_type='event',waypoints=[{'lat':22.,'lon':114.}],return_to_launch=False))
            engine.admit(mission);provider=JournalCollectionContextProvider(path)
            with self.assertRaises(NoCollectionContext): provider('TEST_USER')
            engine.feedback('TEST_USER/event','exec-a',0,'ACCEPTED');engine.feedback('TEST_USER/event','exec-a',0,'ARRIVED',position_valid=True,feedback_seq=1)
            self.assertTrue(provider('TEST_USER')['collection_ready'])
            with self.assertRaises(NoCollectionContext): provider('OTHER')
            saved=json.loads(path.read_text());broken=copy.deepcopy(saved);broken['executions']['exec-a']['phase']='LAND_REQUESTED';path.write_text(json.dumps(broken))
            with self.assertRaises(NoCollectionContext): provider('TEST_USER')
            path.write_text(json.dumps(saved));engine.close()
            with self.assertRaises(NoCollectionContext): provider('TEST_USER')


if __name__=='__main__': unittest.main()
