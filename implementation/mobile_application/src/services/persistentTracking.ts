import { NativeModules, PermissionsAndroid, Platform } from 'react-native';
import { assertCloudIdentity, BoundCloudIdentity, requireCloudIdentity } from './mobileAuth';

export type TrackingSession = {
  session_id: string;
  phone: string;
  state: 'STARTING' | 'ACTIVE' | 'INTERRUPTED' | 'AUTH_PAUSED';
  owner_uid: string;
  owner_project: string;
  started_ms: number;
  last_sample_ms: number;
  next_sequence: number;
  last_error: string;
};

export type TrackingSnapshot = {
  databasePath: string;
  displaySessionId: string;
  serviceRunning: boolean;
  session: TrackingSession | null;
  pendingCount: number;
  latestSyncError: string;
  syncState: 'PENDING' | 'SYNCED' | 'ERROR' | 'AUTH_REQUIRED';
  authPendingCount: number;
  pendingStops: Array<{ sessionId: string; error: string }>;
  totalPoints: number;
  uploadedPoints: number;
  legacyUnboundRoutes: number;
  points: Array<{ latitude: number; longitude: number; timestamp: string }>;
};

type NativeTracking = {
  initialize(): Promise<string>;
  matchesDatabase(path: string): Promise<boolean>;
  snapshot(): Promise<string>;
  start(phone: string, target: string, expectedUid: string, expectedProject: string): Promise<string>;
  resume(expectedUid: string, expectedProject: string, expectedSessionId: string): Promise<boolean>;
  stop(expectedUid: string, expectedProject: string, expectedSessionId: string): Promise<boolean>;
  retrySync(expectedUid: string, expectedProject: string, expectedPhone: string): Promise<boolean>;
  queueRecord(phone: string, target: string, category: string, id: string, payload: string, deleted: boolean, expectedUid: string, expectedProject: string): Promise<string>;
  records(phone: string, category: string, expectedUid: string, expectedProject: string): Promise<string>;
  refreshRecords(phone: string, target: string, category: string, expectedUid: string, expectedProject: string): Promise<string>;
  profile(phone: string, target: string, method: string, payload: string, expectedUid: string, expectedProject: string): Promise<string>;
  clearHistory(expectedUid: string, expectedProject: string, expectedPhone: string): Promise<boolean>;
  listPoints(sessionId: string, afterSequence: number, limit: number, expectedUid: string, expectedProject: string): Promise<string>;
};

const native = (): NativeTracking => {
  if (!NativeModules.PersistentTracking) {
    throw new Error('This Android build does not include persistent recording.');
  }
  return NativeModules.PersistentTracking;
};

export const initializePersistentTracking = (): Promise<string> => native().initialize();
export const matchesPersistentTrackingDatabase = (path: string): Promise<boolean> => native().matchesDatabase(path);
export const readTrackingSnapshot = async (): Promise<TrackingSnapshot> =>
  JSON.parse(await native().snapshot());

const requestTrackingPermission = async (): Promise<void> => {
  const permission = await PermissionsAndroid.request(PermissionsAndroid.PERMISSIONS.ACCESS_FINE_LOCATION);
  if (permission !== PermissionsAndroid.RESULTS.GRANTED) {
    throw new Error('Precise location permission is required to record a hiking route.');
  }
  if (Number(Platform.Version) >= 33) {
    await PermissionsAndroid.request(PermissionsAndroid.PERMISSIONS.POST_NOTIFICATIONS);
  }
};

export const startPersistentTracking = async (phone: string, expected?: BoundCloudIdentity): Promise<string> => {
  const owner = expected ?? await requireCloudIdentity();
  await assertCloudIdentity(owner);
  if (phone !== owner.phone) throw new Error('This phone is not bound to the current account.');
  await requestTrackingPermission();
  await assertCloudIdentity(owner);
  return native().start(phone, owner.target, owner.uid, owner.projectId);
};

