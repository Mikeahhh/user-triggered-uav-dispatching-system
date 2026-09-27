import { buildCaptureV2Payload, hashV2Payload, UavMissionContext } from '../services/uavCaptureV2';
import type { UavRescuePayload } from '../services/uavRescueClient';
export const source: UavRescuePayload = { schema_version: 1, request_id: 'request1', user_id: 'user1', mission_id: 'user1/request1',
  latitude: 22, longitude: 114, accuracy: null, captured_at: '2026-09-09T01:00:00.000Z', client_timestamp_ms: 1788915600100,
  status: 'PENDING', device: 'android', gps_points: [{ latitude: 22, longitude: 114, captured_at: '2026-09-09T01:00:00.000Z' }], test_mode: true };
export const context: UavMissionContext = { schema_version: 2, status: 'CONTEXT_ISSUED', context_id: 'context1', capture_id: 'capture1',
  request_id: 'request1', user_id: 'user1', carrier_mission_id: 'user1/booking1', carrier_execution_id: 'execution1',
  carrier_mission_type: 'GOTO', phase: 'HOVERING', issued_at: '2026-09-09T01:01:00Z' };
export const position = { latitude: 22.1, longitude: 114.1, accuracy: 4, captured_at: '2026-09-09T01:01:01.123Z',
  client_timestamp_ms: Date.parse('2026-09-09T01:01:01.123Z'), capture_started_at_ms: Date.parse('2026-09-09T01:01:01.000Z') };
export const makePayload = () => buildCaptureV2Payload(source, context, position, 'android', true);
export const receiptFor = (payload = makePayload()) => ({ schema_version: 2, status: 'STORED',
  capture_id: payload.capture_id, request_id: payload.request_id, context_id: payload.context_id,
  carrier_mission_id: payload.carrier_mission_id, carrier_execution_id: payload.carrier_execution_id,
  payload_sha256: hashV2Payload(payload), uav_received_at: '2026-09-09T01:01:02Z', duplicate: false,
  receiver_version: 'test', system_release_id: 'test' });
