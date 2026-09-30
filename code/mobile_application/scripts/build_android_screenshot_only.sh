#!/bin/sh

set -eu

MOBILE_DEMO_PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
MOBILE_DEMO_TMP=$(mktemp -d /tmp/mass26-mobile-demo.XXXXXX)
MOBILE_DEMO_COPY="$MOBILE_DEMO_TMP/mobile"
MOBILE_DEMO_DEFAULT_OUTPUT="$MOBILE_DEMO_PROJECT_ROOT/../../local-results/mobile-screenshot"
MOBILE_DEMO_OUTPUT=${MOBILE_SCREENSHOT_OUTPUT_DIR:-$MOBILE_DEMO_DEFAULT_OUTPUT}

cleanup_mobile_demo() {
    case "$MOBILE_DEMO_TMP" in
        /tmp/mass26-mobile-demo.*) rm -rf -- "$MOBILE_DEMO_TMP" ;;
    esac
}
trap cleanup_mobile_demo EXIT HUP INT TERM

if [ -n "${MOBILE_BUILD_JAVA:-}" ]; then
    MOBILE_DEMO_JAVA=$MOBILE_BUILD_JAVA
elif [ -x "/Applications/Android Studio.app/Contents/jbr/Contents/Home/bin/java" ]; then
    MOBILE_DEMO_JAVA="/Applications/Android Studio.app/Contents/jbr/Contents/Home/bin/java"
else
    MOBILE_DEMO_JAVA=$(command -v java || true)
fi
MOBILE_DEMO_KEYTOOL=$(dirname "$MOBILE_DEMO_JAVA")/keytool
MOBILE_DEMO_NODE=$(command -v node || true)

if [ -n "${MOBILE_BUILD_ANDROID_SDK:-}" ]; then
    MOBILE_DEMO_SDK=$MOBILE_BUILD_ANDROID_SDK
elif [ -n "${ANDROID_HOME:-}" ]; then
    MOBILE_DEMO_SDK=$ANDROID_HOME
elif [ -n "${ANDROID_SDK_ROOT:-}" ]; then
    MOBILE_DEMO_SDK=$ANDROID_SDK_ROOT
else
    MOBILE_DEMO_SDK="/Users/$(id -un)/Library/Android/sdk"
fi

test -x "$MOBILE_DEMO_JAVA" || { echo "Java 17+ was not found." >&2; exit 1; }
test -x "$MOBILE_DEMO_KEYTOOL" || { echo "keytool was not found." >&2; exit 1; }
test -x "$MOBILE_DEMO_NODE" || { echo "Node.js was not found." >&2; exit 1; }
test -d "$MOBILE_DEMO_SDK" || { echo "Android SDK was not found." >&2; exit 1; }


rsync -a \
    --exclude '.git/' \
    --exclude '.bundle/' \
    --exclude '.npmrc' \
    --exclude '.yarnrc' \
    --exclude '.yarnrc.yml' \
    --exclude 'docs/' \
    --exclude '__tests__/' \
    --exclude 'coverage/' \
    --exclude 'android/.gradle/' \
    --exclude 'android/build/' \
    --exclude 'android/app/build/' \
    --exclude 'android/local.properties' \
    --exclude 'android/gradle.properties' \
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
    "$MOBILE_DEMO_PROJECT_ROOT/" \
    "$MOBILE_DEMO_COPY/"

MOBILE_DEMO_PROHIBITED=$(find "$MOBILE_DEMO_COPY" -type f \( \
    -name '*.jks' -o -name '*.keystore' -o -name '*.env' -o -name '*.env.*' -o \
    -name 'google-services.json' -o -name 'GoogleService-Info.plist' -o \
    -name 'serviceAccountKey.json' -o -name '*credential*.json' -o \
    -name '*credentials*.json' -o -name '*.p12' -o -name '*.p8' -o \
    -name '*.pfx' -o -name '*.pem' -o -name '*.key' -o -name '*.cer' -o \
    -name '*.mobileprovision' -o -name '*.db' -o -name '*.sqlite' -o \
    -name '*.sqlite3' \
\) -print -quit)
test -z "$MOBILE_DEMO_PROHIBITED" || {
    echo "Refusing screenshot build: prohibited file reached temporary copy." >&2
    exit 1
}

install -m 600 \
    "$MOBILE_DEMO_COPY/scripts/google-services.screenshot-only.json" \
    "$MOBILE_DEMO_COPY/android/app/google-services.json"
install -m 600 \
    "$MOBILE_DEMO_COPY/scripts/gradle.screenshot-only.properties" \
    "$MOBILE_DEMO_COPY/android/gradle.properties"
install -m 600 \
    "$MOBILE_DEMO_COPY/scripts/firebaseConfig.compile-only.ts" \
    "$MOBILE_DEMO_COPY/services/db/firebaseConfig.ts"
install -m 600 \
    "$MOBILE_DEMO_COPY/scripts/buildMode.screenshot-only.ts" \
    "$MOBILE_DEMO_COPY/services/buildMode.ts"

"$MOBILE_DEMO_NODE" \
    "$MOBILE_DEMO_COPY/scripts/sanitize_compile_only_copy.js" \
    "$MOBILE_DEMO_COPY"

