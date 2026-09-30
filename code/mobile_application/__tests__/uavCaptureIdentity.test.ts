import { NativeModules } from 'react-native';
import { createUavCaptureId } from '../services/uavCaptureIdentity';
afterEach(() => { delete NativeModules.UavWifi; });
test('uses the Android identity module', async () => {
  NativeModules.UavWifi = { createCaptureId: jest.fn(async () => '1234567890abcdef1234567890abcdef') };
  expect(await createUavCaptureId()).toBe('1234567890abcdef1234567890abcdef');
});
test('missing native implementation fails instead of weak/random fallback', async () => {
  await expect(createUavCaptureId()).rejects.toThrow();
});
