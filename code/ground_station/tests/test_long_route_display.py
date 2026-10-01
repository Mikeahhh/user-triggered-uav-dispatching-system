import json
import unittest
from unittest.mock import Mock, patch

from test_ground_station_rescue_flow import gs


class LongRouteDisplayTests(unittest.TestCase):
    def test_raw_session_keeps_full_path_and_bounded_numbered_markers(self):
        points=[dict(latitude=22.+(i%2)/1000.,longitude=114.,timestamp=1000000+i*5000) for i in range(100001)]
        session=dict(startTime=1000000,points=points,status='ACTIVE')
        widget=Mock()
        with patch.object(gs,'map_widget',widget),patch.object(gs,'last_click_time',0),patch.object(gs,'_epoch_now_ms',return_value=600000000):
            gs.on_card_click(json.dumps(session),'session')
        self.assertEqual(widget.set_marker.call_count,2)
        route=widget.set_path.call_args.args[0]
        self.assertEqual(len(route),100001)
        self.assertEqual(route[:3],[(22.,114.),(22.001,114.),(22.,114.)])
        self.assertIn('100001',widget.set_marker.call_args.kwargs['text'])

    def test_prepared_route_preview_keeps_every_ordered_coordinate(self):
        route=[(22.+i/100000.,114.) for i in range(1001)];widget=Mock()
        with patch.object(gs,'map_widget',widget): gs._show_waypoint_route(route)
        self.assertEqual(widget.set_marker.call_count,2)
        self.assertEqual(widget.set_path.call_args.args[0],route)

    def test_tombstone_card_cannot_show_a_deleted_booking_route(self):
        widget=Mock()
        with patch.object(gs,'map_widget',widget),patch.object(gs,'last_click_time',0):
            gs.on_card_click(json.dumps(dict(_deleted=True,waypoints=[dict(latitude=22.,longitude=114.)])),'event')
        widget.set_marker.assert_not_called()
        widget.set_path.assert_not_called()


if __name__=='__main__': unittest.main()