grep -q 'SCREENSHOT_DEMO = true' "$MOBILE_DEMO_COPY/services/buildMode.ts"
grep -q 'LOCAL DEMO — NO FIREBASE / NO UAV' "$MOBILE_DEMO_COPY/services/buildMode.ts"

"$MOBILE_DEMO_KEYTOOL" -genkeypair \
    -keystore "$MOBILE_DEMO_COPY/android/app/debug.keystore" \
    -storepass android \
    -alias androiddebugkey \
    -keypass android \
    -dname "CN=MASS26 Screenshot Only,O=Local Demo,C=HK" \
    -keyalg RSA \
    -keysize 2048 \
    -validity 30 \
    -noprompt >/dev/null 2>&1

if [ "${MOBILE_SCREENSHOT_PREPARE_ONLY:-0}" = "1" ]; then
    echo "Screenshot-only temporary copy prepared and sanitized successfully."
    exit 0
fi

cd "$MOBILE_DEMO_COPY/android"


ANDROID_HOME="$MOBILE_DEMO_SDK" "$MOBILE_DEMO_JAVA" \
    -jar gradle/wrapper/gradle-wrapper.jar --offline \
    generateCodegenArtifactsFromSchema

ANDROID_HOME="$MOBILE_DEMO_SDK" "$MOBILE_DEMO_JAVA" \
    -jar gradle/wrapper/gradle-wrapper.jar --offline \
    :react-native-worklets:externalNativeBuildRelease

MOBILE_DEMO_FOUND_WORKLETS=false
for MOBILE_DEMO_WORKLETS_LIB in \
    "$MOBILE_DEMO_COPY"/node_modules/react-native-worklets/android/build/intermediates/cxx/RelWithDebInfo/*/obj/*/libworklets.so
do
    [ -f "$MOBILE_DEMO_WORKLETS_LIB" ] || continue
    MOBILE_DEMO_ABI=$(basename "$(dirname "$MOBILE_DEMO_WORKLETS_LIB")")
    MOBILE_DEMO_WORKLETS_TARGET="$MOBILE_DEMO_COPY/node_modules/react-native-worklets/android/build/intermediates/cmake/release/obj/$MOBILE_DEMO_ABI"
    mkdir -p "$MOBILE_DEMO_WORKLETS_TARGET"
    cp -p "$MOBILE_DEMO_WORKLETS_LIB" "$MOBILE_DEMO_WORKLETS_TARGET/libworklets.so"
    MOBILE_DEMO_FOUND_WORKLETS=true
done
test "$MOBILE_DEMO_FOUND_WORKLETS" = true || {
    echo "Screenshot Worklets native libraries were not generated." >&2
    exit 1
}

ANDROID_HOME="$MOBILE_DEMO_SDK" "$MOBILE_DEMO_JAVA" \
    -jar gradle/wrapper/gradle-wrapper.jar --offline \
    assembleScreenshot

MOBILE_DEMO_APK_SOURCE="$MOBILE_DEMO_COPY/android/app/build/outputs/apk/screenshot/app-screenshot.apk"
test -f "$MOBILE_DEMO_APK_SOURCE"
unzip -l "$MOBILE_DEMO_APK_SOURCE" | grep -q 'assets/index.android.bundle'

MOBILE_DEMO_AAPT=$(find "$MOBILE_DEMO_SDK/build-tools" -type f -name aapt -perm -111 | sort -V | tail -n 1)
test -n "$MOBILE_DEMO_AAPT"
MOBILE_DEMO_PERMISSIONS=$($MOBILE_DEMO_AAPT dump permissions "$MOBILE_DEMO_APK_SOURCE")
if printf '%s\n' "$MOBILE_DEMO_PERMISSIONS" | grep -Eq \
    'android.permission.(INTERNET|ACCESS_NETWORK_STATE|CHANGE_NETWORK_STATE|ACCESS_WIFI_STATE|CHANGE_WIFI_STATE|NEARBY_WIFI_DEVICES)'
then
    echo "Refusing screenshot APK: a network permission remains." >&2
    exit 1
fi

mkdir -p "$MOBILE_DEMO_OUTPUT"
MOBILE_DEMO_APK_TARGET="$MOBILE_DEMO_OUTPUT/MASS26_Mobile_Local_Demo_1.2.0_screenshot-only.apk"
cp -p "$MOBILE_DEMO_APK_SOURCE" "$MOBILE_DEMO_APK_TARGET"
MOBILE_DEMO_HASH=$(shasum -a 256 "$MOBILE_DEMO_APK_TARGET" | awk '{print $1}')
printf '%s  %s\n' "$MOBILE_DEMO_HASH" "$(basename "$MOBILE_DEMO_APK_TARGET")" \
    > "$MOBILE_DEMO_APK_TARGET.sha256"
printf '%s\n' \
    'SCREENSHOT-ONLY LOCAL DEMO' \
    'Application ID: com.fypproject.screenshot' \
    'ABI: arm64-v8a (local Pixel_7 screenshot target)' \
    'Embedded JavaScript bundle: verified' \
    'Firebase configuration: placeholder only' \
    'Android network permissions: none' \
    'Startup route: redacted System Health page' \
    'Not valid as Firebase, UAV, field-flight or production evidence.' \
    > "$MOBILE_DEMO_OUTPUT/README_MOBILE_LOCAL_DEMO.txt"

echo "$MOBILE_DEMO_HASH  $MOBILE_DEMO_APK_TARGET"
