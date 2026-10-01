import { NativeModules, PermissionsAndroid, Platform } from 'react-native';
import {
  initializePersistentTracking, persistCloudRecord, readTrackingSnapshot,
  startPersistentTracking, stopPersistentTracking,
} from '../src/services/persistentTracking';

jest.mock('../src/services/db/firebaseRealtimeDatabase', () => ({
  getRealtimeDatabaseUrl: () => 'https://synthetic-project-default-rtdb.firebaseio.com',
}));

const originalOS = Platform.OS;
const originalVersion = Platform.Version;
beforeEach(() => {
  jest.clearAllMocks();
  Object.defineProperty(Platform, 'OS', { configurable: true, value: 'android' });
  Object.defineProperty(Platform, 'Version', { configurable: true, value: 35 });
  NativeModules.PersistentTracking = {
    initialize: jest.fn().mockResolvedValue('/synthetic/location_tracker.db'),
    snapshot: jest.fn().mockResolvedValue('{"pendingCount":2}'),
    start: jest.fn().mockResolvedValue('stable-session'),
    stop: jest.fn().mockResolvedValue(true),
    queueRecord: jest.fn().mockResolvedValue('{"stored":true,"synchronized":false}'),
  };
  jest.spyOn(PermissionsAndroid, 'request').mockResolvedValue(PermissionsAndroid.RESULTS.GRANTED);
});
afterEach(() => {
  Object.defineProperty(Platform, 'OS', { configurable: true, value: originalOS });
  Object.defineProperty(Platform, 'Version', { configurable: true, value: originalVersion });
  delete NativeModules.PersistentTracking;
  jest.restoreAllMocks();
});

test('starts through native storage without waiting for a cloud request', async () => {
  expect(await initializePersistentTracking()).toBe('/synthetic/location_tracker.db');
  expect(await startPersistentTracking('26080101')).toBe('stable-session');
  expect(NativeModules.PersistentTracking.start).toHaveBeenCalledWith('26080101', 'https://synthetic-project-default-rtdb.firebaseio.com');
  expect(await readTrackingSnapshot()).toEqual({ pendingCount: 2 });
  await stopPersistentTracking();
  expect(NativeModules.PersistentTracking.stop).toHaveBeenCalledTimes(1);
});

test('permission denial cannot create a native session', async () => {
  (PermissionsAndroid.request as jest.Mock).mockResolvedValue(PermissionsAndroid.RESULTS.DENIED);
  await expect(startPersistentTracking('26080101')).rejects.toThrow('permission');
  expect(NativeModules.PersistentTracking.start).not.toHaveBeenCalled();
});

test.each(['booked_events', 'rescue_requests'] as const)('persists %s with its immutable owner and record identifier', async category => {
  const payload = { timestamp: 1800000000000, latitude: 22.4 };
  expect(await persistCloudRecord('26080101', category, 'record_1', payload)).toEqual({ stored: true, synchronized: false });
  expect(NativeModules.PersistentTracking.queueRecord).toHaveBeenCalledWith(
    '26080101', 'https://synthetic-project-default-rtdb.firebaseio.com', category, 'record_1', JSON.stringify(payload), false);
});

test('a missing native module fails visibly rather than falling back to a page timer', async () => {
  delete NativeModules.PersistentTracking;
  await expect(startPersistentTracking('26080101')).rejects.toThrow('does not include persistent recording');
});
