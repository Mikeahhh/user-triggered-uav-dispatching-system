import React from 'react';
import { Alert } from 'react-native';
import ReactTestRenderer from 'react-test-renderer';
import SettingPage from '../src/pages/SettingPage';
import {
  getUavConnectionConfig,
  saveUavConnectionConfig,
  setUavSessionToken,
} from '../src/services/uavRescueClient';
import {
  setUavWifiSessionPassphrase,
  validateUavWifiSessionPassphrase,
} from '../src/services/uavWifiClient';

jest.mock('../src/services/uavRescueClient', () => ({
  DEFAULT_UAV_BASE_URL: 'http://192.168.31.146:8080',
  getUavConnectionConfig: jest.fn(),
  saveUavConnectionConfig: jest.fn(),
  setUavSessionToken: jest.fn(),
}));

jest.mock('../src/services/uavWifiClient', () => ({
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
  });
  (saveUavConnectionConfig as jest.Mock).mockResolvedValue(undefined);
  jest.spyOn(Alert, 'alert').mockImplementation(() => {});
});

afterEach(() => {
  jest.restoreAllMocks();
});

test('saves receiver settings and keeps credentials session-only', async () => {
  let renderer: ReactTestRenderer.ReactTestRenderer;
  await ReactTestRenderer.act(async () => {
    renderer = ReactTestRenderer.create(<SettingPage onSelectPage={jest.fn()} />);
  });

  await ReactTestRenderer.act(async () => {
    renderer!.root
      .findByProps({ testID: 'uav-token-input' })
      .props.onChangeText('session-only-token');
    renderer!.root
      .findByProps({ testID: 'uav-wifi-passphrase-input' })
      .props.onChangeText('session-only-wifi');
  });
  await ReactTestRenderer.act(async () => {
    await renderer!.root
      .findByProps({ testID: 'save-uav-settings-button' })
      .props.onPress();
  });

  expect(saveUavConnectionConfig).toHaveBeenCalledWith({
    baseUrl: 'http://192.168.31.146:8080',
    wifiSsid: 'ALIN1-UAV',
  });
  expect(setUavSessionToken).toHaveBeenCalledWith('session-only-token');
  expect(validateUavWifiSessionPassphrase).toHaveBeenCalledWith(
    'session-only-wifi',
  );
  expect(setUavWifiSessionPassphrase).toHaveBeenCalledWith('session-only-wifi');
});
