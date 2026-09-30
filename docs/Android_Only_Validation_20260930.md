# Android implementation and validation results, 30 September 2026

This report records Android implementation and verification results based on `13fcdc1e26548bb0f65d0a661cdab540ba1b2847`. Verification used the Mac, an isolated Android emulator, a local ROS Noetic container, synthetic inputs and loopback network services.

The requirement source is `main_MetaCom26_anonymous.tex`, SHA-256 `76676318e4e28e8f9c19bc1f037f24bc51dd2a7d05a996a320733ef903eca21f`. Implementation details are in [paper alignment](Paper_Alignment_20260930.md). Structured counts, source snapshot identifiers and test entry points are in the [Android JSON report](Android_Only_Validation_20260930.json); the [paper-alignment JSON report](Paper_Alignment_Validation_20260930.json) records its separate validation run.

## Changes

The iOS project, native Apple-platform resources, Gemfile, iOS launch command, iOS build-number metadata and application-specific iOS branches have been removed. The mobile application now uses the Android native recording implementation directly. The obsolete page-owned Quick Start implementation and its exclusive helpers/tests have also been removed. React Native and other shared dependencies remain where Android requires them; transitive third-party packages are not the repository's own iOS application.

The implementation includes these corrections:

1. **Future GPS timestamps.** The native sampler previously allowed a timestamp up to one second in the future, while the ground station rejected future samples. The native gate now requires capture time no later than the current time. Cross-component freshness tests compile and execute the current `TrackingCore.kt`, then feed accepted timestamps into the actual ground-station monitoring logic. They no longer exercise the deleted JavaScript sampler.
2. **Transfer capacity exhaustion.** Unknown task queries and invalid pre-manifest requests could create directories and consume the 64 incomplete-transfer slots. Unknown queries are now read-only. Allocation follows manifest validation and capacity checks; old binding-only directories are preserved without consuming valid-task capacity. Corrupt stored manifests remain intact and produce explicit recovery errors.
3. **Ambiguous completion evidence.** Malformed execution journals now safely withhold records. Delivery also requires an explicit non-aborted execution, so contradictory completed/aborted flags cannot authorize forwarding. Tests cover every Boolean combination, missing fields, non-Boolean values, malformed JSON and mismatched identities.
4. **Android SQLite initialization.** Actual Android emulator execution exposed a result-producing `PRAGMA busy_timeout` being issued through `execSQL`. The native store now uses and consumes a query cursor, including cleanup when initialization fails.
5. **Android database path aliases.** The native context and JavaScript SQLite connection can expose `/data/user/0/...` and `/data/data/...` for the same bind-mounted file. String equality incorrectly rejected that valid setup. The shared initializer now delegates file identity checking to Android's device/inode metadata, preserving rejection of genuinely different files.

## ROS build and runtime verification

The ROS Noetic/catkin build runs in a local ARM64 Linux container on the Mac. It builds the existing `rescue_bridge` sources and the original `quadrotor_msgs` and `cmake_utils` dependencies, generates message classes, compiles and links C++ nodes, and installs the Python executables and launch resources.

Runtime verification starts ROS and MQTT processes and drives the production bridge and commander with synthetic GPS and odometry. The container uses `--network none`, a read-only source mount and an unprivileged user. Public build dependencies are obtained during environment setup. Each run saves source snapshots, dependency provenance, image digests, build logs and scenario traces.

The [ROS runtime bench](../scripts/ros_local/ros_runtime_bench.py) passed eight scenarios and 57 assertions. The two-point route plus RTL produced three continuous hovers of 5.0963, 5.0867 and 5.2590 seconds. Separate cases verified vertical arrival, stale feedback, drift, mismatched GPS/odometry timestamps, invalid quaternion, missing GPS fix, and coordinate-frame failure followed by explicit recovery. Verification matched the compiled snapshot to all 37 ROS source files recorded for the run. The installed C++ executable is a real Linux ARM64 ELF binary.

## Verification results

| Verification | Result | Entry point |
| --- | --- | --- |
| Software regressions | 658 passed: Android Jest 185, ground station 265, receiver 55, mission bridge 69, recorder/console 22, demo 1 and cross-component contracts 61 | [Software runner](../scripts/verify.py) |
| Native and database contracts | 43 Kotlin and 28 SQLite checks passed; TypeScript, ESLint, metadata, Python syntax and dependency checks passed | [Native checks](../code/mobile_application/scripts/test_tracking_native.py) |
| Output protection and network isolation | 14 checks passed | [Verification checks](../scripts/) |
| Seeded protocol faults | 2,048 scenario groups across two seed ranges and six deterministic regressions passed | [Protocol runner](../scripts/verify_protocol_faults.py) |
| Execution invariants | 100 executions and 300 waypoints including RTL passed | [Execution tests](../code/search_uav/catkin_ws/src/rescue_bridge/src/test_execution_invariants.py) |
| C++ and ROS | Standalone C++ checks, ROS build/install and eight runtime scenarios with 57 assertions passed | [ROS verification](../scripts/ros_local/README.md) |
| Android build and runtime | Offline Gradle build, 19 emulator checks and independent integrity checks of 12 SQLite snapshots passed | [Android recording](../code/mobile_application/ANDROID_RECORDING.md) |
| Record relay | HTTP receipt, completion gating, MQTT transfer, durable ground storage and hash-bound acknowledgement passed | [Relay bench](../code/integration_tests/run_phone_uav_gs_bench.py) |
| Synthetic video | 16 checks and 23 encoded/decoded frames passed | [Video bench](../scripts/run_video_recording_bench.py) |
| Paper simulation | MATLAB 36 checks and Python 52 checks passed; 11 CSV outputs matched the saved numerical results byte for byte | [Simulation checks](../simulation/verify_outputs.py) |

