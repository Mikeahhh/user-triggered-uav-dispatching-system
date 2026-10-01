#!/usr/bin/env python3


from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


CODE_ROOT = (Path(__file__).resolve().parents[2] / "code")
DRONE_RECEIVER = (
    CODE_ROOT
    / "search_uav"
    / "drone_system"
    / "receiver"
)
GROUND_STATION = CODE_ROOT / "ground_station/src"
sys.path.insert(0, str(DRONE_RECEIVER))
sys.path.insert(0, str(GROUND_STATION))
sys.path.insert(0, str(CODE_ROOT / 'search_uav/catkin_ws/src/rescue_bridge/src'))
sys.path.insert(0, str(CODE_ROOT.parent / 'verification/tools'))

from execution_protocol import normalize_execution_payload
from execution_state import ExecutionManager
from capture_store_v2 import JournalCollectionContextProvider
from execution_test_support import arrive, complete_execution
from local_verification import isolated_environment, new_result_directory

import paho.mqtt.client as mqtt
try:
    from paho.mqtt.client import CallbackAPIVersion
    PAHO_V2 = True
except ImportError:
    PAHO_V2 = False

from phone_sos_receiver import MqttRuntime, RescueHttpServer, RescueStore
from rescue_record_protocol import (
    ACK_PUBLISHED,
    RECEIVED_STORED,
    GroundRescueStore,
    process_onboard_envelope,
    publish_ground_ack,
)


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_port(port, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("broker did not open port {}".format(port))


def make_client(client_id):
    if PAHO_V2:
        return mqtt.Client(
            callback_api_version=CallbackAPIVersion.VERSION1,
            client_id=client_id,
        )
    return mqtt.Client(client_id=client_id)


def write_json(path, value):
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def write_checksums(output_dir):
    lines = []
    for path in sorted(output_dir.rglob("*")):
        if (
            not path.is_file()
            or path.name == "SHA256SUMS.txt"
            or path.name == ".bench_mosquitto.conf"
        ):
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        relative = path.relative_to(output_dir).as_posix()
        lines.append("{}  {}".format(digest, relative))
    (output_dir / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(output_dir):
    output_dir = new_result_directory(output_dir)
    broker_port = free_port()
    broker_config = output_dir / ".bench_mosquitto.conf"
    broker_config.write_text(
        "listener {} 127.0.0.1\nallow_anonymous true\npersistence false\n".format(
            broker_port
        ),
        encoding="utf-8",
    )
    broker_log = (output_dir / "broker.log").open("w", encoding="utf-8")
    broker = subprocess.Popen(
        ["mosquitto", "-c", str(broker_config)],
        stdout=broker_log,
        stderr=subprocess.STDOUT,
        text=True,
    )

    server = None
    server_thread = None
    runtime = None
    gs_client = None
    trigger_client = None
    manager = None
    received_event = threading.Event()
    gs_connected_event = threading.Event()
    captured = {}
    failure = None
    try:
        wait_port(broker_port)
        synthetic_clock = [100.0]
        manager = ExecutionManager(str(output_dir / 'execution.json'), clock=lambda: synthetic_clock[0])
        store = RescueStore(output_dir / "uav_store", execution_journal_path=manager.path)
        runtime = MqttRuntime(store, "127.0.0.1", broker_port)
        runtime.start()

        gs_client = make_client("bench_ground_station")

        def on_connect(client, userdata, flags, rc):
            if rc == 0:
                result = client.subscribe("alin1/rescue/onboard_record", qos=1)
                if not isinstance(result, tuple) or result[0] == 0:
                    gs_connected_event.set()

        def on_message(client, userdata, msg):
            captured["envelope_payload"] = bytes(msg.payload)
            received_event.set()

        gs_client.on_connect = on_connect
        gs_client.on_message = on_message
        gs_client.connect("127.0.0.1", broker_port, keepalive=30)
        gs_client.loop_start()
        if not gs_connected_event.wait(5):
            raise RuntimeError("Ground Station client did not subscribe")
        if not runtime.connected.wait(5):
            raise RuntimeError("UAV MQTT runtime did not connect")

        token = "runtime-token-not-written-to-artifacts"
        server = RescueHttpServer(("127.0.0.1", 0), store, token,
                                  collection_context_provider=JournalCollectionContextProvider(manager.path))
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        timestamp_ms = int(time.time() * 1000)
        request_id = "bench_{}".format(timestamp_ms)
        mission_id = "TEST_USER/{}".format(request_id)
        payload = {
            "schema_version": 1,
            "request_id": request_id,
            "mission_id": mission_id,
            "user_id": "TEST_USER",
            "latitude": 22.352,
            "longitude": 114.183,
            "accuracy": 3.0,
            "captured_at": utc_now(),
            "client_timestamp_ms": timestamp_ms,
            "status": "PENDING",
            "device": "synthetic-bench-client",
            "gps_points": [
                {
                    "latitude": 22.3519,
                    "longitude": 114.1829,
                    "captured_at": utc_now(),
                },
                {
                    "latitude": 22.352,
                    "longitude": 114.183,
                    "captured_at": utc_now(),
                },
            ],
            "test_mode": True,
        }
        task = dict(schema_version=2, execution_id='bench-execution-' + str(timestamp_ms),
                    mission_id=mission_id, mission_type='rescue', return_to_launch=True,
                    altitude=5.0, hover_seconds=5.0,
                    waypoints=[dict(latitude=22.352, longitude=114.183)])
        manager.admit(normalize_execution_payload(task), launch_fix=(22.3519, 114.1829))
        arrive(manager)

        body = json.dumps(payload).encode("utf-8")
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=3
        )
        connection.request(
            "POST",
            "/api/v1/rescue-requests",
            body=body,
            headers={
                "Content-Type": "application/json",
                "X-Rescue-Token": token,
            },
        )
        response = connection.getresponse()
        receipt = json.loads(response.read().decode("utf-8"))
        connection.close()
        if response.status != 201 or receipt.get("status") != "STORED":
            raise RuntimeError("HTTP receiver did not store record")

        if runtime.coordinator.handle_sync_request() != 0 or received_event.is_set():
            raise RuntimeError('record forwarded before mission completion')
        completed = complete_execution(manager, synthetic_clock)
        mission_status = {
            **completed,
            "status": "LANDING",
            "message": "bench queue complete; LAND requested",
            "mission_id": mission_id,
            "test_mode": True,
        }
        trigger_client = make_client("bench_mission_status")
        trigger_client.connect("127.0.0.1", broker_port, keepalive=30)
        trigger_client.loop_start()
        time.sleep(0.2)
        trigger_info = trigger_client.publish(
            "alin1/mission/status",
            json.dumps(mission_status),
            qos=1,
        )
        trigger_info.wait_for_publish(timeout=3)
        if hasattr(trigger_info, "is_published") and not trigger_info.is_published():
            raise RuntimeError("mission status publish was not confirmed")
        trigger_client.loop_stop()
        trigger_client.disconnect()
        trigger_client = None

        if not received_event.wait(5):
            raise RuntimeError("Ground Station client did not receive onboard record")

        ground_states = []
        ground_result = process_onboard_envelope(
            captured["envelope_payload"],
            store=GroundRescueStore(output_dir / "ground_station_store"),
            publish_ack=lambda ack: publish_ground_ack(
                gs_client,
                "alin1/rescue/ground_ack",
                ack,
                timeout=3,
            ),
            on_state=lambda state, _record: ground_states.append(state),
        )
        if ground_states != [RECEIVED_STORED, ACK_PUBLISHED]:
            raise RuntimeError("Ground Station persistence/ACK state order was invalid")
        captured["envelope"] = ground_result["envelope"]
        captured["ack"] = ground_result["ack"]

        deadline = time.time() + 5
        delivery = None
        while time.time() < deadline:
            delivery = store.read_delivery(request_id)
            if delivery.get("state") == "ACKNOWLEDGED_BY_GS":
                break
            time.sleep(0.05)
        if not delivery or delivery.get("state") != "ACKNOWLEDGED_BY_GS":
            raise RuntimeError("UAV outbox did not receive Ground Station ack")

        record = store.read_record(request_id)
        envelope = captured["envelope"]
        ack = captured["ack"]
        persisted_record = json.loads(
            ground_result["record_path"].read_text(encoding="utf-8")
        )
        persisted_envelope = json.loads(
            ground_result["envelope_path"].read_text(encoding="utf-8")
        )
        if not (
            receipt["request_id"]
            == record["request_id"]
            == envelope["record"]["request_id"]
            == persisted_record["request_id"]
            == ack["request_id"]
            == delivery["request_id"]
        ):
            raise RuntimeError("request_id join failed")
        if not (
            mission_id
            == receipt["mission_id"]
            == record["mission_id"]
            == envelope["record"]["mission_id"]
            == persisted_record["mission_id"]
            == ack["mission_id"]
            == delivery["mission_id"]
        ):
            raise RuntimeError("mission_id join failed")
        if not (
            receipt["payload_sha256"]
            == record["payload_sha256"]
            == envelope["record"]["payload_sha256"]
            == persisted_record["payload_sha256"]
            == ack["payload_sha256"]
            == delivery["payload_sha256"]
        ):
            raise RuntimeError("payload hash join failed")
        if not (
            envelope["envelope_sha256"]
            == persisted_envelope["envelope_sha256"]
            == ack["envelope_sha256"]
            == delivery["envelope_sha256"]
        ):
            raise RuntimeError("envelope hash join failed")

        write_json(output_dir / "01_phone_request.json", payload)
        write_json(output_dir / "02_uav_http_receipt.json", receipt)
        write_json(output_dir / "03_uav_stored_record.json", record)
        write_json(output_dir / "04_mission_status.json", mission_status)
        write_json(output_dir / "05_mqtt_onboard_envelope.json", envelope)
        write_json(output_dir / "06_ground_station_stored_record.json", persisted_record)
        write_json(output_dir / "07_ground_station_stored_envelope.json", persisted_envelope)
        write_json(output_dir / "08_ground_station_ack.json", ack)
        write_json(output_dir / "09_uav_delivery_state.json", delivery)
        write_json(
            output_dir / "run_summary.json",
            {
                "result": "PASS",
                "completed_at": utc_now(),
                "request_id": request_id,
                "mission_id": mission_id,
                "payload_sha256": record["payload_sha256"],
                "envelope_sha256": envelope["envelope_sha256"],
                "verified": [
                    "HTTP request reached the receiver",
                    "UAV-side JSON was durably stored before receipt",
                    "the actual execution manager completed every synthetic waypoint and continuous hold before LAND was requested",
                    "sync before completion forwarded no record; durable execution evidence enabled MQTT forwarding afterward",
                    "Ground Station verified both hashes and durably stored the record and envelope",
                    "Ground Station published a hash-bound QoS 1 ACK",
                    "the exact ACK closed the matching UAV outbox record",
                    "request ID, mission ID, payload hash, and envelope hash joined across all stages",
                ],
                "boundaries": {
                    "synthetic_test_data": True,
                    "localhost_http": True,
                    "real_mosquitto_mqtt": True,
                    "physical_phone_wifi": False,
                    "firebase": False,
                    "ros_or_flight_control": False,
                    "physical_uav": False,
                    "physical_landing": False,
                    "synthetic_position_feedback": True,
                    "landing_semantics": "mission queue complete and LAND command requested; physical touchdown not confirmed",
                    "touchdown_confirmed": False,
                },
            },
        )
    except Exception as exc:
        failure = exc
        write_json(
            output_dir / "run_summary.json",
            {
                "result": "FAIL",
                "completed_at": utc_now(),
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if server_thread is not None:
            server_thread.join(timeout=2)
        if gs_client is not None:
            gs_client.loop_stop()
            gs_client.disconnect()
        if runtime is not None:
            runtime.stop()
        if trigger_client is not None:
            trigger_client.loop_stop()
            trigger_client.disconnect()
        if manager is not None:
            manager.close()
        broker.terminate()
        try:
            broker.wait(timeout=3)
        except subprocess.TimeoutExpired:
            broker.kill()
        broker_log.close()
        try:
            broker_config.unlink()
        except FileNotFoundError:
            pass
    write_checksums(output_dir)
    if failure is not None:
        raise failure


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if os.environ.get('MASS26_LOCAL_VERIFICATION') != '1':
        completed = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--output', args.output],
                                   env=isolated_environment(args.output, sys.executable))
        raise SystemExit(completed.returncode)
    run(Path(args.output))
    print(args.output)


if __name__ == "__main__":
    main()
