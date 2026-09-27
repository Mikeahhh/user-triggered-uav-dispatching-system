import AsyncStorage from '@react-native-async-storage/async-storage';
import { canonicalV1PayloadJson } from './rescueRecordHash';
import { validateCaptureSource } from './uavCaptureV2';
import type { UavRescuePayload } from './uavRescueClient';

const PREFIX = '@trigger-search/current-sos-v1/';
let tail: Promise<unknown> = Promise.resolve();
const keyFor = (userId: string): string => {
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(userId)) {
    throw new Error('Invalid current SOS user');
  }
  return PREFIX + encodeURIComponent(userId);
};

export const getCurrentSosRequest = async (userId: string): Promise<UavRescuePayload | null> => {
  await tail.catch(() => undefined);
  const raw = await AsyncStorage.getItem(keyFor(userId));
  if (!raw) return null;
  const envelope = JSON.parse(raw);
  if (envelope.storage_schema_version !== 1) throw new Error('Invalid current SOS storage');
  validateCaptureSource(envelope.source_request);
  const source = JSON.parse(canonicalV1PayloadJson(envelope.source_request)) as UavRescuePayload;
  if (source.user_id !== userId) throw new Error('Current SOS user does not match');
  return source;
};


export const saveCurrentSosRequest = (payload: UavRescuePayload): Promise<void> => {
  validateCaptureSource(payload);
  const wire = JSON.parse(JSON.stringify(payload));
  const canonical = canonicalV1PayloadJson(wire);
  const source = JSON.parse(canonical) as UavRescuePayload;
  const operation = tail.catch(() => undefined).then(async () => {
    const key = keyFor(source.user_id);
    const raw = await AsyncStorage.getItem(key);
    if (raw) {
      const old = JSON.parse(raw);
      if (old.storage_schema_version !== 1) throw new Error('Invalid current SOS storage');
      const oldCanonical = canonicalV1PayloadJson(old.source_request);
      if (old.source_request.request_id === source.request_id && oldCanonical !== canonical) {
        throw new Error('An existing SOS request cannot change its content');
      }
    }
    await AsyncStorage.setItem(key, JSON.stringify({ storage_schema_version: 1, source_request: source }));
  });
  tail = operation;
  return operation;
};


export const restoreCurrentSosFromLegacy = async (userId: string, payloads: UavRescuePayload[]): Promise<UavRescuePayload | null> => {
  const existing = await getCurrentSosRequest(userId);
  if (existing) return existing;
  const valid = payloads.filter(payload => payload?.user_id === userId).map(payload => {
    validateCaptureSource(payload);
    return JSON.parse(canonicalV1PayloadJson(payload)) as UavRescuePayload;
  }).sort((a, b) => b.client_timestamp_ms - a.client_timestamp_ms);
  if (!valid.length) return null;
  await saveCurrentSosRequest(valid[0]);
  return getCurrentSosRequest(userId);
};
