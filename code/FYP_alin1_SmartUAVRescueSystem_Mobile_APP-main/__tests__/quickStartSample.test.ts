import { getQuickStartSampleTime } from '../services/quickStartSample';

const base = 1_800_000_000_000;

test('retains the actual sample time inside the acquisition window', () => {
  expect(getQuickStartSampleTime({ timestamp: base + 40 }, base, base + 100)).toEqual({
    milliseconds: base + 40, iso: new Date(base + 40).toISOString(),
  });
});

test.each([base, base + 100])('accepts exact window boundary %s', timestamp => {
  expect(getQuickStartSampleTime({ timestamp }, base, base + 100).milliseconds).toBe(timestamp);
});

test.each([undefined, NaN, 0, Infinity, '1800000000000', base - 1, base + 101])(
  'rejects absent, malformed, cached or future sample time %s', timestamp => {
    expect(() => getQuickStartSampleTime({ timestamp }, base, base + 100)).toThrow();
  },
);

test.each([base + 50, base + 51])('does not accept a duplicate or older sample with previous time %s', previous => {
  expect(() => getQuickStartSampleTime({ timestamp: base + 50 }, base, base + 100, previous)).toThrow();
});

test('accepts a later sample while preserving time instead of using reception time', () => {
  expect(getQuickStartSampleTime({ timestamp: base + 51 }, base, base + 100, base + 50).milliseconds).toBe(base + 51);
});

test.each([[base, base - 1], [NaN, base], [base, Infinity]])('rejects an invalid request clock window %s..%s', (start, end) => {
  expect(() => getQuickStartSampleTime({ timestamp: base }, start, end)).toThrow();
});
