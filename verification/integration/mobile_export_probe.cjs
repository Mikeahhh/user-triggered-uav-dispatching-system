const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createRequire} = require('node:module');
const mobile = path.resolve(__dirname, '../../code/mobile_application');
const mobileRequire = createRequire(path.join(mobile, 'package.json'));
const ts = mobileRequire('typescript');
const cache = new Map();
const values = new Map();
const memoryStorage = {
  getItem: async key => values.get(key) ?? null,
  setItem: async (key, value) => { values.set(key, value); },
  removeItem: async key => { values.delete(key); },
};

function load(filename, stubs = {}) {
  filename = path.resolve(filename);
  if (!filename.startsWith(mobile + path.sep)) throw new Error('Test module outside Mobile tree');
  if (cache.has(filename)) return cache.get(filename).exports;
  const module = {exports: {}};
  cache.set(filename, module);
  const source = fs.readFileSync(filename, 'utf8');
  const transpiled = ts.transpileModule(source, {compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020,
    esModuleInterop: true,
  }}).outputText;
  const requireLocal = name => {
    if (Object.prototype.hasOwnProperty.call(stubs, name)) return stubs[name];
    if (name === '@react-native-async-storage/async-storage') return memoryStorage;
    if (name === 'firebase/app') return {getApp: () => ({options: {}})};
    if (name === './firebaseConfig') return {};
    if (name.startsWith('.')) {
      const base = path.resolve(path.dirname(filename), name);
      const target = [base, base + '.ts', base + '.tsx', base + '.js', base + '.json']
        .find(candidate => fs.existsSync(candidate) && fs.statSync(candidate).isFile());
      if (!target) throw new Error('Cannot resolve test helper import');
      if (target.endsWith('.json')) return JSON.parse(fs.readFileSync(target, 'utf8'));
      return load(target, stubs);
    }

    if (/^(crypto-js|@noble\/hashes)(\/|$)/.test(name)) return mobileRequire(name);
    throw new Error('Unexpected external dependency in pure helper: ' + name);
  };
  vm.runInNewContext(transpiled, {
    module, exports: module.exports, require: requireLocal,
    URL, TextEncoder, TextDecoder, Uint8Array, ArrayBuffer, DataView, Buffer,
    setTimeout, clearTimeout, AbortController,
    fetch: () => { throw new Error('Network forbidden in pure helper test'); },
  }, {filename});
  return module.exports;
}

module.exports.loadMobile = (name, stubs) => load(path.resolve(mobile, name), stubs);

if (require.main === module) (async () => {
  const input = JSON.parse(fs.readFileSync(0, 'utf8'));
  const exports = load(path.resolve(mobile, input.module));
  if (typeof exports[input.export] !== 'function') throw new Error('Requested helper export is unavailable');
  const results = [];
  for (const args of input.arguments) {
    if (input.captureErrors) {
      try { results.push({accepted: true, value: await exports[input.export](...args)}); }
      catch (error) { results.push({accepted: false, error: error.message}); }
    } else results.push(await exports[input.export](...args));
  }
  process.stdout.write(JSON.stringify(results));
})().catch(error => {
  process.stderr.write(error.message + '\n');
  process.exitCode = 1;
});
