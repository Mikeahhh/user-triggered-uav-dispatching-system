import { canonicalV1PayloadJson, hashV1Payload } from '../services/rescueRecordHash';


const vectors: Array<{
  name: string;
  payload: Record<string, unknown>;
  canonical: string;
  sha256: string;
}> = require('./fixtures/rescueRecordV1Golden.json');

test.each(vectors)('matches Python v1 bytes and SHA-256: $name', vector => {
  expect(canonicalV1PayloadJson(vector.payload)).toBe(vector.canonical);
  expect(hashV1Payload(vector.payload)).toBe(vector.sha256);
});

test('rejects an unpaired surrogate instead of hashing different UTF-8 bytes', () => {
  const payload = { ...vectors[0].payload, device: '\ud800' };
  expect(() => hashV1Payload(payload)).toThrow('Unicode surrogate');
});

test('does not relax the existing mission/user/request join', () => {
  const payload = { ...vectors[0].payload, mission_id: 'AUDIT_USER/different' };
  expect(() => hashV1Payload(payload)).toThrow('mission/user/request mismatch');
});

test.each(['2026-02-30T12:00:00Z', '2026-02-29T12:00:00Z',
  '2026-09-09T24:00:00Z', '0000-01-01T00:00:00Z'])(
  'does not normalize an invalid UTC date: %s', timestamp => {
    expect(() => hashV1Payload({ ...vectors[0].payload, captured_at: timestamp })).toThrow('captured_at');
  },
);

test.each(['status', 'device', 'test_mode', 'gps_points'])(
  'explicit null is not a missing default: %s', field => {
    expect(() => hashV1Payload({ ...vectors[0].payload, [field]: null })).toThrow();
  },
);
