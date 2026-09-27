const fs = require('node:fs');
const {loadMobile} = require('./mobile_export_probe.cjs');

(async () => {
  const input = JSON.parse(fs.readFileSync(0, 'utf8'));
  const base = new URL(input.baseUrl);
  if (base.protocol !== 'http:' || base.hostname !== '127.0.0.1') throw new Error('Loopback test only');
  const order = [];
  let gpsCalls = 0;
  const stubs = {
    'react-native': {Platform: {OS: 'android'}, NativeModules: {},
      PermissionsAndroid: {PERMISSIONS: {ACCESS_FINE_LOCATION: 'synthetic-fine-location'},
        RESULTS: {GRANTED: 'synthetic-granted'}, request: async () => 'synthetic-granted'}},
    '@react-native-community/geolocation': {getCurrentPosition: (success, _failure, options) => {
      order.push('gps'); gpsCalls++;
      if (options.maximumAge !== 0 || !options.enableHighAccuracy) throw new Error('Fresh location was not requested');
      success({timestamp: input.position.client_timestamp_ms, coords: {
        latitude: input.position.latitude, longitude: input.position.longitude,
        accuracy: input.position.accuracy}});
    }},
  };
  const {createUavArrivalCaptureClient} = loadMobile('services/uavArrivalCaptureClient.ts', stubs);
  const storage = {
    getItem: async key => {
      const saved = fs.existsSync(input.storagePath) ? JSON.parse(fs.readFileSync(input.storagePath, 'utf8')) : {};
      return saved[key] ?? null;
    },
    setItem: async (key, value) => {
      const saved = fs.existsSync(input.storagePath) ? JSON.parse(fs.readFileSync(input.storagePath, 'utf8')) : {};
      saved[key] = value;
      const fd = fs.openSync(input.storagePath, 'w', 0o600);
      try { fs.writeFileSync(fd, JSON.stringify(saved)); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
      const items = JSON.parse(value).items;
      const latest = items.at(-1);
      order.push(latest.receipt ? 'save-receipt' : latest.payload_json ? 'save-payload'
        : latest.context ? 'save-context' : 'save-intent');
    },
  };
  let requestCount = 0, postCount = 0;
  const bodies = [];
  const fetchImpl = async (url, options) => {
    const target = new URL(url);
    if (target.origin !== base.origin) throw new Error('External network forbidden');
    if (options.method === 'POST') {
      postCount++; bodies.push(options.body); order.push('post');
    } else { requestCount++; order.push('context'); }
    const response = await fetch(url, options);
    if (input.mode === 'lose-response' && options.method === 'POST' && postCount === 1) {
      await response.text();
      throw new Error('Synthetic lost response after actual UAV persistence');
    }
    return response;
  };
  let timeReads = 0;
  const dependencies = {storage, createId: async () => input.captureId, fetchImpl,
    now: () => (++timeReads % 2 === 1 ? input.position.capture_started_at_ms : input.position.client_timestamp_ms + 1),
    device: 'synthetic-mobile-client'};
  const config = {baseUrl: input.baseUrl, wifiSsid: 'SYNTHETIC_UAV', testMode: true};
  const options = {wifiConfirmed: true};
  let receipt, retry, failure;
  if (input.mode === 'retry-only') {
    retry = await createUavArrivalCaptureClient(dependencies).retry(input.source.user_id, config, options);
  } else {
    try { receipt = await createUavArrivalCaptureClient(dependencies).capture(input.source, config, options); }
    catch (error) { failure = error.message; }
    if (input.mode === 'lose-response') {

      retry = await createUavArrivalCaptureClient(dependencies).retry(input.source.user_id, config, options);
    } else if (failure) throw new Error(failure);
  }
  const saved = JSON.parse(fs.readFileSync(input.storagePath, 'utf8'));
  const items = Object.values(saved).flatMap(text => JSON.parse(text).items);
  process.stdout.write(JSON.stringify({receipt, retry, failure, order, gpsCalls, requestCount, postCount, bodies, items}));
})().catch(error => { process.stderr.write(error.stack + '\n'); process.exitCode = 1; });
