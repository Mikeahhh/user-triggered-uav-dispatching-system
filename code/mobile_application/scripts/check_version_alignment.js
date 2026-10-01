#!/usr/bin/env node

const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const read = relativePath =>
  fs.readFileSync(path.join(root, relativePath), 'utf8');
const requireMatch = (text, pattern, label) => {
  if (!pattern.test(text)) throw new Error(`${label} is not aligned`);
};

const expectedVersion = '1.2.0';
const expectedAndroidCode = 3;
const expectedReleaseId = 'MASS26-20260806';
const expectedProtocolSchema = 1;
const expectedOutboxStorageVersion = 3;

const packageJson = JSON.parse(read('package.json'));
const packageLock = JSON.parse(read('package-lock.json'));
if (packageJson.version !== expectedVersion) {
  throw new Error('package.json version is not aligned');
}
if (
  packageLock.version !== expectedVersion ||
  packageLock.packages?.['']?.version !== expectedVersion
) {
  throw new Error('package-lock.json root version is not aligned');
}

const gradle = read('android/app/build.gradle');
requireMatch(
  gradle,
  new RegExp(`versionCode\\s+${expectedAndroidCode}\\b`),
  'Android versionCode',
);
requireMatch(
  gradle,
  new RegExp(`versionName\\s+"${expectedVersion.replace(/\./g, '\\.')}"`),
  'Android versionName',
);

const metadata = read('services/appMetadata.ts');
requireMatch(metadata, /MOBILE_APP_VERSION = '1\.2\.0'/, 'App metadata version');
requireMatch(metadata, /ANDROID_VERSION_CODE = 3/, 'App metadata Android code');
requireMatch(
  metadata,
  new RegExp(`SYSTEM_RELEASE_ID = '${expectedReleaseId}'`),
  'System release ID',
);
requireMatch(
  metadata,
  new RegExp(`UAV_RESCUE_PROTOCOL = 'SOS schema v${expectedProtocolSchema}'`),
  'UAV rescue protocol schema',
);
requireMatch(
  metadata,
  new RegExp(`UAV_OUTBOX_STORAGE_VERSION = ${expectedOutboxStorageVersion}`),
  'UAV outbox storage version',
);

const uavRescueClient = read('services/uavRescueClient.ts');
requireMatch(
  uavRescueClient,
  /schema_version: 1;/,
  'UAV rescue payload schema',
);
requireMatch(
  uavRescueClient,
  /const OUTBOX_KEY = '@trigger-search\/uav-rescue-outbox-v3'/,
  'Persistent UAV outbox key',
);
requireMatch(
  uavRescueClient,
  /const OUTBOX_STORAGE_SCHEMA_VERSION = 3/,
  'Persistent UAV outbox envelope schema',
);

requireMatch(read('pages/SettingPage.tsx'), /v1\.2\.0/, 'Settings footer');
requireMatch(
  read('translations/en/translations.json'),
  /Version: 1\.2\.0/,
  'English About version',
);
requireMatch(
  read('translations/zh/translations.json'),
  /版本：1\.2\.0/,
  'Chinese About version',
);

process.stdout.write(
  `Mobile version alignment OK: ${expectedVersion}, Android ${expectedAndroidCode}, ${expectedReleaseId}, schema ${expectedProtocolSchema}, outbox v${expectedOutboxStorageVersion}\n`,
);
