import unittest

from verify_runtime_dependencies import EXPECTED_RUNTIME_VERSIONS


class DependencyLockTests(unittest.TestCase):
    def test_direct_runtime_lock_is_minimal_and_exact(self):
        self.assertEqual(
            EXPECTED_RUNTIME_VERSIONS,
            {
                "customtkinter": "5.2.2",
                "tkintermapview": "1.29",
                "firebase-admin": "7.4.0",
                "paho-mqtt": "2.1.0",
            },
        )


if __name__ == "__main__":
    unittest.main()
