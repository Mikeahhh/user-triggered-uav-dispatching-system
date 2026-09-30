import React from 'react';
import { Alert, StyleSheet, Platform, PermissionsAndroid } from 'react-native';
import { persistCloudRecord } from '../services/persistentTracking';
import ReactTestRenderer from 'react-test-renderer';
import Geolocation from '@react-native-community/geolocation';
import SosPage from '../pages/SosPage';
import { getCurrentSosRequest, restoreCurrentSosFromLegacy, saveCurrentSosRequest } from '../services/currentSosStore';
import { captureCurrentSosForUav, retrySavedUavCaptures } from '../services/uavArrivalCaptureClient';
import { source, receiptFor } from '../testSupport/uavCaptureFixtures';
import { getDb, initDb } from '../services/db/initDb';
import {
  flushPendingUavRescues,
  getUavConnectionConfig,
  getPendingUavRescues,
  queueRescueForUav,
} from '../services/uavRescueClient';
import {
  connectToUavWifi,
  disconnectFromUavWifi,
  subscribeToUavWifiLoss,
  supportsSystemUavWifiSelection,
} from '../services/uavWifiClient';

jest.mock('@react-native-async-storage/async-storage', () =>
  require('@react-native-async-storage/async-storage/jest/async-storage-mock'),
);

jest.mock('../services/persistentTracking', () => ({ persistCloudRecord: jest.fn() }));

jest.mock('@react-native-community/geolocation', () => ({
  getCurrentPosition: jest.fn(),
}));

jest.mock('../services/db/initDb', () => ({
  initDb: jest.fn(),
  getDb: jest.fn(),
}));

jest.mock('../services/db/firebaseRealtimeDatabase', () => ({
  buildRealtimeDatabaseRestUrl: (...segments: string[]) =>
    `https://test-project-default-rtdb.firebaseio.com/${segments.join('/')}.json`,
}));

jest.mock('../services/uavRescueClient', () => ({
  getUavConnectionConfig: jest.fn(),
  getPendingUavRescues: jest.fn(),
  queueRescueForUav: jest.fn(),
  flushPendingUavRescues: jest.fn(),
}));

jest.mock('../services/currentSosStore', () => ({ getCurrentSosRequest: jest.fn(), saveCurrentSosRequest: jest.fn(), restoreCurrentSosFromLegacy: jest.fn() }));
jest.mock('../services/uavArrivalCaptureClient', () => ({ ...jest.requireActual('../services/uavArrivalCaptureClient'), captureCurrentSosForUav: jest.fn(), retrySavedUavCaptures: jest.fn() }));

jest.mock('../services/uavWifiClient', () => ({
  connectToUavWifi: jest.fn(),
  disconnectFromUavWifi: jest.fn(),
  subscribeToUavWifiLoss: jest.fn(),
  supportsSystemUavWifiSelection: jest.fn(),
}));

jest.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) =>
      ({
        'sosPage.legalWarning':
          'Anyone Who Abuses This Emergency Service May Incur Legal Liabilities',
        'sosPage.confirmText':
          'Please confirm this is a real emergency.\nPress SOS to send your current location to the ground station for operator verification.',
        'sosPage.sosButton': 'SOS',
        'sosPage.callButton': 'Call 999 Now',
        'sosPage.slogan': 'Help is just a Ping Away',
      }[key] ?? key),
  }),
}));

beforeEach(() => {
  jest.clearAllMocks();
  jest.spyOn(PermissionsAndroid, 'request').mockResolvedValue(PermissionsAndroid.RESULTS.GRANTED);
  (persistCloudRecord as jest.Mock).mockResolvedValue({ stored: true, synchronized: false });
  (saveCurrentSosRequest as jest.Mock).mockResolvedValue(undefined);
  (getCurrentSosRequest as jest.Mock).mockResolvedValue(source);
  (restoreCurrentSosFromLegacy as jest.Mock).mockResolvedValue(null);
  (captureCurrentSosForUav as jest.Mock).mockResolvedValue(receiptFor());
  (retrySavedUavCaptures as jest.Mock).mockResolvedValue({ sent: 1, failed: 0, differentReceiver: 0, needsLocation: 0, remaining: 0 });
  (getUavConnectionConfig as jest.Mock).mockResolvedValue({
    baseUrl: 'http://127.0.0.1:8080',
    wifiSsid: 'ALIN1-UAV',
    testMode: true,
  });
  (getPendingUavRescues as jest.Mock).mockResolvedValue([]);
  (supportsSystemUavWifiSelection as jest.Mock).mockReturnValue(true);
  (disconnectFromUavWifi as jest.Mock).mockResolvedValue(undefined);
  (subscribeToUavWifiLoss as jest.Mock).mockReturnValue({
    remove: jest.fn(),
  });
});

