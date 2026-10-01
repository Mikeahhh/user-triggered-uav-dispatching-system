import { formatQuickStartTimestamp } from '../src/services/quickStartMetrics';

test('formats the Android preview capture timestamp and rejects invalid input', () => {
  expect(formatQuickStartTimestamp('not-a-date')).toBe('--:--');
  expect(formatQuickStartTimestamp('2026-08-06T12:00:00.000Z')).not.toBe('--:--');
});
