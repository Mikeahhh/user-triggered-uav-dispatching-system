import React from 'react';
import { Alert, PermissionsAndroid, Platform, Text } from 'react-native';
import ReactTestRenderer from 'react-test-renderer';
import Geolocation from '@react-native-community/geolocation';
import QuickStartPage from '../pages/QuickStartPage';
import { getDb, initDb } from '../services/db/initDb';

jest.mock('@react-native-community/geolocation', () => ({
  getCurrentPosition: jest.fn(),
}));

jest.mock('react-native-maps', () => ({
  __esModule: true,
  default: 'MapView',
  Polyline: 'Polyline',
}));

jest.mock('../services/db/initDb', () => ({
  initDb: jest.fn(),
  getDb: jest.fn(),
}));

jest.mock('../services/db/firebaseRealtimeDatabase', () => ({
  buildRealtimeDatabaseRestUrl: (...segments: string[]) =>
    `https://quick-start.test/${segments.join('/')}.json`,
}));

jest.mock('react-i18next', () => {


  const t = (key: string) => key;
  return { useTranslation: () => ({ t }) };
});

type Position = Parameters<
  Parameters<typeof Geolocation.getCurrentPosition>[0]
>[0];
type LocationFailure = { code: number; message: string };

const firstPosition = {
  coords: {
    latitude: 22.352,
    longitude: 114.183,
    accuracy: 3,
    altitude: 20,
    speed: 0,
    heading: 0,
  },
  timestamp: 1_800_000_000_000,
} as Position;

const originalFetch = global.fetch;
const originalPlatform = Platform.OS;
let renderer: ReactTestRenderer.ReactTestRenderer | undefined;
let executeSql: jest.Mock;
let fetchMock: jest.Mock;
let alertSpy: jest.SpyInstance;
let locationSuccess: (position: Position) => void;
let locationFailure: (error: LocationFailure) => void;
let pendingStart: Promise<void> | undefined;

const visibleText = () =>
  renderer!.root
    .findAllByType(Text)
    .map(node => String(node.props.children))
    .join('\n');

const toggle = () =>
  renderer!.root.findByProps({ testID: 'quick-start-toggle' });

const localWrites = () =>
  executeSql.mock.calls.filter(([sql]) => /^INSERT\b/.test(sql));

const startedAlert = () =>
  alertSpy.mock.calls.some(
    ([title]) => title === 'quickStartPage.alert.trackingStarted.title',
  );

const renderPage = async () => {
  await ReactTestRenderer.act(async () => {
    renderer = ReactTestRenderer.create(<QuickStartPage />);
  });
};

const beginStart = async () => {
  await ReactTestRenderer.act(async () => {


    pendingStart = toggle().props.onPress();
    await Promise.resolve();
  });
};

const provideFirstPosition = async (position = firstPosition) => {
  await ReactTestRenderer.act(async () => {
    locationSuccess(position);
    await pendingStart;
  });
};

beforeEach(() => {
  jest.useFakeTimers();
  jest.setSystemTime(firstPosition.timestamp);
  jest.clearAllMocks();
  Object.defineProperty(Platform, 'OS', { configurable: true, value: 'android' });
  jest
    .spyOn(PermissionsAndroid, 'request')
    .mockResolvedValue(PermissionsAndroid.RESULTS.GRANTED);
  alertSpy = jest.spyOn(Alert, 'alert').mockImplementation(() => {});
  jest.spyOn(console, 'warn').mockImplementation(() => {});
  jest.spyOn(console, 'error').mockImplementation(() => {});
  (initDb as jest.Mock).mockResolvedValue(undefined);
  executeSql = jest.fn().mockImplementation(async (sql: string) => {
    if (sql.startsWith('SELECT phone')) {
      return [{ rows: { length: 1, item: () => ({ phone: '26080101' }) } }];
    }
    if (sql.startsWith('SELECT COUNT')) {
      return [{ rows: { length: 1, item: () => ({ count: 0 }) } }];
    }
    return [{ insertId: 7, rowsAffected: 1 }];
  });
  (getDb as jest.Mock).mockReturnValue({ executeSql });
  fetchMock = jest.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({}),
  });
  global.fetch = fetchMock;
  (Geolocation.getCurrentPosition as jest.Mock).mockImplementation(
    (success, failure) => {
      locationSuccess = success;
      locationFailure = failure;
    },
  );
  pendingStart = undefined;
});

