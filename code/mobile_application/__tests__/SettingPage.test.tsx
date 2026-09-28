import React from 'react';
import { Alert } from 'react-native';
import ReactTestRenderer from 'react-test-renderer';
import SettingPage from '../pages/SettingPage';
import {
  getUavConnectionConfig,
  saveUavConnectionConfig,
  setUavSessionToken,
} from '../services/uavRescueClient';
import {
  setUavWifiSessionPassphrase,
  validateUavWifiSessionPassphrase,
} from '../services/uavWifiClient';

jest.mock('../services/uavRescueClient', () => ({
  DEFAULT_UAV_BASE_URL: 'http://192.168.31.146:8080',
  getUavConnectionConfig: jest.fn(),
  saveUavConnectionConfig: jest.fn(),
  setUavSessionToken: jest.fn(),
}));

jest.mock('../services/uavWifiClient', () => ({
  setUavWifiSessionPassphrase: jest.fn(),
  validateUavWifiSessionPassphrase: jest.fn(),
}));

jest.mock('react-i18next', () => {
  const t = (key: string) => key;
  const i18n = { language: 'en', changeLanguage: jest.fn() };
  return { useTranslation: () => ({ t, i18n }) };
});

beforeEach(() => {
  jest.clearAllMocks();
  (getUavConnectionConfig as jest.Mock).mockResolvedValue({
    baseUrl: 'http://192.168.31.146:8080',
    wifiSsid: 'ALIN1-UAV',
    testMode: true,
  });
  (saveUavConnectionConfig as jest.Mock).mockResolvedValue(undefined);
  jest.spyOn(Alert, 'alert').mockImplementation(() => {});
});

afterEach(() => {
  jest.restoreAllMocks();
});

test('defaults to visible TEST mode and keeps the token session-only', async () => {
  let renderer: ReactTestRenderer.ReactTestRenderer;
  await ReactTestRenderer.act(async () => {
    renderer = ReactTestRenderer.create(<SettingPage onSelectPage={jest.fn()} />);
  });
  expect(
    renderer!.root.findByProps({ testID: 'settings-uav-test-banner' }),
  ).toBeTruthy();

  await ReactTestRenderer.act(async () => {
    renderer!.root
      .findByProps({ testID: 'uav-token-input' })
      .props.onChangeText('session-only-token');
    renderer!.root
      .findByProps({ testID: 'uav-wifi-passphrase-input' })
      .props.onChangeText('session-only-wifi');
    renderer!.root
      .findByProps({ testID: 'uav-test-mode-switch' })
      .props.onValueChange(false);
  });
  await ReactTestRenderer.act(async () => {
    await renderer!.root
      .findByProps({ testID: 'save-uav-settings-button' })
      .props.onPress();
  });

  expect(saveUavConnectionConfig).toHaveBeenCalledWith({
    baseUrl: 'http://192.168.31.146:8080',
    wifiSsid: 'ALIN1-UAV',
    testMode: false,
  });
  expect(setUavSessionToken).toHaveBeenCalledWith('session-only-token');
  expect(validateUavWifiSessionPassphrase).toHaveBeenCalledWith(
    'session-only-wifi',
  );
  expect(setUavWifiSessionPassphrase).toHaveBeenCalledWith('session-only-wifi');
  expect(
    renderer!.root.findAllByProps({ testID: 'settings-uav-test-banner' }),
  ).toHaveLength(0);
});
