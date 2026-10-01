import { sha256 } from '@noble/hashes/sha256';


const ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

const finiteNumber = (value: unknown, field: string): number => {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new Error(`Invalid rescue payload ${field}`);
  }
  return value;
};

export const utcTimestamp = (value: unknown, field: string): string => {
  const parts = typeof value === 'string'
    ? /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?Z$/.exec(value)
    : null;
  if (
    !parts || typeof value !== 'string' ||
    !Number.isFinite(Date.parse(value))
  ) {
    throw new Error(`Invalid rescue payload ${field}`);
  }
  const [year, month, day, hour, minute, second] = parts.slice(1).map(Number);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  if (year < 1 || month < 1 || month > 12 || day < 1 || day > days[month - 1]
    || hour > 23 || minute > 59 || second > 59) {
    throw new Error(`Invalid rescue payload ${field}`);
  }
  return value;
};

const pythonFloat = (value: number): string => {
  if (!Number.isFinite(value)) throw new Error('Non-finite JSON number');
  if (value === 0) return Object.is(value, -0) ? '-0.0' : '0.0';
  const negative = value < 0;
  const [mantissa, exponentText] = Math.abs(value).toString().split('e');
  const decimalIndex = mantissa.includes('.')
    ? mantissa.indexOf('.')
    : mantissa.length;
  const rawDigits = mantissa.replace('.', '');
  const leadingZeros = rawDigits.length - rawDigits.replace(/^0+/, '').length;
  const digits = rawDigits.replace(/^0+/, '').replace(/0+$/, '');
  const exponent = Number(exponentText || 0) + decimalIndex - leadingZeros - 1;
  let result: string;
  if (exponent < -4 || exponent >= 16) {
    const coefficient = digits.length > 1
      ? `${digits[0]}.${digits.slice(1)}`
      : digits;
    const exponentSign = exponent < 0 ? '-' : '+';
    result = `${coefficient}e${exponentSign}${String(Math.abs(exponent)).padStart(2, '0')}`;
  } else if (exponent < 0) {
    result = `0.${'0'.repeat(-exponent - 1)}${digits}`;
  } else if (digits.length <= exponent + 1) {
    result = `${digits}${'0'.repeat(exponent + 1 - digits.length)}.0`;
  } else {
    result = `${digits.slice(0, exponent + 1)}.${digits.slice(exponent + 1)}`;
  }
  return negative ? `-${result}` : result;
};


const encodeUtf8 = (value: string): Uint8Array => {
  const bytes: number[] = [];
  for (const character of value) {
    const cp = character.codePointAt(0)!;
    if (cp >= 0xd800 && cp <= 0xdfff) {
      throw new Error('Invalid Unicode surrogate in rescue payload');
    }
    if (cp < 0x80) bytes.push(cp);
    else if (cp < 0x800) bytes.push(0xc0 | (cp >> 6), 0x80 | (cp & 0x3f));
    else if (cp < 0x10000) {
      bytes.push(0xe0 | (cp >> 12), 0x80 | ((cp >> 6) & 0x3f), 0x80 | (cp & 0x3f));
    } else {
      bytes.push(0xf0 | (cp >> 18), 0x80 | ((cp >> 12) & 0x3f),
        0x80 | ((cp >> 6) & 0x3f), 0x80 | (cp & 0x3f));
    }
  }
  return new Uint8Array(bytes);
};


const FLOAT_FIELDS = new Set(['latitude', 'longitude', 'accuracy']);
const canonicalJson = (value: any, field = ''): string => {
  if (typeof value === 'number') {
    return FLOAT_FIELDS.has(field) ? pythonFloat(value) : JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map(item => canonicalJson(item)).join(',')}]`;
  if (value !== null && typeof value === 'object') {
    return `{${Object.keys(value).sort().map(key =>
      `${JSON.stringify(key)}:${canonicalJson(value[key], key)}`,
    ).join(',')}}`;
  }
  if (typeof value === 'string') encodeUtf8(value);
  return JSON.stringify(value);
};


export const canonicalPythonJson = (value: unknown): string => canonicalJson(value);
export const hashCanonicalText = (value: string): string =>
  Array.from(sha256(encodeUtf8(value)), byte => byte.toString(16).padStart(2, '0')).join('');


export const canonicalV1PayloadJson = (value: unknown): string => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('Rescue payload must be an object');
  }
  const raw = value as Record<string, any>;
  if (raw.schema_version !== 1) throw new Error('Unsupported rescue payload schema');
  const id = (key: string): string => {
    if (typeof raw[key] !== 'string' || !ID_PATTERN.test(raw[key])) {
      throw new Error(`Invalid rescue payload ${key}`);
    }
    return raw[key];
  };
  const requestId = id('request_id');
  const userId = id('user_id');
  if (raw.mission_id !== `${userId}/${requestId}`) {
    throw new Error('Rescue payload mission/user/request mismatch');
  }
  const coordinates = (point: Record<string, any>) => {
    const latitude = finiteNumber(point.latitude, 'latitude');
    const longitude = finiteNumber(point.longitude, 'longitude');
    if (Math.abs(latitude) > 90 || Math.abs(longitude) > 180) {
      throw new Error('Rescue payload coordinates out of range');
    }
    return { latitude, longitude };
  };
  const accuracy = raw.accuracy == null ? null : finiteNumber(raw.accuracy, 'accuracy');
  if (accuracy !== null && accuracy < 0) throw new Error('Invalid rescue payload accuracy');
  if (!Number.isSafeInteger(raw.client_timestamp_ms) || raw.client_timestamp_ms <= 0) {
    throw new Error('Invalid rescue payload client_timestamp_ms');
  }
  const status = raw.status === undefined ? 'PENDING' : raw.status;
  const device = raw.device === undefined ? 'unknown' : raw.device;
  const testMode = raw.test_mode === undefined ? false : raw.test_mode;
  const gpsPoints = raw.gps_points === undefined ? [] : raw.gps_points;
  if (!['PENDING', 'ACCEPTED'].includes(status)) throw new Error('Invalid rescue payload status');
  if (typeof device !== 'string' || !device || Array.from(device).length > 32) {
    throw new Error('Invalid rescue payload device');
  }
  if (typeof testMode !== 'boolean') throw new Error('Invalid rescue payload test_mode');
  if (!Array.isArray(gpsPoints) || gpsPoints.length > 1000) {
    throw new Error('Invalid rescue payload gps_points');
  }
  const payload = {
    schema_version: 1,
    request_id: requestId,
    mission_id: raw.mission_id,
    user_id: userId,
    ...coordinates(raw),
    accuracy,
    captured_at: utcTimestamp(raw.captured_at, 'captured_at'),
    client_timestamp_ms: raw.client_timestamp_ms,
    status,
    device,
    gps_points: gpsPoints.map(point => {
      if (!point || typeof point !== 'object' || Array.isArray(point)) {
        throw new Error('Invalid rescue payload GPS point');
      }
      return { ...coordinates(point), captured_at: utcTimestamp(point.captured_at, 'GPS captured_at') };
    }),
    test_mode: testMode,
  };
  return canonicalJson(payload);
};

export const hashV1Payload = (payload: unknown): string =>
  Array.from(sha256(encodeUtf8(canonicalV1PayloadJson(payload))), byte =>
    byte.toString(16).padStart(2, '0'),
  ).join('');