afterEach(async () => {
  if (renderer) {
    await ReactTestRenderer.act(async () => renderer!.unmount());
    renderer = undefined;
  }
  jest.clearAllTimers();
  jest.useRealTimers();
  jest.restoreAllMocks();
  Object.defineProperty(Platform, 'OS', {
    configurable: true,
    value: originalPlatform,
  });
  global.fetch = originalFetch;
});

test('waits for the first valid fix before creating a route or ACTIVE session', async () => {
  await renderPage();
  await beginStart();

  expect(visibleText()).toContain('quickStartPage.status.locating');
  expect(visibleText()).not.toContain('quickStartPage.status.active');
  expect(toggle().props.disabled).toBe(true);
  expect(localWrites()).toEqual([]);
  expect(fetchMock).not.toHaveBeenCalled();
  expect(startedAlert()).toBe(false);

  await ReactTestRenderer.act(async () => {
    jest.advanceTimersByTime(5000);
  });
  expect(Geolocation.getCurrentPosition).toHaveBeenCalledTimes(1);
  expect(fetchMock).not.toHaveBeenCalled();
});

test('keeps startup pending while Android permission is unresolved', async () => {
  let resolvePermission!: (value: typeof PermissionsAndroid.RESULTS.GRANTED) => void;
  (PermissionsAndroid.request as jest.Mock).mockReturnValue(
    new Promise(resolve => {
      resolvePermission = resolve;
    }),
  );
  await renderPage();
  await beginStart();

  expect(visibleText()).toContain('quickStartPage.status.locating');
  expect(Geolocation.getCurrentPosition).not.toHaveBeenCalled();
  expect(localWrites()).toEqual([]);
  expect(fetchMock).not.toHaveBeenCalled();

  await ReactTestRenderer.act(async () => {
    resolvePermission(PermissionsAndroid.RESULTS.GRANTED);
  });
  expect(Geolocation.getCurrentPosition).toHaveBeenCalledTimes(1);
  expect(fetchMock).not.toHaveBeenCalled();
});

test('reports permission denial as not ready without creating local or cloud records', async () => {
  (PermissionsAndroid.request as jest.Mock).mockResolvedValue(
    PermissionsAndroid.RESULTS.DENIED,
  );
  await renderPage();
  await beginStart();
  await ReactTestRenderer.act(async () => pendingStart);

  expect(Geolocation.getCurrentPosition).not.toHaveBeenCalled();
  expect(localWrites()).toEqual([]);
  expect(fetchMock).not.toHaveBeenCalled();
  expect(visibleText()).toContain('quickStartPage.status.notReady');
  expect(toggle().props.disabled).toBe(false);
  expect(startedAlert()).toBe(false);
  expect(alertSpy).toHaveBeenCalled();
});

test.each([
  [1, 'permission denied'],
  [2, 'position unavailable'],
  [3, 'position timeout'],
])('reports first-fix failure %s as not ready without writing records', async (code, message) => {
  await renderPage();
  await beginStart();
  await ReactTestRenderer.act(async () => {
    locationFailure({ code, message });
    await pendingStart;
  });

  expect(localWrites()).toEqual([]);
  expect(fetchMock).not.toHaveBeenCalled();
  expect(visibleText()).toContain('quickStartPage.status.notReady');
  expect(toggle().props.disabled).toBe(false);
  expect(startedAlert()).toBe(false);
  expect(alertSpy).toHaveBeenCalled();
});

test.each([
  [Number.NaN, 114.183],
  [22.352, Number.POSITIVE_INFINITY],
  [90.01, 114.183],
  [22.352, -180.01],
])('rejects invalid first-fix coordinates (%s, %s)', async (latitude, longitude) => {
  await renderPage();
  await beginStart();
  await provideFirstPosition({
    ...firstPosition,
    coords: { ...firstPosition.coords, latitude, longitude },
  });

  expect(localWrites()).toEqual([]);
  expect(fetchMock).not.toHaveBeenCalled();
  expect(visibleText()).toContain('quickStartPage.status.notReady');
  expect(startedAlert()).toBe(false);
  expect(toggle().props.disabled).toBe(false);
});

