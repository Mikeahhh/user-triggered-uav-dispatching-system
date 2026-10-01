import AsyncStorage from '@react-native-async-storage/async-storage';
import { hashV1Payload } from '../src/services/rescueRecordHash';
import {
  UAV_OUTBOX_MAX_ITEMS,
  UAV_OUTBOX_TTL_MS,
  UavConnectionConfig,
  UavRescuePayload,
  UavRescueTransferError,
  clearPersistentUavOutbox,
  clearUavSessionToken,
  flushPendingUavRescues,
  getUavConnectionConfig,
  getPendingUavRescues,
  hasUavSessionToken,
  normalizeBaseUrl,
  queueRescueForUav,
  saveUavConnectionConfig,
  sendRescueToUav,
  setUavSessionToken,
} from '../src/services/uavRescueClient';

jest.mock('@react-native-async-storage/async-storage', () =>
  require('@react-native-async-storage/async-storage/jest/async-storage-mock'),
);

const configA: UavConnectionConfig = {
  baseUrl: 'http://127.0.0.1:8080',
  wifiSsid: 'ALIN1-UAV',
};
const configB: UavConnectionConfig = {
  baseUrl: 'http://127.0.0.2:8080',
  wifiSsid: 'ALIN1-UAV-B',
};

const makePayload = (requestId = 'bench_001'): UavRescuePayload => ({
  schema_version: 1,
  request_id: requestId,
  mission_id: `TEST_USER/${requestId}`,
  user_id: 'TEST_USER',
  latitude: 22.352,
  longitude: 114.183,
  accuracy: 3,
  captured_at: '2026-08-01T12:00:00.000Z',
  client_timestamp_ms: 1785585600000,
  status: 'PENDING',
  device: 'android',
  gps_points: [
    {
      latitude: 22.352,
      longitude: 114.183,
      captured_at: '2026-08-01T12:00:00.000Z',
    },
  ],
  test_mode: true,
});

const makeReceipt = (requestId = 'bench_001') => ({
  schema_version: 1,
  status: 'STORED',
  request_id: requestId,
  mission_id: `TEST_USER/${requestId}`,
  uav_received_at: '2026-08-01T12:00:01Z',
  payload_sha256: hashV1Payload(makePayload(requestId)),
  duplicate: false,
});

beforeEach(async () => {
  await AsyncStorage.clear();
  await clearPersistentUavOutbox();
  clearUavSessionToken();
});

test('accepts only a matching persisted UAV receipt and uses a session token', async () => {
  const payload = makePayload();
  const fetchImpl = jest.fn().mockResolvedValue({
    ok: true,
    status: 201,
    json: async () => makeReceipt(),
  });
  setUavSessionToken('runtime-only');
  const result = await sendRescueToUav(payload, {
    config: configA,
    fetchImpl: fetchImpl as any,
  });
  expect(result.request_id).toBe(payload.request_id);
  expect(fetchImpl).toHaveBeenCalledWith(
    'http://127.0.0.1:8080/api/v1/rescue-requests',
    expect.objectContaining({
      method: 'POST',
      headers: expect.objectContaining({ 'X-Rescue-Token': 'runtime-only' }),
    }),
  );
});

test('a wrong content hash cannot acknowledge or remove a queued record', async () => {
  const nowMs = Date.now();
  await queueRescueForUav(makePayload(), { config: configA, nowMs });
  const result = await flushPendingUavRescues({
    config: configA,
    nowMs,
    fetchImpl: jest.fn().mockResolvedValue({
      ok: true,
      status: 201,
      json: async () => ({ ...makeReceipt(), payload_sha256: '0'.repeat(64) }),
    }) as any,
  });
  expect(result.sent).toEqual([]);
  expect(result.failed).toBe(1);
  expect(result.remaining).toBe(1);
  expect((await getPendingUavRescues())[0].payload.request_id).toBe('bench_001');
});

