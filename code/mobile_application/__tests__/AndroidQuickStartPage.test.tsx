import React from 'react';
import { Platform, Text } from 'react-native';
import Renderer from 'react-test-renderer';
import AndroidQuickStartPage from '../pages/AndroidQuickStartPage';
import {
  readTrackingSnapshot, resumePersistentTracking, retryPersistentSync,
  startPersistentTracking, stopPersistentTracking,
} from '../services/persistentTracking';

jest.mock('../services/db/initDb', () => ({
  initDb: jest.fn().mockResolvedValue(undefined),
  getDb: () => ({ executeSql: jest.fn().mockResolvedValue([{ rows: { length: 1, item: () => ({ phone: '+852 26080101' }) } }]) }),
}));
jest.mock('../services/persistentTracking', () => ({
  readTrackingSnapshot: jest.fn(), resumePersistentTracking: jest.fn(),
  retryPersistentSync: jest.fn(), startPersistentTracking: jest.fn(),
  stopPersistentTracking: jest.fn(),
}));
jest.mock('react-native-maps', () => ({ __esModule: true, default: 'MapView', Polyline: 'Polyline' }));
jest.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));

let renderer: Renderer.ReactTestRenderer | null;
let snapshot: any;
const originalOS = Platform.OS;
const visible = () => renderer!.root.findAllByType(Text).map(node => node.props.children).flat().join('\n');
const render = async () => {
  await Renderer.act(async () => { renderer = Renderer.create(<AndroidQuickStartPage />); });
};
const advance = async () => { await Renderer.act(async () => { jest.advanceTimersByTime(1000); }); };

beforeEach(() => {
  jest.useFakeTimers();
  jest.clearAllMocks();
  Object.defineProperty(Platform, 'OS', { configurable: true, value: 'android' });
  snapshot = {
    databasePath: '/synthetic/location_tracker.db', serviceRunning: false, session: null,
    pendingCount: 0, pendingStops: [], totalPoints: 0, uploadedPoints: 0, legacyUnboundRoutes: 0, points: [],
  };
  (readTrackingSnapshot as jest.Mock).mockImplementation(async () => ({ ...snapshot }));
  (startPersistentTracking as jest.Mock).mockImplementation(async phone => {
    snapshot = { ...snapshot, serviceRunning: true, session: { session_id: 'fixed-session', phone, state: 'ACTIVE', last_error: '' } };
    return 'fixed-session';
  });
  (stopPersistentTracking as jest.Mock).mockImplementation(async () => {
    snapshot = { ...snapshot, serviceRunning: false, session: null, pendingCount: 1, pendingStops: [{ sessionId: 'fixed-session', error: 'offline' }] };
    return true;
  });
  (retryPersistentSync as jest.Mock).mockResolvedValue(true);
  (resumePersistentTracking as jest.Mock).mockResolvedValue(true);
});

afterEach(async () => {
  if (renderer) await Renderer.act(async () => renderer!.unmount());
  renderer = null;
  jest.clearAllTimers();
  jest.useRealTimers();
  Object.defineProperty(Platform, 'OS', { configurable: true, value: originalOS });
});

test('unmount only removes the view and re-entry restores the native active session', async () => {
  await render();
  await Renderer.act(async () => renderer!.root.findByProps({ testID: 'quick-start-toggle' }).props.onPress());
  expect(startPersistentTracking).toHaveBeenCalledWith('85226080101');
  await Renderer.act(async () => renderer!.unmount());
  renderer = null;
  snapshot.totalPoints = 13;
  snapshot.pendingCount = 14;
  await render();
  expect(visible()).toContain('fixed-session');
  expect(visible()).toContain('13');
  expect(stopPersistentTracking).not.toHaveBeenCalled();
  expect(startPersistentTracking).toHaveBeenCalledTimes(1);
});

test('offline recording reads new native points without invoking network from the page', async () => {
  const fetchSpy = jest.spyOn(global, 'fetch').mockImplementation(() => new Promise(() => {}));
  try {
    await render();
    await Renderer.act(async () => renderer!.root.findByProps({ testID: 'quick-start-toggle' }).props.onPress());
    snapshot.totalPoints = 20;
    snapshot.pendingCount = 21;
    await advance();
    expect(visible()).toContain('20');
    expect(visible()).toContain('persistentTracking.savedLocally');
    expect(fetchSpy).not.toHaveBeenCalled();
  } finally { fetchSpy.mockRestore(); }
});

test('pending stop remains visible after the page is recreated and retries the persistent queue', async () => {
  await render();
  await Renderer.act(async () => renderer!.root.findByProps({ testID: 'quick-start-toggle' }).props.onPress());
  await Renderer.act(async () => renderer!.root.findByProps({ testID: 'quick-start-toggle' }).props.onPress());
  await Renderer.act(async () => renderer!.unmount());
  renderer = null;
  await render();
  expect(visible()).toContain('quickStartPage.stopPending.message');
  await Renderer.act(async () => renderer!.root.findByProps({ testID: 'quick-start-retry-stop' }).props.onPress());
  expect(stopPersistentTracking).toHaveBeenCalledTimes(1);
  expect(retryPersistentSync).toHaveBeenCalledTimes(1);
});

test('a persisted session without a running service is shown as interrupted and requires resume', async () => {
  snapshot.session = { session_id: 'survived', state: 'ACTIVE', last_error: '' };
  await render();
  expect(visible()).toContain('persistentTracking.interrupted');
  await Renderer.act(async () => renderer!.root.findByProps({ testID: 'quick-start-resume' }).props.onPress());
  expect(resumePersistentTracking).toHaveBeenCalledTimes(1);
  expect(startPersistentTracking).not.toHaveBeenCalled();
});

test('unconfirmed sync failures and pending END errors stay visible until resolved', async () => {
  snapshot.syncState = 'ERROR';
  snapshot.latestSyncError = 'The remote point conflicts with the saved original sample';
  snapshot.pendingCount = 2;
  snapshot.pendingStops = [{ sessionId: 'saved-session', error: 'Synchronization returned HTTP 403' }];
  await render();
  expect(visible()).toContain('persistentTracking.sync.ERROR');
  expect(visible()).toContain('The remote point conflicts with the saved original sample');
  expect(visible()).toContain('Synchronization returned HTTP 403');
  snapshot.syncState = 'SYNCED';
  snapshot.latestSyncError = '';
  snapshot.pendingCount = 0;
  snapshot.pendingStops = [];
  await advance();
  expect(visible()).toContain('persistentTracking.sync.SYNCED');
  expect(visible()).not.toContain('Synchronization returned HTTP 403');
});
