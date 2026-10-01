# Paper alignment: implementation and test results, 30 September 2026

This report records the paper-alignment implementation and validation run. Android release, emulator and ROS results are listed in [Android validation](Android_Only_Validation_20260930.md) and its [JSON report](Android_Only_Validation_20260930.json).

The implementation is based on Git commit `13fcdc1e26548bb0f65d0a661cdab540ba1b2847`. Result counts, source snapshot hashes and reproduction commands are recorded in the [paper-alignment JSON report](Paper_Alignment_Validation_20260930.json).

The requirement source is `main_MetaCom26_anonymous.tex`, SHA-256 `76676318e4e28e8f9c19bc1f037f24bc51dd2a7d05a996a320733ef903eca21f`. The implementation and checks cover the Android mobile application, ground station, onboard software and mission simulation.

## Requirement and implementation map

| Paper requirement | Current behavior and regression evidence |
| --- | --- |
| Continuous timestamped GPS history during a hike; local SQLite before cloud synchronization (lines 112, 116, 175) | Android foreground location service owns the session independently of React pages. SQLite saves each accepted source-timestamped fix and its outbox entry in one transaction. Network I/O uses separate workers. `AndroidQuickStartPage.test.tsx`, native tracking checks and tracking bridge tests cover the local contracts. |
| All three kinds of records upload when connectivity is available | Persistent record queues retain booking/SOS operations and trace START/POINT/END operations. Stable identities, conditional writes and ordered per-record retries prevent missing points and destructive replay. Independent queue groups are served fairly. |
| Booking deadline, latest valid GPS timeout or SOS produces a notice (line 119) | Existing trigger calculations and boundary tests remain; delayed uploads retain sampling time. GPS freshness is not measured from the retry/upload time. |
| Operator verification before a search event; explicit route review and dispatch confirmation (lines 121, 127, 135) | The existing notice/event/active-event gates remain. Selection or SOS receipt alone cannot dispatch. Ground-station workflow and cross-component tests exercise each gate. |
| Undispatched events remain pending; UAV acceptance starts execution (line 135) | Broker acknowledgement leaves the event PENDING. Only correlated durable ADMISSION acceptance commits DISPATCHED. Busy/rejected/unknown cases stay visible. Old broker-only success records require reconciliation. |
| Mode 1 planned order; Mode 2 chronological complete history with repeated positions; Mode 3 expanding square (lines 129–132) | Route rules remain. All route validators and target index checks support 100,000 source points plus one RTL point. Chunking preserves the complete ordered task and does not create multiple flights. |
| Sequential navigation, configured hover and final RTL/land request (lines 142–144) | Three-dimensional tolerance, fresh feedback and a continuous dwell interval are required. Drift, stale observations and replayed sequence numbers cannot complete the dwell. Final completion and land publication are separately persisted. |
| Record forwarding after every waypoint is completed and landing has been requested (line 156) | V1/V2 initial delivery, retry and GS SYNC read the same durable execution completion evidence. Legacy land messages, saved envelopes and reconnects cannot grant eligibility. Ground storage acknowledgement ends retries. |
| Downward video for human review (line 159) | Video completion requires a successful recorder exit, positive frame count and a nonempty output file. Disk write/sync/finalization failure and abrupt process exit remain explicit failures. Mission and execution identities prevent cross-session recording control. |
| Terrain simulation and reported parameters (lines 203–226) | Existing parameters and scenario are retained. New checks evaluate aircraft altitude minus interpolated terrain altitude on sampled cruise/return paths, independently of land elevation filtering; takeoff/landing contact is excluded. New outputs are isolated. |

The earlier findings F01–F07 are addressed respectively by admission acknowledgement, native recording lifetime, persistent point retry, independent sampling/network workers, persistent END operations, complete-route transfer and unified delivery eligibility. Additional regression work covers database migration rollback, coordinate-frame conversion, continuous hover and recording completion.

## Android operation and recovery

The Android implementation uses the existing fused-location dependency, Android SQLite and HTTPS REST. The user starts recording while the app is in the foreground with location permission. The service exposes a persistent notification and a Stop action; leaving the page does not stop it. The requested sampling interval is five seconds. Each fix is validated against its coordinates, capture time and current session before persistence.

The database remains `location_tracker.db`. The additive schema migration is transactional, checks actual existing columns and commits the version last. Old routes without a trustworthy user/session binding remain `LEGACY_UNBOUND`; they are not uploaded under the currently logged-in user. New sessions freeze the user and database destination at creation.