test('receipt validation hashes the serialized wire value of negative zero', async () => {
  const payload = { ...makePayload(), latitude: -0 };
  const fetchImpl = jest.fn().mockImplementation(async (_url, options) => ({
    ok: true,
    status: 201,
    json: async () => ({
      ...makeReceipt(),
      payload_sha256: hashV1Payload(JSON.parse(options.body)),
    }),
  }));
  await expect(sendRescueToUav(payload, { config: configA, fetchImpl })).resolves.toMatchObject({
    status: 'STORED',
  });
});

test('never persists the session token with receiver settings', async () => {
  setUavSessionToken('do-not-persist');
  await saveUavConnectionConfig(configA);
  const keys = await AsyncStorage.getAllKeys();
  const stored = await AsyncStorage.multiGet(keys);
  expect(hasUavSessionToken()).toBe(true);
  expect(JSON.stringify(stored)).not.toContain('do-not-persist');
  expect(JSON.stringify(stored)).toContain('ALIN1-UAV');
});

test('loads an existing v2 config without an SSID as manual-join compatible', async () => {
  await AsyncStorage.setItem(
    '@trigger-search/uav-rescue-config-v2',
    JSON.stringify({
      baseUrl: 'http://127.0.0.1:8080',
      testMode: true,
    }),
  );
  await expect(getUavConnectionConfig()).resolves.toEqual({
    baseUrl: 'http://127.0.0.1:8080',
    wifiSsid: '',
  });
  expect(JSON.parse((await AsyncStorage.getItem('@trigger-search/uav-rescue-config-v2'))!)).toEqual({
    baseUrl: 'http://127.0.0.1:8080', wifiSsid: '',
  });
});

test('persists the bounded outbox but never persists credentials or extra fields', async () => {
  const payload = makePayload() as UavRescuePayload & {
    token?: string;
    passphrase?: string;
  };
  payload.token = 'do-not-persist-token';
  payload.passphrase = 'do-not-persist-password';
  await queueRescueForUav(payload, {
    config: {
      ...configA,
      token: 'config-token',
      passphrase: 'config-password',
    } as any,
  });
  const keys = await AsyncStorage.getAllKeys();
  const stored = await AsyncStorage.multiGet(keys);
  expect(await getPendingUavRescues()).toHaveLength(1);
  expect(keys).toContain('@trigger-search/uav-rescue-outbox-v3');
  expect(JSON.stringify(stored)).toContain('TEST_USER');
  expect(JSON.stringify(stored)).toContain('22.352');
  expect(JSON.stringify(stored)).not.toContain('do-not-persist');
  expect(JSON.stringify(stored)).not.toContain('config-token');
  expect(JSON.stringify(stored)).not.toContain('config-password');
});

test('reloads a queued record from persistent storage', async () => {
  await queueRescueForUav(makePayload('persistent_001'), { config: configA });
  const storedBeforeRead = await AsyncStorage.getItem(
    '@trigger-search/uav-rescue-outbox-v3',
  );
  expect(storedBeforeRead).toContain('persistent_001');
  await expect(getPendingUavRescues()).resolves.toEqual([
    expect.objectContaining({
      payload: expect.objectContaining({ request_id: 'persistent_001' }),
    }),
  ]);
});

test('purges an expired persistent record on the next outbox access', async () => {
  const expiredStart = Date.now() - UAV_OUTBOX_TTL_MS - 1000;
  await queueRescueForUav(makePayload('expired_on_read'), {
    config: configA,
    nowMs: expiredStart,
  });
  await expect(getPendingUavRescues()).resolves.toEqual([]);
  await expect(
    AsyncStorage.getItem('@trigger-search/uav-rescue-outbox-v3'),
  ).resolves.toBeNull();
});

test('allows private HTTP but requires HTTPS for public receivers', () => {
  expect(normalizeBaseUrl('http://192.168.4.1:8080/')).toBe(
    'http://192.168.4.1:8080',
  );
  expect(normalizeBaseUrl('https://receiver.example.org')).toBe(
    'https://receiver.example.org',
  );
  expect(() => normalizeBaseUrl('http://receiver.example.org')).toThrow(
    'HTTP is allowed only',
  );
});

