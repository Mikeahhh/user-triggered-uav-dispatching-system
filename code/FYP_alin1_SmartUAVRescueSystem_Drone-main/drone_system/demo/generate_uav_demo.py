#!/usr/bin/env python3


from __future__ import annotations

import argparse
import hashlib
import html
import json
import sys
import tempfile
from pathlib import Path
from string import Template


DRONE_SYSTEM = Path(__file__).resolve().parents[1]
REPO_ROOT = DRONE_SYSTEM.parent
BRIDGE_SRC = REPO_ROOT / "catkin_ws" / "src" / "rescue_bridge" / "src"
RECEIVER_SRC = DRONE_SYSTEM / "receiver"
for module_path in (BRIDGE_SRC, RECEIVER_SRC):
    if str(module_path) not in sys.path:
        sys.path.insert(0, str(module_path))

from mission_protocol import (
    PHASE_LAND_REQUESTED,
    SYSTEM_RELEASE_ID,
    UAV_SOFTWARE_VERSION,
    append_rtl_waypoint,
    build_status_payload,
    normalize_multi_payload,
)
from phone_sos_receiver import (
    RescueDeliveryCoordinator,
    RescueStore,
    utc_now,
)


DEMO_MISSION_ID = "DEMO_USER/demo_sos_001"


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name("." + path.name + ".tmp")
    temp_path.write_text(value, encoding="utf-8")
    temp_path.replace(path)


def build_demo_state() -> dict:
    mission = normalize_multi_payload({
        "mission_id": DEMO_MISSION_ID,
        "mission_type": "sos",
        "waypoints": [
            {"latitude": 22.0000, "longitude": 114.0000},
            {"latitude": 22.0005, "longitude": 114.0005},
            {"latitude": 22.0010, "longitude": 114.0000},
        ],
        "return_to_launch": True,
        "altitude": 5.0,
        "hover_seconds": 5.0,
    })
    queue, rtl_appended = append_rtl_waypoint(
        mission["waypoints"], mission["return_to_launch"], (21.9995, 113.9995)
    )
    status = build_status_payload(
        "LANDING",
        "mission queue complete; LAND command request published",
        mission["mission_id"],
        waypoint_index=len(queue),
        waypoint_total=len(queue),
        queue_remaining=0,
        phase=PHASE_LAND_REQUESTED,
        land_command_requested=True,
    )

    with tempfile.TemporaryDirectory(prefix="mass26-uav-demo-") as temp_dir:
        store = RescueStore(Path(temp_dir))
        record, _ = store.store({
            "schema_version": 1,
            "request_id": "demo_sos_001",
            "mission_id": DEMO_MISSION_ID,
            "user_id": "DEMO_USER",
            "latitude": 22.0000,
            "longitude": 114.0000,
            "accuracy": 3.0,
            "captured_at": "2026-08-06T06:00:00.000Z",
            "client_timestamp_ms": 1785996000000,
            "status": "PENDING",
            "device": "android-demo",
            "gps_points": [{
                "latitude": 22.0000,
                "longitude": 114.0000,
                "captured_at": "2026-08-06T06:00:00.000Z",
            }],
            "test_mode": True,
        })
        published = []
        coordinator = RescueDeliveryCoordinator(
            store, lambda topic, body: published.append((topic, body))
        )
        forwarded = coordinator.handle_mission_status(status)
        envelope = json.loads(published[0][1])
        delivery = store.read_delivery(record["request_id"])
        coordinator.handle_ground_ack({
            "schema_version": 1,
            "request_id": record["request_id"],
            "mission_id": record["mission_id"],
            "payload_sha256": record["payload_sha256"],
            "envelope_sha256": delivery["envelope_sha256"],
            "status": "ACKNOWLEDGED_BY_GS",
            "acknowledged_at": utc_now(),
        })
        delivery = store.read_delivery(record["request_id"])

    return {
        "demo": {
            "mode": "HARDWARE_FREE_LOCAL_SIMULATION",
            "physical_uav_connected": False,
            "ros_started": False,
            "camera_invoked": False,
            "contains_real_mission_data": False,
            "notice": "Synthetic code-path demonstration; not physical-flight evidence.",
        },
        "release": {
            "uav_software_version": UAV_SOFTWARE_VERSION,
            "system_release_id": SYSTEM_RELEASE_ID,
            "receiver_health": "READY (hardware-free store)",
        },
        "mission": {
            "mission_id": mission["mission_id"],
            "mission_type": mission["mission_type"],
            "source_waypoints": len(mission["waypoints"]),
            "rtl_requested": mission["return_to_launch"],
            "rtl_appended": rtl_appended,
            "queue_total": len(queue),
            "queue_remaining": 0,
            "coordinates": "synthetic demo coordinates; hidden from dashboard",
        },
        "status": status,
        "record": {
            "request_id": record["request_id"],
            "mission_id": record["mission_id"],
            "payload_sha256": record["payload_sha256"],
            "storage_state": record["storage_state"],
            "forward_trigger": delivery["forward_trigger"],
            "delivery_attempts": delivery["delivery_attempts"],
            "delivery_state": delivery["state"],
            "ack_status": delivery["ground_ack"]["status"],
            "forwarded_messages": forwarded,
        },
        "recording": {
            "auto_start_trigger": "NAVIGATING",
            "auto_stop_trigger": "LAND_REQUESTED",
            "manifest_states": [
                "STARTING", "RECORDING", "PAUSED", "STOP_REQUESTED",
                "COMPLETED", "FAILED",
            ],
            "demo_camera_invoked": False,
            "note": "Recorder state machine tested; camera hardware is not invoked in this demo.",
        },
    }


