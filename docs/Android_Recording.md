# Persistent Android recording

Android Quick Start uses a native location foreground service. Navigating away
from the page only removes its status polling. Location callbacks write the
original sample, its sequence and its pending upload in one SQLite transaction.
The REST worker runs separately from location collection and UI commands.

The database remains `location_tracker.db` in the Android application database
directory. The JavaScript database entry point initializes the native schema
and compares the SQLite main file's device and inode through Android `Os.stat`
before using it. This accepts Android bind-mount aliases such as `/data/data`
and `/data/user/0` only when they identify the same file. The version 5 migration
preserves existing rows, adds account ownership and rebuilds the mobile record
cache with an owner-scoped key in one transaction. Pre-authentication records
remain unassigned; older routes remain `LEGACY_UNBOUND`. These records are not
assigned to the next signed-in account or uploaded automatically.

Each new session stores its original Firebase UID, project, bound phone and
database target. Google sign-in and an administrator-managed binding establish
the owner; [team-test access](../implementation/mobile_application/firebase/README.md)
describes setup. Uploads use Firebase ID tokens and retain their original owner
across retries. Changing accounts does not transfer pending operations.
An unavailable owner pauses recording as `AUTH_PAUSED`; its original account
can explicitly resume or stop it after restoring its binding. Its durable
queue orders START, POINT and END operations. START conditionally creates an
absent session, POINT uses a stable key, and END changes only completion fields.
Failed requests retain their original payload, capture time and identity.
Groups are served in persisted round-robin order; a long route backlog cannot
monopolize the queue ahead of a later SOS or booking.
Stopping immediately persists the local end state and stops location collection;
the UI separately reports cloud operations awaiting confirmation.

Booking and SOS records also enter the Android SQLite outbox before cloud
synchronization. A local save is displayed as a queued record, not a confirmed
cloud delivery. The separate direct-to-UAV queue continues to use phone record
identifiers and local receiver authentication; it is not a Firebase UID queue.
The SOS screen limits transfers to the administrator-bound phone and cancels
in-progress transfers when the account page is replaced.
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
