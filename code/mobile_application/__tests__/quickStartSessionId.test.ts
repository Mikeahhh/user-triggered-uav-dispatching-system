import { createQuickStartSessionIdGenerator } from '../services/quickStartSessionId';

describe('Quick Start session IDs', () => {
  test('uses epoch milliseconds instead of the number of stored routes', () => {
    const generateSessionId = createQuickStartSessionIdGenerator();

    expect(generateSessionId(1_800_000_000_000)).toBe(
      'session_1800000000000',
    );
  });

  test('allocates unique IDs for multiple sessions in the same millisecond', () => {
    const generateSessionId = createQuickStartSessionIdGenerator();

    expect([
      generateSessionId(1_800_000_000_000),
      generateSessionId(1_800_000_000_000),
      generateSessionId(1_800_000_000_000),
    ]).toEqual([
      'session_1800000000000',
      'session_1800000000001',
      'session_1800000000002',
    ]);
  });

  test('does not reuse an ID when the device clock moves backwards', () => {
    const generateSessionId = createQuickStartSessionIdGenerator();

    expect(generateSessionId(1_800_000_000_100)).toBe(
      'session_1800000000100',
    );
    expect(generateSessionId(1_800_000_000_000)).toBe(
      'session_1800000000101',
    );
  });

  test.each([Number.NaN, -1, 1.5, Number.MAX_SAFE_INTEGER + 1])(
    'rejects an invalid timestamp: %s',
    value => {
      const generateSessionId = createQuickStartSessionIdGenerator();
      expect(() => generateSessionId(value)).toThrow();
    },
  );
});
