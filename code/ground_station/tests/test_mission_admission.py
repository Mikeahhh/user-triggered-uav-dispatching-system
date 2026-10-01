import copy
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch

from test_ground_station_rescue_flow import gs
import test_ground_station_rescue_flow as flow
from mission_execution_protocol import task_fingerprint
from dispatch_journal import DispatchJournal, JournalConflict


class MissionAdmissionTests(unittest.TestCase):
    setUp = flow.RescueEventDispatchWrapperTests.setUp
    tearDown = flow.RescueEventDispatchWrapperTests.tearDown
    call = flow.RescueEventDispatchWrapperTests.call

    def start(self):
        original = self.client.publish
        self.client.publish = lambda topic, body, qos=0, retain=False: original(topic, body, qos)
        return self.call()

    def report(self, accepted=True, attempt=1, decision_seq=1):
        current = self.journal.get(('USER_A','sos__request_1'))
        return dict(schema_version=2, message_type='ADMISSION', execution_id=current['execution_id'],
                    content_fingerprint=current['fingerprint'], mission_id=current['payload']['mission_id'],
                    attempt=attempt,decision_seq=decision_seq,accepted=accepted,
                    status='ACCEPTED' if accepted else 'REJECTED', reason='' if accepted else 'MISSION_BUSY')

    def process(self, report):
        with patch.object(gs,'_schedule_ui'):
            return gs.process_admission_report(report,self.root,self.journal)

    @property
    def user(self): return self.root.data['users']['USER_A']

    def test_broker_confirmation_is_not_uav_acceptance(self):
        result=self.start()
        self.assertEqual(result['status'],gs.DISPATCH_PUBLISHED)
        self.assertEqual(result['firebase_status'],'PENDING')
        self.assertEqual(self.user['rescue_events']['sos__request_1']['status'],'PENDING')
        self.assertIn('sos__request_1',self.user['active_events'])
        self.assertEqual([json.loads(c[1])['kind'] for c in self.client.calls],['MANIFEST','CHUNK','COMMIT'])
        self.process(self.report())
        self.assertEqual(self.user['rescue_events']['sos__request_1']['status'],'DISPATCHED')
        self.assertNotIn('sos__request_1',self.user['active_events'])

    def test_rejected_task_is_retained_and_same_id_retry_increments_attempt(self):
        first=self.start(); self.process(self.report(False))
        self.assertEqual(self.user['rescue_events']['sos__request_1']['status'],'PENDING')
        retried=self.call(resume=True)
        self.assertEqual(retried['execution_id'],first['execution_id'])
        self.assertEqual(json.loads(self.client.calls[-1][1])['attempt'],2)
        self.process(self.report(False,1,1))
        self.assertNotEqual(self.journal.get(('USER_A','sos__request_1'))['state'],'REJECTED')
        self.process(self.report(True,2,2))
        self.assertEqual(self.user['rescue_events']['sos__request_1']['status'],'DISPATCHED')
        self.process(self.report(False,1,1))
        self.assertEqual(self.user['rescue_events']['sos__request_1']['status'],'DISPATCHED')

    def test_unknown_outcome_queries_saved_id_instead_of_new_dispatch(self):
        first=self.start(); count=len(self.client.calls)
        result=self.call(resume=True)
        self.assertEqual(result['execution_id'],first['execution_id'])
        self.assertEqual(len(self.client.calls),count+1)
        self.assertEqual(json.loads(self.client.calls[-1][1])['kind'],'QUERY')

    def test_cloud_failure_after_acceptance_recovers_without_republishing(self):
        self.start(); report=self.report()
        with patch.object(gs,'_commit_execution',side_effect=RuntimeError('write failed')):
            with self.assertRaises(RuntimeError): self.process(report)
        before=len(self.client.calls)
        self.journal.close(); self.journal=DispatchJournal(self.journal_path,'https://synthetic.firebaseio.com')
        result=self.call()
        self.assertEqual(result['firebase_status'],'DISPATCHED')
        self.assertEqual(len(self.client.calls),before)

    def test_mismatched_admission_does_not_commit(self):
        self.start(); report=self.report(); report['content_fingerprint']='0'*64
        with self.assertRaises(JournalConflict): self.process(report)
        self.assertEqual(self.user['rescue_events']['sos__request_1']['status'],'PENDING')

    def test_explicit_execution_rejection_does_not_revoke_admission(self):
        self.start(); report=self.report(); self.process(report)
        report.update(message_type='EXECUTION',phase='TARGET_REJECTED',status='REJECTED',state_revision=2)
        with patch.object(gs,'_schedule_ui'):
            gs.process_execution_report(report,self.root,self.journal)
        event=self.user['rescue_events']['sos__request_1']
        self.assertEqual(event['status'],'DISPATCHED')
        self.assertEqual(event['execution']['last_report']['phase'],'TARGET_REJECTED')

    def test_acceptance_timeout_is_unknown_not_rejection(self):
        self.start(); intent=self.journal.get(('USER_A','sos__request_1'))
        self.journal.expire_admissions(intent['broker_confirmed_at_ms']+15000)
        self.assertEqual(self.journal.get(('USER_A','sos__request_1'))['state'],'UNKNOWN')
        self.assertEqual(self.user['rescue_events']['sos__request_1']['status'],'PENDING')

    def test_immediate_admission_reply_cannot_be_overwritten_by_publish_completion(self):
        original=self.client.publish
        def publish(topic, body, qos=0, retain=False):
            result=original(topic,body,qos)
            if json.loads(body).get('kind') == 'COMMIT': self.process(self.report(False))
            return result
        self.client.publish=publish
        result=self.call()
        self.assertEqual(result['firebase_status'],'PENDING')
        self.assertEqual(self.journal.get(('USER_A','sos__request_1'))['state'],'REJECTED')
        self.assertEqual(self.user['rescue_events']['sos__request_1']['execution']['admission']['reason'],'MISSION_BUSY')

    def test_transfer_query_recovers_only_missing_chunks_for_same_execution(self):
        self.start(); intent=self.journal.get(('USER_A','sos__request_1')); count=len(self.client.calls)
        gs.process_transfer_report(dict(message_type='TRANSFER',request_kind='QUERY',
            execution_id=intent['execution_id'],content_fingerprint=intent['fingerprint'],attempt=1,
            missing_chunks=[],manifest_required=False),self.journal,self.root)
        sent=[json.loads(c[1]) for c in self.client.calls[count:]]
        self.assertEqual([part['kind'] for part in sent],['MANIFEST','COMMIT'])
        self.assertEqual({part['execution_id'] for part in sent},{intent['execution_id']})

    def test_legacy_query_does_not_republish_an_unproven_execution(self):
        self.start(); intent=self.journal.get(('USER_A','sos__request_1'))
        self.journal.connection.execute('UPDATE execution_intents SET protocol_version=0')
        self.journal.connection.commit(); count=len(self.client.calls)
        gs.process_transfer_report(dict(message_type='TRANSFER',request_kind='QUERY',
            execution_id=intent['execution_id'],content_fingerprint=intent['fingerprint'],attempt=1,
            missing_chunks=[],manifest_required=True),self.journal,self.root)
        self.assertEqual(len(self.client.calls),count)

    def test_execution_progress_cannot_regress_on_out_of_order_status(self):
        self.start(); report=self.report(); self.process(report)
        newest=dict(report,message_type='EXECUTION',state_revision=4,phase='HOVERING')
        old=dict(report,message_type='EXECUTION',state_revision=2,phase='TARGET_REJECTED')
        with patch.object(gs,'_schedule_ui'):
            gs.process_execution_report(newest,self.root,self.journal)
            gs.process_execution_report(old,self.root,self.journal)
        self.assertEqual(self.user['rescue_events']['sos__request_1']['execution']['last_report']['phase'],'HOVERING')

    def test_legacy_schema_migration_is_serialized_and_preserves_evidence(self):
        self.start(); saved=self.journal.get(('USER_A','sos__request_1'))
        path=self.journal_path.parent/'legacy.sqlite3'
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE execution_intents (scope TEXT,event_key TEXT,execution_id TEXT,payload TEXT,fingerprint TEXT,state TEXT,detail TEXT,broker_confirmed_at_ms INTEGER,PRIMARY KEY(scope,event_key),UNIQUE(scope,execution_id))")
            connection.execute("INSERT INTO execution_intents VALUES (?,?,?,?,?,?,?,?)",('legacy',json.dumps(['USER_A','sos__request_1'],separators=(',',':')),saved['execution_id'],json.dumps(saved['payload']),saved['fingerprint'],'COMMITTED','original migration evidence',123))
        barrier=threading.Barrier(4)
        def open_legacy(_):
            barrier.wait(timeout=3)
            ledger=DispatchJournal(path,'legacy')
            try: return ledger.get(('USER_A','sos__request_1'))
            finally: ledger.close()
        with ThreadPoolExecutor(max_workers=4) as workers: results=list(workers.map(open_legacy,range(4)))
        self.assertTrue(all(row['state']=='UNKNOWN' and row['legacy_state']=='COMMITTED' and row['legacy_detail']=='original migration evidence' for row in results))

    def test_legacy_broker_commit_requires_live_evidence(self):
        self.start()
        self.journal.connection.execute("UPDATE execution_intents SET state='COMMITTED',detail='old saved evidence',protocol_version=0")
        self.journal.connection.commit(); self.journal.close()
        self.journal=DispatchJournal(self.journal_path,'https://synthetic.firebaseio.com')
        restored=self.journal.get(('USER_A','sos__request_1'))
        self.assertEqual(restored['state'],'UNKNOWN')
        self.assertEqual(restored['legacy_state'],'COMMITTED')
        self.assertEqual(restored['legacy_detail'],'old saved evidence')
        self.assertEqual(self.user['rescue_events']['sos__request_1']['status'],'PENDING')


if __name__ == '__main__': unittest.main()
