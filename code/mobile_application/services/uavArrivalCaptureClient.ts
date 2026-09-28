import AsyncStorage from '@react-native-async-storage/async-storage';
import { Platform } from 'react-native';
import { captureFreshUavPosition, FreshPositionOptions, FreshUavPosition } from './freshUavPosition';
import { createUavCaptureId } from './uavCaptureIdentity';
import { getUavAuthorizationHeaders, normalizeBaseUrl, UavConnectionConfig, UavRescuePayload } from './uavRescueClient';
import { buildCaptureV2Payload, canonicalV2PayloadJson, UavCapturePayload, UavCaptureReceipt, UavMissionContext,
  hashV2Payload, validateCaptureIdentifier, validateCaptureReceipt, validateCaptureSource, validateMissionContext } from './uavCaptureV2';
import { canonicalPythonJson } from './rescueRecordHash';

const STORAGE_KEY = '@trigger-search/uav-arrival-captures-v2';
const MAX_PENDING = 20;
const MAX_RECEIPTS = 20;
export class UavCaptureTransferError extends Error {
  constructor(public readonly code: string) { super(code); }
}
interface CaptureEntry {
  capture_id: string;
  source_request: UavRescuePayload;
  receiver_base_url: string;
  wifi_ssid: string;
  test_mode: boolean;
  device: string;
  context?: UavMissionContext;
  payload_json?: string;
  payload_sha256?: string;
  receipt?: UavCaptureReceipt;
  retired_reason?: 'NO_COLLECTION_CONTEXT' | 'CONTEXT_IDENTITY_CONFLICT';
}
export interface CaptureTransferOptions {
  signal?: AbortSignal;
  wifiConfirmed: boolean;
  onStage?: (stage: 'context' | 'location' | 'sending') => void;
}
interface CaptureDependencies {
  storage?: Pick<typeof AsyncStorage, 'getItem' | 'setItem'>;
  createId?: () => Promise<string>;
  capturePosition?: (options: FreshPositionOptions) => Promise<FreshUavPosition>;
  requestContext?: (identity: { capture_id: string; request_id: string; user_id: string }, config: UavConnectionConfig, signal?: AbortSignal) => Promise<unknown>;
  postCapture?: (serializedPayload: string, config: UavConnectionConfig, signal?: AbortSignal) => Promise<unknown>;
  fetchImpl?: typeof fetch;
  now?: () => number;
  device?: string;
}
export interface CaptureRetryResult { sent: number; failed: number; differentReceiver: number; needsLocation: number; remaining: number }
const cancelled = (signal?: AbortSignal) => {
  if (signal?.aborted) throw new UavCaptureTransferError('CANCELLED');
};
const networkConfig = (config: UavConnectionConfig): UavConnectionConfig => {
  if (typeof config.wifiSsid !== 'string' || config.wifiSsid.trim().length > 32 || typeof config.testMode !== 'boolean') {
    throw new UavCaptureTransferError('INVALID_CONFIG');
  }
  return { baseUrl: normalizeBaseUrl(config.baseUrl), wifiSsid: config.wifiSsid.trim(), testMode: config.testMode };
};
const sameReceiver = (entry: CaptureEntry, config: UavConnectionConfig) =>
  entry.receiver_base_url === config.baseUrl && entry.wifi_ssid === config.wifiSsid;