test('keeps the English call button and slogan in normal scroll flow', async () => {
  let renderer: ReactTestRenderer.ReactTestRenderer;

  await ReactTestRenderer.act(() => {
    renderer = ReactTestRenderer.create(<SosPage />);
  });

  const root = renderer!.root;
  const scrollView = root.findByProps({ testID: 'sos-scroll-view' });
  const callButton = root.findByProps({ testID: 'call-999-button' });
  const slogan = root.findByProps({ testID: 'sos-slogan' });

  const contentStyle = StyleSheet.flatten(
    scrollView.props.contentContainerStyle,
  );
  const callButtonStyle = StyleSheet.flatten(callButton.props.style);
  const sloganStyle = StyleSheet.flatten(slogan.props.style);

  expect(contentStyle.flexGrow).toBe(1);
  expect(callButtonStyle.marginBottom).toBe(16);
  expect(sloganStyle.position).not.toBe('absolute');
  expect(sloganStyle.bottom).toBeUndefined();
});

test('queues the original GPS capture time separately from the SOS request time', async () => {
  const alertSpy = jest.spyOn(Alert, 'alert').mockImplementation(() => {});
  (getUavConnectionConfig as jest.Mock).mockResolvedValue({
    baseUrl: 'http://127.0.0.1:8080',
    wifiSsid: 'ALIN1-UAV',
    testMode: true,
  });
  (initDb as jest.Mock).mockResolvedValue(undefined);
  (getDb as jest.Mock).mockReturnValue({
    executeSql: jest.fn().mockResolvedValue([
      {
        rows: {
          length: 1,
          item: () => ({ phone: '26080101' }),
        },
      },
    ]),
  });
  (Geolocation.getCurrentPosition as jest.Mock).mockImplementation(success =>
    success({
      coords: { latitude: 22.352, longitude: 114.183, accuracy: 3 },
      timestamp: 1_788_912_000_000,
    }),
  );
  (queueRescueForUav as jest.Mock).mockResolvedValue(undefined);
  global.fetch = jest.fn().mockResolvedValue({ ok: true }) as any;

  let renderer: ReactTestRenderer.ReactTestRenderer;
  await ReactTestRenderer.act(async () => {
    renderer = ReactTestRenderer.create(<SosPage />);
  });
  const button = renderer!.root.findByProps({ testID: 'sos-button' });
  await ReactTestRenderer.act(async () => {
    await button.props.onPress();
  });

  const payload = (queueRescueForUav as jest.Mock).mock.calls[0][0];
  expect(payload.test_mode).toBe(true);
  expect(payload.gps_points).toEqual([
    expect.objectContaining({ latitude: 22.352, longitude: 114.183 }),
  ]);
  expect(payload.gps_points).toHaveLength(1);
  expect(payload.captured_at).toBe(new Date(1_788_912_000_000).toISOString());
  expect(payload.gps_points[0].captured_at).toBe(payload.captured_at);
  expect(payload.request_id).toBe(String(payload.client_timestamp_ms));
  expect(global.fetch).not.toHaveBeenCalled();
  expect(persistCloudRecord).toHaveBeenCalledWith('26080101', 'rescue_requests', payload.request_id,
    expect.objectContaining({
      latitude: 22.352, longitude: 114.183,
      timestamp: payload.client_timestamp_ms, status: 'PENDING', device: 'android',
    }));
  expect(queueRescueForUav).toHaveBeenCalledWith(
    payload,
    expect.objectContaining({
      config: expect.objectContaining({ testMode: true }),
    }),
  );
  expect(renderer!.root.findByProps({ testID: 'uav-test-mode-banner' })).toBeTruthy();
  expect(alertSpy).toHaveBeenCalled();
  alertSpy.mockRestore();
});

