#!/bin/sh

set -eu

MOBILE_BUILD_PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
MOBILE_BUILD_TMP=$(mktemp -d /tmp/fyp-mobile-android.XXXXXX)
MOBILE_BUILD_COPY="$MOBILE_BUILD_TMP/mobile"

cleanup_mobile_build() {
    case "$MOBILE_BUILD_TMP" in
        /tmp/fyp-mobile-android.*)
            rm -rf -- "$MOBILE_BUILD_TMP"
            ;;
    esac
}

trap cleanup_mobile_build EXIT HUP INT TERM

if [ -n "${MOBILE_BUILD_JAVA:-}" ]; then
    MOBILE_BUILD_JAVA_CMD=$MOBILE_BUILD_JAVA
elif [ -x "/Applications/Android Studio.app/Contents/jbr/Contents/Home/bin/java" ]; then
    MOBILE_BUILD_JAVA_CMD="/Applications/Android Studio.app/Contents/jbr/Contents/Home/bin/java"
else
    MOBILE_BUILD_JAVA_CMD=$(command -v java || true)
fi

MOBILE_BUILD_NODE_CMD=$(command -v node || true)
MOBILE_BUILD_KEYTOOL_CMD=$(dirname "$MOBILE_BUILD_JAVA_CMD")/keytool

if [ -n "${MOBILE_BUILD_ANDROID_SDK:-}" ]; then
    MOBILE_BUILD_SDK=$MOBILE_BUILD_ANDROID_SDK
elif [ -n "${ANDROID_HOME:-}" ]; then
    MOBILE_BUILD_SDK=$ANDROID_HOME
elif [ -n "${ANDROID_SDK_ROOT:-}" ]; then
    MOBILE_BUILD_SDK=$ANDROID_SDK_ROOT
else
    MOBILE_BUILD_SDK="/Users/$(id -un)/Library/Android/sdk"
fi

if [ ! -x "$MOBILE_BUILD_JAVA_CMD" ]; then
    echo "A Java 17+ executable was not found. Set MOBILE_BUILD_JAVA." >&2
    exit 1
fi
if [ ! -x "$MOBILE_BUILD_KEYTOOL_CMD" ]; then
    echo "keytool was not found beside the selected Java executable." >&2
    exit 1
fi
if [ ! -x "$MOBILE_BUILD_NODE_CMD" ]; then
    echo "Node.js was not found." >&2
    exit 1
fi
if [ ! -d "$MOBILE_BUILD_SDK" ]; then
    echo "Android SDK not found. Set MOBILE_BUILD_ANDROID_SDK." >&2
    exit 1
fi


rsync -a \
    --exclude '.git/' \
    --exclude '.bundle/' \
    --exclude '.npmrc' \
    --exclude '.yarnrc' \
    --exclude '.yarnrc.yml' \
    --exclude 'ios/' \
    --exclude 'docs/' \
    --exclude '__tests__/' \
    --exclude 'coverage/' \
    --exclude 'android/.gradle/' \
    --exclude 'android/build/' \
    --exclude 'android/app/build/' \
    --exclude 'android/local.properties' \
    --exclude 'android/gradle.properties' \
    --exclude 'android/app/src/main/AndroidManifest.xml' \
    --exclude 'services/db/firebaseConfig.ts' \
    --exclude 'google-services.json' \
    --exclude '*.jks' \
    --exclude '*.keystore' \
    --exclude '*.env' \
    --exclude '*.env.*' \
    --exclude '*.p12' \
    --exclude '*.p8' \
    --exclude '*.pfx' \
    --exclude '*.pem' \
    --exclude '*.key' \
    --exclude '*.cer' \
    --exclude '*.mobileprovision' \
    --exclude 'GoogleService-Info.plist' \
    --exclude 'serviceAccountKey.json' \
    --exclude '*credential*.json' \
    --exclude '*credentials*.json' \
    --exclude '*.db' \
    --exclude '*.sqlite' \
    --exclude '*.sqlite3' \
    --exclude '*.apk' \
    --exclude '*.aab' \
    "$MOBILE_BUILD_PROJECT_ROOT/" \
    "$MOBILE_BUILD_COPY/"


MOBILE_BUILD_PROHIBITED=$(find "$MOBILE_BUILD_COPY" -type f \( \
    -name '*.jks' -o -name '*.keystore' -o -name '*.env' -o -name '*.env.*' -o \
    -name 'google-services.json' -o -name 'GoogleService-Info.plist' -o \
    -name 'serviceAccountKey.json' -o -name '*credential*.json' -o \
    -name '*credentials*.json' -o -name '*.p12' -o -name '*.p8' -o \
    -name '*.pfx' -o -name '*.pem' -o -name '*.key' -o -name '*.cer' -o \
    -name '*.mobileprovision' -o -name '*.db' -o \
    -name '*.sqlite' -o -name '*.sqlite3' \
\) -print -quit)
if [ -n "$MOBILE_BUILD_PROHIBITED" ]; then
    echo "Refusing compile-only build: prohibited file reached temporary copy." >&2
    exit 1
fi
if [ -d "$MOBILE_BUILD_COPY/ios" ]; then
    echo "Refusing compile-only build: iOS tree reached temporary copy." >&2
    exit 1
fi

install -m 600 \
    "$MOBILE_BUILD_COPY/scripts/google-services.compile-only.json" \
    "$MOBILE_BUILD_COPY/android/app/google-services.json"
