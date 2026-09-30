import signal
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import cam_recorder


class RecorderFaultTests(unittest.TestCase):
    def run_recorder(self, *, good_frames=1, stop_normally=False, finalize_failure=False, sync_failure=False, write_failure=False):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        output = Path(temp.name) / "flight.avi"
        events, handlers = [], {}

        class Capture:
            def __init__(self, *args):
                self.count = 0

            def isOpened(self):
                return True

            def get(self, prop):
                return {1: 16, 2: 16, 3: 240}[prop]

            def read(self):
                self.count += 1
                if stop_normally and self.count > good_frames:
                    handlers[signal.SIGTERM]()
                    return False, None
                return (True, object()) if self.count <= good_frames else (False, None)

            def release(self):
                pass

        class Writer:
            def __init__(self, path, *args):
                self.path = Path(path)
                self.path.write_bytes(b"SYNTHETIC_HEADER")

            def isOpened(self):
                return True

            def write(self, frame):
                if write_failure: raise OSError("synthetic disk full")
                with self.path.open("ab") as handle:
                    handle.write(b"SYNTHETIC_FRAME")

            def release(self):
                pass

        fake_cv = types.SimpleNamespace(
            VideoCapture=Capture, VideoWriter=Writer, VideoWriter_fourcc=lambda *a: 0,
            CAP_V4L2=0, CAP_PROP_FRAME_WIDTH=1, CAP_PROP_FRAME_HEIGHT=2,
            CAP_PROP_FPS=3, __version__="synthetic",
        )
        original_replace = cam_recorder.os.replace
        original_fsync = cam_recorder.os.fsync

        def fsync(fd):
            if sync_failure: raise OSError("synthetic sync failure")
            return original_fsync(fd)

        def replace(source, target):
            if finalize_failure:
                raise OSError("injected rename failure")
            return original_replace(source, target)

        with patch.dict(sys.modules, {"cv2": fake_cv}), \
             patch.object(cam_recorder, "emit", lambda event, **kw: events.append(dict(event=event, **kw))), \
             patch.object(cam_recorder.signal, "signal", lambda sig, cb: handlers.update({sig: cb})), \
             patch.object(cam_recorder.threading, "Thread", lambda **kw: types.SimpleNamespace(start=lambda: None)), \
             patch.object(cam_recorder.os, "replace", replace), \
             patch.object(cam_recorder.os, "fsync", fsync):
            rc = cam_recorder.main(["0", str(output), "--max-read-failures", "2"])
        return rc, events, output

    def test_camera_failure_retains_previous_frames_as_incomplete(self):
        rc, events, output = self.run_recorder(good_frames=3)
        self.assertEqual(rc, 6)
        self.assertEqual(events[-1]["frames_written"], 3)
        self.assertEqual(events[-1]["event"], "ERROR")
        self.assertEqual(events[-1]["incomplete_file"], "flight.incomplete.avi")
        self.assertTrue(output.with_name("flight.incomplete.avi").exists())
        self.assertFalse(output.exists())

    def test_zero_frame_failure_removes_empty_partial(self):
        rc, events, output = self.run_recorder(good_frames=0)
        self.assertEqual(rc, 6)
        self.assertEqual(events[-1]["incomplete_file"], "")
        self.assertEqual(list(output.parent.iterdir()), [])

    def test_normal_stop_still_produces_done(self):
        rc, events, output = self.run_recorder(stop_normally=True)
        self.assertEqual(rc, 0)
        self.assertEqual(events[-1]["event"], "DONE")
        self.assertTrue(output.exists())

    def test_failed_retention_rename_keeps_partial(self):
        rc, events, output = self.run_recorder(finalize_failure=True)
        self.assertEqual(rc, 6)
        self.assertEqual(events[-1]["incomplete_file"], "flight.part.avi")
        self.assertTrue(output.with_name("flight.part.avi").exists())

    def test_failed_finalization_is_not_done_and_keeps_partial(self):
        rc, events, output = self.run_recorder(stop_normally=True, finalize_failure=True)
        self.assertEqual(rc, 9)
        self.assertEqual(events[-1]["error_code"], "FINALIZE_FAILED")
        self.assertTrue(output.with_name("flight.part.avi").exists())
        self.assertFalse(output.exists())
    def test_disk_sync_failure_does_not_report_done(self):
        rc, events, output = self.run_recorder(stop_normally=True, sync_failure=True)
        self.assertEqual(rc, 9)
        self.assertEqual(events[-1]["error_code"], "FINALIZE_SYNC_FAILED")
        self.assertFalse(output.exists())
        self.assertTrue(output.with_name("flight.part.avi").exists())

    def test_disk_full_does_not_report_done(self):
        rc, events, output = self.run_recorder(write_failure=True)
        self.assertNotEqual(rc, 0)
        self.assertEqual(events[-1]["event"], "ERROR")
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
