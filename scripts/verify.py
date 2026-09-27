import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description="Run local software and simulation checks")
    parser.add_argument("--output", default="local-results/verification")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    subprocess.run([sys.executable, str(ROOT / "scripts/prepare_local.py")], check=True)
    env = os.environ.copy()
    env["MASS26_PYTHON"] = sys.executable
    env["MASS26_GROUND_PYTHON"] = sys.executable
    runs = [
        ("software", ["sh", "code/run_all_local_verification.sh"]),
        ("simulation", [sys.executable, "simulation/verify_outputs.py"]),
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
              "all_passed": all(item["exit_code"] == 0 for item in results), "results": results}
    (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
