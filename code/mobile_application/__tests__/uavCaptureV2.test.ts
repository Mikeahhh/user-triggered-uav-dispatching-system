import { canonicalV2PayloadJson, hashV2Payload, validateCaptureV2Payload, validateCaptureReceipt, validateMissionContext } from '../services/uavCaptureV2';
import { source, context, position, makePayload, receiptFor } from '../testSupport/uavCaptureFixtures';


test('keeps source SOS immutable and carries exactly one fresh sample under a separate execution', () => {
  const before = JSON.stringify(source);
  const payload = makePayload();
  expect(Object.keys(payload)).toHaveLength(16);
  expect(payload.source_request).toEqual(source);
  expect(JSON.stringify(source)).toBe(before);
  expect(payload.carrier_mission_id).not.toBe(payload.source_request.mission_id);
  expect(payload).not.toHaveProperty('gps_points');
  expect(payload.latitude).toBe(position.latitude);
});
test('normalizes zeros only under v2 and uses Python float encoding recursively', () => {
  const payload = makePayload(); payload.latitude = -0; payload.source_request.gps_points[0].longitude = -0;
  const canonical = canonicalV2PayloadJson(payload);
  expect(canonical).toContain('"latitude":0.0');
  expect(canonical).toContain('"longitude":0.0');
  expect(canonical).not.toContain('-0.0');
  expect(hashV2Payload(payload)).toMatch(/^[0-9a-f]{64}$/);
});
test.each([
  { schema_version: 1 }, { accuracy: undefined }, { latitude: NaN }, { longitude: 181 }, { latitude: true },
  { captured_at: '2026-02-30T01:01:01.123Z' }, { captured_at: '2026-09-09T01:01:01.123001Z' },
  { client_timestamp_ms: position.client_timestamp_ms + 1 }, { capture_started_at_ms: position.client_timestamp_ms + 1 },
  { capture_id: 'capture1\n' }, { user_id: 'another' }, { carrier_mission_id: 'another/booking' },
  { device: '\ud800' }, { test_mode: null }, { mission_id: 'user1/request1' },
])('rejects malformed capture %p', change => expect(() => validateCaptureV2Payload({ ...makePayload(), ...change })).toThrow());
test.each(['device', 'status', 'gps_points', 'test_mode', 'accuracy'])('does not accept missing source field %s', key => {
  const payload: any = makePayload(); delete payload.source_request[key];
  expect(() => validateCaptureV2Payload(payload)).toThrow();
});
test('rejects unknown source/point fields rather than dropping data', () => {
  const payload = makePayload();
  expect(() => validateCaptureV2Payload({ ...payload, source_request: { ...source, unknown: 1 } })).toThrow();
  payload.source_request.gps_points[0] = { ...payload.source_request.gps_points[0], accuracy: 2 } as any;
  expect(() => validateCaptureV2Payload(payload)).toThrow();
});
test('requires all matching context and receipt identity/content fields', () => {
  expect(validateMissionContext(context, context)).toEqual(context);
  expect(() => validateMissionContext({ ...context, capture_id: 'other' }, context)).toThrow();
  expect(validateCaptureReceipt(receiptFor(), makePayload()).status).toBe('STORED');
  for (const key of ['capture_id', 'request_id', 'context_id', 'carrier_mission_id', 'carrier_execution_id', 'payload_sha256']) {
    expect(() => validateCaptureReceipt({ ...receiptFor(), [key]: 'other' }, makePayload())).toThrow();
  }
});

test.each(['device', 'status', 'gps_points', 'test_mode', 'accuracy'])('does not supply defaults for explicit undefined source field %s', key => {
  const payload: any = makePayload(); payload.source_request[key] = undefined;
  expect(() => validateCaptureV2Payload(payload)).toThrow();
});
