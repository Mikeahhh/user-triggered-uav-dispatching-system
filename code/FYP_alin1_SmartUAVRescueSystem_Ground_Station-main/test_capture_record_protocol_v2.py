import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
import capture_record_v2 as wire
from rescue_record_protocol import (GroundRescueStore,RescueRecordError,RescueRecordPersistenceError,
    parse_onboard_envelope,parse_onboard_record,build_ground_ack,validate_ground_ack,
    process_onboard_envelope,format_record_summary)

FIXTURES=Path(__file__).resolve().parents[4]/'output/保留论文补齐代码_20260909/记录v2跨端夹具.json'


def envelope():
    value=json.loads(FIXTURES.read_text())['cases'][0]['normalized']
    record=dict(value,uav_received_at='2026-09-09T00:00:02Z',payload_sha256=wire.digest(value),storage_state='STORED')
    return wire.build_envelope(record,'MISSION_QUEUE_COMPLETE_LAND_REQUESTED','2026-09-09T00:00:03Z')


class CaptureProtocolV2Tests(unittest.TestCase):
    def test_wire_copies_and_all_golden_cases_match(self):
        peer=Path(__file__).resolve().parents[1]/'FYP_alin1_SmartUAVRescueSystem_Drone-main/drone_system/receiver/capture_record_v2.py'
        self.assertEqual(Path(wire.__file__).read_bytes(),peer.read_bytes())
        for case in json.loads(FIXTURES.read_text())['cases']:
            result=wire.validate_payload(case['payload'])
            self.assertEqual(result,case['normalized']);self.assertEqual(wire.digest(result),case['sha256'])

    def test_v2_source_and_carrier_are_distinct_and_summary_shows_both(self):
        record=parse_onboard_record(envelope())
        self.assertNotIn('mission_id',record)
        self.assertEqual(record['source_request']['mission_id'],'TEST_USER/bench_001')
        self.assertEqual(record['carrier_mission_id'],'TEST_USER/event')
        summary=format_record_summary(record,'RECEIVED_STORED')
        for phrase in ('Source SOS','Carrier execution','Original SOS GPS','New GPS'):
            self.assertIn(phrase,summary)

    def test_outer_and_inner_hashes_cover_source_and_new_gps(self):
        for key in ('latitude','carrier_execution_id','context_id'):
            bad=envelope();bad['record'][key]=23. if key=='latitude' else 'changed'
            with self.subTest(key=key),self.assertRaises(RescueRecordError): parse_onboard_envelope(bad)
        bad=envelope();bad['record']['source_request']['latitude']=23.
        bad['envelope_sha256']=wire.envelope_hash(bad)
        with self.assertRaises(RescueRecordError): parse_onboard_envelope(bad)

    def test_ack_v2_retains_all_identity_and_rejects_v1_shaped_capture(self):
        ack=build_ground_ack(envelope())
        self.assertEqual(set(ack),wire.ACK_FIELDS)
        self.assertEqual(ack['capture_id'],'cap-fixture')
        self.assertEqual(validate_ground_ack(ack),ack)
        bad=copy.deepcopy(ack);bad['mission_id']=bad.pop('carrier_mission_id')
        with self.assertRaises(RescueRecordError): validate_ground_ack(bad)
        bad=copy.deepcopy(ack);bad['schema_version']=1
        with self.assertRaises(RescueRecordError): validate_ground_ack(bad)

    def test_store_isolated_namespaces_and_same_capture_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            store=GroundRescueStore(Path(directory));first=envelope()
            saved=store.persist(first);self.assertEqual(saved['record_path'].parent.name,'captures_v2')
            self.assertEqual(store.persist(first)['record'],saved['record'])
            changed=copy.deepcopy(first);changed['record']['latitude']=23.
            payload={k:changed['record'][k] for k in wire.PAYLOAD_FIELDS}
            changed['record']['payload_sha256']=wire.digest(payload);changed['envelope_sha256']=wire.envelope_hash(changed)
            with self.assertRaises(RescueRecordPersistenceError): store.persist(changed)
            self.assertEqual(json.loads(saved['record_path'].read_text()),saved['record'])

    def test_durable_store_precedes_ack_and_ack_failure_keeps_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            store=GroundRescueStore(Path(directory));states=[]
            def acknowledge(ack):
                self.assertTrue(list((Path(directory)/'captures_v2').glob('*.json')))
                raise RuntimeError('injected ACK outage')
            with self.assertRaises(RuntimeError): process_onboard_envelope(envelope(),store,acknowledge,lambda state,record:states.append(state))
            self.assertEqual(states,['RECEIVED_STORED'])
            result=process_onboard_envelope(envelope(),store,lambda ack:None)
            self.assertEqual(result['state'],'ACK_PUBLISHED')


if __name__=='__main__': unittest.main()
