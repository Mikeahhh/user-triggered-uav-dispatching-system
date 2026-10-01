import unittest
from unittest.mock import patch

import ground_station


class RuntimeConfigurationTests(unittest.TestCase):
    def test_production_endpoint_is_explicitly_unconfigured_by_default(self):
        with patch.object(ground_station, "MQTT_BROKER", ""):
            with patch.object(ground_station, "MQTT_PORT", 1883):
                self.assertEqual(
                    ground_station.mqtt_endpoint_label(),
                    "Broker: not configured",
                )


if __name__ == "__main__":
    unittest.main()
