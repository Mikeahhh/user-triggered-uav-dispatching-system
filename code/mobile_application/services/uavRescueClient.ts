import AsyncStorage from '@react-native-async-storage/async-storage';
import { hashV1Payload } from './rescueRecordHash';

export const DEFAULT_UAV_BASE_URL = 'http://192.168.31.146:8080';
export const DEFAULT_UAV_WIFI_SSID = '';
export const UAV_OUTBOX_MAX_ITEMS = 20;
export const UAV_OUTBOX_TTL_MS = 24 * 60 * 60 * 1000;

const CONFIG_KEY = '@trigger-search/uav-rescue-config-v2';
const LEGACY_CONFIG_KEY = '@trigger-search/uav-rescue-config-v1';
const LEGACY_OUTBOX_KEY = '@trigger-search/uav-rescue-outbox-v1';
const LEGACY_OUTBOX_V2_KEY = '@trigger-search/uav-rescue-outbox-v2';
const OUTBOX_KEY = '@trigger-search/uav-rescue-outbox-v3';
const OUTBOX_STORAGE_SCHEMA_VERSION = 3;
const DEFAULT_TIMEOUT_MS = 5000;

let sessionToken = '';
let outboxTail: Promise<void> = Promise.resolve();

export interface UavConnectionConfig {
  baseUrl: string;
  wifiSsid: string;
  testMode: boolean;
}

export interface StoredGpsPoint {
  latitude: number;
  longitude: number;
  captured_at: string;
}

export interface UavRescuePayload {
  schema_version: 1;
  request_id: string;
  mission_id: string;
  user_id: string;
  latitude: number;
  longitude: number;
  accuracy: number | null;
  captured_at: string;
  client_timestamp_ms: number;
  status: 'PENDING' | 'ACCEPTED';
  device: string;
  gps_points: StoredGpsPoint[];
  test_mode: boolean;
}

export interface UavStoredReceipt {
  schema_version: 1;
  status: 'STORED';
  request_id: string;
  mission_id: string;
  uav_received_at: string;
  payload_sha256: string;
  duplicate: boolean;
}

export interface UavOutboxItem {
  payload: UavRescuePayload;
  receiver_base_url: string;
  wifi_ssid: string;
  created_at: string;
  expires_at: string;
}

export interface SendOptions {
  config?: UavConnectionConfig;
  token?: string;
  timeoutMs?: number;
  fetchImpl?: typeof fetch;
  signal?: AbortSignal;
}

export interface QueueOptions {
  config?: UavConnectionConfig;
  nowMs?: number;
}

export interface FlushOptions extends SendOptions {
  nowMs?: number;
}

export interface FlushResult {
  sent: UavStoredReceipt[];
  failed: number;
  expired: number;
  differentReceiver: number;
  differentWifi: number;
  remaining: number;
}

export class UavRescueTransferError extends Error {}

interface PersistedOutboxEnvelope {
  storage_schema_version: 3;
  items: UavOutboxItem[];
}

const withOutboxLock = async <T>(operation: () => Promise<T>): Promise<T> => {
  const previous = outboxTail;
  let release: () => void = () => {};
  outboxTail = new Promise<void>(resolve => {
    release = resolve;
  });
  await previous;
  try {
    return await operation();
  } finally {
    release();
  }
};

const normalizeWifiSsid = (value: unknown): string => {
  const normalized = typeof value === 'string' ? value.trim() : '';
  if (normalized.length > 32) {
    throw new UavRescueTransferError('UAV Wi-Fi SSID is too long');
  }
  return normalized;
};

const isPrivateHttpHost = (hostname: string): boolean => {
  const host = hostname.replace(/^\[|\]$/g, '').toLowerCase();
  if (host === 'localhost' || host === '::1') return true;
  if (/^(fc|fd)[0-9a-f]{2}:/i.test(host) || /^fe[89ab][0-9a-f]:/i.test(host)) {
    return true;
  }
  const parts = host.split('.');
  if (parts.length !== 4 || parts.some(part => !/^\d{1,3}$/.test(part))) {
    return false;
  }
  const octets = parts.map(Number);
  if (octets.some(octet => octet < 0 || octet > 255)) return false;
  return (
    octets[0] === 10 ||
    octets[0] === 127 ||
    (octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31) ||
    (octets[0] === 192 && octets[1] === 168) ||
    (octets[0] === 169 && octets[1] === 254)
  );
};

