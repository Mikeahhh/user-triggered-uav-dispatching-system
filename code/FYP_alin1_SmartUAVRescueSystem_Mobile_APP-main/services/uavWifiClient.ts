import {
  DeviceEventEmitter,
  NativeModules,
  PermissionsAndroid,
  Platform,
} from 'react-native';

export interface UavWifiConnection {
  ssid: string;
  processBound: boolean;
}

interface UavWifiNativeModule {
  connect: (ssid: string, passphrase: string) => Promise<UavWifiConnection>;
  disconnect: () => Promise<void>;
}

let sessionPassphrase = '';

export class UavWifiConnectionError extends Error {}
const UAV_WIFI_LOST_EVENT = 'UavWifiLost';

const getAndroidApiLevel = (): number => Number(Platform.Version);

const getNativeUavWifi = (): UavWifiNativeModule => {
  const module = NativeModules.UavWifi as UavWifiNativeModule | undefined;
  if (!module?.connect || !module?.disconnect) {
    throw new UavWifiConnectionError(
      'UAV Wi-Fi native module is unavailable in this build',
    );
  }
  return module;
};

export const validateUavWifiSessionPassphrase = (passphrase: string): void => {
  if (passphrase.length > 0 && (passphrase.length < 8 || passphrase.length > 63)) {
    throw new UavWifiConnectionError(
      'A WPA2 passphrase must contain between 8 and 63 characters',
    );
  }
};

export const setUavWifiSessionPassphrase = (passphrase: string): void => {
  validateUavWifiSessionPassphrase(passphrase);
  sessionPassphrase = passphrase;
};

export const clearUavWifiSessionPassphrase = (): void => {
  sessionPassphrase = '';
};

export const hasUavWifiSessionPassphrase = (): boolean =>
  sessionPassphrase.length > 0;

export const supportsSystemUavWifiSelection = (): boolean =>
  Platform.OS === 'android' && getAndroidApiLevel() >= 29;

const requestWifiRuntimePermission = async (): Promise<void> => {
  const apiLevel = getAndroidApiLevel();
  const permission =
    apiLevel >= 33
      ? PermissionsAndroid.PERMISSIONS.NEARBY_WIFI_DEVICES
      : PermissionsAndroid.PERMISSIONS.ACCESS_FINE_LOCATION;
  const result = await PermissionsAndroid.request(permission);
  if (result !== PermissionsAndroid.RESULTS.GRANTED) {
    throw new UavWifiConnectionError('UAV Wi-Fi permission was denied');
  }
};

export const connectToUavWifi = async (
  ssidValue: string,
): Promise<UavWifiConnection> => {
  if (!supportsSystemUavWifiSelection()) {
    throw new UavWifiConnectionError(
      'System-confirmed UAV Wi-Fi selection requires Android 10 or later',
    );
  }
  const ssid = ssidValue.trim();
  if (!ssid) {
    throw new UavWifiConnectionError('UAV Wi-Fi SSID is not configured');
  }
  await requestWifiRuntimePermission();
  try {
    return await getNativeUavWifi().connect(ssid, sessionPassphrase);
  } catch (error: any) {
    throw new UavWifiConnectionError(
      typeof error?.message === 'string'
        ? error.message
        : 'UAV Wi-Fi connection failed',
    );
  }
};

export const disconnectFromUavWifi = async (): Promise<void> => {
  if (!supportsSystemUavWifiSelection()) return;
  const module = NativeModules.UavWifi as UavWifiNativeModule | undefined;
  if (module?.disconnect) {
    await module.disconnect();
  }
};

export const subscribeToUavWifiLoss = (
  listener: () => void,
): { remove: () => void } =>
  DeviceEventEmitter.addListener(UAV_WIFI_LOST_EVENT, listener);
