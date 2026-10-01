jest.mock('../src/services/db/firebaseConfig', () => ({}));

const mockGetApp = jest.fn();

jest.mock('firebase/app', () => ({
  getApp: () => mockGetApp(),
}));

import {
  buildRealtimeDatabaseRestUrl,
  describeRealtimeDatabaseTarget,
  getRealtimeDatabaseUrl,
  resolveRealtimeDatabaseUrl,
} from '../src/services/db/firebaseRealtimeDatabase';

const projectId = 'rescue-drone-fyp-e0c23';

beforeEach(() => {
  mockGetApp.mockReset();
});

test('uses a configured legacy Firebase RTDB HTTPS origin', () => {
  expect(
    resolveRealtimeDatabaseUrl({
      projectId,
      databaseURL:
        'https://rescue-drone-fyp-e0c23-default-rtdb.firebaseio.com/',
    }),
  ).toBe('https://rescue-drone-fyp-e0c23-default-rtdb.firebaseio.com');
});

test('accepts a regional Firebase RTDB HTTPS origin for the same project', () => {
  expect(
    resolveRealtimeDatabaseUrl({
      projectId,
      databaseURL:
        'https://rescue-drone-fyp-e0c23-default-rtdb.asia-southeast1.firebasedatabase.app',
    }),
  ).toBe(
    'https://rescue-drone-fyp-e0c23-default-rtdb.asia-southeast1.firebasedatabase.app',
  );
});

test('derives the current default RTDB origin when databaseURL is absent', () => {
  expect(resolveRealtimeDatabaseUrl({ projectId })).toBe(
    'https://rescue-drone-fyp-e0c23-default-rtdb.firebaseio.com',
  );
});

test('describes explicit versus derived targets without changing resolution', () => {
  expect(describeRealtimeDatabaseTarget({ projectId }).source).toBe('derived');
  const databaseURL = `  https://${projectId}-default-rtdb.firebaseio.com/  `;
  expect(describeRealtimeDatabaseTarget({ projectId, databaseURL })).toEqual({
    url: `https://${projectId}-default-rtdb.firebaseio.com`, source: 'explicit',
  });
});

test.each(['\n', '\t', '\r', '\u0000', '\u007f', ' ', '\u00a0'])(
  'rejects embedded URL whitespace or controls %#', character => {
    const databaseURL = `https://${projectId}-default-rtdb.fire${character}baseio.com`;
    expect(() => resolveRealtimeDatabaseUrl({ projectId, databaseURL })).toThrow();
  },
);

test.each([
  'http://rescue-drone-fyp-e0c23-default-rtdb.firebaseio.com',
  'https://rescue-drone-fyp-e0c23-default-rtdb.firebaseio.com.evil.example',
  'https://another-project-default-rtdb.firebaseio.com',
  'https://rescue-drone-fyp-e0c23-other.firebaseio.com',
  'https://rescue-drone-fyp-e0c23-default-rtdb.firebaseio.com/path',
  'https://rescue-drone-fyp-e0c23-default-rtdb.firebaseio.com?auth=secret',
])('rejects unsafe or mismatched databaseURL %s', databaseURL => {
  expect(() =>
    resolveRealtimeDatabaseUrl({ projectId, databaseURL }),
  ).toThrow();
});

test('reads the initialized Firebase app options and builds an encoded REST URL', () => {
  mockGetApp.mockReturnValue({
    options: {
      projectId,
      databaseURL:
        'https://rescue-drone-fyp-e0c23-default-rtdb.firebaseio.com',
    },
  });

  expect(getRealtimeDatabaseUrl()).toBe(
    'https://rescue-drone-fyp-e0c23-default-rtdb.firebaseio.com',
  );
  expect(
    buildRealtimeDatabaseRestUrl(
      'users',
      '26080101',
      'booked_events',
      'event 1',
    ),
  ).toBe(
    'https://rescue-drone-fyp-e0c23-default-rtdb.firebaseio.com/users/26080101/booked_events/event%201.json',
  );
});

test('rejects an invalid Firebase path segment', () => {
  mockGetApp.mockReturnValue({ options: { projectId } });

  expect(() =>
    buildRealtimeDatabaseRestUrl('users', '2608/0101', 'profile'),
  ).toThrow('Firebase Realtime Database path segment is invalid');
});
