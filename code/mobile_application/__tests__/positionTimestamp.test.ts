import { getPositionCaptureTime } from '../src/services/positionTimestamp';

test('preserves the actual capture timestamp of a cached location', () => {
  expect(getPositionCaptureTime({ timestamp: 1_788_912_000_000 })).toEqual({
    milliseconds: 1_788_912_000_000,
    iso: new Date(1_788_912_000_000).toISOString(),
  });
});

test.each([undefined, null, NaN, Infinity, -1, 0, '1788912000000', 1.5, 9e15])(
  'does not invent a current time for an invalid sample: %s', timestamp => {
    expect(() => getPositionCaptureTime({ timestamp })).toThrow('capture time');
  },
);