export const createUavArrivalCaptureClient = (dependencies: CaptureDependencies = {}) => {
  const storage = dependencies.storage || AsyncStorage;
  const capturePosition = dependencies.capturePosition || captureFreshUavPosition;
  const createId = dependencies.createId || createUavCaptureId;
  let tail: Promise<unknown> = Promise.resolve();
  const locked = <T>(operation: () => Promise<T>): Promise<T> => {
    const result = tail.catch(() => undefined).then(operation);
    tail = result;
    return result;
  };
  const read = async (): Promise<CaptureEntry[]> => {
    const text = await storage.getItem(STORAGE_KEY);
    if (!text) return [];
    const raw = JSON.parse(text);
    if (raw.storage_schema_version !== 2 || !Array.isArray(raw.items)) throw new UavCaptureTransferError('STORAGE_INVALID');
    const ids = new Set<string>();
    return raw.items.map((entry: CaptureEntry) => {
      if (!entry || typeof entry !== 'object') throw new UavCaptureTransferError('STORAGE_INVALID');
      validateCaptureIdentifier(entry.capture_id);
      if (ids.has(entry.capture_id)) throw new UavCaptureTransferError('STORAGE_INVALID');
      ids.add(entry.capture_id);
      const source = validateCaptureSource(entry.source_request);
      const config = networkConfig({ baseUrl: entry.receiver_base_url, wifiSsid: entry.wifi_ssid, testMode: entry.test_mode });
      if (!sameReceiver(entry, config) || typeof entry.device !== 'string' || !entry.device) throw new UavCaptureTransferError('STORAGE_INVALID');
      if (entry.retired_reason !== undefined && (!['NO_COLLECTION_CONTEXT', 'CONTEXT_IDENTITY_CONFLICT'].includes(entry.retired_reason)
        || entry.payload_json !== undefined || entry.receipt !== undefined)) throw new UavCaptureTransferError('STORAGE_INVALID');
      if (entry.context) validateMissionContext(entry.context, { capture_id: entry.capture_id, request_id: source.request_id, user_id: source.user_id });
      if (entry.payload_json !== undefined) {
        if (!entry.context || typeof entry.payload_json !== 'string') throw new UavCaptureTransferError('STORAGE_INVALID');
        const payload = JSON.parse(entry.payload_json) as UavCapturePayload;
        if (canonicalV2PayloadJson(payload) !== entry.payload_json || hashV2Payload(payload) !== entry.payload_sha256 || payload.capture_id !== entry.capture_id
          || payload.context_id !== entry.context.context_id || payload.carrier_execution_id !== entry.context.carrier_execution_id
          || payload.carrier_mission_id !== entry.context.carrier_mission_id
          || canonicalPythonJson(payload.source_request) !== canonicalPythonJson(source)
          || payload.device !== entry.device || payload.test_mode !== entry.test_mode) throw new UavCaptureTransferError('STORAGE_INVALID');
        if (entry.receipt) validateCaptureReceipt(entry.receipt, payload);
      } else if (entry.receipt || entry.payload_sha256 !== undefined) throw new UavCaptureTransferError('STORAGE_INVALID');
      return entry;
    });
  };
  const write = async (items: CaptureEntry[]) => {
    const receipts = items.filter(item => item.receipt).slice(-MAX_RECEIPTS);
    await storage.setItem(STORAGE_KEY, JSON.stringify({ storage_schema_version: 2,
      items: items.filter(item => !item.receipt).concat(receipts) }));
  };
  const request = async (path: string, config: UavConnectionConfig, signal?: AbortSignal, body?: string): Promise<unknown> => {
    cancelled(signal);
    const controller = new AbortController();
    const abort = () => controller.abort();
    signal?.addEventListener('abort', abort);
    const timeout = setTimeout(abort, 5000);
    try {
      const response = await (dependencies.fetchImpl || fetch)(`${config.baseUrl}${path}`, {
        method: body === undefined ? 'GET' : 'POST',
        headers: { ...getUavAuthorizationHeaders(), ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
        body, signal: controller.signal,
      });
      const data = await response.json();
      cancelled(signal);
      if (controller.signal.aborted) throw new UavCaptureTransferError('TRANSFER_TIMEOUT');
      if (!response.ok) throw new UavCaptureTransferError(typeof data?.error_code === 'string' ? data.error_code : 'TRANSFER_FAILED');
      return data;
    } finally {
      clearTimeout(timeout);
      signal?.removeEventListener('abort', abort);
    }
  };
  const requestContext = dependencies.requestContext || ((identity, config, signal) => request(
    `/api/v2/mission-context?user_id=${encodeURIComponent(identity.user_id)}&request_id=${encodeURIComponent(identity.request_id)}&capture_id=${encodeURIComponent(identity.capture_id)}`,
    config, signal));
  const postCapture = dependencies.postCapture || ((serialized, config, signal) => request('/api/v2/rescue-captures', config, signal, serialized));
  const send = async (entry: CaptureEntry, items: CaptureEntry[], config: UavConnectionConfig, options: CaptureTransferOptions): Promise<UavCaptureReceipt> => {
    cancelled(options.signal);
    if (!entry.payload_json) throw new UavCaptureTransferError('NEW_LOCATION_REQUIRED');
    options.onStage?.('sending');

    const response = await postCapture(entry.payload_json, config, options.signal);
    cancelled(options.signal);
    const receipt = validateCaptureReceipt(response, JSON.parse(entry.payload_json));
    entry.receipt = receipt;
    await write(items);
    return receipt;
  };
  return {
    capture: (sourceValue: UavRescuePayload, inputConfig: UavConnectionConfig, options: CaptureTransferOptions): Promise<UavCaptureReceipt> => locked(async () => {
      if (!options.wifiConfirmed) throw new UavCaptureTransferError('WIFI_CONFIRMATION_REQUIRED');
      cancelled(options.signal);
      const source = validateCaptureSource(sourceValue);
      const config = networkConfig(inputConfig);
      const items = await read();


      let entry = [...items].reverse().find(item => !item.payload_json && !item.retired_reason && sameReceiver(item, config)
        && canonicalPythonJson(item.source_request) === canonicalPythonJson(source));
      if (!entry) {
        if (items.filter(item => !item.receipt && !item.retired_reason).length >= MAX_PENDING) throw new UavCaptureTransferError('OUTBOX_FULL');
        const captureId = validateCaptureIdentifier(await createId());
        if (items.some(item => item.capture_id === captureId)) throw new UavCaptureTransferError('CAPTURE_ID_CONFLICT');
        entry = { capture_id: captureId, source_request: source, receiver_base_url: config.baseUrl,
          wifi_ssid: config.wifiSsid, test_mode: config.testMode, device: dependencies.device || Platform.OS };
        items.push(entry);
        await write(items);
      }
      options.onStage?.('context');
      cancelled(options.signal);
      const identity = { capture_id: entry.capture_id, request_id: source.request_id, user_id: source.user_id };
      try {
        const verifiedContext = validateMissionContext(await requestContext(identity, config, options.signal), identity);
        if (entry.context && (entry.context.context_id !== verifiedContext.context_id
          || entry.context.carrier_mission_id !== verifiedContext.carrier_mission_id
          || entry.context.carrier_execution_id !== verifiedContext.carrier_execution_id)) {
          throw new UavCaptureTransferError('CONTEXT_IDENTITY_CONFLICT');
        }
        entry.context = verifiedContext;
      } catch (error) {
        if (error instanceof UavCaptureTransferError && (error.code === 'NO_COLLECTION_CONTEXT' || error.code === 'CONTEXT_IDENTITY_CONFLICT')) {
          entry.retired_reason = error.code;
          await write(items);
        }
        throw error;
      }
      await write(items);
      cancelled(options.signal);
      options.onStage?.('location');
      const position = await capturePosition({ signal: options.signal, now: dependencies.now });
      cancelled(options.signal);
      const payload = buildCaptureV2Payload(source, entry.context!, position, entry.device, entry.test_mode);
      entry.payload_json = canonicalV2PayloadJson(payload);
      entry.payload_sha256 = hashV2Payload(payload);
      await write(items);
      return send(entry, items, config, options);
    }),
    retry: (userId: string, inputConfig: UavConnectionConfig, options: CaptureTransferOptions): Promise<CaptureRetryResult> => locked(async () => {
      if (!options.wifiConfirmed) throw new UavCaptureTransferError('WIFI_CONFIRMATION_REQUIRED');
      validateCaptureIdentifier(userId);
      const config = networkConfig(inputConfig);
      const items = await read();
      const result: CaptureRetryResult = { sent: 0, failed: 0, differentReceiver: 0, needsLocation: 0, remaining: 0 };
      for (const item of items.filter(entry => !entry.receipt && !entry.retired_reason && entry.source_request.user_id === userId)) {
        cancelled(options.signal);
        if (!sameReceiver(item, config)) { result.differentReceiver++; continue; }
        if (!item.payload_json) { result.needsLocation++; continue; }
        try { await send(item, items, config, options); result.sent++; } catch { result.failed++; }
      }
      cancelled(options.signal);
      result.remaining = (await read()).filter(entry => !entry.receipt && !entry.retired_reason && entry.source_request.user_id === userId).length;
      return result;
    }),
  };
};
const client = createUavArrivalCaptureClient();
export const captureCurrentSosForUav = client.capture;
export const retrySavedUavCaptures = client.retry;
