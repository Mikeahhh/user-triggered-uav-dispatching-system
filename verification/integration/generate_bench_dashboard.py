#!/usr/bin/env python3


from __future__ import annotations

import argparse
import html
import json
from pathlib import Path


def render(summary: dict) -> str:
    if summary.get("result") != "PASS":
        raise ValueError("dashboard requires a PASS run_summary.json")
    boundaries = summary.get("boundaries", {})
    required_false = (
        "firebase",
        "physical_phone_wifi",
        "ros_or_flight_control",
        "physical_uav",
        "physical_landing",
        "touchdown_confirmed",
    )
    if any(boundaries.get(key) is not False for key in required_false):
        raise ValueError("bench evidence boundaries are incomplete")
    if boundaries.get("real_mosquitto_mqtt") is not True:
        raise ValueError("bench summary does not confirm the local Mosquitto path")

    esc = lambda value: html.escape(str(value))
    verified = "".join("<li>{}</li>".format(esc(item)) for item in summary["verified"])
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>MASS26 Local Integration Bench</title>
<style>
:root { --bg:#07131f; --panel:#102438; --line:#2b4863; --text:#eff7ff; --muted:#9db2c6;
--cyan:#31d6e6; --green:#39e29d; --amber:#ffd166; --red:#ff6b6b; --blue:#67a9ff; }
* { box-sizing:border-box } body { margin:0; background:radial-gradient(circle at 80% 0,#18364f,#07131f 50%);
color:var(--text); font-family:Inter,"Segoe UI",Arial,sans-serif; min-width:1180px; }
.shell { width:100vw; min-height:100vh; padding:18px 30px; margin:auto; }
.top { display:flex; justify-content:space-between; align-items:flex-start; }
.eyebrow { color:var(--cyan); font-weight:800; letter-spacing:.18em; font-size:13px; }
h1 { margin:5px 0 3px; font-size:27px; }.sub { color:var(--muted); font-size:13px; }
.release { text-align:right; color:var(--muted); font:15px/1.65 Consolas,monospace; }
.banner { margin:13px 0; padding:9px 16px; border:2px solid var(--amber); border-radius:11px;
background:#3b3014; color:#fff0b8; text-align:center; font-weight:900; letter-spacing:.05em; }
.flow { display:flex; align-items:center; gap:8px; margin-bottom:13px; }
.node { flex:1; min-height:80px; display:flex; flex-direction:column; justify-content:center; text-align:center;
background:linear-gradient(145deg,#132a41,#0e1e30); border:1px solid #315372; border-radius:13px; padding:12px; }
.node strong { color:var(--green); font-size:15px; }.node small { color:var(--muted); margin-top:6px; line-height:1.35; }
.arrow { color:var(--blue); font-size:20px; }.grid { display:grid; grid-template-columns:1fr 1fr 1.1fr; gap:13px; }
.card { background:linear-gradient(145deg,#12273c,#0d1c2c); border:1px solid var(--line); border-radius:14px;
padding:14px 17px; min-height:190px; box-shadow:0 12px 30px #0005; }
.card h2 { margin:0 0 15px; color:var(--muted); font-size:14px; letter-spacing:.14em; }
.mono { font-family:Consolas,monospace; overflow-wrap:anywhere; }.value { color:var(--cyan); font-size:20px; font-weight:800; }
.kv { display:flex; justify-content:space-between; gap:14px; padding:8px 0; border-bottom:1px solid #213a50; }
.kv span { color:var(--muted) }.true { color:var(--green); font-weight:800 }.false { color:var(--red); font-weight:800 }
.hash { margin-top:10px; padding:10px; border-radius:8px; background:#071521; color:#b5d2eb; font-size:13px; }
.verified { grid-column:span 2; }.verified ul { columns:2; padding-left:20px; margin:0; }
.verified li { color:#dce9f4; line-height:1.35; margin:0 18px 7px 0; }.boundary { border-left:4px solid var(--amber); }
.foot { margin-top:9px; color:var(--muted); text-align:right; font-size:11px; }
</style></head><body><main class="shell">
<div class="top"><div><div class="eyebrow">MASS26 • CODE COMPLETION EVIDENCE</div>
<h1>Phone → UAV → Ground Station Local Integration Bench</h1>
<div class="sub">One synthetic record joined by request ID, mission ID, payload hash and envelope hash.</div></div>
<div class="release">Mobile 1.2.0<br>UAV 1.2.0<br>Ground Station 0.4.0 / V7<br>MASS26-20260806</div></div>
<div class="banner">LOCAL SYNTHETIC BENCH • NO FIREBASE • NO PHYSICAL PHONE WLAN • NO ROS/FCU • NO PHYSICAL UAV OR LANDING</div>
<section class="flow">
<div class="node"><strong>SYNTHETIC PHONE</strong><small>HTTP SOS/GPS record</small></div><div class="arrow">→</div>
<div class="node"><strong>UAV RECEIVER</strong><small>validated + durable JSON</small></div><div class="arrow">→</div>
<div class="node"><strong>LAND_REQUESTED</strong><small>queue-complete software trigger</small></div><div class="arrow">→</div>
<div class="node"><strong>LOCAL MOSQUITTO</strong><small>QoS 1 onboard envelope</small></div><div class="arrow">→</div>
<div class="node"><strong>GROUND STATION</strong><small>hash verification + durable store</small></div><div class="arrow">→</div>
<div class="node"><strong>ACKNOWLEDGED</strong><small>matching ACK closes outbox</small></div>
</section>
<section class="grid">
<article class="card"><h2>RUN IDENTITY</h2><div class="value mono">$mission_id</div>
<div class="kv"><span>Result</span><strong class="true">PASS</strong></div>
<div class="kv"><span>Completed UTC</span><strong>$completed_at</strong></div>
<div class="kv"><span>Transport</span><strong>localhost HTTP + MQTT</strong></div></article>
<article class="card"><h2>HASH JOIN</h2><div class="kv"><span>Payload</span><strong class="true">MATCHED</strong></div>
<div class="hash mono">$payload_hash</div><div class="kv"><span>Envelope</span><strong class="true">MATCHED</strong></div>
<div class="hash mono">$envelope_hash</div></article>
<article class="card boundary"><h2>EVIDENCE BOUNDARY</h2>
<div class="kv"><span>Firebase</span><strong class="false">false</strong></div>
<div class="kv"><span>Physical phone WLAN</span><strong class="false">false</strong></div>
<div class="kv"><span>ROS / flight control</span><strong class="false">false</strong></div>
<div class="kv"><span>Physical UAV</span><strong class="false">false</strong></div>
<div class="kv"><span>touchdown_confirmed</span><strong class="false">false</strong></div></article>
<article class="card verified"><h2>VERIFIED BY THE BENCH</h2><ul>$verified</ul></article>
<article class="card"><h2>LANDING COMPATIBILITY</h2><div class="value">LANDING → LAND_REQUESTED</div>
<p style="color:#dce9f4;line-height:1.55">Mission queue complete and LAND command requested; physical touchdown was not confirmed.</p>
<div class="kv"><span>Real local Mosquitto</span><strong class="true">true</strong></div></article>
</section><div class="foot">Rendered from hash-verified run_summary.json; no credentials or precise coordinates shown.</div>
</main></body></html>""".replace("$mission_id", esc(summary["mission_id"])) \
        .replace("$completed_at", esc(summary["completed_at"])) \
        .replace("$payload_hash", esc(summary["payload_sha256"])) \
        .replace("$envelope_hash", esc(summary["envelope_sha256"])) \
        .replace("$verified", verified)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bench-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    bench_dir = Path(args.bench_dir)
    summary = json.loads((bench_dir / "run_summary.json").read_text(encoding="utf-8"))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(summary), encoding="utf-8")
    print(output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