test('connects, flushes, and always releases the UAV Wi-Fi binding', async () => {
  const alertSpy = jest.spyOn(Alert, 'alert').mockImplementation(() => {});
  (getPendingUavRescues as jest.Mock).mockResolvedValue([
    { payload: { request_id: 'queued_1' } },
  ]);
  (connectToUavWifi as jest.Mock).mockResolvedValue({
    ssid: 'ALIN1-UAV',
    processBound: true,
  });
  (flushPendingUavRescues as jest.Mock).mockResolvedValue({
    sent: [
      {
        request_id: 'queued_1',
        mission_id: '26080101/queued_1',
      },
    ],
    failed: 0,
    expired: 0,
    differentReceiver: 0,
    differentWifi: 0,
    remaining: 0,
  });

  let renderer: ReactTestRenderer.ReactTestRenderer;
  await ReactTestRenderer.act(async () => {
    renderer = ReactTestRenderer.create(<SosPage />);
  });
  await ReactTestRenderer.act(async () => {
    await renderer!.root
      .findByProps({ testID: 'sync-uav-button' })
      .props.onPress();
  });

  expect(connectToUavWifi).toHaveBeenCalledWith('ALIN1-UAV');
  expect(flushPendingUavRescues).toHaveBeenCalledWith(
    expect.objectContaining({
      config: expect.objectContaining({ wifiSsid: 'ALIN1-UAV' }),
      signal: expect.any(Object),
    }),
  );
  expect(disconnectFromUavWifi).toHaveBeenCalledTimes(1);
  expect(
    (connectToUavWifi as jest.Mock).mock.invocationCallOrder[0],
  ).toBeLessThan(
    (flushPendingUavRescues as jest.Mock).mock.invocationCallOrder[0],
  );
  expect(
    (flushPendingUavRescues as jest.Mock).mock.invocationCallOrder[0],
  ).toBeLessThan(
    (disconnectFromUavWifi as jest.Mock).mock.invocationCallOrder[0],
  );
  expect(alertSpy).toHaveBeenCalled();
  alertSpy.mockRestore();
});

test('aborts the flush and releases the binding when the UAV WLAN is lost', async () => {
  const alertSpy = jest.spyOn(Alert, 'alert').mockImplementation(() => {});
  let notifyWifiLost: (() => void) | undefined;
  const removeListener = jest.fn();
  (getPendingUavRescues as jest.Mock).mockResolvedValue([
    { payload: { request_id: 'queued_lost' } },
  ]);
  (subscribeToUavWifiLoss as jest.Mock).mockImplementation(listener => {
    notifyWifiLost = listener;
    return { remove: removeListener };
  });
  (connectToUavWifi as jest.Mock).mockResolvedValue({
    ssid: 'ALIN1-UAV',
    processBound: true,
  });
  (flushPendingUavRescues as jest.Mock).mockImplementation(async options => {
    notifyWifiLost?.();
    expect(options.signal.aborted).toBe(true);


    return {
      sent: [],
      failed: 1,
      expired: 0,
      differentReceiver: 0,
      differentWifi: 0,
      remaining: 1,
    };
  });

  let renderer: ReactTestRenderer.ReactTestRenderer;
  await ReactTestRenderer.act(async () => {
    renderer = ReactTestRenderer.create(<SosPage />);
  });
  await ReactTestRenderer.act(async () => {
    await renderer!.root
      .findByProps({ testID: 'sync-uav-button' })
      .props.onPress();
  });

  expect(removeListener).toHaveBeenCalledTimes(1);
  expect(disconnectFromUavWifi).toHaveBeenCalledTimes(1);
  expect(
    renderer!.root.findByProps({ testID: 'uav-transfer-status' }).props.children,
  ).toBe('sosPage.uav.wifiConnectionLost');
  expect(alertSpy).toHaveBeenCalledWith(
    'sosPage.errorTitle',
    'sosPage.uav.wifiConnectionLost',
  );
  alertSpy.mockRestore();
});

