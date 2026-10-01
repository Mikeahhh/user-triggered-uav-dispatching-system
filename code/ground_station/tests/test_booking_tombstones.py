import unittest
from unittest.mock import Mock, patch

import active_event_queue
import test_ground_station_rescue_flow as flow
from rescue_event_manager import detect_event_booking_timeout, scan_user_records


gs = flow.gs


class BookingTombstoneTests(unittest.TestCase):
    tearDown = flow.RescueEventDispatchWrapperTests.tearDown
    call = flow.RescueEventDispatchWrapperTests.call

    def setUp(self):
        flow.RescueEventDispatchWrapperTests.setUp(self)
        self.root.data['users']['USER_A'] = {
            'booked_events': {'booking_1': {'expectedEndAtMs':100, 'waypoints':[{'latitude':22.,'longitude':114.}]}},
            'rescue_events': {'sos__request_1':flow._event('sos__request_1',trigger_type='EVENT_BOOKING_TIMEOUT',
                primary_record_type='booked_events',primary_record_id='booking_1',primary_expected_end_at_ms=100,abnormal_since_ms=100)}}

    @property
    def user(self): return self.root.data['users']['USER_A']

    def delete(self): self.user['booked_events']['booking_1'].update(_deleted=True,_client_revision=999)

    def test_tombstone_creates_no_notice_or_event_even_after_end_time(self):
        self.delete(); booking=self.user['booked_events']['booking_1']
        self.assertIsNone(detect_event_booking_timeout('USER_A','booking_1',booking,1000))
        result=scan_user_records('USER_A',{'booked_events':{'booking_1':{'_deleted':True,'_client_revision':999}}},1000,quick_start_timeout_ms=1000)
        self.assertFalse(result['alerts']); self.assertFalse(result['events']); self.assertFalse(result['issues'])

    def test_deleted_primary_cannot_be_prepared_or_fingerprinted(self):
        self.delete(); event=self.user['rescue_events']['sos__request_1']
        with self.assertRaisesRegex(ValueError,'deleted'): gs.prepare_mission_for_rescue_event(self.user,event)
        with self.assertRaisesRegex(ValueError,'deleted'): active_event_queue.source_fingerprint(self.user,event)
        self.assertIn('deleted',gs._booking_episode_problem(self.user,event))

    def test_deletion_during_dispatch_confirmation_invalidates_saved_preparation(self):
        def confirm(*args): self.delete(); return True
        result=self.call(confirm)
        self.assertEqual(result['status'],gs.DISPATCH_INVALID_PARAMETERS)
        self.assertEqual(self.client.calls,[])
        self.assertNotIn('execution',self.user['rescue_events']['sos__request_1'])

    def test_deleted_booking_is_hidden_from_history_and_supplementary_count(self):
        self.delete()
        with patch.object(gs,'_section_heading'), patch.object(gs.ctk,'CTkLabel',return_value=Mock()), patch.object(gs,'create_mission_card') as card:
            gs._render_raw_records_read_only({'USER_A':self.user},1000)
        card.assert_not_called()
        self.assertEqual(gs.supplementary_record_summary(self.user,{'primary_record_type':'other','primary_record_id':'other'}),'None')

    def test_deleted_source_cannot_restart_an_incomplete_saved_transfer(self):
        result=self.call(); self.assertEqual(result['status'],gs.DISPATCH_PUBLISHED)
        intent=self.journal.get(('USER_A','sos__request_1')); sent=len(self.client.calls)
        self.delete()
        gs.process_transfer_report(dict(message_type='TRANSFER',request_kind='QUERY',
            execution_id=intent['execution_id'],content_fingerprint=intent['fingerprint'],attempt=1,
            missing_chunks=[],manifest_required=True),self.journal,self.root)
        self.assertEqual(len(self.client.calls),sent)
        self.assertEqual(self.journal.get(('USER_A','sos__request_1'))['state'],'UNKNOWN')


if __name__=='__main__': unittest.main()
