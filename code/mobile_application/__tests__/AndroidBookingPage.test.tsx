import React from 'react';
import { Alert, Platform, Text, TextInput } from 'react-native';
import Renderer from 'react-test-renderer';
import EventBookingPage from '../src/pages/EventBookingPage';
import { persistCloudRecord, readPersistentRecords, refreshPersistentRecords } from '../src/services/persistentTracking';

jest.mock('../src/services/db/initDb', () => ({
  initDb: jest.fn().mockResolvedValue(undefined),
  getDb: () => ({ executeSql: jest.fn().mockResolvedValue([{ rows: { length: 1, item: () => ({ phone: '26080101' }) } }]) }),
}));
jest.mock('../src/services/db/firebaseRealtimeDatabase', () => ({ buildRealtimeDatabaseRestUrl: jest.fn() }));
jest.mock('../src/services/persistentTracking', () => ({
  persistCloudRecord: jest.fn(), readPersistentRecords: jest.fn(), refreshPersistentRecords: jest.fn(),
}));
jest.mock('react-native-maps', () => ({ __esModule: true, default: 'MapView', Polyline: 'Polyline', Marker: 'Marker' }));
jest.mock('react-native-date-picker', () => ({ __esModule: true, default: 'DatePicker' }));
jest.mock('react-i18next', () => {
  const t = (key: string) => key;
  return { useTranslation: () => ({ t }) };
});

const originalOS = Platform.OS;
let renderer: Renderer.ReactTestRenderer | null;
beforeEach(() => {
  jest.clearAllMocks();
  Object.defineProperty(Platform, 'OS', { configurable: true, value: 'android' });
  jest.spyOn(Alert, 'alert').mockImplementation(() => {});
  (readPersistentRecords as jest.Mock).mockResolvedValue([]);
  (refreshPersistentRecords as jest.Mock).mockRejectedValue(new Error('offline'));
  (persistCloudRecord as jest.Mock).mockResolvedValue({ stored: true, synchronized: false });
});
afterEach(async () => {
  if (renderer) await Renderer.act(async () => renderer!.unmount());
  renderer = null;
  Object.defineProperty(Platform, 'OS', { configurable: true, value: originalOS });
  jest.restoreAllMocks();
});

test('loads locally saved booking records even when the cloud is unavailable', async () => {
  (readPersistentRecords as jest.Mock).mockResolvedValue([{ id: 'saved', title: 'Offline route', date: '2026-09-30', startTime: '10:00', endTime: '12:00', waypoints: [] }]);
  await Renderer.act(async () => { renderer = Renderer.create(<EventBookingPage />); });
  expect(renderer!.root.findAllByType(Text).map(node => node.props.children).flat()).toContain('Offline route');
  expect(Alert.alert).not.toHaveBeenCalled();
});

test('a booking is saved locally with route order and numeric expected end time', async () => {
  await Renderer.act(async () => { renderer = Renderer.create(<EventBookingPage />); });
  await Renderer.act(async () => renderer!.root.findByProps({ testID: 'booking-form-toggle' }).props.onPress());
  await Renderer.act(async () => renderer!.root.findByType(TextInput).props.onChangeText('Hiking route'));
  const map = renderer!.root.findByType('MapView' as any);
  await Renderer.act(async () => map.props.onLongPress({ nativeEvent: { coordinate: { latitude: 22.4, longitude: 114.3 } } }));
  await Renderer.act(async () => map.props.onLongPress({ nativeEvent: { coordinate: { latitude: 22.41, longitude: 114.31 } } }));
  await Renderer.act(async () => renderer!.root.findByProps({ testID: 'booking-save' }).props.onPress());
  expect(persistCloudRecord).toHaveBeenCalledWith('26080101', 'booked_events', expect.any(String), expect.objectContaining({
    title: 'Hiking route', expectedEndAtMs: expect.any(Number),
    waypoints: [{ latitude: 22.4, longitude: 114.3 }, { latitude: 22.41, longitude: 114.31 }],
  }));
  expect(Alert.alert).toHaveBeenCalledWith('eventBookingPage.alert.saveSuccess.title', 'persistentTracking.recordQueued');
});
