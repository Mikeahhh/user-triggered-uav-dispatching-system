import ast
import json
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path

from recording_state import matches_recording_status

SOURCE = Path(__file__).with_name('drone_console.py')
BRIDGE = SOURCE.resolve().parents[2] / 'catkin_ws' / 'src' / 'rescue_bridge' / 'src'
sys.path.insert(0, str(BRIDGE))
from execution_protocol import normalize_execution_payload


def console_class():
    tree = ast.parse(SOURCE.read_text())
    cls = next(value for value in tree.body if isinstance(value, ast.ClassDef) and value.name == 'DroneConsole')
    wanted = [value for value in cls.body if isinstance(value, ast.FunctionDef) and value.name in ('_on_status_update','_load_verified_route','_on_dispatch_seen','_draw_map')]
    namespace = dict(__file__=str(SOURCE), matches_recording_status=matches_recording_status, COLOR_DIM='grey', COLOR_WARN='yellow', COLOR_TEXT='white', COLOR_ACCENT='red')
    exec(compile(ast.fix_missing_locations(ast.Module(body=wanted, type_ignores=[])), str(SOURCE), 'exec'), namespace)
    return type('ConsoleUnderTest', (), {name:namespace[name] for name in ('_on_status_update','_load_verified_route','_on_dispatch_seen','_draw_map')})


class ConsoleTransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.journal = Path(self.temp.name)/'executions.json'
        self.console = c = console_class()()
        c.cfg = dict(execution_journal_path=str(self.journal))
        c._lock = threading.RLock()
        c.current_status = 'OFFLINE'; c.current_phase = 'OFFLINE'
        c.current_mission_id=''; c.current_execution_id=''; c.queue_waypoints=[]; c.takeoff_gps=None
        c._recording=False; c._rec_starting=False; c._rec_stopping=False
        c._rec_mission_id='user/event'; c._rec_execution_id='exec-a'
        self.draws=[]; self.starts=[]; self.stops=[]; self.logs=[]
        c.root=types.SimpleNamespace(after=lambda _, callback: callback())
        c._draw_map=lambda: self.draws.append(list(c.queue_waypoints))
        c._refresh_telemetry=lambda: None
        c._log=lambda *args, **kwargs: self.logs.append(args)
        c._start_recording=lambda **kwargs: self.starts.append(kwargs)
        c._stop_recording=lambda **kwargs: self.stops.append(kwargs)
        self.task=dict(schema_version=2, execution_id='exec-a', mission_id='user/event', mission_type='quick_start',
                       waypoints=[dict(latitude=22.,longitude=114.),dict(latitude=22.,longitude=114.),dict(latitude=22.1,longitude=114.1)],
                       return_to_launch=True, altitude=5., hover_seconds=5.)
        self.fingerprint=normalize_execution_payload(self.task)['content_fingerprint']

    def status(self, **updates):
        value=dict(schema_version=2,message_type='EXECUTION',execution_id='exec-a',active_execution_id='exec-a',
                   mission_id='user/event',content_fingerprint=self.fingerprint,accepted=True,state_revision=2,
                   status='NAVIGATING',phase='NAVIGATING',source_waypoint_total=3,waypoint_total=4,waypoint_index=0)
        value.update(updates)
        return value

    def write_task(self):
        directory=Path(str(self.journal)+'.transfers')/'exec-a'; directory.mkdir(parents=True)
        (directory/'task.json').write_text(json.dumps(self.task))
        return directory

    def test_verified_accepted_execution_restores_order_and_repeated_points(self):
        self.write_task(); self.console._on_status_update(self.status())
        self.assertEqual(self.console.queue_waypoints,[(22.,114.),(22.,114.),(22.1,114.1)])
        self.assertEqual(len(self.starts),1)
        self.assertEqual(self.console.current_execution_id,'exec-a')

    def test_transfer_admission_protocol_messages_never_change_flight_or_recording(self):
        self.console.current_status='NAVIGATING'; self.console.current_execution_id='active'
        self.console.queue_waypoints=[(1,2)]; self.console._recording=True
        for kind in ('TRANSFER','ADMISSION','PROTOCOL_ERROR'):
            self.console._on_status_update(self.status(message_type=kind,status='LANDING',phase='LAND_REQUESTED'))
        self.assertEqual(self.console.current_execution_id,'active')
        self.assertEqual(self.console.queue_waypoints,[(1,2)])
        self.assertEqual(self.starts+self.stops,[])

    def test_partial_transfer_or_wrong_hash_cannot_supply_displayed_route(self):
        directory=Path(str(self.journal)+'.transfers')/'exec-a'; directory.mkdir(parents=True)
        (directory/'chunk-0000.json').write_text(json.dumps(self.task['waypoints']))
        self.console.queue_waypoints=[(99,99)]
        self.console._on_status_update(self.status())
        self.assertEqual(self.console.queue_waypoints,[])
        (directory/'task.json').write_text(json.dumps(self.task))
        self.assertFalse(self.console._load_verified_route(self.status(content_fingerprint='0'*64)))
        self.assertFalse(self.console._load_verified_route(self.status(execution_id='../escape')))

    def test_old_revision_and_other_active_execution_cannot_stop_recording(self):
        self.write_task(); self.console._on_status_update(self.status(state_revision=4))
        self.console._recording=True
        self.console._on_status_update(self.status(state_revision=3,status='LANDING',phase='LAND_REQUESTED'))
        self.console._on_status_update(self.status(state_revision=5,active_execution_id='another',status='LANDING',phase='LAND_REQUESTED'))
        self.assertEqual(self.console.current_phase,'NAVIGATING')
        self.assertEqual(self.stops,[])

    def test_large_route_preview_is_bounded_without_modifying_execution_points(self):
        calls=[]
        class Canvas:
            def winfo_width(self): return 800
            def winfo_height(self): return 600
            def __getattr__(self,name): return lambda *args,**kwargs:calls.append((name,args,kwargs))
        c=self.console;c.canvas=Canvas();c.drone_gps=None;c.current_wp_index=50123
        c.queue_waypoints=[(22.+i/1000000.,114.) for i in range(100000)]
        original=list(c.queue_waypoints)
        type(c)._draw_map(c)
        self.assertEqual(c.queue_waypoints,original)
        self.assertLess(sum(name=='create_line' for name,_,_ in calls),1100)
        self.assertLess(sum(name=='create_text' for name,_,_ in calls),100)
        self.assertTrue(any('100000 source waypoints' in str(kw.get('text','')) for _,_,kw in calls))

    def test_legacy_route_and_status_remain_visible_without_transfer_files(self):
        self.console._on_dispatch_seen('legacy',dict(waypoints=self.task['waypoints']))
        self.console._on_status_update(dict(status='NAVIGATING',mission_id='legacy/task',waypoint_index=1,waypoint_total=3))
        self.assertEqual(len(self.console.queue_waypoints),3)
        self.assertEqual(self.console.current_mission_id,'legacy/task')


if __name__=='__main__': unittest.main()