test('reports failure when Android cannot restore the default network', async () => {
  const alertSpy = jest.spyOn(Alert, 'alert').mockImplementation(() => {});
  (getPendingUavRescues as jest.Mock).mockResolvedValue([
    { payload: { request_id: 'queued_unbind' } },
  ]);
  (connectToUavWifi as jest.Mock).mockResolvedValue({
    ssid: 'ALIN1-UAV',
    processBound: true,
  });
  (flushPendingUavRescues as jest.Mock).mockResolvedValue({
    sent: [],
    failed: 1,
    expired: 0,
    differentReceiver: 0,
    differentWifi: 0,
    remaining: 1,
  });
  (disconnectFromUavWifi as jest.Mock).mockRejectedValue(
    new Error('unbind failed'),
  );

  let renderer: ReactTestRenderer.ReactTestRenderer;
  await ReactTestRenderer.act(async () => {
    renderer = ReactTestRenderer.create(<SosPage />);
  });
  await ReactTestRenderer.act(async () => {
    await renderer!.root
      .findByProps({ testID: 'sync-uav-button' })
      .props.onPress();
  });

  expect(
    renderer!.root.findByProps({ testID: 'uav-transfer-status' }).props.children,
  ).toBe('sosPage.uav.disconnectFailed');
  expect(alertSpy).toHaveBeenCalledWith(
    'sosPage.errorTitle',
    'sosPage.uav.disconnectFailed',
  );
  alertSpy.mockRestore();
});

test('does not request Wi-Fi when no session-queued record exists', async () => {
  const alertSpy = jest.spyOn(Alert, 'alert').mockImplementation(() => {});
  let renderer: ReactTestRenderer.ReactTestRenderer;
  await ReactTestRenderer.act(async () => {
    renderer = ReactTestRenderer.create(<SosPage />);
  });
  await ReactTestRenderer.act(async () => {
    await renderer!.root
      .findByProps({ testID: 'sync-uav-button' })
      .props.onPress();
  });
  expect(connectToUavWifi).not.toHaveBeenCalled();
  expect(flushPendingUavRescues).not.toHaveBeenCalled();
  expect(disconnectFromUavWifi).not.toHaveBeenCalled();
  expect(alertSpy).toHaveBeenCalled();
  alertSpy.mockRestore();
});

test('keeps the queue and releases the request when Wi-Fi confirmation fails', async () => {
  const alertSpy = jest.spyOn(Alert, 'alert').mockImplementation(() => {});
  (getPendingUavRescues as jest.Mock).mockResolvedValue([
    { payload: { request_id: 'queued_2' } },
  ]);
  (connectToUavWifi as jest.Mock).mockRejectedValue(
    new Error('system dialog cancelled'),
  );

  let renderer: ReactTestRenderer.ReactTestRenderer;
  await ReactTestRenderer.act(async () => {
    renderer = ReactTestRenderer.create(<SosPage />);
  });
  await ReactTestRenderer.act(async () => {
    await renderer!.root
      .findByProps({ testID: 'sync-uav-button' })
      .props.onPress();
  });

  expect(flushPendingUavRescues).not.toHaveBeenCalled();
  expect(disconnectFromUavWifi).toHaveBeenCalledTimes(1);
  expect(alertSpy).toHaveBeenCalled();
  alertSpy.mockRestore();
});

