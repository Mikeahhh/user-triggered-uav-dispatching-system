# Mountain Search UAV

A system that connects hikers' mobile records with UAV search missions confirmed by an operator. It accompanies *A User-Triggered UAV Dispatching System for Precise and Fast Mountain Search Missions* and contains the mobile application, ground station, UAV software, MATLAB terrain simulation and experimental records.

The implementation baseline is `UAV-SEARCH-20260928`; Git history identifies subsequent changes. Application and wire-protocol versions are recorded separately in [source metadata](code/source_revision.json).

The mobile application supports Android. The [Android validation report](docs/Android_Only_Validation_20260930.md) records 658 software tests, 43 Kotlin checks and 28 SQLite checks, with emulator, ROS and simulation results listed separately.

## Demo videos

| Test component | Demo video |
| --- | --- |
| SOS search pattern and end-to-end pipeline | [DEMO — SOS Search Pattern  End to End Pipeline](https://www.youtube.com/watch?v=oQvX7AQdywA) |
| Onboard console and failsafe interface | [DEMO — Drone Console  On Board Failsafe GUI](https://www.youtube.com/watch?v=A7RMnk3LN9k) |
| Real-world test footage | [DEMO — Real-World Test Footage](https://www.youtube.com/watch?v=Zf9cXaNFMGM) |

Video links and related test materials are listed in [Demo videos and test materials](docs/Demo_Videos.md).

## System workflow

| Service mode | Mobile record | Notice for operator verification |
| --- | --- | --- |
| Mode 1: Event Booking | Planned route and departure time; end time estimated at 4 km/h | The trip exceeds its estimated end time |
| Mode 2: Quick Start | GPS positions and sample timestamps | The latest valid sample reaches the configured update timeout |
| Mode 3: SOS | SOS position and request time | A pending SOS request arrives |

For every mode, the operator contacts the user or emergency contact and explicitly confirms a search before an event is created. The operator then selects the event, prepares and reviews its route, and confirms dispatch. All modes start with the same event priority. SOS uploads go to this system's database and ground station; the separate call button opens the phone dialer. The application retains English and Chinese interface options.

## Start here

| Goal | Entry point | Requirements |
| --- | --- | --- |
| Understand setup and behavior | Technical guide: [Markdown](docs/Technical_Guide.md), [PDF](docs/Technical_Guide.pdf), [Word](docs/Technical_Guide.docx) | Document reader |
| Find implementation and supporting tests | [Implementation and evidence index](docs/Technical_Guide.md#9-implementation-and-evidence-index) | Source files and saved records |
| Run local software and saved-data checks | [scripts/verify.py](scripts/verify.py) | Python 3.12 with Tk, Node.js 24, C++14 compiler |
| Run the phone-record relay bench | [run_phone_uav_gs_bench.py](code/integration_tests/run_phone_uav_gs_bench.py) | Python dependencies and Mosquitto |
| Build and exercise the actual ROS mission package locally | [ROS verification](scripts/ros_local/README.md) | Local Linux container and archived message dependencies |
| Inspect or redraw the terrain figure | [simulation/](simulation/), `run_all('paper')` | MATLAB; saved inputs are included |
| Prepare the onboard environment | [Flight environment setup](docs/Flight_Environment_Setup.txt) | Ubuntu 20.04, ROS Noetic, external flight stack and configured hardware |

The components are [mobile_application](code/mobile_application/), [ground_station](code/ground_station/) and [search_uav](code/search_uav/). Cross-component tests are in [integration_tests](code/integration_tests/).

## Local verification

Use Python 3.12, Node.js 24 and a C++14 compiler. On Linux, the Python installation needs Tk support for ground-station imports. Run from the repository root:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-verification.txt
python scripts/prepare_local.py
cd code/mobile_application
npm ci
cd ../..
python scripts/verify.py --output local-results/verification-01
```

Choose a new output directory for each run. Read `summary.json`, the per-step logs and `simulation_checks.json` in that directory. The checks use synthetic inputs and mocked service connections. `prepare_local.py` creates an ignored Firebase placeholder only when the local file is absent.

To repeat the relay bench with a real local broker:

```sh
python code/integration_tests/run_phone_uav_gs_bench.py --output local-results/phone-relay-01
```

The bench starts a temporary loopback-only Mosquitto broker. It validates local HTTP upload, MQTT forwarding, persistence and acknowledgement with synthetic records. The actual local execution manager must complete every waypoint and persist a successful land request before forwarding. An early synchronization request or landing-status message alone cannot authorize forwarding.

Published summaries: [Android validation](docs/Android_Only_Validation_20260930.md), [Android JSON](docs/Android_Only_Validation_20260930.json), [paper alignment](docs/Paper_Alignment_20260930.md) and [paper-alignment JSON](docs/Paper_Alignment_Validation_20260930.json).

Test materials include the [30 September verification](records/verification_20260930/summary.json), [28 September verification](records/verification_summary.json), [phone-record relay records](experiments/phone_relay_20260928/), [outdoor footage and images](experiments/outdoor/) and [demo videos](docs/Demo_Videos.md).

## Simulation and deployment

In MATLAB, open `simulation/` and use `run_all('paper')` to redraw the paper figure from saved data into a new directory under `simulation/regenerated/`. The saved figure is [terrain_missions.png](simulation/paper_current/terrain_missions.png); its editable `.fig` is beside it. MATLAB R2025b was used for the archived simulation.

Simulation and video regeneration require a new explicit output directory; archived outputs are protected. See `simulation/run_all.m` for supported modes and output arguments. Video generation requires ffmpeg. New renderings use Event Booking, Quick Start and SOS; archived outputs retain their original provenance.

For live operation, configure the mobile Firebase file, ground-station service account, database URL, both timeout thresholds, the shared broker and UAV receiver token as described in the guide. Thresholds in the configuration template remain unset; the 1-second GPS interval is a test setting. The onboard stack is pinned to its source commit in the flight setup guide.

## Integrity and sources

Check file integrity with:

```sh
python scripts/check_archive.py
```

This command checks the packaged snapshot against its file manifest. Source changes are reported as checksum differences. Source snapshots and verification results are recorded in the [published JSON reports](docs/Android_Only_Validation_20260930.json); generated results are excluded from the archive manifest. Original repository commits and local input hashes are in [source provenance](records/source_provenance.json).

The walking-speed reference is Ordnance Survey's *Map Reading* guide; see [Sources](docs/Sources.txt). Elevation data comes from Mapzen / Tilezen Skadi tile N22E114. Routes and GPS history are synthetic simulation inputs. Component licenses and third-party notices remain in [LICENSES/](LICENSES/); the repository does not apply one replacement license to all components.