const captureSessionOwner = async (sessionId: string): Promise<BoundCloudIdentity> => {
  const owner = await requireCloudIdentity();
  const snapshot = await readTrackingSnapshot();
  if (!sessionId || snapshot.session?.session_id !== sessionId ||
      snapshot.session.owner_uid !== owner.uid || snapshot.session.owner_project !== owner.projectId ||
      snapshot.session.phone !== owner.phone) throw new Error('The selected recording or account changed.');
  await assertCloudIdentity(owner);
  return owner;
};

export const resumePersistentTracking = async (sessionId: string): Promise<boolean> => {
  const owner = await captureSessionOwner(sessionId);
  await requestTrackingPermission();
  await assertCloudIdentity(owner);
  return native().resume(owner.uid, owner.projectId, sessionId);
};

export const stopPersistentTracking = async (sessionId: string): Promise<boolean> => {
  const owner = await captureSessionOwner(sessionId);
  return native().stop(owner.uid, owner.projectId, sessionId);
};
export const retryPersistentSync = async (): Promise<boolean> => {
  const owner = await requireCloudIdentity();
  return native().retrySync(owner.uid, owner.projectId, owner.phone);
};
export const clearPersistentTrackingHistory = async (): Promise<boolean> => {
  const owner = await requireCloudIdentity();
  return native().clearHistory(owner.uid, owner.projectId, owner.phone);
};

export const persistCloudRecord = async (
  phone: string, category: 'booked_events' | 'rescue_requests', id: string,
  payload: object, deleted = false, expected?: BoundCloudIdentity,
): Promise<{ stored: boolean; synchronized: boolean }> => {
  const owner = expected ?? await requireCloudIdentity();
  await assertCloudIdentity(owner);
  if (phone !== owner.phone) throw new Error('This phone is not bound to the current account.');
  return JSON.parse(await native().queueRecord(phone, owner.target, category, id, JSON.stringify(payload), deleted, owner.uid, owner.projectId));
};

export const readPersistentRecords = async <T>(phone: string, category: string, expected?: BoundCloudIdentity): Promise<T[]> => {
  const owner = expected ?? await requireCloudIdentity();
  await assertCloudIdentity(owner);
  if (phone !== owner.phone) throw new Error('This phone is not bound to the current account.');
  const result = JSON.parse(await native().records(phone, category, owner.uid, owner.projectId));
  await assertCloudIdentity(owner);
  return result;
};

export const refreshPersistentRecords = async <T>(phone: string, category: string, expected?: BoundCloudIdentity): Promise<T[]> => {
  const owner = expected ?? await requireCloudIdentity();
  await assertCloudIdentity(owner);
  if (phone !== owner.phone) throw new Error('This phone is not bound to the current account.');
  const result = JSON.parse(await native().refreshRecords(phone, owner.target, category, owner.uid, owner.projectId));
  await assertCloudIdentity(owner);
  return result;
};

export const readCloudProfile = async (owner: BoundCloudIdentity): Promise<unknown> => {
  await assertCloudIdentity(owner);
  const value = JSON.parse(await native().profile(owner.phone, owner.target, 'GET', '', owner.uid, owner.projectId));
  await assertCloudIdentity(owner);
  return value;
};

export const writeCloudProfile = async (owner: BoundCloudIdentity, payload: object): Promise<void> => {
  await assertCloudIdentity(owner);
  await native().profile(owner.phone, owner.target, 'PUT', JSON.stringify(payload), owner.uid, owner.projectId);
  await assertCloudIdentity(owner);
};


export type RecordedPoint = {
  sequence: number; latitude: number; longitude: number; timestamp: string;
  accuracy: number | null; altitude: number | null; speed: number | null; heading: number | null;
};
export const listPersistentPoints = async (
  sessionId: string, afterSequence = 0, limit = 1000,
): Promise<{ points: RecordedPoint[]; nextSequence: number; hasMore: boolean }> => {
  const owner = await requireCloudIdentity();
  const result = JSON.parse(await native().listPoints(sessionId, afterSequence, limit, owner.uid, owner.projectId));
  await assertCloudIdentity(owner);
  return result;
};
