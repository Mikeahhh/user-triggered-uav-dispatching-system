import { PermissionsAndroid, Platform } from 'react-native';
import Geolocation from '@react-native-community/geolocation';
import { captureFreshUavPosition } from '../services/freshUavPosition';
jest.mock('@react-native-community/geolocation', () => ({ getCurrentPosition: jest.fn() }));
const start = Date.parse('2026-09-09T01:00:00Z');
const good = { timestamp: start + 50, coords: { latitude: 22, longitude: 114, accuracy: 5 } };
beforeEach(() => {
  jest.useFakeTimers(); jest.clearAllMocks();
  Object.defineProperty(Platform, 'OS', { configurable: true, value: 'android' });
  jest.spyOn(PermissionsAndroid, 'request').mockResolvedValue(PermissionsAndroid.RESULTS.GRANTED);
});
afterEach(() => { jest.useRealTimers(); jest.restoreAllMocks(); });
test('uses native GPS timestamp, maximumAge zero and one callback sample', async () => {
  (Geolocation.getCurrentPosition as jest.Mock).mockImplementation(success => success(good));
  const now = jest.fn().mockReturnValueOnce(start).mockReturnValueOnce(start + 100);
  const result = await captureFreshUavPosition({ now });
  expect(result.client_timestamp_ms).toBe(good.timestamp); expect(result.capture_started_at_ms).toBe(start);
  expect(result.captured_at).toBe(new Date(good.timestamp).toISOString());
  expect(Geolocation.getCurrentPosition).toHaveBeenCalledWith(expect.any(Function), expect.any(Function), { maximumAge: 0, enableHighAccuracy: true, timeout: 15000 });
});
test.each([start - 1, start + 101, undefined, NaN, start + 0.5])('rejects stale/future/missing/invalid native timestamp %p', async timestamp => {
  (Geolocation.getCurrentPosition as jest.Mock).mockImplementation(success => success({ ...good, timestamp }));
  await expect(captureFreshUavPosition({ now: jest.fn().mockReturnValueOnce(start).mockReturnValueOnce(start + 100) })).rejects.toThrow();
});
test('denied permission never requests location', async () => {
  (PermissionsAndroid.request as jest.Mock).mockResolvedValue(PermissionsAndroid.RESULTS.DENIED);
  await expect(captureFreshUavPosition()).rejects.toThrow(); expect(Geolocation.getCurrentPosition).not.toHaveBeenCalled();
});
test('JS timeout rejects even if native fused provider never calls back; late fix is ignored', async () => {
  let callback: any;
  (Geolocation.getCurrentPosition as jest.Mock).mockImplementation(success => { callback = success; });
  const promise = captureFreshUavPosition({ now: () => start });
  const rejected = promise.catch(error => error);
  await Promise.resolve(); jest.advanceTimersByTime(15000); expect((await rejected).message).toContain('in time');
  callback(good); expect(jest.getTimerCount()).toBe(0);
});
test('Wi-Fi loss abort cancels sampling and ignores later native success', async () => {
  let callback: any;
  (Geolocation.getCurrentPosition as jest.Mock).mockImplementation(success => { callback = success; });
  const controller = new AbortController();
  const promise = captureFreshUavPosition({ signal: controller.signal, now: () => start });
  const rejected = promise.catch(error => error);
  await Promise.resolve(); controller.abort(); expect((await rejected).message).toContain('cancelled'); callback(good);
  expect(jest.getTimerCount()).toBe(0);
});
