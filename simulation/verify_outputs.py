from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np

parser = argparse.ArgumentParser(description="Check saved simulation data without replacing archived results")
parser.add_argument("--output", type=Path, help="Write the full report to a new JSON file")
args = parser.parse_args()
if args.output is not None and args.output.exists():
    parser.error(f"output already exists: {args.output}")

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"
config_record = json.loads((OUT / "scenario_and_settings.json").read_text())
cfg = config_record["config"]
scenario = config_record["scenario"]
grid = np.fromfile(ROOT / "data/N22E114.hgt", dtype=">i2").reshape(3601, 3601)
checks: list[dict] = []


def check(name: str, passed: bool, **details) -> None:
    checks.append({"name": name, "passed": bool(passed), **details})


def csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def matrix(rows, columns) -> np.ndarray:
    return np.array([[float(row[column]) for column in columns] for row in rows])


def elevation(east, north):
    lat = cfg["launch_lat"] + np.asarray(north) / cfg["metres_per_degree"]
    lon = cfg["launch_lon"] + np.asarray(east) / (
        cfg["metres_per_degree"] * np.cos(np.deg2rad(cfg["launch_lat"]))
    )
    row = (23 - lat) * 3600
    col = (lon - 114) * 3600
    r = np.floor(row).astype(int)
    c = np.floor(col).astype(int)
    dy = row - r
    dx = col - c
    return (
        grid[r, c] * (1 - dx) * (1 - dy)
        + grid[r + 1, c] * (1 - dx) * dy
        + grid[r, c + 1] * dx * (1 - dy)
        + grid[r + 1, c + 1] * dx * dy
    )


spec = importlib.util.spec_from_file_location("reference_sos", ROOT / "reference/sos_pattern.py")
reference = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(reference)
expected_spiral = reference.generate_spiral(
    scenario["target_lat"], scenario["target_lon"],
    radius_m=cfg["spiral_generation_threshold_m"],
    spacing_m=cfg["spiral_spacing_m"],
)
actual_spiral = matrix(csv_rows(OUT / "mode3_mission_waypoints.csv"), ["latitude_deg", "longitude_deg"])
expected_ll = np.array([[p["latitude"], p["longitude"]] for p in expected_spiral])
check("MATLAB spiral matches inspected GS algorithm", actual_spiral.shape == expected_ll.shape
      and np.allclose(actual_spiral, expected_ll, atol=1e-10, rtol=0),
      expected_waypoints=len(expected_spiral), actual_waypoints=len(actual_spiral))

target = np.array(scenario["target_en_m"])
check("Target lies on mountain terrain", 100 <= elevation(*target) <= 500,
      ground_elevation_m=float(elevation(*target)))
results = []
for mode in (1, 2, 3):
    rows = csv_rows(OUT / f"mode{mode}_execution_trace.csv")
    a = matrix(rows, ["time_s", "east_m", "north_m", "terrain_msl_m", "uav_msl_m", "agl_m", "phase_code"])
    dt = np.diff(a[:, 0])
    ground = elevation(a[:, 1], a[:, 2])
    check(f"Mode {mode}: source terrain reproduces recorded ground", np.allclose(ground, a[:, 3], rtol=0, atol=1e-7),
          max_error_m=float(np.max(np.abs(ground - a[:, 3]))))
    check(f"Mode {mode}: positive time steps", np.all(dt > 0))
    check(f"Mode {mode}: launch and landed at common home", np.max(np.abs(a[[0, -1]][:, [1, 2, 5]])) < 1e-8)
    cruise = np.isin(a[:, 6], [2, 3, 4, 5, 6])
    check(f"Mode {mode}: all cruise samples keep 80 m AGL", np.allclose(a[cruise, 4] - ground[cruise], 80, rtol=0, atol=1e-7))
    horizontal_speed = np.linalg.norm(np.diff(a[:, 1:3], axis=0), axis=1) / dt
    vertical_speed = np.abs(np.diff(a[:, 4])) / dt
    check(f"Mode {mode}: speed limits", np.max(horizontal_speed) <= 15 + 1e-7 and np.max(vertical_speed) <= 3 + 1e-7,
          max_horizontal_mps=float(np.max(horizontal_speed)), max_vertical_mps=float(np.max(vertical_speed)))
    check(f"Mode {mode}: no route samples over sea", np.min(ground) >= cfg["route_min_ground_msl_m"])
    waypoints = matrix(csv_rows(OUT / f"mode{mode}_mission_waypoints.csv"), ["east_m", "north_m"])
    events = csv_rows(OUT / f"mode{mode}_events.csv")
    arrivals = [event for event in events if event["event"] == "waypoint_arrival"]
    check(f"Mode {mode}: waypoint indices preserved", [int(e["waypoint_index"]) for e in arrivals] == list(range(1, len(waypoints)+1)))
    errors = []
    for event, waypoint in zip(arrivals, waypoints):
        index = np.argmin(np.abs(a[:, 0] - float(event["time_s"])))
        errors.append(float(np.linalg.norm(a[index, 1:3] - waypoint)))
    check(f"Mode {mode}: recorded arrival at every input waypoint", max(errors) < 1e-7, max_position_error_m=max(errors))
    check(f"Mode {mode}: target occurs in mission input", np.min(np.linalg.norm(waypoints-target, axis=1)) < 1e-7)
    return_event = next(event for event in events if event["event"] == "return_start")
    check(f"Mode {mode}: all waypoints visited before return", float(return_event["time_s"]) >= float(arrivals[-1]["time_s"]) + cfg["hover_seconds"] - 1e-7)
    check(f"Mode {mode}: final event is landing completion", events[-1]["event"] == "landing_complete"
          and abs(float(events[-1]["time_s"]) - a[-1, 0]) < 1e-8)
    results.append({"mode": mode, "samples": len(a), "waypoints": len(waypoints), "duration_s": a[-1, 0],
                    "ground_elevation_range_m": [float(ground.min()), float(ground.max())]})

axes = json.loads((OUT / "figure_axes_audit.json").read_text())
for group in ("main_3d_axes", "main_top_axes", "target_insets"):
    group_axes = axes[group]
    check(f"{group}: complete frames", all(ax["box"] == "on" for ax in group_axes))
    check(f"{group}: same geographic limits across modes", all(ax["xlim"] == group_axes[0]["xlim"]
          and ax["ylim"] == group_axes[0]["ylim"] for ax in group_axes))
    check(f"{group}: equal horizontal metre scaling", all(abs(ax["data_aspect_ratio"][0] - ax["data_aspect_ratio"][1]) < 1e-9 for ax in group_axes))

source_manifest = json.loads((ROOT / "verification/source_manifest.json").read_text())
for source in source_manifest["sources"]:
    check(f"Source copy unchanged: {source['local_copy']}", hashlib.sha256((ROOT / source["local_copy"]).read_bytes()).hexdigest() == source["sha256"])

report = {"completed_at": datetime.now(timezone.utc).isoformat(),
          "all_passed": all(c["passed"] for c in checks), "check_count": len(checks), "checks": checks,
          "missions": results, "scope": "Independent numeric and figure-axis audit. No new flight-stack or field validation."}
if args.output is not None:
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, indent=2) + "\n")
print(json.dumps({"all_passed": report["all_passed"], "check_count":len(checks), "missions": results,
                  "failed": [c for c in checks if not c["passed"]]}, indent=2))
raise SystemExit(0 if report["all_passed"] else 1)
