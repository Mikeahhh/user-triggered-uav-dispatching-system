# Local ROS Noetic verification

This bench builds the repository's actual `rescue_bridge` package, generated ROS messages, C++ `mission_commander`, installed Python nodes, and the original `quadrotor_msgs` and `cmake_utils` dependencies. It uses the existing OrbStack daemon through an explicit local Unix socket.

The base image is the official ARM64-capable ROS Noetic image, pinned to `ros@sha256:72b8bc59035dc0a5b8e07aae28c16caa84192971d72d207c72ed734fb1d5e97d`. Public package downloads occur while preparing the image. The actual catkin build and runtime container use `--network none`, no published ports, no device mappings, an unprivileged user, dropped capabilities and a read-only root filesystem. The only writable host mount is a new result directory. Source dependencies are copied into that directory, and their original paths and SHA-256 hashes are retained.

From the repository root, first obtain the image through the inspected local daemon:

```sh
docker --host "unix://$HOME/.orbstack/run/docker.sock" pull ros@sha256:72b8bc59035dc0a5b8e07aae28c16caa84192971d72d207c72ed734fb1d5e97d
```

Set `FLIGHT_WORKSPACE` to the Fast-Drone workspace, then run with a new output directory:

```sh
.venv/bin/python verification/tools/run_ros_local_verification.py \
  --output local-results/a-new-ros-verification-directory \
  --base-image ros@sha256:72b8bc59035dc0a5b8e07aae28c16caa84192971d72d207c72ed734fb1d5e97d \
  --flight-source "$FLIGHT_WORKSPACE"
```

The input source must contain `src/utils/quadrotor_msgs` and `src/utils/cmake_utils`. The runner preserves those original directories. Both `catkin_make` and `catkin_make install` must succeed before runtime checks begin. Runtime nodes are launched from the installed package tree.

The runtime checks use real roscore, ROS TCPROS/XMLRPC transport, real generated messages and a loopback Mosquitto broker. Synthetic GPS and odometry are published at 20 Hz. A bounded kinematic position response follows the published goal. The commander uses a 5 m local-frame target altitude, 1 m horizontal and vertical tolerances, 5 Hz feedback and a 1-second feedback timeout. These settings do not copy the separate 80 m AGL MATLAB scenario into flight configuration.

Scenarios cover two source points plus RTL with three five-second continuous holds; horizontal-only arrival with incorrect altitude; stale odometry; drift during a hold; GPS/odometry timestamps separated by 0.5 seconds; invalid quaternion; GPS without a fix; and an invalid odometry message followed by a changed coordinate frame, recalibration, abort, explicit release and a new execution.

The output contains package/source provenance, image digests, build/install logs, installed dependency versions, isolation evidence, per-scenario sensor and message traces, persistent execution journals and structured results. The historical output directory is never reused.

The checks cover package compilation and installation, generated message types, sequential targets, continuous hover, sensor freshness, frame alignment and explicit recovery. The output directory contains `result.json`, `runtime/result.json`, `source-provenance.json`, build/install logs and scenario traces. The [Android validation report](../../reports/Android_Only_Validation_20260930.md#ros-build-and-runtime-verification) and [JSON summary](../../reports/Android_Only_Validation_20260930.json) record the completed run.
