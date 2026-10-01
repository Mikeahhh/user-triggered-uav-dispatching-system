'use strict';

if (process.env.MASS26_LOCAL_VERIFICATION === '1') {
  const net = require('node:net');
  const dns = require('node:dns');
  const dgram = require('node:dgram');

  function requireLocal(host) {
    const normalized = String(host ?? '').toLowerCase();
    const loopback = normalized === 'localhost' || normalized === '::1'
      || (net.isIPv4(normalized) && normalized.startsWith('127.'))
      || (net.isIPv6(normalized) && /^::ffff:127\./.test(normalized));
    if (!loopback) {
      const error = new Error('Local verification forbids non-loopback network access');
      error.code = 'LOCAL_NETWORK_ONLY';
      throw error;
    }
  }

  function connectionHost(args, binding) {
    const first = Array.isArray(args[0]) ? args[0][0] : args[0];
    if (first && typeof first === 'object') {
      if (first.path && first.port === undefined) return null;
      return first.host ?? (binding ? '' : 'localhost');
    }
    if (typeof first === 'string' && !/^\d+$/.test(first)) return null;
    return typeof args[1] === 'string' ? args[1] : (binding ? '' : 'localhost');
  }

  for (const [prototype, method, binding] of [
    [net.Socket.prototype, 'connect', false],
    [net.Server.prototype, 'listen', true],
  ]) {
    const original = prototype[method];
    prototype[method] = function (...args) {
      const host = connectionHost(args, binding);
      if (host !== null) requireLocal(host);
      return original.apply(this, args);
    };
  }

  for (const api of [dns, dns.promises, dns.Resolver.prototype, dns.promises.Resolver.prototype]) {
    for (const method of ['lookup', 'resolve', 'resolve4', 'resolve6', 'resolveAny',
      'resolveCaa', 'resolveCname', 'resolveMx', 'resolveNaptr', 'resolveNs',
      'resolvePtr', 'resolveSoa', 'resolveSrv', 'resolveTxt', 'reverse']) {
      const original = api[method];
      if (typeof original !== 'function') continue;
      api[method] = function (host, ...args) {
        requireLocal(host);
        if (method !== 'lookup') {
          const error = new Error('Local verification forbids DNS resolver traffic');
          error.code = 'LOCAL_NETWORK_ONLY';
          throw error;
        }
        return original.call(this, host, ...args);
      };
    }
  }

  const originalDatagram = dgram.createSocket;
  dgram.createSocket = function (...args) {
    const socket = originalDatagram.apply(this, args);
    for (const method of ['bind', 'connect']) {
      const original = socket[method];
      socket[method] = function (...values) {
        const host = values[0] && typeof values[0] === 'object'
          ? values[0].address : values[1];
        requireLocal(host);
        return original.apply(this, values);
      };
    }
    const send = socket.send;
    socket.send = function (...values) {
      const end = typeof values.at(-1) === 'function' ? values.length - 2 : values.length - 1;
      if (typeof values[end] === 'string' && end > 0) requireLocal(values[end]);
      else {
        const peer = socket.remoteAddress();
        requireLocal(peer.address);
      }
      return send.apply(this, values);
    };
    return socket;
  };

  if (typeof globalThis.fetch === 'function') {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = function (input, options) {
      const target = new URL(typeof input === 'string' || input instanceof URL ? input : input.url);
      requireLocal(target.hostname.replace(/^\[|\]$/g, ''));
      return originalFetch.call(this, input, options);
    };
  }
}