test('publishes ACTIVE with point_1 only after saving the first route and point', async () => {
  await renderPage();
  await beginStart();
  await provideFirstPosition();

  expect(fetchMock).toHaveBeenCalledTimes(1);
  const [url, options] = fetchMock.mock.calls[0];
  expect(url).toMatch(/\/users\/26080101\/QuickStartSessions\/session_[^/]+\.json$/);
  expect(options.method).toBe('PUT');
  const session = JSON.parse(options.body);
  expect(session).toEqual(
    expect.objectContaining({
      status: 'ACTIVE',
      startTime: expect.any(String),
      points: {
        point_1: expect.objectContaining({
          latitude: firstPosition.coords.latitude,
          longitude: firstPosition.coords.longitude,
          timestamp: expect.any(Number),
        }),
      },
    }),
  );
  const writes = localWrites();
  expect(writes).toHaveLength(2);
  expect(writes[0][0]).toMatch(/^INSERT INTO routes/);
  expect(writes[1][0]).toMatch(/^INSERT INTO locations/);
  expect(writes[1][1].slice(0, 3)).toEqual([7, 22.352, 114.183]);
  const pointSaveIndex = executeSql.mock.calls.findIndex(([sql]) =>
    sql.startsWith('INSERT INTO locations'),
  );
  expect(executeSql.mock.invocationCallOrder[pointSaveIndex]).toBeLessThan(
    fetchMock.mock.invocationCallOrder[0],
  );
  expect(visibleText()).toContain('quickStartPage.status.active');
  expect(startedAlert()).toBe(true);
});

test('rejects a cached first fix and requests an uncached location', async () => {
  await renderPage();
  await beginStart();
  expect((Geolocation.getCurrentPosition as jest.Mock).mock.calls[0][2]).toEqual(
    expect.objectContaining({ maximumAge: 0 }),
  );
  await provideFirstPosition({ ...firstPosition, timestamp: firstPosition.timestamp - 120_000 });
  expect(localWrites()).toEqual([]);
  expect(fetchMock).not.toHaveBeenCalled();
  expect(visibleText()).toContain('quickStartPage.status.notReady');
});

test('preserves a fresh first sample time in SQLite and the initial cloud point', async () => {
  await renderPage();
  await beginStart();
  const timestamp = firstPosition.timestamp + 400;
  jest.setSystemTime(firstPosition.timestamp + 1000);
  await provideFirstPosition({ ...firstPosition, timestamp });
  const session = JSON.parse(fetchMock.mock.calls[0][1].body);
  expect(session.startTime).toBe(new Date(timestamp).toISOString());
  expect(session.points.point_1.timestamp).toBe(timestamp);
  expect(session.points.point_1.timestampISO).toBe(new Date(timestamp).toISOString());
  const pointWrite = localWrites().find(([sql]) => sql.startsWith('INSERT INTO locations'));
  expect(pointWrite![1][3]).toBe(new Date(timestamp).toISOString());
});

test('a first sample without capture time cannot create ACTIVE or local route data', async () => {
  await renderPage();
  await beginStart();
  await provideFirstPosition({ ...firstPosition, timestamp: NaN });
  expect(fetchMock).not.toHaveBeenCalled();
  expect(localWrites()).toEqual([]);
  expect(visibleText()).toContain('quickStartPage.status.notReady');
});

test('waits for successful cloud publication before reporting active tracking', async () => {
  let resolveFetch!: (value: { ok: boolean; status: number }) => void;
  fetchMock.mockReturnValue(
    new Promise(resolve => {
      resolveFetch = resolve;
    }),
  );
  await renderPage();
  await beginStart();
  await ReactTestRenderer.act(async () => {
    locationSuccess(firstPosition);
  });

  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(visibleText()).not.toContain('quickStartPage.status.active');
  expect(startedAlert()).toBe(false);
  await ReactTestRenderer.act(async () => {
    jest.advanceTimersByTime(5000);
  });
  expect(Geolocation.getCurrentPosition).toHaveBeenCalledTimes(1);

  await ReactTestRenderer.act(async () => {
    resolveFetch({ ok: true, status: 200 });
    await pendingStart;
  });
  expect(visibleText()).toContain('quickStartPage.status.active');
  expect(startedAlert()).toBe(true);
});

