import ast
import io
import json
import threading
import tempfile
import types
import unittest
from pathlib import Path

from recording_state import recording_result_error, RecordingStateError, matches_recording_status


def console_method(name):
    path = Path(__file__).with_name("drone_console.py")
    tree = ast.parse(path.read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "DroneConsole")
    fn = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)
    namespace = {"json": json, "recording_result_error": recording_result_error, "matches_recording_status": matches_recording_status,
                 "RecordingStateError": RecordingStateError, "RECORDER_EVENT_PREFIX": "RECORDER_EVENT "}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])), str(path), "exec"), namespace)
    return namespace[name]


class RecordingSessionTests(unittest.TestCase):
    def make_console(self):
        calls = []
        console = types.SimpleNamespace(
            _lock=threading.RLock(), current_status="NAVIGATING", current_mission_id="AUDIT/a",
            current_execution_id="run-a", _rec_mission_id="AUDIT/a", _rec_execution_id="run-a",
            _recording=True, _rec_starting=False, _rec_stopping=False, _rec_paused=False,
            root=types.SimpleNamespace(after=lambda *a: None), _refresh_telemetry=lambda: None,
            _update_rec_ui=lambda: None, _log=lambda *a, **kw: None,
            _stop_recording=lambda **kw: calls.append(kw), _start_recording=lambda **kw: None,
        )
        return console, calls

    def test_only_own_execution_stops_recording(self):
        method = console_method("_on_status_update")
        for mid, eid, expected in (("AUDIT/old", "run-a", False),
                                   ("AUDIT/a", "run-old", False),
                                   ("AUDIT/a", "", False),
                                   ("AUDIT/a", "run-a", True)):
            with self.subTest(mid=mid, eid=eid):
                console, calls = self.make_console()
                method(console, {"schema_version": 2, "mission_id": mid, "execution_id": eid,
                                 "status": "LANDING", "phase": "LAND_REQUESTED"})
                self.assertEqual(bool(calls), expected)

    def test_manual_session_not_ended_by_mission(self):
        self.assertFalse(matches_recording_status("manual", "", {"mission_id": "manual"}))

    def test_legacy_match_is_explicit(self):
        self.assertTrue(matches_recording_status("AUDIT/a", "", {"mission_id": "AUDIT/a"}))
        self.assertFalse(matches_recording_status("AUDIT/a", "", {"schema_version": 2, "mission_id": "AUDIT/a"}))

    def test_old_process_terminal_cannot_clear_new_session(self):
        console, _ = self.make_console()
        old_journal = types.SimpleNamespace(transition=lambda *a, **kw: None,
                                            load=lambda: {"status": "COMPLETED"})
        old = types.SimpleNamespace(stdout=io.StringIO('RECORDER_EVENT {"event":"DONE","frames_written":1}\n'),
                                    wait=lambda: 0)
        new = object()
        console._rec_proc = new
        console._rec_journal = object()
        console_method("_rec_stdout_reader")(console, old, old_journal)
        self.assertIs(console._rec_proc, new)
        self.assertTrue(console._recording)
        self.assertFalse(console._rec_stopping)
    def test_done_requires_real_nonempty_output_and_successful_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'synthetic.avi'
            self.assertEqual(recording_result_error(output, 2, 0), 'OUTPUT_FILE_MISSING_OR_EMPTY')
            output.write_bytes(b'')
            self.assertTrue(recording_result_error(output, 2, 0))
            output.write_bytes(b'SYNTHETIC_FRAME')
            self.assertTrue(recording_result_error(output, 2, 9))
            self.assertTrue(recording_result_error(output, 0, 0))
            self.assertEqual(recording_result_error(output, 2, 0), '')

    def test_sudden_process_exit_marks_current_session_failed(self):
        console, _ = self.make_console()
        changes = []
        journal = types.SimpleNamespace(transition=lambda status, **kw: changes.append((status, kw)),
                                        load=lambda: {'status': 'RECORDING'})
        proc = types.SimpleNamespace(stdout=io.StringIO(''), wait=lambda: -9)
        console._rec_proc = proc
        console._rec_journal = journal
        console_method('_rec_stdout_reader')(console, proc, journal)
        self.assertEqual(changes[-1][0], 'FAILED')
        self.assertEqual(changes[-1][1]['details']['error_code'], 'PROCESS_EXIT_-9')
        self.assertFalse(console._recording)


if __name__ == "__main__":
    unittest.main()
