import { canonicalPythonJson, canonicalV1PayloadJson, hashCanonicalText, utcTimestamp } from './rescueRecordHash';
import type { UavRescuePayload } from './uavRescueClient';
import type { FreshUavPosition } from './freshUavPosition';

export interface UavMissionContext {
  schema_version: 2;
  status: 'CONTEXT_ISSUED';
  context_id: string;
  capture_id: string;
  request_id: string;
  user_id: string;
  carrier_mission_id: string;
  carrier_execution_id: string;
  carrier_mission_type: string;
  phase: string;
  issued_at: string;
}
export interface UavCapturePayload extends FreshUavPosition {
  schema_version: 2;
  capture_id: string;
  request_id: string;
  user_id: string;
  context_id: string;
  carrier_mission_id: string;
  carrier_execution_id: string;
  source_request: UavRescuePayload;
  device: string;
  test_mode: boolean;
}
export interface UavCaptureReceipt {
  schema_version: 2;
  status: 'STORED';
  capture_id: string;
  request_id: string;
  context_id: string;
  carrier_mission_id: string;
  carrier_execution_id: string;
  payload_sha256: string;
  uav_received_at: string;
  duplicate: boolean;
  receiver_version: string;
  system_release_id: string;
}
const SOURCE_FIELDS = 'schema_version request_id mission_id user_id latitude longitude accuracy captured_at client_timestamp_ms status device gps_points test_mode'.split(' ');
const PAYLOAD_FIELDS = 'schema_version capture_id request_id user_id context_id carrier_mission_id carrier_execution_id source_request latitude longitude accuracy captured_at client_timestamp_ms capture_started_at_ms device test_mode'.split(' ');
const CONTEXT_FIELDS = 'schema_version status context_id capture_id request_id user_id carrier_mission_id carrier_execution_id carrier_mission_type phase issued_at'.split(' ');
const RECEIPT_FIELDS = 'schema_version status capture_id request_id context_id carrier_mission_id carrier_execution_id payload_sha256 uav_received_at duplicate receiver_version system_release_id'.split(' ');
const exact = (value: unknown, fields: string[]): Record<string, any> => {
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || Object.keys(value).sort().join('|') !== [...fields].sort().join('|')
    || fields.some(key => (value as Record<string, unknown>)[key] === undefined)) {
    throw new Error('Invalid capture fields');
  }
  return value as Record<string, any>;
};
export const validateCaptureIdentifier = (value: unknown): string => {
  if (typeof value !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(value) || /\s/.test(value)) {
    throw new Error('Invalid capture identity');
  }
  return value;
};
const carrier = (value: unknown, userId?: string): string => {
  if (typeof value !== 'string') throw new Error('Invalid carrier identity');
  const parts = value.split('/');
  if (parts.length !== 2 || (userId !== undefined && parts[0] !== userId)) throw new Error('Invalid carrier user');
  parts.forEach(validateCaptureIdentifier);
  return value;
};
const normalizeZero = (value: any): any => {
  if (typeof value === 'number') return value === 0 ? 0 : value;
  if (Array.isArray(value)) return value.map(normalizeZero);
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, normalizeZero(item)]));
  return value;
};
export const validateCaptureSource = (value: unknown): UavRescuePayload => {
  const source = exact(value, SOURCE_FIELDS);
  validateCaptureIdentifier(source.user_id);
  validateCaptureIdentifier(source.request_id);
  if (!Array.isArray(source.gps_points)) throw new Error('Invalid source GPS points');
  source.gps_points.forEach((point: unknown) => exact(point, ['latitude', 'longitude', 'captured_at']));
  return normalizeZero(JSON.parse(canonicalV1PayloadJson(source))) as UavRescuePayload;
};
export const validateCaptureV2Payload = (value: unknown): UavCapturePayload => {
  const raw = exact(value, PAYLOAD_FIELDS);
  if (raw.schema_version !== 2) throw new Error('Invalid capture schema');
  ['capture_id', 'request_id', 'user_id', 'context_id', 'carrier_execution_id'].forEach(key => validateCaptureIdentifier(raw[key]));
  carrier(raw.carrier_mission_id, raw.user_id);
  const source = validateCaptureSource(raw.source_request);
  if (source.user_id !== raw.user_id || source.request_id !== raw.request_id) throw new Error('Capture source mismatch');
  for (const [key, limit] of [['latitude', 90], ['longitude', 180]] as const) {
    if (typeof raw[key] !== 'number' || !Number.isFinite(raw[key]) || Math.abs(raw[key]) > limit) throw new Error('Invalid capture coordinates');
  }
  if (raw.accuracy !== null && (typeof raw.accuracy !== 'number' || !Number.isFinite(raw.accuracy) || raw.accuracy < 0)) throw new Error('Invalid capture accuracy');
  for (const key of ['client_timestamp_ms', 'capture_started_at_ms']) {
    if (!Number.isSafeInteger(raw[key]) || raw[key] <= 0) throw new Error('Invalid capture time');
  }
  const captured = utcTimestamp(raw.captured_at, 'capture captured_at');
  const fraction = /\.(\d+)Z$/.exec(captured)?.[1] || '';
  if (raw.capture_started_at_ms > raw.client_timestamp_ms || Date.parse(captured) !== raw.client_timestamp_ms
    || /[1-9]/.test(fraction.slice(3))) throw new Error('Capture sample timestamp mismatch');
  if (typeof raw.device !== 'string' || !raw.device || Array.from(raw.device).length > 32 || typeof raw.test_mode !== 'boolean') throw new Error('Invalid capture device');
  const result = normalizeZero({ ...raw, source_request: source }) as UavCapturePayload;
  canonicalPythonJson(result);
  return result;
};
export const canonicalV2PayloadJson = (value: unknown): string => canonicalPythonJson(validateCaptureV2Payload(value));
export const hashV2Payload = (value: unknown): string => hashCanonicalText(canonicalV2PayloadJson(value));

