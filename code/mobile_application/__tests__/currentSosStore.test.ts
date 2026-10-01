import AsyncStorage from '@react-native-async-storage/async-storage';
import { getCurrentSosRequest, restoreCurrentSosFromLegacy, saveCurrentSosRequest } from '../src/services/currentSosStore';
import { source } from './support/uavCaptureFixtures';
jest.mock('@react-native-async-storage/async-storage', () => require('@react-native-async-storage/async-storage/jest/async-storage-mock'));
beforeEach(async () => { await AsyncStorage.clear(); jest.clearAllMocks(); });
test('source survives outbox removal and is isolated by user', async () => {
  await saveCurrentSosRequest(source);
  await AsyncStorage.removeItem('@trigger-search/uav-rescue-outbox-v3');
  expect(await getCurrentSosRequest(source.user_id)).toEqual(source);
  expect(await getCurrentSosRequest('another')).toBeNull();
});
test('new SOS replaces current pointer, but same ID cannot change original content', async () => {
  await saveCurrentSosRequest(source);
  await expect(saveCurrentSosRequest({ ...source, latitude: 23 })).rejects.toThrow();
  const next = { ...source, request_id: 'new', mission_id: 'user1/new' };
  await saveCurrentSosRequest(next);
  expect(await getCurrentSosRequest('user1')).toEqual(next);
});
test('legacy recovery only selects latest strict local v1 source for the same user', async () => {
  const next = { ...source, request_id: 'new', mission_id: 'user1/new', client_timestamp_ms: source.client_timestamp_ms + 100 };
  const other = { ...source, user_id: 'another', mission_id: 'another/request1', client_timestamp_ms: source.client_timestamp_ms + 1000 };
  expect(await restoreCurrentSosFromLegacy('user1', [other, source, next])).toEqual(next);
  expect(await restoreCurrentSosFromLegacy('missing', [other, source])).toBeNull();
});
test('does not infer source location timestamp from legacy cloud timestamp', async () => {
  const broken: any = { ...source }; delete broken.captured_at;
  await expect(restoreCurrentSosFromLegacy('user1', [broken])).rejects.toThrow();
  expect(await getCurrentSosRequest('user1')).toBeNull();
});
test('corrupt or cross-user current storage cannot silently become a different source', async () => {
  await AsyncStorage.setItem('@trigger-search/current-sos-v1/another', JSON.stringify({ storage_schema_version: 1, source_request: source }));
  await expect(getCurrentSosRequest('another')).rejects.toThrow();
});
