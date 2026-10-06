# Team-test database access

[database.rules.json](database.rules.json) defines access for Google-authenticated team testers whose existing phone records have been assigned by an administrator. This file is a deployment template; committing it does not publish rules or configure a Firebase project.

## Account binding

The administrator verifies the tester's Firebase Authentication UID and the phone record being assigned, then creates `mobile_account_bindings/{uid}` with two fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `phone` | String of 3–20 digits | Exact existing key beneath `users`; retain leading zeros and the stored country-code convention |
| `enabled` | Boolean | `true` permits access; `false` suspends it |

Use the Firebase console or a trusted Admin SDK environment to maintain bindings. Every client write to the binding branch is denied, including writes from an account with a ground-station `admin` role. The mobile client can read only its own binding. An authenticated Google account without an enabled binding has no access to phone records.

Check ownership before assigning existing records. A Google login does not verify a user-entered phone number. Do not infer a binding from a profile name, email address or phone string, or silently move older records into a new account. Review any reassignment and avoid assigning one person's records to multiple testers.

## Access boundaries

An enabled tester can read and write only these groups under the bound `users/{phone}` key:

- `profile`
- `booked_events`
- `QuickStartSessions`
- `rescue_requests`

Mobile access to other users, the database root, and the parent `users` or `users/{phone}` nodes is denied. The ground-station groups `rescue_alerts`, `rescue_events` and `quick_start_monitoring` have no mobile read or write grant. Clients must request individual allowed groups rather than download their parent.

For `ground_station_users/{uid}`, a signed-in account can read its own entry; an existing ground-station administrator can read and maintain individual account entries. Ordinary accounts cannot promote themselves to administrator. The ground station's service-account Admin SDK uses its separately configured administrative permissions.

These rules enforce account and branch ownership. They do not establish the accuracy of submitted locations or replace the application's record validation and operator confirmation.

## Configure the Android application

1. Register package `com.fypproject` in the team's Firebase project. Add the SHA-1 and SHA-256 fingerprints of the signing certificate used by the installed build. From the application's `android/` directory, run `./gradlew signingReport` to inspect configured certificates. Debug, locally signed release and Play app-signing certificates may differ; register each certificate actually used for testing or distribution.
2. Enable **Google** under Firebase Authentication's sign-in providers. Set the required support and OAuth consent details. When the consent screen is in Testing, add the team's Google accounts as test users.
3. Download the updated Android `google-services.json` after enabling Google sign-in, and place it in `android/app/`. The file must contain the intended Realtime Database URL and a Web OAuth client (`client_type: 3`). The Google Services plugin generates `default_web_client_id`; the native sign-in request uses that Web client ID, rather than an Android OAuth client ID. Keep this deployment file untracked.
4. Confirm that the Android database origin is the same one configured for the ground station. The active mobile cloud flow obtains its project and database origin from native Firebase options; it does not require the older JavaScript Firebase configuration template.
5. Review the full rule tree in the target Realtime Database and publish the tested rules. A broader grant at a parent node overrides restrictions below it. Keep access restricted while configuring authentication; do not open public reads or writes to accommodate an older client.

Credential Manager supplies a Google ID token, which the application exchanges through Firebase Authentication. Database requests use the resulting Firebase ID token and the configured database origin. Service-account private keys belong only in trusted administrative environments, never in the mobile application.

This repository supplies configuration and rules; it does not establish the current access state of any deployed database. Verify the target project's rules and enabled authentication providers in its console when deploying.

## Bind a team tester

1. The tester signs in with Google in the app's **Settings** and provides the displayed Firebase UID to the administrator. The administrator also checks that UID in Firebase Authentication and verifies whose existing phone records are being assigned.
2. In Realtime Database, create `mobile_account_bindings/{uid}` with the following shape. Replace the example UID and phone key with the verified values; keep the phone as a JSON string.

   ```json
   {
     "phone": "85200000000",
     "enabled": true
   }
   ```

3. The tester selects **Refresh account binding** while online. A missing or disabled binding leaves the account unbound. Do not put Google OAuth tokens, Firebase ID tokens or service-account keys in this binding node.
4. Test the tester's own allowed groups and verify that another tester's groups and ground-station verification groups remain inaccessible. To disable access, set `enabled` to `false` using the administrator's console or trusted Admin SDK.

First sign-in and binding refresh read the server. The native app caches only verified owner metadata for offline recording, and Firebase Authentication manages the login session. Cloud requests remain subject to the server's current rules. If an administrator disables or changes a binding while a phone is offline, the phone cannot discover that change until connectivity resumes; its existing local records retain their original owner.

## Existing device data and verification

The SQLite migration preserves pre-authentication records and queues without assigning them to the next Google account. Unassigned operations do not upload automatically. Owned SQLite profiles and cloud-synchronization records are scoped by UID, project, database origin and phone key; returning to its original verified account can resume its pending work. Signing out does not erase local history. Active recording must be stopped before a deliberate account change; an unavailable owner causes recording to pause without moving its data.

Direct-to-UAV storage uses the phone record identifier and separate local receiver authentication. It does not encode Firebase UID ownership. The SOS screen filters its transfer queue by the bound phone; assigning the same phone to another account also grants that account access to that phone's existing records.

Local checks cover owner-matching validation, database-origin restrictions and the rules' permitted and rejected paths. Compilation checks that the Android authentication and recording modules build together. Team acceptance still requires the actual signed app and Firebase project: Google account selection, missing and disabled bindings, cross-user denial, reconnect and queued upload recovery, account switching, and ground-station operation. Record those results separately from compilation or Emulator Suite output.

References: [Google sign-in on Android](https://firebase.google.com/docs/auth/android/google-signin), [Realtime Database rule syntax](https://firebase.google.com/docs/database/security/core-syntax), [REST authentication](https://firebase.google.com/docs/database/rest/auth), and [testing rules with the Emulator Suite](https://firebase.google.com/docs/rules/unit-tests).