export const validateMissionContext = (value: unknown, identity: { capture_id: string; request_id: string; user_id: string }): UavMissionContext => {
  const raw = exact(value, CONTEXT_FIELDS);
  if (raw.schema_version !== 2 || raw.status !== 'CONTEXT_ISSUED') throw new Error('Invalid mission context');
  for (const key of ['context_id', 'capture_id', 'request_id', 'user_id', 'carrier_execution_id']) validateCaptureIdentifier(raw[key]);
  for (const key of ['capture_id', 'request_id', 'user_id'] as const) {
    if (raw[key] !== identity[key]) throw new Error('Mission context identity mismatch');
  }
  carrier(raw.carrier_mission_id, identity.user_id);

  validateCaptureIdentifier(raw.carrier_mission_type);
  validateCaptureIdentifier(raw.phase);
  utcTimestamp(raw.issued_at, 'context issued_at');
  return { ...raw } as UavMissionContext;
};
export const buildCaptureV2Payload = (source: UavRescuePayload, context: UavMissionContext, position: FreshUavPosition, device: string): UavCapturePayload => {
  validateMissionContext(context, { capture_id: context.capture_id, request_id: source.request_id, user_id: source.user_id });
  return validateCaptureV2Payload({ schema_version: 2, capture_id: context.capture_id, request_id: source.request_id,
    user_id: source.user_id, context_id: context.context_id, carrier_mission_id: context.carrier_mission_id,
    carrier_execution_id: context.carrier_execution_id, source_request: source, ...position, device, test_mode: false });
};
export const validateCaptureReceipt = (value: unknown, payload: UavCapturePayload): UavCaptureReceipt => {
  const raw = exact(value, RECEIPT_FIELDS);
  if (raw.schema_version !== 2 || raw.status !== 'STORED' || typeof raw.duplicate !== 'boolean') throw new Error('Invalid capture receipt');
  for (const key of ['capture_id', 'request_id', 'context_id', 'carrier_mission_id', 'carrier_execution_id'] as const) {
    if (raw[key] !== payload[key]) throw new Error('Capture receipt identity mismatch');
  }
  if (raw.payload_sha256 !== hashV2Payload(payload)) throw new Error('Capture receipt content mismatch');
  utcTimestamp(raw.uav_received_at, 'capture receipt time');
  for (const key of ['receiver_version', 'system_release_id']) {
    if (typeof raw[key] !== 'string' || !raw[key] || raw[key].length > 128) throw new Error('Invalid capture receipt version');
  }
  return { ...raw } as UavCaptureReceipt;
};
