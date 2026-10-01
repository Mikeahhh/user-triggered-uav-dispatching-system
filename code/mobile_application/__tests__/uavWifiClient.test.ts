import {
  NativeModules,
  PermissionsAndroid,
  Platform,
} from 'react-native';
import {
  clearUavWifiSessionPassphrase,
  connectToUavWifi,
  disconnectFromUavWifi,
  setUavWifiSessionPassphrase,
  supportsSystemUavWifiSelection,
} from '../src/services/uavWifiClient';

const nativeConnect = jest.fn();
const nativeDisconnect = jest.fn();

const setAndroidVersion = (version: number) => {
  Object.defineProperty(Platform, 'OS', {
    configurable: true,
    value: 'android',
  });
  Object.defineProperty(Platform, 'Version', {
    configurable: true,
    value: version,
  });
};

beforeEach(() => {
  jest.clearAllMocks();
  clearUavWifiSessionPassphrase();
  setAndroidVersion(33);
  NativeModules.UavWifi = {
    connect: nativeConnect,
    disconnect: nativeDisconnect,
  };
  jest
    .spyOn(PermissionsAndroid, 'request')
    .mockResolvedValue(PermissionsAndroid.RESULTS.GRANTED);
  nativeConnect.mockResolvedValue({
    ssid: 'ALIN1-UAV',
    processBound: true,
  });
  nativeDisconnect.mockResolvedValue(undefined);
});

afterEach(() => {
  jest.restoreAllMocks();
});

test('requests nearby Wi-Fi permission and passes the session-only password', async () => {
  setUavWifiSessionPassphrase('temporary-password');
  await expect(connectToUavWifi(' ALIN1-UAV ')).resolves.toEqual({
    ssid: 'ALIN1-UAV',
    processBound: true,
  });
  expect(PermissionsAndroid.request).toHaveBeenCalledWith(
    PermissionsAndroid.PERMISSIONS.NEARBY_WIFI_DEVICES,
  );
  expect(nativeConnect).toHaveBeenCalledWith(
    'ALIN1-UAV',
    'temporary-password',
  );
});

test('uses fine location permission on Android 10 through 12L', async () => {
  setAndroidVersion(31);
  await connectToUavWifi('ALIN1-UAV');
  expect(PermissionsAndroid.request).toHaveBeenCalledWith(
    PermissionsAndroid.PERMISSIONS.ACCESS_FINE_LOCATION,
  );
});

test('does not call native Wi-Fi after permission denial', async () => {
  (PermissionsAndroid.request as jest.Mock).mockResolvedValue(
    PermissionsAndroid.RESULTS.DENIED,
  );
  await expect(connectToUavWifi('ALIN1-UAV')).rejects.toThrow(
    'permission was denied',
  );
  expect(nativeConnect).not.toHaveBeenCalled();
});

test('rejects unsupported Android versions and invalid passwords', async () => {
  setAndroidVersion(28);
  expect(supportsSystemUavWifiSelection()).toBe(false);
  await expect(connectToUavWifi('ALIN1-UAV')).rejects.toThrow(
    'Android 10 or later',
  );
  expect(() => setUavWifiSessionPassphrase('short')).toThrow(
    'between 8 and 63',
  );
});

test('releases the process network binding', async () => {
  await disconnectFromUavWifi();
  expect(nativeDisconnect).toHaveBeenCalledTimes(1);
});
