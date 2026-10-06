# Mobile Application

**The hiker’s entry point to the User-Triggered UAV Dispatching System.** Plan a trip, record timestamped GPS positions or submit an SOS request. The Android application keeps records locally and shows their synchronization state while the ground station handles operator verification.

[System overview](../../README.md) · [Ground station](../ground_station/README.md) · [Search UAV](../search_uav/README.md) · [Technical guide](../../docs/Technical_Guide.md)

**Stack:** React Native 0.80.2 · React 19.1 · TypeScript 5.0.4 · Kotlin · SQLite · Firebase Authentication · Realtime Database

[Interface](#interface) · [Service modes](#service-modes) · [Architecture](#application-architecture) · [Setup](#getting-started)

## Interface

<img src="../../docs/assets/screenshots/mobile-paper.png" alt="The paper’s existing screenshots of Event Booking, Quick Track and SOS" width="100%">

The three service screens are reproduced from the paper’s existing mobile-interface figure. They show route booking, recorded GPS positions and the SOS interface. The original screenshot labels are preserved. Current behavior and operator verification are described below. [Open the figure at full size](../../docs/assets/screenshots/mobile-paper.png).

## Service modes

| Mode | User action | What the system retains |
| --- | --- | --- |
| **Event Booking** | Name the trip, choose departure time and add ordered map points | Planned route, horizontal route distance and estimated end timestamp |
| **Quick Start** | Start a GPS recording; stop it at the end of the trip | Original sample time, session identity, point sequence and durable upload operations |
| **SOS** | Submit the current location with a help request | A locally persisted request with its pending or confirmed synchronization state |

Booking estimates use `ceil(route_distance_m / 4000 × 60)` minutes. Editing the route or departure time recalculates the full end timestamp. All three modes produce information for **ground-station verification**; the operator confirms a search and reviews the flight route. The separate emergency-call button opens the phone dialer.

## Application architecture

![Android application architecture: screens, TypeScript services, native location service, SQLite and upload workers](../../docs/assets/diagrams/mobile-architecture.svg)

**Recording continues independently of the Quick Start page.** Android’s location foreground service validates samples and writes the sample and pending operation in one SQLite transaction. The upload worker retries saved operations separately. Navigating away removes screen polling while recording remains owned by the native service.

The requested sampling interval is five seconds. Stopping persists the local end state immediately; cloud completion is tracked separately. Force-stop interrupts acquisition, and reopening offers an explicit Resume action. No positions are fabricated for the interruption. The map preview is capped at 1,000 points; accepted history remains in storage and uploads in full. See [Persistent Android recording](../../docs/Android_Recording.md) for lifecycle and queue details.

### Where this component sits

`Phone records → shared database → operator verification → reviewed UAV mission`

The phone can also submit records to the UAV’s local HTTP receiver. Onboard persistence and later ground-station acknowledgement belong to the [UAV record-delivery workflow](../search_uav/README.md#phone-record-delivery). A local save, a completed upload and a confirmed search are separate states.

## Screen map

| Screen | Purpose | Implementation |
| --- | --- | --- |
| Map | Location view and access to rescue services | [MapPage.tsx](src/pages/MapPage.tsx) |
| Event Booking | Trip details, ordered route and estimated end time | [EventBookingPage.tsx](src/pages/EventBookingPage.tsx) |
| Quick Start | Start, resume, stop and inspect persistent GPS recording | [AndroidQuickStartPage.tsx](src/pages/AndroidQuickStartPage.tsx) |
| SOS | Help request, local UAV transfer and separate call action | [SosPage.tsx](src/pages/SosPage.tsx) |
| Profile | Hiker and emergency-contact information | [ProfilePage.tsx](src/pages/ProfilePage.tsx) |
| Settings | Google sign-in, administrator binding, language, profile access and local UAV connection settings | [SettingPage.tsx](src/pages/SettingPage.tsx) |

The interface includes English and Chinese translations.

## Project structure

| Location | Contents |
| --- | --- |
| [src/](src/) | Application entry component, pages, services and translations |
| [android/](android/) | Native Android application and recording service |

The root `package.json` and `package-lock.json` define and lock npm dependencies. `app.json` identifies the registered application, and `tsconfig.json` supplies the TypeScript configuration. These files stay at the application root for the standard toolchain commands.

## Database structure

![Firebase Realtime Database structure: phone records and ground-station verification records](../../docs/assets/diagrams/database-structure.svg)

The diagram shows source-defined paths and selected fields, rather than a capture of the operational database. Braces denote record identifiers.

The shared Firebase Realtime Database groups records under `users/{phone}`. For team testing, an administrator assigns the exact phone key to a Firebase Authentication UID through `mobile_account_bindings/{uid}`. The mobile app uses this verified binding; editing a profile or entering a phone number cannot select another person's records. The application and ground station use these paths:

```text
mobile_account_bindings/{uid}/
  phone
  enabled
users/{phone}/
  profile
  booked_events/{eventId}
  QuickStartSessions/{sessionId}
    points/point_{sequence}
  rescue_requests/{requestId}
  rescue_alerts/{alertId}
  rescue_events/{eventId}
  quick_start_monitoring/{sessionId}
```

| Record | Main fields and purpose |
| --- | --- |
| `profile` | `first_name`, `last_name`, `gender`, `phone`, `email`, `medical_notes`, `emergency_contacts` and `updated_at`. The current writer stores `emergency_contacts` as a JSON-encoded array of contact names and phone numbers. |
| `booked_events` — Mode 1 | `title`, `date`, `startTime`, `endDate`, `endTime`, ordered `waypoints` and `createdAt`. New bookings also store `expectedEndAtMs`, `routeDistanceM`, `estimatedDurationMinutes`, `walkingSpeedKmh` and `estimationMethod` for the route-based end-time estimate. |
| `QuickStartSessions` — Mode 2 | `startTime`, `status`, `points` and, after stopping, `endTime`. Each point records `latitude`, `longitude`, the original sample `timestamp` in Unix milliseconds and `timestampISO`; available sensor measurements include `accuracy`, `altitude`, `speed` and `heading`. Point keys retain their sequence. |
| `rescue_requests` — Mode 3 | `latitude`, `longitude`, `status`, `timestamp` and `device`. The phone initially writes `status: PENDING`; the timestamp identifies the request time. |

The Android queue adds `_client_revision` and `_deleted` to booking and SOS records. Deleting a booking retains a versioned deletion record so that a delayed upload cannot restore it.

The ground station maintains three additional groups. `rescue_alerts` stores verification status, the triggering record and contact-check outcomes. `rescue_events` links a confirmed search to its source alert and original phone record, including `search_confirmed_at_ms`. `quick_start_monitoring` retains per-session freshness and timeout state, including `latest_sample_at_ms`, `effective_timeout_ms` and `monitoring_status`. These groups keep incoming phone records separate from operator-confirmed search events.

The phone's local SQLite database, `location_tracker.db`, is separate from the shared cloud database. It retains local records and pending upload operations. New records are bound to the Firebase UID, project, database origin and assigned phone key. A local save does not establish cloud synchronization. See [persistent Android recording](../../docs/Android_Recording.md).

Existing records without a verified owner remain locally preserved and unassigned. They are not automatically claimed by the next account or uploaded under its identity. Signing out retains recorded data and pending operations. Returning to the original verified account permits access to its owned records and queued uploads. A recording whose owner becomes unavailable is paused until that owner is restored; stop an active recording before deliberately signing out or switching accounts.

This repository documents the structure and provides configuration templates. The operational database and its user records are not included. Configure your own database and use the same database URL in the mobile application and ground station.

Sources: [profile](src/pages/ProfilePage.tsx), [booking fields](src/services/eventBookingRecord.ts), [native storage and upload paths](android/app/src/main/java/com/fypproject/tracking/TrackingStore.kt), [SOS record](src/pages/SosPage.tsx), and [ground-station event handling](../ground_station/src/rescue_event_manager.py).

## Getting started

Use Node.js 24 for the documented local workflow, plus the Android SDK and JDK required by the project’s Gradle configuration. Commands below start in this directory.

```sh
npm ci
```

Set up the team's Firebase Android app before a device login:

1. Register the Android package `com.fypproject` in your Firebase project. Add the SHA-1 and SHA-256 fingerprints for the certificate used to sign the installed build. `./gradlew signingReport`, run from `android/`, reports configured signing certificates; register the release or Play app-signing certificate as well when using those builds.
2. Enable the **Google** provider in Firebase Authentication. Complete the project's OAuth consent settings and, if its consent screen is in Testing, include the team's Google accounts as test users.
3. Download the updated `google-services.json` into `android/app/`. It must include the project's Realtime Database URL and a Web OAuth client entry, from which the Google Services plugin generates `default_web_client_id`. Credential Manager uses this Web client ID to obtain the Google credential; Firebase Authentication then establishes the native session. The configured database must match the ground station's `GS_FIREBASE_DATABASE_URL`.
4. Review and deploy the [team-test database rules](firebase/database.rules.json) to that same database, following the [configuration and binding guide](firebase/README.md). Repository files do not configure a live Firebase project automatically.
5. Build and sign in under **Settings**. Give the displayed Firebase UID to the administrator, who verifies ownership and creates its enabled phone binding. Select **Refresh account binding** after it is assigned. An account without an enabled binding cannot access phone records.

Current cloud requests use the native Firebase configuration and session. The retained JavaScript `firebaseConfig.example.ts` template is not a required configuration step for this authentication flow. The native `google-services.json` file and local environment files are ignored by Git. Supply `MAPS_API_KEY` through the local Gradle property or environment configuration used by `android/app/build.gradle`.

Keep service-account private keys and GitHub access tokens out of the mobile application. The ground station loads its service-account file through `GS_FIREBASE_CREDENTIALS`; keep that file outside the repository. Start Metro and the Android build in separate terminals:

```sh
npm start
```

```sh
npm run android
```

The source and build targets in this repository support Android. Initial sign-in and binding refresh require connectivity. After a successful binding, the same signed-in account can continue recording locally during a connection loss; uploads require a valid Firebase ID token and permission under the deployed rules. Binding metadata is cached on the device, while Firebase Authentication manages the native login session. The application does not add tokens to its recording database or expose them to JavaScript.

Before team use, verify Google sign-in on the actual signed device build, access to the assigned records, denial of another user's records, disabled and changed bindings, interrupted connectivity, queued upload recovery and account switching. Kotlin compilation and local policy or rules tests do not establish that the target Firebase project or device login is configured correctly.

## Source map

| Area | Files |
| --- | --- |
| Screen components and translations | [pages/](src/pages/) · [translations/](src/translations/) |
| Route timing and record shape | [eventBookingRecord.ts](src/services/eventBookingRecord.ts) |
| JavaScript/native recording boundary | [persistentTracking.ts](src/services/persistentTracking.ts) |
| Google login and administrator binding | [mobileAuth.ts](src/services/mobileAuth.ts) · [native authentication](android/app/src/main/java/com/fypproject/auth/) · [database rules and setup](firebase/README.md) |
| Sampling, storage and synchronization | [Android tracking package](android/app/src/main/java/com/fypproject/tracking/) |
| Local database access | [services/db/](src/services/db/) |
| UAV transfer clients | [uavRescueClient.ts](src/services/uavRescueClient.ts) · [uavArrivalCaptureClient.ts](src/services/uavArrivalCaptureClient.ts) |

[Next: ground-station verification and dispatch →](../ground_station/README.md)
