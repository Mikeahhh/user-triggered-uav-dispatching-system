# Mountain Search UAV

A system that connects hikers' mobile records with operator-reviewed UAV search missions. This repository contains the mobile application, ground station, UAV software, three-mode MATLAB terrain simulation and experimental records.

Source revision: `UAV-SEARCH-20260928`.

## Contents

- `code/`: source code and tests for the three components.
- `simulation/`: MATLAB scripts, terrain data, trajectories, editable figures and animation.
- `experiments/`: local relay-test records and outdoor footage.
- [Technical guide (PDF)](docs/Technical_Guide.pdf), [Word](docs/Technical_Guide.docx) and [Markdown](docs/Technical_Guide.md): setup, system behavior, simulation settings and verification results.
- `records/`: verification logs, source provenance and file checksums.

Mode 1 estimates the trip end time from the planned route at 4 km/h. Mode 2 checks GPS update timeouts. Mode 3 requires operator contact verification and explicit confirmation before creating an SOS search event. After selecting an event, the operator reviews the generated route and dispatches the mission. The software retains its existing language options.

## Verification

Run these commands from the repository root:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-verification.txt
python scripts/prepare_local.py
cd code/FYP_alin1_SmartUAVRescueSystem_Mobile_APP-main
npm ci
cd ../..
python scripts/verify.py --output local-results/verification
```

In MATLAB, open `simulation/` and run `run_all('simulate')`. Use `run_all('paper')` to redraw the paper figure from saved data, or `run_all('video')` to create the animation.

Database, broker and onboard settings are described in the technical guide. The external flight stack uses a pinned commit; see [Flight environment setup](docs/Flight_Environment_Setup.txt).

Check file integrity with:

```sh
python scripts/check_archive.py
```

## Sources

The walking-speed reference is Ordnance Survey's *Map Reading* guide; see [Sources](docs/Sources.txt). Elevation data comes from Mapzen / Tilezen Skadi tile N22E114. Routes and GPS history are synthetic simulation inputs. Source licenses and copyright notices are retained in `LICENSES/`.
