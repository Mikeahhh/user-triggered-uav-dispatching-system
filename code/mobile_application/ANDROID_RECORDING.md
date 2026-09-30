# Persistent Android recording

Android Quick Start uses a native location foreground service. Navigating away
from the page only removes its status polling. Location callbacks write the
original sample, its sequence and its pending upload in one SQLite transaction.
The REST worker runs separately from location collection and UI commands.

The database remains `location_tracker.db` in the Android application database
directory. Both JavaScript database entry points initialize the native schema
and compare the SQLite main file's device and inode through Android `Os.stat`
before using it. This accepts Android bind-mount aliases such as `/data/data`
and `/data/user/0` only when they identify the same file. The additive version 4
migration preserves existing rows and commits its version only after success.
Older routes without a verified phone binding remain `LEGACY_UNBOUND`; they are
not assigned to the current profile or uploaded automatically.

Each new session stores its original phone and database target. Its durable
queue orders START, POINT and END operations. START conditionally creates an
absent session, POINT uses a stable key, and END changes only completion fields.
Failed requests retain their original payload, capture time and identity.
Groups are served in persisted round-robin order; a long route backlog cannot
monopolize the queue ahead of a later SOS or booking.
Stopping immediately persists the local end state and stops location collection;
the UI separately reports cloud operations awaiting confirmation.

Booking and SOS records also enter the Android SQLite outbox before cloud
synchronization. A local save is displayed as a queued record, not a confirmed
cloud delivery. Existing SOS-to-UAV storage and transfer remain available.
Deleting a booking writes a versioned cloud tombstone. Both record refresh and
local lists hide tombstones, and a delayed earlier request cannot recreate the
deleted booking. Outbox errors remain visible until synchronization succeeds.
Background synchronization uses the foreground service while recording and the
Android JobScheduler when a connection is available.

Opening a previously interrupted session shows its persisted state and an
explicit Resume action. No points are invented for a gap. Force-stop, permission
revocation, unavailable GPS, storage failure and Android scheduling can interrupt
recording. Recording requests a five-second sampling interval and resumes through
the explicit Resume action after a force-stop. The map previews at most 1,000
points; the database and upload history retain every accepted point, including repeated
coordinates at different capture times. The native and JavaScript listPoints
cursor interface reads the complete history in bounded pages.

The application source and build targets support Android only.

## Local verification

`npm test -- --runInBand` runs page and bridge regressions.
`npm run typecheck` validates TypeScript.
`npm run test:tracking-native` runs the production Kotlin validation and REST
decision code with synthetic responses, plus the production schema and queue
query against local SQLite databases. The native checks use existing cached
compiler dependencies and do not download packages or contact Firebase.

`npm run android:debug:safe` builds an isolated, sanitized Android copy with
Gradle offline mode. The production manifest is preserved, including the recording
and synchronization services; only the API-key value is replaced. Set
`MOBILE_BUILD_OUTPUT_DIR` to select a new output directory. The resulting
APK uses placeholder configuration. Its `COMPILE-ONLY-NO-LIVE-FIREBASE` name
identifies its placeholder service configuration.

The 2026-09-30 local Android emulator run also installed this APK and loaded the
JavaScript from the recorded validation snapshot through isolated local Metro
with synthetic configuration.
The emulator and its child processes were restricted to host loopback from
startup. A separate ADB server was limited to the temporary emulator; no physical
device or existing AVD was used. The actual Android runtime verified database
initialization and file identity, location foreground-service startup, continued
SQLite writes while Hermes JavaScript was paused, page changes, backgrounding,
and Activity destruction/recreation. Force-stop ceased collection and reopening
displayed the explicit Resume action. Resuming preserved the session and its
sampling gap. Stop persisted END, and another process restart retained all 49
positions and 51 pending START/POINT/END operations with a visible network error.

See the [Android runtime results](../../docs/Android_Only_Validation_20260930.md#verification-results)
and [APK checksum and validation snapshot](../../docs/Android_Only_Validation_20260930.json).
These runtime checks exposed and verified fixes for result-returning SQLite
PRAGMA execution and Android's database path aliases. Separate local
synthetic-transport tests cover acknowledgements, retries and conflict decisions.