test('rejects a receipt for a different request', async () => {
  const fetchImpl = jest.fn().mockResolvedValue({
    ok: true,
    status: 201,
    json: async () => ({ ...makeReceipt(), request_id: 'other' }),
  });
  await expect(
    sendRescueToUav(makePayload(), {
      config: configA,
      token: '',
      fetchImpl: fetchImpl as any,
    }),
  ).rejects.toBeInstanceOf(UavRescueTransferError);
});

test('keeps failed records in the outbox', async () => {
  const nowMs = Date.now();
  await queueRescueForUav(makePayload(), { config: configA, nowMs });
  const fetchImpl = jest.fn().mockRejectedValue(new Error('offline'));
  const result = await flushPendingUavRescues({
    config: configA,
    token: '',
    fetchImpl: fetchImpl as any,
    nowMs: nowMs + 1000,
  });
  expect(result.failed).toBe(1);
  expect(result.remaining).toBe(1);
  expect(await getPendingUavRescues()).toHaveLength(1);
});

test('does not send an outbox item to a different configured receiver', async () => {
  const nowMs = Date.parse('2026-08-01T12:00:00Z');
  await queueRescueForUav(makePayload(), { config: configA, nowMs });
  const fetchImpl = jest.fn();
  const result = await flushPendingUavRescues({
    config: configB,
    token: '',
    fetchImpl: fetchImpl as any,
    nowMs: nowMs + 1000,
  });
  expect(fetchImpl).not.toHaveBeenCalled();
  expect(result.differentReceiver).toBe(1);
  expect(result.remaining).toBe(1);
});

test('does not send a queued record through a different UAV Wi-Fi SSID', async () => {
  const nowMs = Date.parse('2026-08-01T12:00:00Z');
  await queueRescueForUav(makePayload(), { config: configA, nowMs });
  const fetchImpl = jest.fn();
  const result = await flushPendingUavRescues({
    config: { ...configA, wifiSsid: 'OTHER-UAV' },
    token: '',
    fetchImpl: fetchImpl as any,
    nowMs: nowMs + 1000,
  });
  expect(fetchImpl).not.toHaveBeenCalled();
  expect(result.differentWifi).toBe(1);
  expect(result.remaining).toBe(1);
});

test('aborts without sending when the bound UAV network is lost', async () => {
  const nowMs = Date.parse('2026-08-01T12:00:00Z');
  await queueRescueForUav(makePayload(), { config: configA, nowMs });
  const controller = new AbortController();
  controller.abort();
  const fetchImpl = jest.fn();
  const result = await flushPendingUavRescues({
    config: configA,
    token: '',
    fetchImpl: fetchImpl as any,
    signal: controller.signal,
    nowMs: nowMs + 1000,
  });
  expect(fetchImpl).not.toHaveBeenCalled();
  expect(result.failed).toBe(1);
  expect(result.remaining).toBe(1);
});

test('reports and removes expired records without transmitting them', async () => {
  const nowMs = Date.parse('2026-08-01T12:00:00Z');
  await queueRescueForUav(makePayload(), { config: configA, nowMs });
  const fetchImpl = jest.fn();
  const result = await flushPendingUavRescues({
    config: configA,
    token: '',
    fetchImpl: fetchImpl as any,
    nowMs: nowMs + UAV_OUTBOX_TTL_MS + 1,
  });
  expect(fetchImpl).not.toHaveBeenCalled();
  expect(result.expired).toBe(1);
  expect(result.remaining).toBe(0);
  expect(await getPendingUavRescues()).toHaveLength(0);
});

test('refuses a full outbox without discarding an existing record', async () => {
  const nowMs = Date.now();
  for (let index = 0; index < UAV_OUTBOX_MAX_ITEMS; index++) {
    await queueRescueForUav(makePayload(`bench_${index}`), {
      config: configA,
      nowMs,
    });
  }
  await expect(
    queueRescueForUav(makePayload('bench_overflow'), {
      config: configA,
      nowMs,
    }),
  ).rejects.toThrow('no record was discarded');
  expect(await getPendingUavRescues()).toHaveLength(UAV_OUTBOX_MAX_ITEMS);
});