The emulator ran the React Native application and native services from the recorded validation snapshot with synthetic GPS and a synthetic profile. Recording continued while the JavaScript debugger was paused (9 to 20 stored points), after navigation to Settings, while the Activity was backgrounded (27 to 29), and across actual Activity destruction/recreation (38 to 39). Force-stop correctly stopped acquisition (44 to 44), and reopening displayed the interrupted state and explicit Resume action. Resume retained the same session. Normal stop left 49 positions and 51 durable START/POINT/END operations; reopening preserved those values, with no active session or recording service. Pending synchronization errors were visible. No points were fabricated during the interruption.

The emulator and supporting processes used a loopback-only sandbox, a private ADB port and an explicit emulator target. The APK uses placeholder service configuration and the filename `COMPILE-ONLY-NO-LIVE-FIREBASE`. Its merged manifest retains the location foreground service and persistent synchronization job.

Seeded groups and additional checks are listed separately from the software-test total. MATLAB, video and ROS results have source-hash verification records. Preservation checks confirmed the original hashes of all 85 archived evidence files and the manuscript.

## Paper correspondence

| Paper rule | Implementation and verification |
| --- | --- |
| Three record modes with local timestamped GPS history and eventual upload | Android service-owned sampling and transactional persistent outbox; native Kotlin/SQLite, React and cross-component checks |
| Overdue booking, GPS timeout and SOS create notices before operator verification | Actual ground-station trigger and repository tests; source timestamps remain distinct from upload/processing time |
| Verified event, route review and explicit dispatch confirmation | Confirmation gates and seeded changes/cancellation/revocation cases cannot publish without valid authorization |
| Mode 1 planned order, Mode 2 chronological full history with repeated positions, Mode 3 expanding square | Deterministic route regressions, seeded list/map ordering tests and the paper's MATLAB study |
| Stage 3 begins only after UAV acceptance | Durable correlated admission, late/duplicate report handling and restart recovery; MQTT delivery alone cannot commit dispatch |
| Sequential arrival, configured hover, final RTL and landing request | Production execution-state tests, C++ geometry checks, continuous-feedback invariants and real ROS scenarios |
| Collected phone records forwarded only after full completion and normal landing request | Shared persistent gate, all completion/abort combinations, actual loopback relay and ground-store acknowledgement tests |
| Onboard video for human and post-flight review | Production recorder/console/journal with synthetic video encoding and decoding |
| 80 m AGL, 15/3 m/s speed limits, 5 s hover and at most 5 m simulation sampling | Unchanged MATLAB recomputation and independent numerical checks; terrain clearance is aircraft altitude minus terrain altitude |

## Reproduce the checks

Follow the [environment setup](../README.md#local-verification), then run from the repository root with a new output directory for each command:

```sh
python scripts/verify.py --output local-results/android-verification-01
python scripts/verify_protocol_faults.py --output local-results/protocol-a-01 --seed 20260930 --cases 256
python scripts/verify_protocol_faults.py --output local-results/protocol-b-01 --seed 8675309 --cases 256
python code/integration_tests/run_phone_uav_gs_bench.py --output local-results/relay-01
```

For Android compilation, use `npm run android:debug:safe` from `code/mobile_application`; see [Android recording](../code/mobile_application/ANDROID_RECORDING.md). For ROS, set `FLIGHT_WORKSPACE` to the Fast-Drone workspace containing `src/utils/quadrotor_msgs` and `src/utils/cmake_utils`, then run:

```sh
python scripts/run_ros_local_verification.py --output local-results/ros-01 --base-image ros@sha256:72b8bc59035dc0a5b8e07aae28c16caa84192971d72d207c72ed734fb1d5e97d --flight-source "$FLIGHT_WORKSPACE"
```

The [ROS guide](../scripts/ros_local/README.md) lists container setup and generated outputs. [Paper alignment](Paper_Alignment_20260930.md#reproduce-local-verification) includes simulation and video commands. These commands create local logs and structured results; the published summaries are the [Android JSON](Android_Only_Validation_20260930.json) and [paper-alignment JSON](Paper_Alignment_Validation_20260930.json).
