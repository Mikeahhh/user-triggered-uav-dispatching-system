import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import subprocess
import sys
from local_verification import isolated_environment, new_result_directory

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description="Run local software and simulation checks")
    parser.add_argument("--output", default="local-results/verification")
    parser.add_argument("--simulation-dir", type=Path,
                        help="Read new simulation data from this directory; archived data is the default")
    args = parser.parse_args()
    try:
        output = new_result_directory(args.output)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    env = isolated_environment(output, sys.executable)
    subprocess.run([sys.executable, str(ROOT / "scripts/prepare_local.py")], check=True, env=env)
    simulation_command = [sys.executable, "simulation/verify_outputs.py", "--output", str(output / "simulation_checks.json")]
    if args.simulation_dir is not None:
        simulation_command.extend(["--data-dir", str(args.simulation_dir.resolve())])
    runs = [
        ("verification_outputs", [sys.executable, "-m", "unittest", "discover", "-s", "scripts", "-p", "test_*.py", "-v"]),
        ("software", ["sh", "code/run_all_local_verification.sh"]),
        ("simulation", simulation_command),
    ]
    results = []
    for name, command in runs:
        with (output / f"{name}.log").open("w") as stream:
            completed = subprocess.run(command, cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT)
        results.append({"name": name, "exit_code": completed.returncode, "log": f"{name}.log"})
        print(f"{name}: {'PASS' if completed.returncode == 0 else 'FAIL'}", flush=True)
    report = {"completed_at": datetime.now(timezone.utc).isoformat(), "python": platform.python_version(),
              "system": platform.system(), "machine": platform.machine(),
              "source_revision": json.loads((ROOT / "code/source_revision.json").read_text())["source_revision_id"],
              "all_passed": all(item["exit_code"] == 0 for item in results), "results": results,
              "network_policy": "Python and Node subprocesses enforce loopback-only sockets and DNS; synthetic runtime configuration; no device or live-cloud launch commands",
              "scope": "Local software, protocol and saved simulation checks"}
    (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