const mockCaptureUser = () => {
  (initDb as jest.Mock).mockResolvedValue(undefined);
  (getDb as jest.Mock).mockReturnValue({ executeSql: jest.fn().mockResolvedValue([{ rows: { length: 1, item: () => ({ phone: '26080101' }) } }]) });
};
test('new location button waits for explicit UAV Wi-Fi selection and passes only the current user source', async () => {
  jest.spyOn(Alert, 'alert').mockImplementation(() => {});
  mockCaptureUser();
  (connectToUavWifi as jest.Mock).mockResolvedValue({ processBound: true });
  let renderer: ReactTestRenderer.ReactTestRenderer;
  await ReactTestRenderer.act(() => { renderer = ReactTestRenderer.create(<SosPage />); });
  await ReactTestRenderer.act(async () => { await renderer!.root.findByProps({ testID: 'capture-uav-button' }).props.onPress(); });
  expect(getCurrentSosRequest).toHaveBeenCalledWith('26080101');
  expect(captureCurrentSosForUav).toHaveBeenCalledWith(source, expect.any(Object), expect.objectContaining({ wifiConfirmed: true }));
  expect((connectToUavWifi as jest.Mock).mock.invocationCallOrder[0]).toBeLessThan((captureCurrentSosForUav as jest.Mock).mock.invocationCallOrder[0]);
  expect((captureCurrentSosForUav as jest.Mock).mock.invocationCallOrder[0]).toBeLessThan((disconnectFromUavWifi as jest.Mock).mock.invocationCallOrder[0]);
  expect(flushPendingUavRescues).not.toHaveBeenCalled();
});
test('missing original SOS prompts user before network or fresh capture', async () => {
  const alert = jest.spyOn(Alert, 'alert').mockImplementation(() => {});
  mockCaptureUser(); (getCurrentSosRequest as jest.Mock).mockResolvedValue(null);
  let renderer: ReactTestRenderer.ReactTestRenderer;
  await ReactTestRenderer.act(() => { renderer = ReactTestRenderer.create(<SosPage />); });
  await ReactTestRenderer.act(async () => { await renderer!.root.findByProps({ testID: 'capture-uav-button' }).props.onPress(); });
  expect(alert).toHaveBeenCalledWith('sosPage.errorTitle', 'sosPage.uav.currentSosRequired');
  expect(connectToUavWifi).not.toHaveBeenCalled(); expect(captureCurrentSosForUav).not.toHaveBeenCalled();
});
test('saved-location retry calls only the v2 replay path for the current user', async () => {
  jest.spyOn(Alert, 'alert').mockImplementation(() => {});
  mockCaptureUser(); (connectToUavWifi as jest.Mock).mockResolvedValue({ processBound: true });
  let renderer: ReactTestRenderer.ReactTestRenderer;
  await ReactTestRenderer.act(() => { renderer = ReactTestRenderer.create(<SosPage />); });
  await ReactTestRenderer.act(async () => { await renderer!.root.findByProps({ testID: 'retry-capture-uav-button' }).props.onPress(); });
  expect(retrySavedUavCaptures).toHaveBeenCalledWith('26080101', expect.any(Object), expect.objectContaining({ wifiConfirmed: true }));
  expect(captureCurrentSosForUav).not.toHaveBeenCalled(); expect(flushPendingUavRescues).not.toHaveBeenCalled();
});
test('manual network path does not start capture before user confirmation', async () => {
  mockCaptureUser(); (supportsSystemUavWifiSelection as jest.Mock).mockReturnValue(false);
  let confirm: (() => void) | undefined;
  jest.spyOn(Alert, 'alert').mockImplementation((title, _message, buttons) => {
    if (title === 'sosPage.uav.manualWifiTitle') confirm = buttons?.[1].onPress;
  });
  let renderer: ReactTestRenderer.ReactTestRenderer;
  await ReactTestRenderer.act(() => { renderer = ReactTestRenderer.create(<SosPage />); });
  let pending: Promise<void>;
  await ReactTestRenderer.act(async () => { pending = renderer!.root.findByProps({ testID: 'capture-uav-button' }).props.onPress(); });
  expect(captureCurrentSosForUav).not.toHaveBeenCalled(); expect(confirm).toBeDefined();
  await ReactTestRenderer.act(async () => { confirm!(); await pending; });
  expect(captureCurrentSosForUav).toHaveBeenCalledTimes(1); expect(connectToUavWifi).not.toHaveBeenCalled();
});
test('SOS source survives native cloud-queue storage failure without a delivery claim', async () => {
  const alert = jest.spyOn(Alert, 'alert').mockImplementation(() => {});
  mockCaptureUser();
  (Geolocation.getCurrentPosition as jest.Mock).mockImplementation(success => success({ timestamp: 1788912000000, coords: { latitude: 22, longitude: 114, accuracy: 5 } }));
  (persistCloudRecord as jest.Mock).mockRejectedValue(new Error('local queue unavailable'));
  (queueRescueForUav as jest.Mock).mockResolvedValue(undefined);
  let renderer: ReactTestRenderer.ReactTestRenderer;
  await ReactTestRenderer.act(() => { renderer = ReactTestRenderer.create(<SosPage />); });
  await ReactTestRenderer.act(async () => { await renderer!.root.findByProps({ testID: 'sos-button' }).props.onPress(); });
  expect(saveCurrentSosRequest).toHaveBeenCalledWith(expect.objectContaining({ user_id: '26080101', captured_at: new Date(1788912000000).toISOString() }));
  expect(alert).toHaveBeenCalledWith('sosPage.errorTitle', 'sosPage.savedLocallyOnly');
});
test('invalid UAV settings do not prevent saving the new original SOS independently', async () => {
  jest.spyOn(Alert, 'alert').mockImplementation(() => {}); mockCaptureUser();
  (getUavConnectionConfig as jest.Mock).mockRejectedValue(new Error('invalid config'));
  (Geolocation.getCurrentPosition as jest.Mock).mockImplementation(success => success({ timestamp: 1788912000000, coords: { latitude: 22, longitude: 114, accuracy: 5 } }));
  global.fetch = jest.fn().mockResolvedValue({ ok: true });
  let renderer: ReactTestRenderer.ReactTestRenderer;
  await ReactTestRenderer.act(() => { renderer = ReactTestRenderer.create(<SosPage />); });
  await ReactTestRenderer.act(async () => { await renderer!.root.findByProps({ testID: 'sos-button' }).props.onPress(); });
  expect(saveCurrentSosRequest).toHaveBeenCalledWith(expect.objectContaining({ user_id: '26080101', schema_version: 1 }));
  expect(queueRescueForUav).not.toHaveBeenCalled();
});


