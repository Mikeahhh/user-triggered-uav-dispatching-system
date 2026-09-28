import React from 'react';
import { Text } from 'react-native';
import ReactTestRenderer from 'react-test-renderer';
import SystemHealthPage from '../pages/SystemHealthPage';
import {
  getPendingUavRescues,
  getUavConnectionConfig,
} from '../services/uavRescueClient';

jest.mock('../services/buildMode', () => ({
  SCREENSHOT_DEMO: true,
  SCREENSHOT_DEMO_BANNER: 'LOCAL DEMO — NO FIREBASE / NO UAV',
}));

jest.mock('../services/uavRescueClient', () => ({
  UAV_OUTBOX_MAX_ITEMS: 20,
  UAV_OUTBOX_TTL_MS: 24 * 60 * 60 * 1000,
  getPendingUavRescues: jest.fn(),
  getUavConnectionConfig: jest.fn(),
}));

jest.mock('../services/uavWifiClient', () => ({
  supportsSystemUavWifiSelection: () => true,
}));

jest.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, values?: { count?: number }) =>
      key === 'systemHealth.runtime.hours'
        ? `${values?.count} hours`
        : key,
  }),
}));

beforeEach(() => {
  jest.clearAllMocks();
  (getUavConnectionConfig as jest.Mock).mockResolvedValue({
    baseUrl: 'http://192.168.31.146:8080',
    wifiSsid: 'PRIVATE-UAV-SSID',
    testMode: true,
  });
  (getPendingUavRescues as jest.Mock).mockResolvedValue([
    {
      payload: {
        user_id: 'PRIVATE-PHONE',
        latitude: 22.352,
        longitude: 114.183,
      },
    },
  ]);
});

test('renders a screenshot-safe release and readiness view without sensitive values', async () => {
  const onSelectPage = jest.fn();
  let renderer: ReactTestRenderer.ReactTestRenderer;
  await ReactTestRenderer.act(async () => {
    renderer = ReactTestRenderer.create(
      <SystemHealthPage onSelectPage={onSelectPage} />,
    );
  });

  const visibleText = renderer!.root
    .findAllByType(Text)
    .map(node => String(node.props.children))
    .join('\n');
  expect(visibleText).toContain('LOCAL DEMO — NO FIREBASE / NO UAV');
  expect(visibleText).toContain('MASS26-20260806');
  expect(visibleText).toContain('1/20');
  expect(visibleText).not.toContain('192.168.31.146');
  expect(visibleText).not.toContain('PRIVATE-UAV-SSID');
  expect(visibleText).not.toContain('PRIVATE-PHONE');
  expect(visibleText).not.toContain('22.352');
  expect(visibleText).not.toContain('114.183');

  await ReactTestRenderer.act(async () => {
    renderer!.root
      .findByProps({ testID: 'system-health-back-button' })
      .props.onPress();
  });
  expect(onSelectPage).toHaveBeenCalledWith(3);
});
