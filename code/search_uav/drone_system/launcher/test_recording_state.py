import tempfile
import unittest
from pathlib import Path

from recording_state import RecordingJournal, RecordingStateError


class RecordingJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "recording.json"
        self.journal = RecordingJournal(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_full_recording_lifecycle_is_persisted(self):
        self.journal.start(
            "demo-session",
            "DEMO_USER/event_1",
            "/private/path/flight.avi",
            "MISSION_NAVIGATING",
        )
        self.journal.transition("RECORDING", details={
            "width": 1280,
            "height": 720,
            "fps": 30.0,
            "opencv_version": "4.x",
        })
        self.journal.transition("PAUSED", reason="operator pause")
        self.journal.transition("RECORDING", reason="operator resume")
        self.journal.transition("STOP_REQUESTED", reason="LAND_REQUESTED")
        state = self.journal.transition(
            "COMPLETED", details={"frames_written": 120}
        )

        self.assertEqual(state["status"], "COMPLETED")
        self.assertEqual(state["output_file"], "flight.avi")
        self.assertNotIn("/private/path", str(state))
        self.assertEqual(state["frames_written"], 120)
        self.assertEqual(len(state["events"]), 6)

    def test_invalid_transition_is_rejected(self):
        self.journal.start("demo", "manual", "flight.avi", "MANUAL")
        with self.assertRaises(RecordingStateError):
            self.journal.transition("COMPLETED")

    def test_existing_journal_cannot_be_silently_overwritten(self):
        self.journal.start("demo", "manual", "flight.avi", "MANUAL")
        with self.assertRaises(RecordingStateError):
            self.journal.start("other", "manual", "other.avi", "MANUAL")


if __name__ == "__main__":
    unittest.main()
