import unittest
from unittest.mock import patch

import ground_station
from ground_station_demo import DEMO_BROKER_LABEL


class DemoRuntimeIsolationTests(unittest.TestCase):
    def setUp(self):
        self.demo_mode = ground_station.DEMO_SCREENSHOT_MODE

    def tearDown(self):
        ground_station.DEMO_SCREENSHOT_MODE = self.demo_mode

    def test_demo_refuses_firebase_initialization_before_credential_lookup(self):
        ground_station.DEMO_SCREENSHOT_MODE = True
        with patch.object(ground_station, "get_firebase_credential_path") as get_key:
            with patch.object(ground_station.firebase_admin, "initialize_app") as init:
                with self.assertRaisesRegex(RuntimeError, "disabled in local demo"):
                    ground_station.initialize_firebase()
        get_key.assert_not_called()
        init.assert_not_called()

    def test_demo_broker_label_never_uses_runtime_endpoint(self):
        ground_station.DEMO_SCREENSHOT_MODE = True
        with patch.object(ground_station, "MQTT_BROKER", "198.51.100.42"):
            with patch.object(ground_station, "MQTT_PORT", 1883):
                self.assertEqual(
                    ground_station.mqtt_endpoint_label(),
                    DEMO_BROKER_LABEL,
                )

    def test_production_endpoint_is_explicitly_unconfigured_by_default(self):
        ground_station.DEMO_SCREENSHOT_MODE = False
        with patch.object(ground_station, "MQTT_BROKER", ""):
            with patch.object(ground_station, "MQTT_PORT", 1883):
                self.assertEqual(
                    ground_station.mqtt_endpoint_label(),
                    "Broker: not configured",
                )


if __name__ == "__main__":
    unittest.main()