export const normalizeBaseUrl = (value: string): string => {
  const normalized = value.trim().replace(/\/+$/, '');
  let parsed: URL;
  try {
    parsed = new URL(normalized);
  } catch {
    throw new UavRescueTransferError('Invalid UAV receiver URL');
  }
  if (
    !['http:', 'https:'].includes(parsed.protocol) ||
    parsed.username ||
    parsed.password ||
    parsed.search ||
    parsed.hash ||
    (parsed.pathname && parsed.pathname !== '/')
  ) {
    throw new UavRescueTransferError('Invalid UAV receiver URL');
  }
  if (parsed.protocol === 'http:' && !isPrivateHttpHost(parsed.hostname)) {
    throw new UavRescueTransferError(
      'HTTP is allowed only for loopback, RFC1918, or link-local UAV addresses',
    );
  }
  return parsed.origin;
};

const removeLegacySensitiveStorage = async (): Promise<void> => {
  await Promise.all([
    AsyncStorage.removeItem(LEGACY_CONFIG_KEY),
    AsyncStorage.removeItem(LEGACY_OUTBOX_KEY),
    AsyncStorage.removeItem(LEGACY_OUTBOX_V2_KEY),
  ]);
};

export const setUavSessionToken = (token: string): void => {
  sessionToken = token.trim();
};

export const clearUavSessionToken = (): void => {
  sessionToken = '';
};

export const hasUavSessionToken = (): boolean => sessionToken.length > 0;


export const getUavAuthorizationHeaders = (token?: string): Record<string, string> => {
  const value = token === undefined ? sessionToken : token.trim();
  return value ? { 'X-Rescue-Token': value } : {};
};

export const getUavConnectionConfig = async (): Promise<UavConnectionConfig> => {
  await removeLegacySensitiveStorage();
  const raw = await AsyncStorage.getItem(CONFIG_KEY);
  if (!raw) {
    return {
      baseUrl: DEFAULT_UAV_BASE_URL,
      wifiSsid: DEFAULT_UAV_WIFI_SSID,
      testMode: true,
    };
  }
  try {
    const parsed = JSON.parse(raw);
    return {
      baseUrl: normalizeBaseUrl(parsed.baseUrl || DEFAULT_UAV_BASE_URL),
      wifiSsid: normalizeWifiSsid(parsed.wifiSsid),
      testMode: parsed.testMode !== false,
    };
  } catch {
    throw new UavRescueTransferError(
      'Stored UAV receiver settings are invalid; re-save them in Settings',
    );
  }
};

export const saveUavConnectionConfig = async (
  config: UavConnectionConfig,
): Promise<void> => {
  const normalized: UavConnectionConfig = {
    baseUrl: normalizeBaseUrl(config.baseUrl),
    wifiSsid: normalizeWifiSsid(config.wifiSsid),
    testMode: config.testMode !== false,
  };
  await removeLegacySensitiveStorage();
  await AsyncStorage.setItem(CONFIG_KEY, JSON.stringify(normalized));
  if (normalized.wifiSsid) {
    await withOutboxLock(async () => {
      const items = await readOutboxUnlocked();
      const updated = items.map(item =>
        item.wifi_ssid
          ? item
          : { ...item, wifi_ssid: normalized.wifiSsid },
      );
      await writeOutboxUnlocked(updated);
    });
  }
};

const requireFiniteNumber = (value: unknown, field: string): number => {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new UavRescueTransferError(`Invalid UAV outbox ${field}`);
  }
  return value;
};

const requireString = (
  value: unknown,
  field: string,
  maxLength: number,
): string => {
  if (
    typeof value !== 'string' ||
    value.length === 0 ||
    value.length > maxLength
  ) {
    throw new UavRescueTransferError(`Invalid UAV outbox ${field}`);
  }
  return value;
};

