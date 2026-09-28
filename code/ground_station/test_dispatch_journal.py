import json
import subprocess
import sys
import tempfile
import threading
import stat
import sqlite3
from unittest.mock import patch
import unittest
from pathlib import Path

from dispatch_journal import DispatchJournal, JournalConflict
from mission_execution_protocol import normalized_task, task_fingerprint

FIXTURES = json.loads((Path(__file__).parent / "test_fixtures/task_v2_fingerprints.json").read_text())


class TaskProtocolTests(unittest.TestCase):
    def test_all_shared_golden_fixtures(self):
        for case in FIXTURES["cases"]:
            with self.subTest(case=case["name"]):
                self.assertEqual(normalized_task(case["input"]), case["normalized"])
                self.assertEqual(task_fingerprint(case["input"]), case["sha256"])

    def test_bad_identity_coordinates_and_oversize_rejected(self):
        original = FIXTURES["cases"][0]["input"]
        for field, value in (("execution_id", None), ("mission_id", ""),
                             ("schema_version", True), ("return_to_launch", 1),
                             ("waypoints", []), ("waypoints", [{"lat": True, "lon": 0}]),
                             ("waypoints", [{"lat": float("nan"), "lon": 0}]),
                             ("waypoints", [{"lat": 91, "lon": 0}]),
                             ("waypoints", [{"lat": 0, "lon": 0}] * 1001)):
            with self.subTest(field=field), self.assertRaises((ValueError, TypeError)):
                task_fingerprint({**original, field: value})


class JournalTests(unittest.TestCase):
    def test_immutable_intent_and_target_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite3"
            journal = DispatchJournal(path, "synthetic-scope-A")
            payload = FIXTURES["cases"][0]["input"]
            journal.prepare(("U", "E"), payload)
            with self.assertRaises(JournalConflict):
                journal.prepare(("U", "E"), {**payload, "hover_seconds": 7})
            journal.set_state(("U", "E"), payload["execution_id"], "BROKER_CONFIRMED")
            with self.assertRaises(JournalConflict):
                journal.set_state(("U", "E"), payload["execution_id"], "UNKNOWN")
            journal.close()
            other = DispatchJournal(path, "synthetic-scope-B")
            self.assertIsNone(other.get(("U", "E")))
            other.close()

    def test_two_connections_cannot_race_a_terminal_state_backwards(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "race.sqlite3"
            first = DispatchJournal(path, "synthetic")
            second = DispatchJournal(path, "synthetic")
            payload = FIXTURES["cases"][0]["input"]
            key = ("U", "E")
            first.prepare(key, payload)
            read = threading.Event(); release = threading.Event(); committed = threading.Event()
            original_get = first.get
            errors = []
            def paused_get(event_key):
                result = original_get(event_key)
                if not read.is_set():
                    read.set()
                    if not release.wait(3): raise RuntimeError("test synchronization timed out")
                return result
            first.get = paused_get
            def write(journal, state):
                try:
                    journal.set_state(key, payload["execution_id"], state)
                    if state == "COMMITTED": committed.set()
                except Exception as exc: errors.append(exc)
            older = threading.Thread(target=write, args=(first, "UNKNOWN"))
            newer = threading.Thread(target=write, args=(second, "COMMITTED"))
            older.start()
            self.assertTrue(read.wait(3))
            newer.start()
            was_blocked = not committed.wait(0.1)
            release.set()
            older.join(3); newer.join(3)
            self.assertTrue(was_blocked, "second writer must wait for the read/update transaction")
            self.assertFalse(errors)
            self.assertEqual(second.get(key)["state"], "COMMITTED")
            with self.assertRaises(JournalConflict): first.set_state(key, payload["execution_id"], "UNKNOWN")
            first.close(); second.close()

    def test_database_and_live_wal_shm_are_private(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private" / "journal.sqlite3"
            journal = DispatchJournal(path, "synthetic")
            journal.prepare(("U", "E"), FIXTURES["cases"][0]["input"])
            for suffix in ("", "-wal", "-shm"):
                companion = Path(str(path) + suffix)
                self.assertTrue(companion.exists())
                self.assertEqual(stat.S_IMODE(companion.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
            journal.close()

    def test_platform_without_fchmod_can_open_and_recover(self):


        with tempfile.TemporaryDirectory() as directory, patch("dispatch_journal.os.fchmod", None):
            path = Path(directory) / "journal.sqlite3"
            writer = DispatchJournal(path, "synthetic")
            writer.prepare(("U", "E"), FIXTURES["cases"][0]["input"])
            writer.close()
            reader = DispatchJournal(path, "synthetic")
            self.assertEqual(reader.get(("U", "E"))["state"], "PREPARED")
            reader.close()

    def test_previous_journal_schema_adds_time_column_without_changing_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.sqlite3"
            payload = FIXTURES["cases"][0]["input"]
            encoded = json.dumps(payload)
            connection = sqlite3.connect(path)
            connection.execute("CREATE TABLE execution_intents (scope TEXT,event_key TEXT,execution_id TEXT,"
                               "payload TEXT,fingerprint TEXT,state TEXT,detail TEXT,PRIMARY KEY(scope,event_key),"
                               "UNIQUE(scope,execution_id))")
            connection.execute("INSERT INTO execution_intents VALUES (?,?,?,?,?,?,?)", (
                "synthetic", '["U","E"]', payload["execution_id"], encoded, task_fingerprint(payload), "UNKNOWN", ""))
            connection.commit(); connection.close()
            journal = DispatchJournal(path, "synthetic")
            recovered = journal.get(("U", "E"))
            self.assertEqual(recovered["payload"], payload)
            self.assertEqual(recovered["state"], "UNKNOWN")
            self.assertIsNone(recovered["broker_confirmed_at_ms"])
            journal.close()

    def test_new_process_reads_durable_broker_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite3"
            payload = FIXTURES["cases"][0]["input"]
            writer = DispatchJournal(path, "synthetic")
            writer.prepare(("U", "E"), payload)
            with patch("dispatch_journal.time.time", return_value=1234.5):
                writer.set_state(("U", "E"), payload["execution_id"], "BROKER_CONFIRMED")
            writer.close()
            script = ("from dispatch_journal import DispatchJournal; import sys; "
                      "j=DispatchJournal(sys.argv[1],'synthetic'); "
                      "print(j.get(('U','E'))['state'], j.get(('U','E'))['broker_confirmed_at_ms']); j.close()")
            result = subprocess.run([sys.executable, "-B", "-c", script, str(path)],
                                    cwd=Path(__file__).parent, text=True, capture_output=True, check=True)
            self.assertEqual(result.stdout.strip(), "BROKER_CONFIRMED 1234500")


if __name__ == "__main__":
    unittest.main()
