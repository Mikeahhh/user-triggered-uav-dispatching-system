import { createUavArrivalCaptureClient, UavCaptureTransferError } from '../services/uavArrivalCaptureClient';
import { context, source, position, receiptFor } from '../testSupport/uavCaptureFixtures';
import { canonicalV2PayloadJson, hashV2Payload } from '../services/uavCaptureV2';
jest.mock('@react-native-async-storage/async-storage', () => require('@react-native-async-storage/async-storage/jest/async-storage-mock'));
jest.mock('@react-native-community/geolocation', () => ({ getCurrentPosition: jest.fn() }));
const config = { baseUrl: 'http://127.0.0.1:8080', wifiSsid: 'TEST-UAV' };
const confirmed = { wifiConfirmed: true };
const setup = () => {
  let raw: string | null = null;
  const order: string[] = [];
  const storage = { getItem: jest.fn(async () => raw), setItem: jest.fn(async (_key: string, value: string) => { order.push('persist'); raw = value; }) };
  let counter = 0;
  const createId = jest.fn(async () => `capture${++counter}`);
  const requestContext = jest.fn(async identity => { order.push('context'); return { ...context, ...identity }; });
  const capturePosition = jest.fn(async () => { order.push('gps'); return position; });
  const postCapture = jest.fn(async (body: string) => { order.push('post'); return receiptFor(JSON.parse(body)); });
  const deps = { storage, createId, requestContext, capturePosition, postCapture, device: 'android', now: () => position.capture_started_at_ms };
  return { client: createUavArrivalCaptureClient(deps), deps, order, raw: () => raw, setRaw: (value: string) => { raw = value; } };
};
test('persists identity/context/fresh payload before network effects, then receipt', async () => {
  const { client, deps, order } = setup();
  await client.capture(source, config, confirmed);
  expect(order).toEqual(['persist', 'context', 'persist', 'gps', 'persist', 'post', 'persist']);
  const body = deps.postCapture.mock.calls[0][0];
  expect(body).toBe(canonicalV2PayloadJson(JSON.parse(body)));
  expect(JSON.parse(body).source_request).toEqual(source);
  expect(JSON.parse(body).test_mode).toBe(false);
  expect(deps.capturePosition).toHaveBeenCalledTimes(1);
});
test('refuses unconfirmed Wi-Fi without any location or network work', async () => {
  const { client, deps } = setup();
  await expect(client.capture(source, config, { wifiConfirmed: false })).rejects.toThrow('WIFI_CONFIRMATION_REQUIRED');
  expect(deps.requestContext).not.toHaveBeenCalled(); expect(deps.capturePosition).not.toHaveBeenCalled();
});
test('retries an earlier stored payload without changing its content or hash', async () => {
  const { client, deps, raw, setRaw } = setup();
  deps.postCapture.mockRejectedValueOnce(new Error('response lost'));
  await expect(client.capture(source, config, confirmed)).rejects.toThrow();
  const saved = JSON.parse(raw()!);
  const entry = saved.items[0];
  const earlierPayload = { ...JSON.parse(entry.payload_json), test_mode: true };
  entry.test_mode = true;
  entry.payload_json = canonicalV2PayloadJson(earlierPayload);
  entry.payload_sha256 = hashV2Payload(earlierPayload);
  setRaw(JSON.stringify(saved));
  const restarted = createUavArrivalCaptureClient(deps);
  expect((await restarted.retry(source.user_id, config, confirmed)).sent).toBe(1);
  expect(deps.postCapture.mock.calls[1][0]).toBe(entry.payload_json);
  expect(JSON.parse(raw()!).items[0].receipt.payload_sha256).toBe(entry.payload_sha256);
  expect(deps.capturePosition).toHaveBeenCalledTimes(1);
});
test('a lost POST response followed by process restart resends identical bytes and identity', async () => {
  const { client, deps } = setup();
  deps.postCapture.mockRejectedValueOnce(new Error('response lost'));
  await expect(client.capture(source, config, confirmed)).rejects.toThrow();
  const restarted = createUavArrivalCaptureClient(deps);
  const result = await restarted.retry(source.user_id, config, confirmed);
  expect(result.sent).toBe(1);
  expect(deps.postCapture.mock.calls[1][0]).toBe(deps.postCapture.mock.calls[0][0]);
  expect(deps.requestContext).toHaveBeenCalledTimes(1); expect(deps.capturePosition).toHaveBeenCalledTimes(1);
  await restarted.retry(source.user_id, config, confirmed);
  expect(deps.postCapture).toHaveBeenCalledTimes(2);
});
test('retry cannot take a new GPS sample after failed positioning; explicit capture rechecks current context', async () => {
  const { client, deps } = setup();
  deps.capturePosition.mockRejectedValueOnce(new Error('no fresh fix'));
  await expect(client.capture(source, config, confirmed)).rejects.toThrow();
  expect((await client.retry(source.user_id, config, confirmed)).needsLocation).toBe(1);
  expect(deps.capturePosition).toHaveBeenCalledTimes(1); expect(deps.postCapture).not.toHaveBeenCalled();
  deps.requestContext.mockRejectedValueOnce(new UavCaptureTransferError('NO_COLLECTION_CONTEXT'));
  await expect(client.capture(source, config, confirmed)).rejects.toThrow('NO_COLLECTION_CONTEXT');
  expect(deps.capturePosition).toHaveBeenCalledTimes(1);
  await client.capture(source, config, confirmed);
  expect(deps.createId).toHaveBeenCalledTimes(2);
  expect(deps.requestContext).toHaveBeenCalledTimes(3);
  expect(deps.capturePosition).toHaveBeenCalledTimes(2);
});
test('does not relabel an intent with a different carrier execution', async () => {
  const { client, deps } = setup();
  deps.capturePosition.mockRejectedValueOnce(new Error('no fresh fix'));
  await expect(client.capture(source, config, confirmed)).rejects.toThrow();
  deps.requestContext.mockImplementationOnce(async identity => ({ ...context, ...identity, carrier_execution_id: 'next' }));
  await expect(client.capture(source, config, confirmed)).rejects.toThrow('CONTEXT_IDENTITY_CONFLICT');
  expect(deps.capturePosition).toHaveBeenCalledTimes(1);
});
test('a sample in progress after a valid context may finish and POST without a second GET', async () => {
  const { client, deps } = setup();
  deps.capturePosition.mockImplementationOnce(async () => { deps.requestContext.mockRejectedValue(new Error('terminal')); return position; });
  await client.capture(source, config, confirmed);
  expect(deps.postCapture).toHaveBeenCalledTimes(1); expect(deps.requestContext).toHaveBeenCalledTimes(1);
});
test.each([{ ...config, baseUrl: 'http://127.0.0.2:8080' }, { ...config, wifiSsid: 'OTHER' }])('never replays to changed receiver/network %p', async different => {
  const { client, deps } = setup();
  deps.postCapture.mockRejectedValueOnce(new Error('lost'));
  await expect(client.capture(source, config, confirmed)).rejects.toThrow();
  expect((await client.retry(source.user_id, different, confirmed)).differentReceiver).toBe(1);
  expect((await client.retry('anotherUser', config, confirmed)).remaining).toBe(0);
  expect(deps.postCapture).toHaveBeenCalledTimes(1);
});
test('new explicit capture after success obtains a new ID/context/sample', async () => {
  const { client, deps } = setup();
  await client.capture(source, config, confirmed); await client.capture(source, config, confirmed);
  expect(deps.requestContext).toHaveBeenCalledTimes(2); expect(deps.capturePosition).toHaveBeenCalledTimes(2);
  expect(JSON.parse(deps.postCapture.mock.calls[0][0]).capture_id).not.toBe(JSON.parse(deps.postCapture.mock.calls[1][0]).capture_id);
});
test.each([1, 2, 3])('persistence failure at checkpoint %i prevents subsequent POST', async failAt => {
  const { client, deps } = setup();
  const original = deps.storage.setItem.getMockImplementation()!;
  let writes = 0;
  deps.storage.setItem.mockImplementation(async (key, value) => { if (++writes === failAt) throw new Error('disk full'); await original(key, value); });
  await expect(client.capture(source, config, confirmed)).rejects.toThrow('disk full');
  expect(deps.postCapture).not.toHaveBeenCalled();
});
test('receipt persistence failure allows exact replay and a mismatching receipt never completes it', async () => {
  const { client, deps } = setup();
  const original = deps.storage.setItem.getMockImplementation()!;
  let writes = 0;
  deps.storage.setItem.mockImplementation(async (key, value) => { if (++writes === 4) throw new Error('disk full'); await original(key, value); });
  await expect(client.capture(source, config, confirmed)).rejects.toThrow('disk full');
  deps.postCapture.mockImplementationOnce(async body => ({ ...receiptFor(JSON.parse(body)), payload_sha256: 'bad' }));
  expect((await client.retry(source.user_id, config, confirmed)).failed).toBe(1);
  expect((await client.retry(source.user_id, config, confirmed)).sent).toBe(1);
  expect(deps.postCapture.mock.calls.every(call => call[0] === deps.postCapture.mock.calls[0][0])).toBe(true);
});
test('corrupt durable content fails closed instead of silently erasing/replacing it', async () => {
  const { client, deps, raw, setRaw } = setup();
  deps.postCapture.mockRejectedValueOnce(new Error('lost'));
  await expect(client.capture(source, config, confirmed)).rejects.toThrow();
  const damaged = JSON.parse(raw()!); damaged.items[0].payload_json = damaged.items[0].payload_json.replace('22.1', '23.1');
  setRaw(JSON.stringify(damaged));
  await expect(client.retry(source.user_id, config, confirmed)).rejects.toThrow();
  expect(deps.postCapture).toHaveBeenCalledTimes(1);
});
test('definitive old-execution conflict retires an unfilled intent; next explicit action gets a new ID', async () => {
  const { client, deps, raw } = setup();
  deps.capturePosition.mockRejectedValueOnce(new Error('no fresh fix'));
  await expect(client.capture(source, config, confirmed)).rejects.toThrow();
  deps.requestContext.mockRejectedValueOnce(new UavCaptureTransferError('CONTEXT_IDENTITY_CONFLICT'));
  await expect(client.capture(source, config, confirmed)).rejects.toThrow('CONTEXT_IDENTITY_CONFLICT');
  expect(JSON.parse(raw()!).items[0].retired_reason).toBe('CONTEXT_IDENTITY_CONFLICT');
  expect((await client.retry(source.user_id, config, confirmed)).remaining).toBe(0);
  deps.requestContext.mockImplementationOnce(async identity => ({ ...context, ...identity, carrier_execution_id: 'next' }));
  await client.capture(source, config, confirmed);
  const sent = JSON.parse(deps.postCapture.mock.calls[0][0]);
  expect(sent.capture_id).toBe('capture2'); expect(sent.carrier_execution_id).toBe('next');
  expect(JSON.parse(raw()!).items[0].context.carrier_execution_id).toBe('execution1');
});
test('unknown GET outcome retains capture identity on next explicit action', async () => {
  const { client, deps } = setup();
  deps.requestContext.mockRejectedValueOnce(new Error('timeout'));
  await expect(client.capture(source, config, confirmed)).rejects.toThrow();
  await client.capture(source, config, confirmed);
  expect(deps.createId).toHaveBeenCalledTimes(1);
  expect(deps.requestContext.mock.calls[0][0].capture_id).toBe(deps.requestContext.mock.calls[1][0].capture_id);
});
test('retired intents do not fill the pending outbox limit', async () => {
  const { client, deps } = setup();
  deps.requestContext.mockRejectedValue(new UavCaptureTransferError('NO_COLLECTION_CONTEXT'));
  for (let i = 0; i < 22; i++) await expect(client.capture(source, config, confirmed)).rejects.toThrow('NO_COLLECTION_CONTEXT');
  deps.requestContext.mockImplementation(async identity => ({ ...context, ...identity }));
  await client.capture(source, config, confirmed);
  expect(deps.postCapture).toHaveBeenCalledTimes(1);
});
test('real HTTP adapter uses v2 endpoints and parses matching receipt without v1 fallback', async () => {
  const state = setup();
  const fetchImpl = jest.fn(async (url: string, options: any) => ({ ok: true, json: async () => {
    if (options.method === 'GET') return { ...context, capture_id: 'capture1' };
    return receiptFor(JSON.parse(options.body));
  } }));
  const client = createUavArrivalCaptureClient({ ...state.deps, requestContext: undefined, postCapture: undefined, fetchImpl: fetchImpl as any });
  await client.capture(source, config, confirmed);
  expect(fetchImpl.mock.calls[0][0]).toContain('/api/v2/mission-context?user_id=user1&request_id=request1&capture_id=capture1');
  expect(fetchImpl.mock.calls[0][1].method).toBe('GET');
  expect(fetchImpl.mock.calls[1][0]).toBe(config.baseUrl + '/api/v2/rescue-captures');
  expect(fetchImpl.mock.calls[1][1].method).toBe('POST');
  expect(fetchImpl.mock.calls).toHaveLength(2);
});
test('known HTTP context rejection does not request GPS and preserves its retired intent', async () => {
  const state = setup();
  const fetchImpl = jest.fn(async () => ({ ok: false, json: async () => ({ error: 'not collecting', error_code: 'NO_COLLECTION_CONTEXT' }) }));
  const client = createUavArrivalCaptureClient({ ...state.deps, requestContext: undefined, postCapture: undefined, fetchImpl: fetchImpl as any });
  await expect(client.capture(source, config, confirmed)).rejects.toThrow('NO_COLLECTION_CONTEXT');
  expect(state.deps.capturePosition).not.toHaveBeenCalled();
  expect(JSON.parse(state.raw()!).items[0].retired_reason).toBe('NO_COLLECTION_CONTEXT');
});
