# Mobile Application

**The hiker’s entry point to the User-Triggered UAV Dispatching System.** Plan a trip, record timestamped GPS positions or submit an SOS request. The Android application keeps records locally and shows their synchronization state while the ground station handles operator verification.

[System overview](../../README.md) · [Ground station](../ground_station/README.md) · [Search UAV](../search_uav/README.md) · [Technical guide](../../docs/Technical_Guide.md)

**Stack:** React Native 0.80.2 · React 19.1 · TypeScript 5.0.4 · Kotlin · SQLite · Realtime Database

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
| Settings | Language, profile access and local UAV connection settings | [SettingPage.tsx](src/pages/SettingPage.tsx) |

The interface includes English and Chinese translations.

## Project structure

| Location | Contents |
| --- | --- |
| [src/](src/) | Application entry component, pages, services and translations |
| [android/](android/) | Native Android application and recording service |

The root `package.json` and `package-lock.json` define and lock npm dependencies. `app.json` identifies the registered application, and `tsconfig.json` supplies the TypeScript configuration. These files stay at the application root for the standard toolchain commands.

## Getting started

Use Node.js 24 for the documented local workflow, plus the Android SDK and JDK required by the project’s Gradle configuration. Commands below start in this directory.

```sh
npm ci
```

For a device build, supply the project’s client configuration using [firebaseConfig.example.ts](src/services/db/firebaseConfig.example.ts), the native Android service file and map credentials. Match the ground station’s database URL. Then start Metro and the Android build in separate terminals:

```sh
npm start
```

```sh
npm run android
```

The source and build targets in this repository support Android. See [persistent Android recording](../../docs/Android_Recording.md) for the location-service lifecycle and synchronization behavior.

## Source map

| Area | Files |
| --- | --- |
| Screen components and translations | [pages/](src/pages/) · [translations/](src/translations/) |
| Route timing and record shape | [eventBookingRecord.ts](src/services/eventBookingRecord.ts) |
| JavaScript/native recording boundary | [persistentTracking.ts](src/services/persistentTracking.ts) |
| Sampling, storage and synchronization | [Android tracking package](android/app/src/main/java/com/fypproject/tracking/) |
| Local database access | [services/db/](src/services/db/) |
| UAV transfer clients | [uavRescueClient.ts](src/services/uavRescueClient.ts) · [uavArrivalCaptureClient.ts](src/services/uavArrivalCaptureClient.ts) |

[Next: ground-station verification and dispatch →](../ground_station/README.md)
