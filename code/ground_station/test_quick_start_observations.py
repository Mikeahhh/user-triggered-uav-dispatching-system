from pathlib import Path
import tempfile
import unittest

from quick_start_observations import ObservationStore


class ObservationTests(unittest.TestCase):
    def test_duplicate_restart_and_scope_preserve_first_observation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "observations.sqlite3"
            store = ObservationStore(path, "synthetic-database")
            identity = store.workstation_id
            self.assertEqual(store.observe("u", "s", 1000, 1100), 1100)
            self.assertEqual(store.observe("u", "s", 1000, 1200), 1100)
            store.close()
            store = ObservationStore(path, "synthetic-database")
            self.addCleanup(store.close)
            self.assertEqual(store.workstation_id, identity)
            self.assertEqual(store.observe("u", "s", 1000, 1300), 1100)
            self.assertEqual(store.observe("u", "other", 1000, 1300), 1300)
            other = ObservationStore(path, "other-database")
            self.addCleanup(other.close)
            self.assertEqual(other.observe("u", "s", 1000, 1400), 1400)

    def test_rejects_invalid_and_future_time_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ObservationStore(Path(directory) / "local.sqlite3", "synthetic")
            self.addCleanup(store.close)
            for sample, seen in [(True, 10), (1.5, 10), (-1, 10), (11, 10),
                                 (10, float("nan")), (10, 2 ** 63)]:
                with self.subTest(sample=sample, seen=seen), self.assertRaises(ValueError):
                    store.observe("u", "s", sample, seen)
            self.assertEqual(store.observe("u", "s", 10, 11), 11)

            self.assertEqual(store.observe("u", "s", 10, 10), 11)


if __name__ == "__main__":
    unittest.main()
