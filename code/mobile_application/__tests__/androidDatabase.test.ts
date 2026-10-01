import { Platform } from 'react-native';

jest.mock('react-native-sqlite-storage', () => ({
  enablePromise: jest.fn(), openDatabase: jest.fn(),
}));
jest.mock('../src/services/db/firebaseRealtimeDatabase', () => ({
  getRealtimeDatabaseUrl: () => 'https://synthetic-project.firebaseio.com',
}));

const originalOS = Platform.OS;
beforeEach(() => {
  jest.resetModules();
  Object.defineProperty(Platform, 'OS', { configurable: true, value: 'android' });
});
afterEach(() => {
  Object.defineProperty(Platform, 'OS', { configurable: true, value: originalOS });
});

test('both database entry points use the native migrated file and do not run legacy migrations', async () => {
  const rn = require('react-native');
  Object.defineProperty(rn.Platform, 'OS', { configurable: true, value: 'android' });
  rn.NativeModules.PersistentTracking = {
    initialize: jest.fn().mockResolvedValue('/same/location_tracker.db'),
    matchesDatabase: jest.fn().mockResolvedValue(true),
  };
  const sqlite = require('react-native-sqlite-storage');
  const executeSql = jest.fn().mockResolvedValue([{ rows: { length: 1, item: () => ({ name: 'main', file: '/same/location_tracker.db' }) } }]);
  const opened = { executeSql, close: jest.fn() };
  sqlite.openDatabase.mockResolvedValue(opened);
  const first = require('../src/services/db/initDb');
  const second = require('../src/services/db/database');
  await Promise.all([first.initDb(), second.getDB()]);
  expect(first.getDb()).toBe(opened);
  expect(await second.getDB()).toBe(opened);
  expect(sqlite.openDatabase).toHaveBeenCalledTimes(1);
  expect(executeSql).toHaveBeenCalledWith('PRAGMA database_list');
  expect(executeSql).toHaveBeenCalledTimes(1);
  expect(rn.NativeModules.PersistentTracking.matchesDatabase).toHaveBeenCalledWith('/same/location_tracker.db');
});

test('a different database path is rejected before it is used', async () => {
  const rn = require('react-native');
  Object.defineProperty(rn.Platform, 'OS', { configurable: true, value: 'android' });
  rn.NativeModules.PersistentTracking = {
    initialize: jest.fn().mockResolvedValue('/native/location_tracker.db'),
    matchesDatabase: jest.fn().mockResolvedValue(false),
  };
  const sqlite = require('react-native-sqlite-storage');
  const close = jest.fn();
  sqlite.openDatabase.mockResolvedValue({
    close,
    executeSql: jest.fn().mockResolvedValue([{ rows: { length: 1, item: () => ({ name: 'main', file: '/wrong/location_tracker.db' }) } }]),
  });
  const database = require('../src/services/db/initDb');
  await expect(database.initDb()).rejects.toThrow('different databases');
  expect(close).toHaveBeenCalledTimes(1);
  expect(() => database.getDb()).toThrow('not initialized');
});

test('Android bind mount aliases are accepted only when native file identity agrees', async () => {
  const rn = require('react-native');
  rn.NativeModules.PersistentTracking = {
    initialize: jest.fn().mockResolvedValue('/data/user/0/com.fypproject/databases/location_tracker.db'),
    matchesDatabase: jest.fn().mockResolvedValue(true),
  };
  const sqlite = require('react-native-sqlite-storage');
  const alias = '/data/data/com.fypproject/databases/location_tracker.db';
  const opened = {
    close: jest.fn(),
    executeSql: jest.fn().mockResolvedValue([{ rows: { length: 1, item: () => ({ name: 'main', file: alias }) } }]),
  };
  sqlite.openDatabase.mockResolvedValue(opened);
  const database = require('../src/services/db/initDb');
  await database.initDb();
  expect(database.getDb()).toBe(opened);
  expect(rn.NativeModules.PersistentTracking.matchesDatabase).toHaveBeenCalledWith(alias);
  expect(opened.close).not.toHaveBeenCalled();
});