const sanitizePayloadForStorage = (value: unknown): UavRescuePayload => {
  const payload = value as Partial<UavRescuePayload> | null;
  if (!payload || payload.schema_version !== 1) {
    throw new UavRescueTransferError('Invalid UAV outbox payload schema');
  }

  const latitude = requireFiniteNumber(payload.latitude, 'latitude');
  const longitude = requireFiniteNumber(payload.longitude, 'longitude');
  if (latitude < -90 || latitude > 90 || longitude < -180 || longitude > 180) {
    throw new UavRescueTransferError('Invalid UAV outbox coordinates');
  }
  const accuracy =
    payload.accuracy === null
      ? null
      : requireFiniteNumber(payload.accuracy, 'accuracy');
  if (accuracy !== null && accuracy < 0) {
    throw new UavRescueTransferError('Invalid UAV outbox accuracy');
  }
  const capturedAt = requireString(payload.captured_at, 'captured_at', 64);
  if (!Number.isFinite(Date.parse(capturedAt))) {
    throw new UavRescueTransferError('Invalid UAV outbox captured_at');
  }
  if (!['PENDING', 'ACCEPTED'].includes(payload.status || '')) {
    throw new UavRescueTransferError('Invalid UAV outbox status');
  }
  if (
    !Array.isArray(payload.gps_points) ||
    payload.gps_points.length === 0 ||
    payload.gps_points.length > 100
  ) {
    throw new UavRescueTransferError('Invalid UAV outbox gps_points');
  }
  if (typeof payload.test_mode !== 'boolean') {
    throw new UavRescueTransferError('Invalid UAV outbox test_mode');
  }

  const gpsPoints = payload.gps_points.map(point => {
    const pointLatitude = requireFiniteNumber(point?.latitude, 'gps latitude');
    const pointLongitude = requireFiniteNumber(point?.longitude, 'gps longitude');
    const pointCapturedAt = requireString(
      point?.captured_at,
      'gps captured_at',
      64,
    );
    if (
      pointLatitude < -90 ||
      pointLatitude > 90 ||
      pointLongitude < -180 ||
      pointLongitude > 180 ||
      !Number.isFinite(Date.parse(pointCapturedAt))
    ) {
      throw new UavRescueTransferError('Invalid UAV outbox GPS point');
    }
    return {
      latitude: pointLatitude,
      longitude: pointLongitude,
      captured_at: pointCapturedAt,
    };
  });

  return {
    schema_version: 1,
    request_id: requireString(payload.request_id, 'request_id', 128),
    mission_id: requireString(payload.mission_id, 'mission_id', 256),
    user_id: requireString(payload.user_id, 'user_id', 128),
    latitude,
    longitude,
    accuracy,
    captured_at: capturedAt,
    client_timestamp_ms: requireFiniteNumber(
      payload.client_timestamp_ms,
      'client_timestamp_ms',
    ),
    status: payload.status as 'PENDING' | 'ACCEPTED',
    device: requireString(payload.device, 'device', 64),
    gps_points: gpsPoints,
    test_mode: payload.test_mode,
  };
};

const sanitizeOutboxItem = (value: unknown): UavOutboxItem => {
  const item = value as Partial<UavOutboxItem> | null;
  if (!item) throw new UavRescueTransferError('Invalid UAV outbox item');
  const createdAt = requireString(item.created_at, 'created_at', 64);
  const expiresAt = requireString(item.expires_at, 'expires_at', 64);
  const createdMs = Date.parse(createdAt);
  const expiresMs = Date.parse(expiresAt);
  if (
    !Number.isFinite(createdMs) ||
    !Number.isFinite(expiresMs) ||
    expiresMs <= createdMs
  ) {
    throw new UavRescueTransferError('Invalid UAV outbox lifetime');
  }
  return {
    payload: sanitizePayloadForStorage(item.payload),
    receiver_base_url: normalizeBaseUrl(
      requireString(item.receiver_base_url, 'receiver_base_url', 2048),
    ),
    wifi_ssid: normalizeWifiSsid(item.wifi_ssid),
    created_at: createdAt,
    expires_at: expiresAt,
  };
};

