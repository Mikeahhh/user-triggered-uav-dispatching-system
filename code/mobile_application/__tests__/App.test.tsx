import React from 'react';
import ReactTestRenderer from 'react-test-renderer';
import App from '../src/App';

jest.mock('../src/translations/i18n', () => ({}));
jest.mock('../src/services/db/initDb', () => ({
  initDb: jest.fn().mockResolvedValue(undefined),
}));
jest.mock('../src/services/uavRescueClient', () => ({
  pruneExpiredUavOutbox: jest.fn().mockResolvedValue(0),
}));
jest.mock('../src/components', () => {
  const MockReact = require('react');
  const { View: MockView } = require('react-native');
  return {
    Header: () => MockReact.createElement(MockView, { testID: 'header' }),
    Footer: () => MockReact.createElement(MockView, { testID: 'footer' }),
    Content: () => MockReact.createElement(MockView, { testID: 'content' }),
  };
});

test('renders correctly', async () => {
  await ReactTestRenderer.act(async () => {
    ReactTestRenderer.create(<App />);
    await Promise.resolve();
  });
});
