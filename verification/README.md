# Verification

Automated regression checks and saved evidence for the mobile application, Windows ground station and onboard software. Application entry points and runtime modules are under [code/](../code/).

## Directory guide

| Location | Purpose |
| --- | --- |
| [run.py](run.py) | Run the complete local software and saved-simulation checks |
| [integration/](integration/) | Cross-component contracts and the local phone-to-UAV-to-ground relay bench |
| [tools/](tools/) | Verification helpers, network isolation, configuration checks and optional ROS/video benches |
| [reports/](reports/) | Dated validation summaries and manuscript correspondence |
| [records/](records/) | Saved logs, source provenance and the publication file manifest |
| [records/phone_relay/](records/phone_relay/) | Saved relay inputs, stored records, acknowledgements and checksums |

Component unit tests stay with their respective software: [ground-station tests](../code/ground_station/tests/), [mobile tests](../code/mobile_application/__tests__/), and the onboard receiver, bridge and recorder tests. Test fixtures contain synthetic inputs for repeatable checks.

The JSON files in saved runs contain machine-readable inputs, results, source hashes or protocol records. Historical logs and reports retain the values and paths recorded for their dated source snapshot. They should be read with that snapshot's scope; current paths and commands are documented here and in the component guides.

The [1 October layout verification](reports/layout_verification_20261001.json) records the complete regression run after the directory reorganization, along with the diagram and entry-point checks.

## Run the checks

From the repository root, use Python 3.12 with Tk support, Node.js 24 and a C++14 compiler:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r verification/requirements.txt
python verification/tools/prepare_local.py
cd code/mobile_application
npm ci
cd ../..
python verification/run.py --output local-results/verification-01
```

Use a new output directory for every run. `summary.json` records the result of each stage; the accompanying logs contain the individual checks. New outputs go under the ignored `local-results/` directory.

For the local relay bench, install Mosquitto and run:

```sh
python verification/integration/run_phone_uav_gs_bench.py --output local-results/phone-relay-01
```

To verify the packaged files against their manifest:

```sh
python verification/tools/check_archive.py
```

The complete verification runs with synthetic configuration and loopback-only network access. Windows packaging, installed Android builds, ROS integration and physical flight have their own validation scopes. The [30 September validation report](reports/Android_Only_Validation_20260930.md) distinguishes those results.

[Return to the system overview](../README.md)