const readOutboxUnlocked = async (): Promise<UavOutboxItem[]> => {
  await Promise.all([
    AsyncStorage.removeItem(LEGACY_OUTBOX_KEY),
    AsyncStorage.removeItem(LEGACY_OUTBOX_V2_KEY),
  ]);
  const raw = await AsyncStorage.getItem(OUTBOX_KEY);
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw) as Partial<PersistedOutboxEnvelope>;
    if (
      parsed.storage_schema_version !== OUTBOX_STORAGE_SCHEMA_VERSION ||
      !Array.isArray(parsed.items) ||
      parsed.items.length > UAV_OUTBOX_MAX_ITEMS
    ) {
      throw new UavRescueTransferError('Invalid UAV outbox envelope');
    }
    return parsed.items.map(sanitizeOutboxItem);
  } catch (error) {
    if (error instanceof UavRescueTransferError) throw error;
    throw new UavRescueTransferError('Stored UAV outbox is invalid');
  }
};

const writeOutboxUnlocked = async (items: UavOutboxItem[]): Promise<void> => {
  if (items.length > UAV_OUTBOX_MAX_ITEMS) {
    throw new UavRescueTransferError('UAV outbox exceeds its storage limit');
  }
  if (items.length === 0) {
    await AsyncStorage.removeItem(OUTBOX_KEY);
    return;
  }
  const envelope: PersistedOutboxEnvelope = {
    storage_schema_version: OUTBOX_STORAGE_SCHEMA_VERSION,


    items: items.map(sanitizeOutboxItem),
  };
  await AsyncStorage.setItem(OUTBOX_KEY, JSON.stringify(envelope));
};

const readOutbox = async (): Promise<UavOutboxItem[]> =>
  withOutboxLock(async () => {
    const items = await readOutboxUnlocked();
    const nowMs = Date.now();
    const activeItems = items.filter(item => {
      const expiryMs = Date.parse(item.expires_at);
      return Number.isFinite(expiryMs) && expiryMs > nowMs;
    });
    if (activeItems.length !== items.length) {
      await writeOutboxUnlocked(activeItems);
    }
    return activeItems;
  });

export const pruneExpiredUavOutbox = async (
  nowMs: number = Date.now(),
): Promise<number> =>
  withOutboxLock(async () => {
    const items = await readOutboxUnlocked();
    const activeItems = items.filter(item => {
      const expiryMs = Date.parse(item.expires_at);
      return Number.isFinite(expiryMs) && expiryMs > nowMs;
    });
    await writeOutboxUnlocked(activeItems);
    return items.length - activeItems.length;
  });

export const clearPersistentUavOutbox = async (): Promise<void> =>
  withOutboxLock(async () => {
    await AsyncStorage.removeItem(OUTBOX_KEY);
  });

export const queueRescueForUav = async (
  payload: UavRescuePayload,
  options: QueueOptions = {},
): Promise<void> => {
  const config = options.config || (await getUavConnectionConfig());
  const receiverBaseUrl = normalizeBaseUrl(config.baseUrl);
  const nowMs = options.nowMs ?? Date.now();
  await withOutboxLock(async () => {
    const storedItems = await readOutboxUnlocked();
    const items = storedItems.filter(item => {
      const expiryMs = Date.parse(item.expires_at);
      return Number.isFinite(expiryMs) && expiryMs > nowMs;
    });
    const existingIndex = items.findIndex(
      item => item.payload?.request_id === payload.request_id,
    );
    const queuedItem: UavOutboxItem = sanitizeOutboxItem({
      payload,
      receiver_base_url: receiverBaseUrl,
      wifi_ssid: normalizeWifiSsid(config.wifiSsid),
      created_at: new Date(nowMs).toISOString(),
      expires_at: new Date(nowMs + UAV_OUTBOX_TTL_MS).toISOString(),
    });

    if (existingIndex >= 0) {
      const updated = [...items];
      updated[existingIndex] = queuedItem;
      await writeOutboxUnlocked(updated);
      return;
    }
    if (items.length >= UAV_OUTBOX_MAX_ITEMS) {
      throw new UavRescueTransferError(
        `UAV outbox is full (${UAV_OUTBOX_MAX_ITEMS} records); no record was discarded`,
      );
    }
    await writeOutboxUnlocked([...items, queuedItem]);
  });
};

export const getPendingUavRescues = readOutbox;

export const removePendingUavRescue = async (requestId: string): Promise<void> => {
  await withOutboxLock(async () => {
    const items = await readOutboxUnlocked();
    await writeOutboxUnlocked(
      items.filter(item => {
        const expiryMs = Date.parse(item.expires_at);
        return (
          Number.isFinite(expiryMs) &&
          expiryMs > Date.now() &&
          item.payload?.request_id !== requestId
        );
      }),
    );
  });
};

