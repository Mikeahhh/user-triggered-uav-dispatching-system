module.exports = {
  root: true,
  extends: '@react-native',
  overrides: [{files: ['services/rescueRecordHash.ts'], rules: {'no-bitwise': 'off'}}],
};
