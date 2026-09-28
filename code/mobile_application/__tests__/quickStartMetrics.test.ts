import {
  calculateQuickStartDistance,
  calculateQuickStartDuration,
  formatQuickStartTimestamp,
} from '../services/quickStartMetrics';

test('calculates duration from ISO timestamps without producing NaN', () => {
  const duration = calculateQuickStartDuration([
    { timestamp: '2026-08-06T12:00:00.000Z' },
    { timestamp: '2026-08-06T12:01:31.000Z' },
  ]);

  expect(duration).toBe('2 min');
  expect(duration).not.toContain('NaN');
});

test('fails closed to zero for invalid or reversed timestamps', () => {
  expect(
    calculateQuickStartDuration([
      { timestamp: '9:04 PM' },
      { timestamp: '9:05 PM' },
    ]),
  ).toBe('0 min');
  expect(
    calculateQuickStartDuration([
      { timestamp: '2026-08-06T12:01:00.000Z' },
      { timestamp: '2026-08-06T12:00:00.000Z' },
    ]),
  ).toBe('0 min');
});

test('calculates distance and formats only valid display timestamps', () => {
  expect(
    calculateQuickStartDistance([
      { lat: 22.35, lng: 114.18 },
      { lat: 22.35, lng: 114.18 },
    ]),
  ).toBe('0m');
  expect(formatQuickStartTimestamp('not-a-date')).toBe('--:--');
  expect(formatQuickStartTimestamp('2026-08-06T12:00:00.000Z')).not.toBe('--:--');
});