export const sendRescueToUav = async (
  payload: UavRescuePayload,
  options: SendOptions = {},
): Promise<UavStoredReceipt> => {
  const config = options.config || (await getUavConnectionConfig());
  const baseUrl = normalizeBaseUrl(config.baseUrl);
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const fetchImpl = options.fetchImpl || fetch;

  const serializedPayload = JSON.stringify(payload);
  const expectedPayloadHash = hashV1Payload(JSON.parse(serializedPayload));
  const controller = new AbortController();
  let timeoutTriggered = false;
  const timeout = setTimeout(() => {
    timeoutTriggered = true;
    controller.abort();
  }, timeoutMs);
  const externalSignal = options.signal;
  const abortForExternalSignal = () => controller.abort();
  if (externalSignal?.aborted) {
    controller.abort();
  } else {
    externalSignal?.addEventListener('abort', abortForExternalSignal);
  }

  try {
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    };
    const token = options.token === undefined ? sessionToken : options.token.trim();
    if (token) headers['X-Rescue-Token'] = token;

    const response = await fetchImpl(`${baseUrl}/api/v1/rescue-requests`, {
      method: 'POST',
      headers,
      body: serializedPayload,
      signal: controller.signal,
    });
    let body: any;
    try {
      body = await response.json();
    } catch {
      throw new UavRescueTransferError('UAV returned invalid JSON');
    }
    if (!response.ok) {
      throw new UavRescueTransferError(`UAV rejected request (${response.status})`);
    }
    if (
      body?.schema_version !== 1 ||
      body?.status !== 'STORED' ||
      body?.request_id !== payload.request_id ||
      body?.mission_id !== payload.mission_id ||
      typeof body?.uav_received_at !== 'string' ||
      body?.payload_sha256 !== expectedPayloadHash
    ) {
      throw new UavRescueTransferError('UAV receipt did not match request');
    }
    return body as UavStoredReceipt;
  } catch (error: any) {
    if (error?.name === 'AbortError') {
      if (externalSignal?.aborted && !timeoutTriggered) {
        throw new UavRescueTransferError('UAV Wi-Fi connection was lost');
      }
      throw new UavRescueTransferError('UAV receiver timed out');
    }
    if (error instanceof UavRescueTransferError) throw error;
    throw new UavRescueTransferError('UAV receiver is unreachable');
  } finally {
    clearTimeout(timeout);
    externalSignal?.removeEventListener('abort', abortForExternalSignal);
  }
};

export const flushPendingUavRescues = async (
  options: FlushOptions = {},
): Promise<FlushResult> => {
  const config = options.config || (await getUavConnectionConfig());
  const receiverBaseUrl = normalizeBaseUrl(config.baseUrl);
  const nowMs = options.nowMs ?? Date.now();
  const configuredWifiSsid = normalizeWifiSsid(config.wifiSsid);
  return withOutboxLock(async () => {
    const items = await readOutboxUnlocked();
    const sent: UavStoredReceipt[] = [];
    const remaining: UavOutboxItem[] = [];
    let failed = 0;
    let expired = 0;
    let differentReceiver = 0;
    let differentWifi = 0;


    for (const item of items) {
      const expiryMs = Date.parse(item.expires_at);
      if (!Number.isFinite(expiryMs) || expiryMs <= nowMs) {
        expired += 1;
        continue;
      }
      if (normalizeBaseUrl(item.receiver_base_url) !== receiverBaseUrl) {
        differentReceiver += 1;
        remaining.push(item);
        continue;
      }
      if (normalizeWifiSsid(item.wifi_ssid) !== configuredWifiSsid) {
        differentWifi += 1;
        remaining.push(item);
        continue;
      }
      if (options.signal?.aborted) {
        failed += 1;
        remaining.push(item);
        continue;
      }
      try {
        const receipt = await sendRescueToUav(item.payload, {
          ...options,
          config,
        });
        sent.push(receipt);
      } catch {
        failed += 1;
        remaining.push(item);
      }
    }
    await writeOutboxUnlocked(remaining);
    return {
      sent,
      failed,
      expired,
      differentReceiver,
      differentWifi,
      remaining: remaining.length,
    };
  });
};
