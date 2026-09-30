import sys
from pathlib import Path

BRIDGE_SOURCE = Path(__file__).resolve().parents[2] / 'catkin_ws/src/rescue_bridge/src'
if str(BRIDGE_SOURCE) not in sys.path:
    sys.path.insert(0, str(BRIDGE_SOURCE))
from execution_protocol import normalize_execution_payload
from execution_state import ExecutionManager
from phone_sos_receiver import RescueStore


def task(mid='TEST_USER/bench_001', eid='exec-a', kind='event'):
    return normalize_execution_payload(dict(schema_version=2, mission_id=mid, execution_id=eid,
        mission_type=kind, waypoints=[{'lat': 22., 'lon': 114.}], return_to_launch=False, hover_seconds=0))


def context_for(mid='TEST_USER/bench_001', eid='exec-a', kind='event'):
    value = task(mid, eid, kind)
    return dict(schema_version=2, context_epoch='a'*32, collection_ready=True,
                mission_id=mid, execution_id=eid, mission_type=kind, phase='HOVERING', waypoint_index=0,
                source_waypoint_total=1, arrival_observed=True, content_fingerprint=value['content_fingerprint'])


def completed_status(mid='TEST_USER/bench_001', eid='exec-a'):
    return dict(schema_version=2, status='LAND_REQUESTED', phase='LAND_REQUESTED', mission_id=mid,
                execution_id=eid, all_waypoints_completed=True, land_command_requested=True, delivery_eligible=True)


def completed_store(root, mid='TEST_USER/bench_001'):
    root = Path(root)
    path = root / 'execution.json'
    if not path.exists():
        engine = ExecutionManager(str(path))
        try:
            engine.admit(task(mid))
            engine.feedback(mid, 'exec-a', 0, 'ACCEPTED')
            engine.feedback(mid, 'exec-a', 0, 'ARRIVED', position_valid=True, feedback_seq=1)
            engine.tick()
            engine.land_result(True)
        finally:
            engine.close()
    return RescueStore(root, path)
