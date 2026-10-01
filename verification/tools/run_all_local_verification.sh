#!/bin/sh

set -eu

MASS26_CODE_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../../code" && pwd)
MASS26_VERIFY_ROOT="$MASS26_CODE_ROOT/../verification"
MASS26_MOBILE="$MASS26_CODE_ROOT/mobile_application"
MASS26_GROUND="$MASS26_CODE_ROOT/ground_station"
MASS26_DRONE="$MASS26_CODE_ROOT/search_uav"
MASS26_BRIDGE="$MASS26_DRONE/catkin_ws/src/rescue_bridge/src"
MASS26_GROUND_PYTHON=${MASS26_GROUND_PYTHON:-python3}
MASS26_PYTHON=${MASS26_PYTHON:-python3}

if [ "${MASS26_LOCAL_VERIFICATION:-0}" != "1" ]; then
    exec "$MASS26_PYTHON" "$MASS26_VERIFY_ROOT/run.py" "$@"
fi

run_step() {
    MASS26_STEP_NAME=$1
    shift
    printf '\n[%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$MASS26_STEP_NAME"
    "$@"
}

run_in_dir() {
    MASS26_STEP_DIR=$1
    shift
    (
        cd "$MASS26_STEP_DIR"
        "$@"
    )
}

run_step "Mobile ESLint" run_in_dir "$MASS26_MOBILE" \
    ./node_modules/.bin/eslint . --max-warnings=0
run_step "Mobile TypeScript" run_in_dir "$MASS26_MOBILE" \
    ./node_modules/.bin/tsc --noEmit
run_step "Mobile release metadata alignment" run_in_dir "$MASS26_MOBILE" \
    node scripts/check_version_alignment.js
run_step "Mobile Jest" run_in_dir "$MASS26_MOBILE" \
    ./node_modules/.bin/jest --runInBand --no-cache
run_step "Mobile SQLite and Kotlin tracking contracts" run_in_dir "$MASS26_MOBILE" \
    "$MASS26_PYTHON" scripts/test_tracking_native.py

run_step "All first-party Python and packaging syntax" \
    "$MASS26_PYTHON" "$MASS26_VERIFY_ROOT/tools/verify_source_syntax.py"
run_step "Ground Station unit tests" run_in_dir "$MASS26_GROUND" \
    "$MASS26_GROUND_PYTHON" tests/run.py
run_step "Ground Station dependency lock" run_in_dir "$MASS26_GROUND" \
    "$MASS26_GROUND_PYTHON" packaging/verify_runtime_dependencies.py

run_step "UAV receiver tests" run_in_dir "$MASS26_DRONE/drone_system/receiver" \
    "$MASS26_PYTHON" -m unittest discover -s . -p 'test_*.py' -v
run_step "UAV bridge tests" run_in_dir "$MASS26_BRIDGE" \
    "$MASS26_PYTHON" -m unittest discover -s . -p 'test_*.py' -v
run_step "UAV recorder tests" run_in_dir "$MASS26_DRONE/drone_system/launcher" \
    "$MASS26_PYTHON" -m unittest discover -s . -p 'test_*.py' -v
run_step "Pure C++ target lifecycle compile and execution" \
    sh "$MASS26_VERIFY_ROOT/tools/run_cpp_core_verification.sh"

run_step "Cross-component release and protocol integration" run_in_dir \
    "$MASS26_VERIFY_ROOT/integration" \
    "$MASS26_GROUND_PYTHON" -m unittest discover -s . -p 'test_*.py' -v

printf '\nALL LOCAL SOFTWARE VERIFICATION STEPS PASSED\n'
