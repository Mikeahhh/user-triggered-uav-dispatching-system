import React from 'react';
import ReactTestRenderer from 'react-test-renderer';
import App from '../App';

jest.mock('../translations/i18n', () => ({}));
jest.mock('../services/db/initDb', () => ({
  initDb: jest.fn().mockResolvedValue(undefined),
}));
jest.mock('../services/uavRescueClient', () => ({
  pruneExpiredUavOutbox: jest.fn().mockResolvedValue(0),
}));
jest.mock('../components', () => {
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