test('Android SOS is persisted for cloud retry without claiming delivery or using a page network request', async () => {
  const originalOS = Platform.OS;
  Object.defineProperty(Platform, 'OS', { configurable: true, value: 'android' });
  const permission = jest.spyOn(PermissionsAndroid, 'request').mockResolvedValue(PermissionsAndroid.RESULTS.GRANTED);
  const alert = jest.spyOn(Alert, 'alert').mockImplementation(() => {});
  let renderer: ReactTestRenderer.ReactTestRenderer | undefined;
  try {
    mockCaptureUser();
    (persistCloudRecord as jest.Mock).mockResolvedValue({ stored: true, synchronized: false });
    (Geolocation.getCurrentPosition as jest.Mock).mockImplementation(success => success({
      timestamp: Date.now(), coords: { latitude: 22.4, longitude: 114.3, accuracy: 5 },
    }));
    (queueRescueForUav as jest.Mock).mockResolvedValue(undefined);
    global.fetch = jest.fn();
    await ReactTestRenderer.act(async () => { renderer = ReactTestRenderer.create(<SosPage />); });
    await ReactTestRenderer.act(async () => renderer!.root.findByProps({ testID: 'sos-button' }).props.onPress());
    expect(persistCloudRecord).toHaveBeenCalledWith('26080101', 'rescue_requests', expect.any(String),
      expect.objectContaining({ latitude: 22.4, longitude: 114.3, status: 'PENDING' }));
    expect(global.fetch).not.toHaveBeenCalled();
    expect(alert).toHaveBeenCalledWith('sosPage.successTitle', 'persistentTracking.recordQueued');
    expect(saveCurrentSosRequest).toHaveBeenCalledTimes(1);
  } finally {
    if (renderer) await ReactTestRenderer.act(async () => renderer!.unmount());
    Object.defineProperty(Platform, 'OS', { configurable: true, value: originalOS });
    permission.mockRestore();
    alert.mockRestore();
  }
});
