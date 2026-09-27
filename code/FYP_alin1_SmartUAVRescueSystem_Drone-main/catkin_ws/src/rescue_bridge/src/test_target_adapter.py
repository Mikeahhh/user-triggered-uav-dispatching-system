import sys
import types
import threading
import unittest
from unittest.mock import patch
import test_mqtt_bridge_core
from target_lifecycle import TargetLifecycle


def pose():
    return types.SimpleNamespace(position=types.SimpleNamespace(x=0., y=0., z=0.),
                                 orientation=types.SimpleNamespace(x=0., y=0., z=0., w=1.))


class PoseStamped:
    def __init__(self):
        self.header = types.SimpleNamespace(stamp=None, frame_id='')
        self.pose = pose()


for pkg, name, klass in (('geometry_msgs', 'PoseStamped', PoseStamped), ('nav_msgs', 'Odometry', object)):
    sys.modules[pkg] = types.ModuleType(pkg)
    mod = types.ModuleType(pkg+'.msg'); setattr(mod, name, klass); sys.modules[pkg+'.msg'] = mod
from sim_gps_adapter import SimGpsAdapter


def target(index=0, eid='exec-a'):
    return dict(mission_id='AUDIT/a', execution_id=eid, waypoint_index=index,
                latitude=22., longitude=114., altitude=5.)


class TargetCoreTests(unittest.TestCase):
    def test_sequence_identity_duplicate_and_abort(self):
        core = TargetLifecycle()
        self.assertEqual(core.offer(target(2), True)[1], 'WAYPOINT_SEQUENCE_MISMATCH')
        self.assertEqual(core.offer(target(), False, 'GPS_NOT_READY')[0], 'REJECTED')
        self.assertEqual(core.offer(target(), True), ('ACCEPTED','',True))
        self.assertFalse(core.offer(target(), True)[2])
        self.assertEqual(core.offer(target(1), True)[1], 'TARGET_BUSY')
        self.assertFalse(core.arrived(-1,1)); self.assertFalse(core.arrived(0,float('inf')))
        self.assertFalse(core.arrived(1,1)); self.assertTrue(core.arrived(.9,1))
        self.assertEqual(core.offer(target(), True)[0], 'ARRIVED')
        self.assertEqual(core.offer(target(2), True)[1], 'WAYPOINT_SEQUENCE_MISMATCH')
        self.assertTrue(core.offer(target(1), True)[2])
        self.assertFalse(core.control('AUDIT/other','exec-a'))
        self.assertTrue(core.control('AUDIT/a','exec-a'))
        self.assertEqual(core.offer(target(1), True)[1], 'EXECUTION_RELEASED')
        self.assertTrue(core.control('AUDIT/a','exec-a',True))
        self.assertTrue(core.control('AUDIT/a','exec-a',True))
        self.assertTrue(core.offer(target(eid='exec-b'),True)[2])


class AdapterCallbackTests(unittest.TestCase):
    def setUp(self):
        self.adapter = a = SimGpsAdapter.__new__(SimGpsAdapter)
        a._lock = threading.RLock()
        a.home_lat, a.home_lon, a.init_x, a.init_y = 22.,114.,0.,0.
        a.flight_alt, a.arrival_threshold, a.data_timeout = 1.,5.,5.
        a.current_odom = None; a.odom_received_at = None
        a.receiver_session_id = 'receiver-a'
        a.lifecycle = TargetLifecycle()
        a.has_active_goal = False
        self.goals, self.feedbacks = [], []
        a.goal_pub = types.SimpleNamespace(publish=self.goals.append)
        a.status_pub = types.SimpleNamespace(publish=self.feedbacks.append)

    def odom(self, stamp=100., x=0., y=0.):
        msg = types.SimpleNamespace(header=types.SimpleNamespace(stamp=types.SimpleNamespace(to_sec=lambda:stamp)),
                                    pose=types.SimpleNamespace(pose=pose()))
        msg.pose.pose.position.x=x; msg.pose.pose.position.y=y
        self.adapter.odom_callback(msg)

    def command(self, session='receiver-a', altitude=9., index=0):
        values=target(index); values.update(receiver_session_id=session, altitude=altitude, altitude_specified=True)
        self.adapter.gps_callback(types.SimpleNamespace(**values))

    def test_missing_stale_and_wrong_receiver_reject_before_goal(self):
        self.command(); self.assertEqual(self.feedbacks[-1].reason,'ODOMETRY_NOT_READY')
        self.odom(stamp=90.); self.command(); self.assertEqual(len(self.goals),0)
        self.odom(); self.command(session='receiver-old')
        self.assertEqual(self.feedbacks[-1].reason,'RECEIVER_SESSION_MISMATCH')
        self.assertEqual(len(self.goals),0)

    def test_altitude_honored_and_duplicates_replay_feedback_only(self):
        self.odom(); self.command()
        self.assertEqual(self.goals[0].pose.position.z,9.)
        self.assertEqual(self.feedbacks[-1].status,'ACCEPTED')
        self.command(); self.assertEqual(len(self.goals),1)
        self.odom(stamp=90.)
        self.assertTrue(self.adapter.lifecycle.active)
        self.odom()
        self.assertEqual(self.feedbacks[-1].status,'ARRIVED')
        self.command()
        self.assertEqual([m.status for m in self.feedbacks[-2:]],['ACCEPTED','ARRIVED'])
        self.assertEqual(len(self.goals),1)

    def test_receiver_restart_cannot_accept_old_target(self):
        self.odom(); self.command()
        self.adapter.receiver_session_id='receiver-b'
        self.adapter.lifecycle=TargetLifecycle()
        self.command(session='receiver-a')
        self.assertEqual(self.feedbacks[-1].reason,'RECEIVER_SESSION_MISMATCH')
        self.assertEqual(len(self.goals),1)

    def test_local_goal_publish_failure_not_cached_as_accepted(self):
        self.odom()
        self.adapter.goal_pub.publish=lambda msg: (_ for _ in ()).throw(RuntimeError('transport failure'))
        self.command()
        self.assertEqual(self.feedbacks[-1].reason,'LOCAL_GOAL_PUBLICATION_FAILED')
        self.command()
        self.assertEqual(self.feedbacks[-1].reason,'EXECUTION_RELEASED')


if __name__=='__main__': unittest.main()
