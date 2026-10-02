module.exports = {
  root: true,
  extends: '@react-native',
  overrides: [{files: ['src/services/rescueRecordHash.ts'], rules: {'no-bitwise': 'off'}}],
};