`PersistentTracking` exposes initialization, start/resume/stop, snapshot, retry, complete-history pagination, local record listing/refresh and queued booking/SOS writes. A transient STARTING state represents waiting for the first valid GPS fix. ACTIVE, STOPPED and INTERRUPTED describe recording; pending/error/synchronized information is separate. A stopped session can still have pending END synchronization. If the operating system kills or force-stops recording, the gap remains visible and requires explicit resumption when automatic restart is unavailable. No samples are fabricated.

Each stream orders START before its POINT operations and END after all earlier points. Every retry uses the original sample time and stable key. Session and point creation use conditional writes; conflicting contents remain unresolved errors. REST calls have bounded timeouts. UI snapshots and Stop do not wait for network completion. The map may show a recent window; full retained points are available through the paginated interface.

Booking deletion is a persisted revisioned tombstone. A delayed earlier write cannot recreate a deleted booking, and stale refresh responses cannot overwrite a newer local revision. The ground station excludes tombstones from notices, route preparation and pending transfer recovery, including when a previously prepared source is deleted during operator confirmation.

## Ground-station and UAV contract

The task content schema stays at version 2. Transport version 1 uses `MANIFEST`, `CHUNK`, `COMMIT` and `QUERY` on `alin1/mission/transfer` by default. Each chunk contains at most 256 points, each message at most 64 KiB, each task at most 100,000 source points and at most 16 MiB of canonical content. These are engineering resource bounds. Input above either bound is rejected explicitly without truncation.

The UAV atomically stores chunks, verifies completeness and the full content fingerprint, then admits a single execution. It persists the complete task separately from the small changing execution journal. A missing or conflicting chunk cannot publish a first waypoint. Queries report missing chunks and known admission/execution state. Legacy short messages remain readable; new dispatch does not fall back to broker-only success.

Status reports distinguish `ADMISSION`, `TRANSFER`, `EXECUTION` and protocol errors. Admission evidence includes mission/execution identity, fingerprint, attempt, acceptance decision and a persisted decision sequence. Execution snapshots include a persisted state revision. The ground station ignores unrelated or stale reports. A confirmed rejection permits an operator to retry the same task with a higher attempt; a 15-second acceptance timeout becomes UNKNOWN and requires querying the saved execution. Acceptance is monotonic. A rejected first target remains an accepted mission with an execution fault; the dedicated operator retry keeps its target index and identity.

Old local/cloud dispatch states retain their original status details when migrated to reconciliation. A broker acknowledgement is not reconstructed as UAV acceptance. Onboard restart requires recovery and never automatically replays the flight queue.

Frame alignment uses paired GPS east/north displacement and local odometry displacement, with at least a two-metre baseline in both frames and source timestamps no more than 0.25 seconds apart. Target projection also requires a time-matched, fresh position pair. Unavailable alignment rejects a target. A coordinate-frame change is tracked independently of observation validity and latches an existing target invalid. It reports `COORDINATE_FRAME_CHANGED` and moves the execution to `RECOVERY_REQUIRED`; recalibration cannot resume the old target, and an operator must reconcile and release the execution. The configured horizontal tolerance defaults to 1 metre and vertical tolerance to 1 metre. Position feedback defaults to 5 Hz; the configurable maximum hold-feedback gap defaults to 1 second. The feedback period must be shorter than the permitted gap. Flight-altitude settings remain separate from the 80-metre simulation setting.

Record delivery requires matching mission/execution identity and fingerprint, `all_waypoints_completed=true`, `land_command_requested=true` and `delivery_eligible=true` in the execution journal. Normal final landing publication sets eligibility; abort does not. The journal records LAND command publication. V1 carrier bindings are sidecars that preserve the original payload and hash. V2 contexts retain the carrier fingerprint. Missing or ambiguous legacy bindings remain withheld; receiver health reports pending/withheld/unbound counts. Completion evidence survives bridge exit or restart.

## Reproduce local verification

From the repository root, with the existing Python environment and Node runtime on PATH:

```sh
python scripts/verify.py --output local-results/verification-01
```

The entry point uses loopback-only Python/Node networking and synthetic service configuration and inputs. It runs mobile lint/types/Jest, SQLite/Kotlin contracts, Python suites, a compiled C++ core, cross-component tests and saved simulation validation. Android compilation uses Gradle offline mode, cached dependencies and the production manifest/service declarations.

New MATLAB outputs use a new explicit output directory. The independent checker may be pointed at them with `--simulation-dir` on `scripts/verify.py`. Archived output paths and existing result directories are protected against accidental replacement. The local relay bench uses an isolated loopback Mosquitto process, a synthetic HTTP client and the actual execution manager; it verifies that early SYNC sends nothing and that completed execution permits durable forwarding and acknowledgement.

