#!/bin/sh

set -eu
MASS26_CODE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
MASS26_CPP_BRIDGE="$MASS26_CODE_DIR/search_uav/catkin_ws/src/rescue_bridge"
MASS26_CXX=${CXX:-c++}
set --
if [ "$(uname -s)" = Darwin ] && [ -z "${CXX:-}" ] && [ -x /Library/Developer/CommandLineTools/usr/bin/clang++ ]; then
    for UAV_SDK in /Library/Developer/CommandLineTools/SDKs/MacOSX.sdk /Library/Developer/CommandLineTools/SDKs/MacOSX*.sdk; do
        if [ -f "$UAV_SDK/usr/include/c++/v1/cmath" ]; then
            MASS26_CXX=/Library/Developer/CommandLineTools/usr/bin/clang++
            set -- -isysroot "$UAV_SDK" -isystem "$UAV_SDK/usr/include/c++/v1"
            break
        fi
    done
fi

MASS26_CPP_TMP=$(mktemp -d "${TMPDIR:-/tmp}/mass26-cpp-check.XXXXXX")
trap 'rm -rf "$MASS26_CPP_TMP"' EXIT HUP INT TERM
"$MASS26_CXX" "$@" -std=c++14 -Wall -Wextra -Werror \
    -I "$MASS26_CPP_BRIDGE/include" \
    "$MASS26_CPP_BRIDGE/test/test_target_lifecycle.cpp" \
    -o "$MASS26_CPP_TMP/test_target_lifecycle"
"$MASS26_CPP_TMP/test_target_lifecycle"
printf 'PASS: pure C++ target lifecycle compiled and ran; ROS node build is not covered.\n'
