import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys

from local_verification import isolated_environment, new_result_directory

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description='Run seeded local mission protocol fault regressions')
    parser.add_argument('--output', required=True)
    parser.add_argument('--seed', type=int, default=20260930)
    parser.add_argument('--cases', type=int, default=32)
    args = parser.parse_args()
    if not 1 <= args.cases <= 1000: parser.error('cases must be between 1 and 1000')
    try:
        output = new_result_directory(args.output)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    env = isolated_environment(output, sys.executable)
    env.update(SAR_PROTOCOL_FAULT_SEED=str(args.seed), SAR_PROTOCOL_FAULT_CASES=str(args.cases))
    command = [sys.executable, 'code/ground_station/tests/run.py',
               '-p', 'test_seeded_protocol_faults.py']
    started = datetime.now(timezone.utc).isoformat()
    with (output / 'protocol_faults.log').open('w') as stream:
        completed = subprocess.run(command, cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT)
    files = ['code/ground_station/tests/test_seeded_protocol_faults.py', 'code/ground_station/src/ground_station.py',
             'code/ground_station/src/dispatch_journal.py', 'code/ground_station/src/active_event_queue.py',
             'code/ground_station/src/rescue_event_manager.py', 'code/ground_station/src/mission_transfer_protocol.py',
             'code/search_uav/catkin_ws/src/rescue_bridge/src/mission_transfer_store.py',
             'code/search_uav/catkin_ws/src/rescue_bridge/src/execution_state.py',
             'verification/tools/verify_protocol_faults.py', 'verification/tools/local_network_guard/sitecustomize.py']
    report = dict(started_at=started, completed_at=datetime.now(timezone.utc).isoformat(),
                  all_passed=completed.returncode == 0, exit_code=completed.returncode,
                  seed=args.seed, cases_per_seeded_test=args.cases,
                  seed_range_inclusive=[args.seed, args.seed + args.cases - 1],
                  python=platform.python_version(), platform=platform.platform(),
                  command=command, log='protocol_faults.log',
                  source_sha256={name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in files},
                  scope='Real local Python mission store, execution engine and GS workflow with synthetic Firebase/MQTT adapters',
                  network_policy='Loopback-only socket and DNS guard; external address and DNS rejection tested',
                  limitations='This runner covers local mission transport and dispatch-state behavior using synthetic adapters.')
    (output / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: report[key] for key in ('all_passed', 'seed', 'cases_per_seeded_test', 'log')}))
    return completed.returncode


if __name__ == '__main__':
    raise SystemExit(main())