Result summaries, source snapshot hashes and reproduction commands are listed in the [JSON report](Paper_Alignment_Validation_20260930.json).

## Verification results

The paper-alignment run completed at 12:53:40 UTC on 30 September 2026. All steps passed: software verification (selected archived counts: 244 mobile, 255 ground station, 51 receiver, 68 bridge, 22 recorder/console and 61 integration), 42 Kotlin checks, 28 SQLite checks, 14 output-protection/network-isolation/clearance tests, TypeScript, ESLint, dependency and syntax checks, and the compiled C++ core. Long-route tests include 1,000, 1,001, 17,281 and 100,000 source points, complete ordered reconstruction with one extra RTL point, and explicit overflow rejection.

| Item | Final status | Evidence |
| --- | --- | --- |
| F01: broker receipt incorrectly treated as UAV admission | Fixed; local tests passed | Ground-station, bridge and integration suites; correlated admission, rejection, unknown-state recovery and late reports |
| F02: recording ends with page lifetime | Fixed; local contracts and offline build passed | Android page/service separation tests and packaged service declarations |
| F03: missing retry of individual history points | Fixed; local tests passed | Stable point keys, ordered persistent outbox, conditional write conflicts and queue fairness checks |
| F04: network waits block local collection | Fixed; local contracts passed | Separate sampling/control/synchronization workers and local-first persistence checks |
| F05: recording end state can be lost | Fixed; local tests passed | Durable END, separate synchronization state and interruption/resumption checks |
| F06: long routes are inconsistently limited | Fixed; local tests passed | Ground route generation, manifest/chunk/commit persistence, complete ordered reconstruction and C++ index bounds |
| F07: retry/SYNC can forward collected records early | Fixed; local tests and loopback record-transfer bench passed | Shared persistent completion gate, failed landing publication, abort, restart and acknowledgement regressions |
| Migration, GPS/local-frame conversion and continuous hold | Fixed; local checks passed | SQLite rollback and legacy preservation; paired-frame C++ tests; latched frame recovery; vertical/stale/drift/continuous-dwell tests |
| Video failure reporting and task association | Fixed; synthetic recorder tests and actual file encoding passed | File existence/size, frame count, exit status, finalization faults and execution identity; 23 production-encoded frames decoded by OpenCV and counted independently by ffprobe |
| Simulation settings, route order and terrain clearance | Local recomputation and independent checks passed | 36 MATLAB assertions, 52 Python checks and 11 CSV files byte-identical to archived results |

Android compilation used the recorded source snapshot with the actual service manifest, Gradle offline mode and placeholder cloud configuration. Its APK checksum and binary-manifest results are recorded in the [JSON report](Paper_Alignment_Validation_20260930.json). The build entry point is [build_android_debug_safe.sh](../code/mobile_application/scripts/build_android_debug_safe.sh).

The [relay bench](../code/integration_tests/run_phone_uav_gs_bench.py) verifies HTTP receipt, completion-gated forwarding, durable ground storage and acknowledgement. [MATLAB simulation](../simulation/run_all.m) and the [independent checker](../simulation/verify_outputs.py) provide the numerical checks. Preservation checks confirmed the original hashes of all 85 evidence files and the nominated manuscript.

The separate video bench passed 16 checks using a synthetic local video, the actual production recorder, real OpenCV encoding, production console event methods and `RecordingJournal`. Its 23 recorded frames decode successfully and preserve the expected mission/execution binding. The [video bench](../scripts/run_video_recording_bench.py) writes output files, per-file hashes, source hashes and input-validation results to its selected directory. The repository-local optional test environment uses `opencv-python-headless==4.12.0.88` and `numpy==2.2.6`; it does not alter the system Python environment.

To repeat the video bench from the repository root:

```sh
python -m venv local-results/.video-venv
local-results/.video-venv/bin/python -m pip install -r scripts/requirements-video-verification.txt
local-results/.video-venv/bin/python scripts/run_video_recording_bench.py --output local-results/video-01
```

Dependency setup may download the pinned packages. The bench itself re-executes under the loopback network guard and only permits the explicitly generated local video file; camera indices, device paths and URLs are rejected. It requires the existing `ffprobe` executable for the independent frame-count check.

To recompute the terrain scenario, open `simulation/` in MATLAB and run `run_all('simulate', fullfile(pwd, '..', 'local-results', 'simulation-01'))`. Verify those outputs with `python scripts/verify.py --output local-results/simulation-verification-01 --simulation-dir local-results/simulation-01`. Commands write logs and structured results into their selected local output directories.