test('does not start periodic acquisition after a rejected cloud session write', async () => {
  fetchMock.mockResolvedValue({ ok: false, status: 503 });
  await renderPage();
  await beginStart();
  await provideFirstPosition();

  expect(visibleText()).not.toContain('quickStartPage.status.active');
  expect(startedAlert()).toBe(false);
  expect(toggle().props.disabled).toBe(false);
  await ReactTestRenderer.act(async () => {
    jest.advanceTimersByTime(15000);
  });
  expect(Geolocation.getCurrentPosition).toHaveBeenCalledTimes(1);
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

test('allows a new first-fix attempt after permission was denied', async () => {
  (PermissionsAndroid.request as jest.Mock)
    .mockResolvedValueOnce(PermissionsAndroid.RESULTS.DENIED)
    .mockResolvedValue(PermissionsAndroid.RESULTS.GRANTED);
  await renderPage();
  await beginStart();
  await ReactTestRenderer.act(async () => pendingStart);
  expect(visibleText()).toContain('quickStartPage.status.notReady');
  expect(fetchMock).not.toHaveBeenCalled();

  await beginStart();
  await provideFirstPosition();

  expect(PermissionsAndroid.request).toHaveBeenCalledTimes(2);
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(visibleText()).toContain('quickStartPage.status.active');
  expect(startedAlert()).toBe(true);
});

test('stores the next periodic fix as point_2 under the successful session', async () => {
  await renderPage();
  await beginStart();
  await provideFirstPosition();
  const initialUrl = fetchMock.mock.calls[0][0];

  await ReactTestRenderer.act(async () => {
    jest.advanceTimersByTime(5000);
  });
  expect(Geolocation.getCurrentPosition).toHaveBeenCalledTimes(2);
  await ReactTestRenderer.act(async () => {
    locationSuccess({
      ...firstPosition,
      coords: { ...firstPosition.coords, latitude: 22.353 },
      timestamp: Date.now(),
    });
  });

  expect(fetchMock).toHaveBeenCalledTimes(2);
  const [nextUrl, nextOptions] = fetchMock.mock.calls[1];
  expect(nextUrl).toBe(initialUrl.replace(/\.json$/, '/points/point_2.json'));
  expect(JSON.parse(nextOptions.body).latitude).toBe(22.353);
  const writes = localWrites();
  expect(writes).toHaveLength(3);
  expect(writes[2][1][0]).toBe(7);
});

test('finishing an older stop request cannot clear a newly started session', async () => {
  await renderPage();
  await beginStart();
  await provideFirstPosition();
  const oldUrl = fetchMock.mock.calls[0][0];
  let finishOldStop!: (response: { ok: boolean; status: number }) => void;
  fetchMock.mockImplementation((url, options) => {
    if (url === oldUrl && options?.method === 'PATCH') {
      return new Promise(resolve => { finishOldStop = resolve; });
    }
    return Promise.resolve({ ok: true, status: 200 });
  });
  let pendingStop: Promise<void> | undefined;
  await ReactTestRenderer.act(async () => { pendingStop = toggle().props.onPress(); });
  expect(fetchMock).toHaveBeenLastCalledWith(oldUrl, expect.objectContaining({ method: 'PATCH' }));
  await beginStart();
  await provideFirstPosition();
  const newUrl = fetchMock.mock.calls.find(([url, options]) =>
    url !== oldUrl && JSON.parse(options.body).status === 'ACTIVE',
  )![0];
  const newSessionId = newUrl.split('/').pop().replace(/\.json$/, '');
  await ReactTestRenderer.act(async () => {
    finishOldStop({ ok: true, status: 200 });
    await pendingStop;
  });
  expect(visibleText()).toContain(newSessionId);
  expect(visibleText()).toContain('quickStartPage.status.active');
  await ReactTestRenderer.act(async () => { jest.advanceTimersByTime(5000); });
  await ReactTestRenderer.act(async () => {
    locationSuccess({ ...firstPosition, timestamp: Date.now() });
  });
  expect(fetchMock).toHaveBeenLastCalledWith(
    newUrl.replace(/\.json$/, '/points/point_2.json'), expect.anything(),
  );
});

test('accepts new samples at the same coordinates without requiring movement', async () => {
  await renderPage();
  await beginStart();
  await provideFirstPosition();
  await ReactTestRenderer.act(async () => { jest.advanceTimersByTime(5000); });
  await ReactTestRenderer.act(async () => {
    locationSuccess({ ...firstPosition, timestamp: Date.now() });
  });
  expect(localWrites()).toHaveLength(3);
  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual(expect.objectContaining({
    latitude: firstPosition.coords.latitude,
    longitude: firstPosition.coords.longitude,
    timestamp: firstPosition.timestamp + 5000,
  }));
});

test.each([0, -1000, 6000])('rejects duplicate, older or future periodic sample offset %s', async offset => {
  await renderPage();
  await beginStart();
  await provideFirstPosition();
  await ReactTestRenderer.act(async () => { jest.advanceTimersByTime(5000); });
  await ReactTestRenderer.act(async () => {
    locationSuccess({ ...firstPosition, timestamp: firstPosition.timestamp + offset });
  });
  expect(localWrites()).toHaveLength(2);
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(visibleText()).toContain('quickStartPage.stats.points\n1\n');
  expect(visibleText()).toContain('quickStartPage.status.active');
});

test('does not overlap location requests while the previous acquisition is pending', async () => {
  await renderPage();
  await beginStart();
  await provideFirstPosition();
  await ReactTestRenderer.act(async () => { jest.advanceTimersByTime(5000); });
  await ReactTestRenderer.act(async () => { jest.advanceTimersByTime(15000); });
  expect(Geolocation.getCurrentPosition).toHaveBeenCalledTimes(2);
  await ReactTestRenderer.act(async () => {
    locationSuccess({ ...firstPosition, timestamp: Date.now() });
  });
  expect(localWrites()).toHaveLength(3);
  await ReactTestRenderer.act(async () => { jest.advanceTimersByTime(5000); });
  expect(Geolocation.getCurrentPosition).toHaveBeenCalledTimes(3);
});

test('does not publish or display a periodic point when SQLite storage fails', async () => {
  await renderPage();
  await beginStart();
  await provideFirstPosition();
  const saved = executeSql.getMockImplementation()!;
  executeSql.mockImplementation((sql, ...args) => sql.startsWith('INSERT INTO locations')
    ? Promise.reject(new Error('disk full')) : saved(sql, ...args));
  await ReactTestRenderer.act(async () => { jest.advanceTimersByTime(5000); });
  await ReactTestRenderer.act(async () => {
    locationSuccess({ ...firstPosition, timestamp: Date.now() });
  });
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(alertSpy).toHaveBeenCalledWith('quickStartPage.alert.locationError.title', expect.any(String));
});

test('ignores an old pending fix after stop and a new session start', async () => {
  await renderPage();
  await beginStart();
  await provideFirstPosition();
  await ReactTestRenderer.act(async () => { jest.advanceTimersByTime(5000); });
  const lateOldSuccess = locationSuccess;
  await ReactTestRenderer.act(async () => { await toggle().props.onPress(); });
  await beginStart();
  await provideFirstPosition({ ...firstPosition, timestamp: Date.now() });
  const writesBefore = localWrites().length;
  const callsBefore = fetchMock.mock.calls.length;
  await ReactTestRenderer.act(async () => {
    lateOldSuccess({ ...firstPosition, timestamp: Date.now() });
  });
  expect(localWrites()).toHaveLength(writesBefore);
  expect(fetchMock).toHaveBeenCalledTimes(callsBefore);
  expect(visibleText()).toContain('quickStartPage.status.active');
});

test('waits for an already issued point upload before completing that session', async () => {
  await renderPage();
  await beginStart();
  await provideFirstPosition();
  let finishPoint!: (response: { ok: boolean; status: number }) => void;
  fetchMock.mockImplementation(url => url.includes('/points/')
    ? new Promise(resolve => { finishPoint = resolve; })
    : Promise.resolve({ ok: true, status: 200 }));
  await ReactTestRenderer.act(async () => { jest.advanceTimersByTime(5000); });
  await ReactTestRenderer.act(async () => {
    locationSuccess({ ...firstPosition, timestamp: Date.now() });
  });
  await ReactTestRenderer.act(async () => { jest.advanceTimersByTime(10000); });
  expect(Geolocation.getCurrentPosition).toHaveBeenCalledTimes(2);
  let pendingStop: Promise<void> | undefined;
  await ReactTestRenderer.act(async () => { pendingStop = toggle().props.onPress(); });
  expect(fetchMock.mock.calls.filter(([, options]) => options?.method === 'PATCH')).toHaveLength(0);
  await ReactTestRenderer.act(async () => {
    finishPoint({ ok: true, status: 200 });
    await pendingStop;
  });
  const stopOptions = fetchMock.mock.calls[2][1];
  expect(stopOptions.method).toBe('PATCH');
  expect(JSON.parse(stopOptions.body)).toEqual({ status: 'COMPLETED', endTime: expect.any(String) });
});

test.each(['http', 'network'])('retains a retryable stop when cloud completion fails: %s', async failure => {
  await renderPage();
  await beginStart();
  await provideFirstPosition();
  if (failure === 'http') fetchMock.mockResolvedValueOnce({ ok: false, status: 503 });
  else fetchMock.mockRejectedValueOnce(new Error('offline'));
  await ReactTestRenderer.act(async () => { await toggle().props.onPress(); });
  expect(visibleText()).toContain('quickStartPage.stopPending.message');
  expect(alertSpy.mock.calls.some(([title]) => title === 'quickStartPage.alert.trackingStopped.title')).toBe(false);
  const failedOptions = fetchMock.mock.calls[1][1];
  expect(failedOptions.method).toBe('PATCH');
  const retry = renderer!.root.findByProps({ testID: 'quick-start-retry-stop' });
  await ReactTestRenderer.act(async () => { await retry.props.onPress(); });
  expect(fetchMock.mock.calls[2][1]).toEqual(failedOptions);
  expect(visibleText()).not.toContain('quickStartPage.stopPending.message');
});

test('an upload failure retains the local point without claiming cloud success', async () => {
  await renderPage();
  await beginStart();
  await provideFirstPosition();
  fetchMock.mockResolvedValueOnce({ ok: false, status: 503 });
  await ReactTestRenderer.act(async () => { jest.advanceTimersByTime(5000); });
  await ReactTestRenderer.act(async () => {
    locationSuccess({ ...firstPosition, timestamp: Date.now() });
  });
  expect(localWrites()).toHaveLength(3);
  expect(visibleText()).toContain('quickStartPage.stats.points\n2\n');
  expect(alertSpy).toHaveBeenCalledWith(
    'quickStartPage.alert.uploadFailed.title', 'quickStartPage.alert.uploadFailed.message',
  );
  await ReactTestRenderer.act(async () => { jest.advanceTimersByTime(5000); });
  await ReactTestRenderer.act(async () => {
    locationSuccess({ ...firstPosition, timestamp: Date.now() });
  });
  expect(fetchMock.mock.calls[2][0]).toContain('/points/point_3.json');
});

test('a duplicate callback from one request cannot create another point', async () => {
  await renderPage();
  await beginStart();
  await provideFirstPosition();
  await ReactTestRenderer.act(async () => { jest.advanceTimersByTime(5000); });
  const callback = locationSuccess;
  const position = { ...firstPosition, timestamp: Date.now() };
  await ReactTestRenderer.act(async () => { callback(position); callback(position); });
  expect(localWrites()).toHaveLength(3);
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

test('a local save finishing after stop cannot publish a late point', async () => {
  await renderPage();
  await beginStart();
  await provideFirstPosition();
  let finishSave!: (result: unknown) => void;
  const originalSql = executeSql.getMockImplementation()!;
  executeSql.mockImplementation((sql, ...args) => sql.startsWith('INSERT INTO locations')
    ? new Promise(resolve => { finishSave = resolve; }) : originalSql(sql, ...args));
  await ReactTestRenderer.act(async () => { jest.advanceTimersByTime(5000); });
  await ReactTestRenderer.act(async () => { locationSuccess({ ...firstPosition, timestamp: Date.now() }); });
  await ReactTestRenderer.act(async () => { await toggle().props.onPress(); });
  await ReactTestRenderer.act(async () => { finishSave([{ insertId: 8, rowsAffected: 1 }]); });
  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(fetchMock.mock.calls[1][1].method).toBe('PATCH');
  expect(visibleText()).toContain('quickStartPage.stats.points\n1\n');
});

test('ignores a periodic fix returned after unmounting', async () => {
  await renderPage();
  await beginStart();
  await provideFirstPosition();
  await ReactTestRenderer.act(async () => { jest.advanceTimersByTime(5000); });
  const callback = locationSuccess;
  await ReactTestRenderer.act(async () => { renderer!.unmount(); renderer = undefined; });
  await ReactTestRenderer.act(async () => { callback({ ...firstPosition, timestamp: Date.now() }); });
  expect(localWrites()).toHaveLength(2);
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

test('ignores a pending first fix returned after the page has unmounted', async () => {
  await renderPage();
  await beginStart();
  expect(Geolocation.getCurrentPosition).toHaveBeenCalledTimes(1);
  await ReactTestRenderer.act(async () => {
    renderer!.unmount();
    renderer = undefined;
  });

  await provideFirstPosition();

  expect(localWrites()).toEqual([]);
  expect(fetchMock).not.toHaveBeenCalled();
  expect(startedAlert()).toBe(false);
  await ReactTestRenderer.act(async () => {
    jest.advanceTimersByTime(15000);
  });
  expect(Geolocation.getCurrentPosition).toHaveBeenCalledTimes(1);
});
