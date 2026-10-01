import { NativeModules, PermissionsAndroid, Platform } from 'react-native';
import { getRealtimeDatabaseUrl } from './db/firebaseRealtimeDatabase';

export type TrackingSession = {
  session_id: string;
  phone: string;
  state: 'STARTING' | 'ACTIVE' | 'INTERRUPTED';
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
  syncState: 'PENDING' | 'SYNCED' | 'ERROR';
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
  start(phone: string, target: string): Promise<string>;
  resume(): Promise<boolean>;
  stop(): Promise<boolean>;
  retrySync(): Promise<boolean>;
  queueRecord(phone: string, target: string, category: string, id: string, payload: string, deleted: boolean): Promise<string>;
  records(phone: string, category: string): Promise<string>;
  refreshRecords(phone: string, target: string, category: string): Promise<string>;
  clearHistory(): Promise<boolean>;
  listPoints(sessionId: string, afterSequence: number, limit: number): Promise<string>;
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

export const startPersistentTracking = async (phone: string): Promise<string> => {
  await requestTrackingPermission();
  return native().start(phone, getRealtimeDatabaseUrl());
};

export const resumePersistentTracking = async (): Promise<boolean> => {
  await requestTrackingPermission();
  return native().resume();
};

export const stopPersistentTracking = (): Promise<boolean> => native().stop();
export const retryPersistentSync = (): Promise<boolean> => native().retrySync();
export const clearPersistentTrackingHistory = (): Promise<boolean> => native().clearHistory();

export const persistCloudRecord = async (
  phone: string, category: 'booked_events' | 'rescue_requests', id: string,
  payload: object, deleted = false,
): Promise<{ stored: boolean; synchronized: boolean }> =>
  JSON.parse(await native().queueRecord(phone, getRealtimeDatabaseUrl(), category, id, JSON.stringify(payload), deleted));

export const readPersistentRecords = async <T>(phone: string, category: string): Promise<T[]> =>
  JSON.parse(await native().records(phone, category));

export const refreshPersistentRecords = async <T>(phone: string, category: string): Promise<T[]> =>
  JSON.parse(await native().refreshRecords(phone, getRealtimeDatabaseUrl(), category));


export type RecordedPoint = {
  sequence: number; latitude: number; longitude: number; timestamp: string;
  accuracy: number | null; altitude: number | null; speed: number | null; heading: number | null;
};
export const listPersistentPoints = async (
  sessionId: string, afterSequence = 0, limit = 1000,
): Promise<{ points: RecordedPoint[]; nextSequence: number; hasMore: boolean }> =>
  JSON.parse(await native().listPoints(sessionId, afterSequence, limit));
