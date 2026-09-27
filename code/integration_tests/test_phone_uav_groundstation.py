import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path


CODE_ROOT = Path(__file__).resolve().parents[1]
DRONE_RECEIVER = (
    CODE_ROOT
    / "FYP_alin1_SmartUAVRescueSystem_Drone-main"
    / "drone_system"
    / "receiver"
)
GROUND_STATION = CODE_ROOT / "FYP_alin1_SmartUAVRescueSystem_Ground_Station-main"
sys.path.insert(0, str(DRONE_RECEIVER))
sys.path.insert(0, str(GROUND_STATION))

from phone_sos_receiver import (
    RescueDeliveryCoordinator,
    RescueHttpServer,
    RescueStore,
)
from rescue_record_protocol import (
    ACK_PUBLISHED,
    RECEIVED_STORED,
    GroundRescueStore,
    process_onboard_envelope,
)


class PhoneUavGroundStationIntegrationTest(unittest.TestCase):
    def test_synthetic_record_is_stored_forwarded_and_acknowledged(self):
        payload = {
            "schema_version": 1,
            "request_id": "bench_20260801_001",
            "mission_id": "TEST_USER/bench_20260801_001",
            "user_id": "TEST_USER",
            "latitude": 22.352,
            "longitude": 114.183,
            "accuracy": 3.0,
            "captured_at": "2026-08-01T12:00:00.000Z",
            "client_timestamp_ms": 1785585600000,
            "status": "PENDING",
            "device": "android-test-client",
            "gps_points": [
                {
                    "latitude": 22.352,
                    "longitude": 114.183,
                    "captured_at": "2026-08-01T12:00:00.000Z",
                }
            ],
            "test_mode": True,
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            store = RescueStore(Path(temp_dir))
            server = RescueHttpServer(("127.0.0.1", 0), store)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                health_conn = http.client.HTTPConnection(
                    "127.0.0.1", server.server_port, timeout=3
                )
                health_conn.request("GET", "/health")
                health_response = health_conn.getresponse()
                health = json.loads(health_response.read().decode("utf-8"))
                health_conn.close()
                self.assertEqual(health_response.status, 200)
                self.assertEqual(health["schema_version"], 1)
                self.assertEqual(health["receiver_version"], "1.2.0")
                self.assertEqual(health["uav_software_version"], "1.2.0")
                self.assertEqual(health["system_release_id"], "MASS26-20260806")

                body = json.dumps(payload).encode("utf-8")
                conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                conn.request(
                    "POST",
                    "/api/v1/rescue-requests",
                    body=body,
                    headers={"Content-Type": "application/json"},
                )
                response = conn.getresponse()
                receipt = json.loads(response.read().decode("utf-8"))
                conn.close()
                self.assertEqual(response.status, 201)
                self.assertEqual(receipt["status"], "STORED")

                mqtt_messages = []
                coordinator = RescueDeliveryCoordinator(
                    store, lambda topic, value: mqtt_messages.append((topic, value))
                )
                self.assertEqual(
                    coordinator.handle_mission_status(
                        {
                            "status": "LANDING",
                            "phase": "LAND_REQUESTED",
                            "land_command_requested": True,
                            "touchdown_confirmed": False,
                            "mission_id": "TEST_USER/bench_20260801_001",
                        }
                    ),
                    1,
                )
                ground_states = []
                published_acks = []
                ground_result = process_onboard_envelope(
                    mqtt_messages[0][1],
                    store=GroundRescueStore(Path(temp_dir) / "ground_station"),
                    publish_ack=published_acks.append,
                    on_state=lambda state, _record: ground_states.append(state),
                )
                ground_record = ground_result["record"]
                ack = published_acks[0]
                coordinator.handle_ground_ack(ack)

                self.assertEqual(ground_record["request_id"], receipt["request_id"])
                self.assertEqual(
                    ground_record["payload_sha256"], receipt["payload_sha256"]
                )
                self.assertEqual(
                    ack["envelope_sha256"], ground_record["envelope_sha256"]
                )
                self.assertEqual(
                    ground_states,
                    [RECEIVED_STORED, ACK_PUBLISHED],
                )
                self.assertTrue(ground_result["record_path"].is_file())
                self.assertTrue(ground_result["envelope_path"].is_file())
                self.assertEqual(
                    store.read_delivery(receipt["request_id"])["state"],
                    "ACKNOWLEDGED_BY_GS",
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
