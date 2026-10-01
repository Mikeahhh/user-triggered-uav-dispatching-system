import {
  buildEventBookingRecord,
  createTimeBasedEventId,
  estimateBookingTime,
  routeDistanceMetres,
  parseLocalCalendarDate,
  parseLocalDateTime,
  type EventBookingItem,
} from '../src/services/eventBookingRecord';

const route = [
  {latitude: 0, longitude: 0},
  {latitude: 0, longitude: 0.03597281454898152},
];
const input = {
  title: '  Ridge route  ',
  selectedDate: new Date(2026, 8, 30, 12),
  startTime: new Date(2000, 0, 1, 8, 5),
  waypoints: route,
  createdAt: '2026-09-28T01:00:00.000Z',
};

describe('Automatic booking finish', () => {
  test('estimates a four kilometre route at four kilometres per hour', () => {
    expect(routeDistanceMetres(route)).toBeCloseTo(4000, 5);
    const record = buildEventBookingRecord(input);
    expect(record.title).toBe('Ridge route');
    expect(record.walkingSpeedKmh).toBe(4);
    expect(record.estimatedDurationMinutes).toBe(60);
    expect(record.endTime).toBe('09:05');
    expect(record.expectedEndAtMs).toBe(new Date(2026, 8, 30, 9, 5).getTime());
    expect(record.createdAt).toBe(input.createdAt);
    expect(record.waypoints).toEqual(route);
    expect(record.waypoints[0]).not.toBe(route[0]);
  });

  test.each([[2026, 11, 31, '2027-01-01'], [2028, 1, 28, '2028-02-29']])(
    'keeps the full deadline when a route crosses midnight', (year, month, day, endDate) => {
      const record = buildEventBookingRecord({...input,
        selectedDate: new Date(year, month, day, 12), startTime: new Date(2000, 0, 1, 23, 30)});
      expect(record.endDate).toBe(endDate);
      expect(record.endTime).toBe('00:30');
      expect(record.expectedEndAtMs).toBe(new Date(year, month, day, 23, 30).getTime() + 3600000);
    },
  );

  test('adds every route segment including the return leg', () => {
    const record = buildEventBookingRecord({...input, waypoints: [...route, route[0]]});
    expect(record.routeDistanceM).toBeCloseTo(8000, 5);
    expect(record.estimatedDurationMinutes).toBe(120);
  });

  test('recalculates after the route or departure changes and retains creation time', () => {
    const original = buildEventBookingRecord(input);
    const edited = buildEventBookingRecord({...input,
      startTime: new Date(2000, 0, 1, 10), waypoints: [...route, route[0]]});
    expect(edited.expectedEndAtMs).not.toBe(original.expectedEndAtMs);
    expect(edited.endTime).toBe('12:00');
    expect(edited.createdAt).toBe(input.createdAt);
  });

  test('rounds a partial minute upward', () => {
    const value = estimateBookingTime(input.selectedDate, input.startTime,
      [{latitude: 0, longitude: 0}, {latitude: 0, longitude: 0.0001}]);
    expect(value.estimatedDurationMinutes).toBe(1);
  });

  test.each([[], [route[0]], [route[0], route[0]],
    [route[0], {latitude: 91, longitude: 0}],
    [route[0], {latitude: 0, longitude: NaN}],
    [route[0], {latitude: 0, longitude: 181}]].map(waypoints => [waypoints]))('rejects an invalid route %p', waypoints => {
      expect(() => buildEventBookingRecord({...input, waypoints})).toThrow();
    });

  test('rejects an invalid departure date', () => {
    expect(() => buildEventBookingRecord({...input, selectedDate: new Date(NaN)})).toThrow();
  });

  test('parses existing local booking dates', () => {
    expect(parseLocalCalendarDate('2027-01-01').getDate()).toBe(1);
    expect(parseLocalDateTime('2027-01-01', '23:45').getHours()).toBe(23);
    const legacy: EventBookingItem = {
      id: 'event_legacy', title: 'Legacy route', date: '2026-12-31', startTime: '08:00',
      endTime: '09:00', waypoints: [], createdAt: input.createdAt,
    };
    expect(legacy.expectedEndAtMs).toBeUndefined();
  });
});

describe('Event Booking IDs', () => {
  test('uses the current epoch milliseconds when it is available', () => {
    expect(createTimeBasedEventId(1_800_000_000_000, ['event_1'])).toBe(
      'event_1800000000000',
    );
  });

  test('increments until it finds an unused ID', () => {
    expect(
      createTimeBasedEventId(1_800_000_000_000, [
        'event_1800000000000',
        'event_1800000000001',
        'event_42',
      ]),
    ).toBe('event_1800000000002');
  });

  test.each([Number.NaN, -1, 1.5, Number.MAX_SAFE_INTEGER + 1])(
    'rejects an invalid timestamp: %s',
    value => {
      expect(() => createTimeBasedEventId(value, [])).toThrow();
    },
  );
});