def render_html(state: dict) -> str:
    esc = lambda value: html.escape(str(value))
    mission = state["mission"]
    status = state["status"]
    record = state["record"]
    release = state["release"]
    recording = state["recording"]
    hash_short = record["payload_sha256"][:16] + "…" + record["payload_sha256"][-12:]
    template = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>MASS26 UAV Hardware-Free Demo</title>
<style>
:root { --bg:#07111d; --panel:#101f31; --line:#28415b; --text:#edf5ff; --muted:#91a8bf;
--cyan:#25d7e6; --green:#3be29d; --amber:#ffd166; --red:#ff6b6b; --blue:#60a5fa; }
* { box-sizing:border-box; } body { margin:0; background:radial-gradient(circle at 80% 0,#16334b 0,#07111d 46%);
color:var(--text); font-family:Inter,"Segoe UI",Arial,sans-serif; min-width:1280px; }
.shell { width:1540px; height:960px; padding:34px 42px; margin:0 auto; }
.top { display:flex; align-items:flex-start; justify-content:space-between; margin-bottom:22px; }
.eyebrow { color:var(--cyan); letter-spacing:.18em; font-size:13px; font-weight:800; }
h1 { font-size:32px; margin:8px 0 4px; } .sub { color:var(--muted); font-size:15px; }
.release { text-align:right; font-family:Consolas,monospace; color:var(--muted); line-height:1.7; }
.banner { border:2px solid var(--amber); background:#3a3016; color:#fff3c4; padding:13px 18px;
border-radius:10px; font-weight:800; letter-spacing:.05em; text-align:center; margin-bottom:22px; }
.grid { display:grid; grid-template-columns:1.05fr 1fr 1fr; gap:18px; }
.card { background:linear-gradient(145deg,#12253a,#0d1b2b); border:1px solid var(--line); border-radius:14px;
padding:20px 22px; min-height:218px; box-shadow:0 12px 30px #0005; }
.card h2 { margin:0 0 14px; font-size:15px; color:var(--muted); letter-spacing:.12em; }
.value { font-size:22px; font-weight:800; color:var(--cyan); overflow-wrap:anywhere; }
.metric-row { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; margin-top:18px; }
.metric { background:#091522; border:1px solid #20384f; border-radius:9px; padding:12px; }
.metric b { display:block; font-size:24px; color:var(--text); }.metric span { color:var(--muted); font-size:12px; }
.kv { display:flex; justify-content:space-between; gap:16px; padding:8px 0; border-bottom:1px solid #1e3348; }
.kv:last-child { border-bottom:0; }.kv span:first-child { color:var(--muted); }.mono { font-family:Consolas,monospace; }
.true { color:var(--green); font-weight:800; }.false { color:var(--red); font-weight:800; }
.phase { font-size:29px; color:var(--amber); font-weight:900; margin:4px 0 10px; }
.semantic { font-size:13px; color:#d6e3ef; line-height:1.45; padding:10px; background:#2f2715; border-left:4px solid var(--amber); }
.wide { grid-column:span 2; min-height:240px; }.flow { display:flex; align-items:center; gap:9px; margin:26px 0 16px; }
.node { flex:1; text-align:center; padding:14px 8px; border:1px solid #345879; border-radius:10px; background:#0a1725; }
.node strong { display:block; color:var(--green); font-size:14px; }.node small { color:var(--muted); }.arrow { color:var(--blue); font-size:22px; }
.record-hash { background:#07121e; padding:11px; border-radius:8px; color:#a7c8e8; margin-top:12px; }
.states { display:flex; flex-wrap:wrap; gap:8px; margin-top:16px; }.pill { border:1px solid #345879; color:#bcd4e9;
padding:6px 9px; border-radius:99px; font:12px Consolas,monospace; }.foot { margin-top:18px; color:var(--muted); font-size:12px; text-align:right; }
</style></head><body><main class="shell">
<div class="top"><div><div class="eyebrow">MASS26 • UAV SOFTWARE COMPLETION</div><h1>Onboard Mission & Rescue Record Dashboard</h1>
<div class="sub">Deterministic demonstration generated from the production validation, persistence and status code paths.</div></div>
<div class="release">UAV v$uav_version<br>$release_id<br>Receiver health: READY</div></div>
<div class="banner">LOCAL HARDWARE-FREE SIMULATION • NO PHYSICAL UAV • NO ROS/FCU STARTED • NO CAMERA INVOKED • SYNTHETIC DATA ONLY</div>
<section class="grid">
<article class="card"><h2>MISSION</h2><div class="value mono">$mission_id</div>
<div class="metric-row"><div class="metric"><b>$source</b><span>source waypoints</span></div><div class="metric"><b>+1</b><span>RTL appended</span></div><div class="metric"><b>$total</b><span>queue total</span></div></div>
<div class="kv"><span>Queue remaining</span><strong>0</strong></div><div class="kv"><span>Coordinates</span><strong>synthetic / hidden</strong></div></article>
<article class="card"><h2>MISSION STATUS</h2><div class="kv"><span>Compatibility status</span><strong>$compat</strong></div>
<div class="phase">$phase</div><div class="kv"><span>land_command_requested</span><strong class="true">true</strong></div>
<div class="kv"><span>touchdown_confirmed</span><strong class="false">false</strong></div><div class="semantic">$semantics</div></article>
<article class="card"><h2>ONBOARD RECORD</h2><div class="value mono">$request_id</div>
<div class="kv"><span>Storage</span><strong class="true">$storage</strong></div><div class="kv"><span>Delivery attempts</span><strong>$attempts</strong></div>
<div class="kv"><span>Ground Station ACK</span><strong class="true">$ack</strong></div><div class="record-hash mono">SHA-256 $hash_short</div></article>
<article class="card wide"><h2>PERSISTED RECORD DELIVERY FLOW</h2><div class="flow">
<div class="node"><strong>PHONE RECORD</strong><small>validated schema</small></div><div class="arrow">→</div>
<div class="node"><strong>STORED_ONBOARD</strong><small>atomic JSON + hash</small></div><div class="arrow">→</div>
<div class="node"><strong>LAND_REQUESTED</strong><small>queue complete trigger</small></div><div class="arrow">→</div>
<div class="node"><strong>FORWARDED_TO_GS</strong><small>QoS 1 envelope</small></div><div class="arrow">→</div>
<div class="node"><strong>ACKNOWLEDGED_BY_GS</strong><small>matching hashes</small></div></div>
<div class="semantic">This flow proves the software record/ACK path in a local deterministic demo. It does not prove a physical flight or physical landing.</div></article>
<article class="card"><h2>VIDEO RECORDER STATE MODEL</h2><div class="kv"><span>Auto start</span><strong>NAVIGATING</strong></div>
<div class="kv"><span>Auto stop</span><strong>LAND_REQUESTED</strong></div><div class="kv"><span>Camera invoked in demo</span><strong class="false">false</strong></div>
<div class="states">$state_pills</div><div class="foot">Runtime READY includes OpenCV version, frame size and FPS.</div></article>
</section><div class="foot">Generated offline from UAV v$uav_version; no network, credentials, ROS nodes or hardware access.</div>
</main></body></html>"""
    return Template(template).substitute(
        uav_version=esc(release["uav_software_version"]), release_id=esc(release["system_release_id"]),
        mission_id=esc(mission["mission_id"]), source=mission["source_waypoints"], total=mission["queue_total"],
        compat=esc(status["status"]), phase=esc(status["phase"]), semantics=esc(status["semantics"]),
        request_id=esc(record["request_id"]), storage=esc(record["storage_state"]), attempts=record["delivery_attempts"],
        ack=esc(record["ack_status"]), hash_short=esc(hash_short),
        state_pills="".join('<span class="pill">{}</span>'.format(esc(item)) for item in recording["manifest_states"]),
    )


def generate(output_dir: Path) -> dict:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    state = build_demo_state()
    state_text = json.dumps(state, indent=2, sort_keys=True) + "\n"
    html_text = render_html(state)
    state_path = output_dir / "uav_demo_state.json"
    html_path = output_dir / "uav_demo_dashboard.html"
    _atomic_text(state_path, state_text)
    _atomic_text(html_path, html_text)
    checksums = []
    checksum_path = output_dir / "SHA256SUMS.txt"
    for path in sorted(
        item for item in output_dir.iterdir()
        if item.is_file() and item != checksum_path and not item.name.startswith(".")
    ):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        checksums.append("{}  {}".format(digest, path.name))
    _atomic_text(checksum_path, "\n".join(checksums) + "\n")
    return state


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    state = generate(Path(args.output_dir))
    print(json.dumps({
        "output_dir": str(Path(args.output_dir).resolve()),
        "mission_id": state["mission"]["mission_id"],
        "phase": state["status"]["phase"],
        "touchdown_confirmed": state["status"]["touchdown_confirmed"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