install -m 600 \
    "$MOBILE_BUILD_COPY/scripts/AndroidManifest.compile-only.xml" \
    "$MOBILE_BUILD_COPY/android/app/src/main/AndroidManifest.xml"
install -m 600 \
    "$MOBILE_BUILD_COPY/scripts/gradle.compile-only.properties" \
    "$MOBILE_BUILD_COPY/android/gradle.properties"
install -m 600 \
    "$MOBILE_BUILD_COPY/scripts/firebaseConfig.compile-only.ts" \
    "$MOBILE_BUILD_COPY/services/db/firebaseConfig.ts"


"$MOBILE_BUILD_NODE_CMD" \
    "$MOBILE_BUILD_COPY/scripts/sanitize_compile_only_copy.js" \
    "$MOBILE_BUILD_COPY"


"$MOBILE_BUILD_KEYTOOL_CMD" -genkeypair \
    -keystore "$MOBILE_BUILD_COPY/android/app/debug.keystore" \
    -storepass android \
    -alias androiddebugkey \
    -keypass android \
    -dname "CN=Compile Only,O=Trigger Search,C=HK" \
    -keyalg RSA \
    -keysize 2048 \
    -validity 30 \
    -noprompt >/dev/null 2>&1

if [ "${MOBILE_BUILD_PREPARE_ONLY:-0}" = "1" ]; then
    echo "Compile-only temporary copy prepared and sanitized successfully."
    exit 0
fi

cd "$MOBILE_BUILD_COPY/android"

ANDROID_HOME="$MOBILE_BUILD_SDK" "$MOBILE_BUILD_JAVA_CMD" \
    -jar gradle/wrapper/gradle-wrapper.jar \
    :react-native-worklets:externalNativeBuildDebug


MOBILE_BUILD_FOUND_WORKLETS=false
for MOBILE_BUILD_WORKLETS_LIB in \
    "$MOBILE_BUILD_COPY"/node_modules/react-native-worklets/android/build/intermediates/cxx/Debug/*/obj/*/libworklets.so
do
    if [ ! -f "$MOBILE_BUILD_WORKLETS_LIB" ]; then
        continue
    fi
    MOBILE_BUILD_ABI=$(basename "$(dirname "$MOBILE_BUILD_WORKLETS_LIB")")
    MOBILE_BUILD_WORKLETS_TARGET="$MOBILE_BUILD_COPY/node_modules/react-native-worklets/android/build/intermediates/cmake/debug/obj/$MOBILE_BUILD_ABI"
    mkdir -p "$MOBILE_BUILD_WORKLETS_TARGET"
    cp -p "$MOBILE_BUILD_WORKLETS_LIB" "$MOBILE_BUILD_WORKLETS_TARGET/libworklets.so"
    MOBILE_BUILD_FOUND_WORKLETS=true
done

if [ "$MOBILE_BUILD_FOUND_WORKLETS" != true ]; then
    echo "Worklets native libraries were not generated." >&2
    exit 1
fi

ANDROID_HOME="$MOBILE_BUILD_SDK" "$MOBILE_BUILD_JAVA_CMD" \
    -jar gradle/wrapper/gradle-wrapper.jar \
    assembleDebug

MOBILE_BUILD_APK_SOURCE="$MOBILE_BUILD_COPY/android/app/build/outputs/apk/debug/app-debug.apk"
MOBILE_BUILD_OUTPUT_DIR="$MOBILE_BUILD_PROJECT_ROOT/android/app/build/outputs/apk/compile-only"
MOBILE_BUILD_APK_TARGET="$MOBILE_BUILD_OUTPUT_DIR/app-debug-COMPILE-ONLY-NO-LIVE-FIREBASE.apk"
MOBILE_BUILD_HASH_TARGET="$MOBILE_BUILD_APK_TARGET.sha256"
MOBILE_BUILD_NOTICE="$MOBILE_BUILD_OUTPUT_DIR/README-COMPILE-ONLY.txt"

mkdir -p "$MOBILE_BUILD_OUTPUT_DIR"
cp -p "$MOBILE_BUILD_APK_SOURCE" "$MOBILE_BUILD_APK_TARGET"
printf '%s\n' \
    'COMPILE-ONLY ARTIFACT' \
    'This APK uses placeholder Google/Firebase configuration.' \
    'All source Firebase REST URLs were replaced in the temporary build copy.' \
    'This debug artifact may require Metro; no production JS bundle is embedded.' \
    'It is not a live Firebase, field-flight, or production artifact.' \
    'Verify the APK with the adjacent .sha256 file.' \
    > "$MOBILE_BUILD_NOTICE"

echo "Compile-only debug APK: $MOBILE_BUILD_APK_TARGET"
echo "WARNING: not usable for live Firebase or field-flight evidence."
MOBILE_BUILD_APK_HASH=$(shasum -a 256 "$MOBILE_BUILD_APK_TARGET" | awk '{print $1}')
printf '%s  %s\n' \
    "$MOBILE_BUILD_APK_HASH" \
    "$(basename "$MOBILE_BUILD_APK_TARGET")" \
    > "$MOBILE_BUILD_HASH_TARGET"
echo "$MOBILE_BUILD_APK_HASH  $MOBILE_BUILD_APK_TARGET"
