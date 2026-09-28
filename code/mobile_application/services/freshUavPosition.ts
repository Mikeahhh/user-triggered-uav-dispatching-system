import Geolocation from '@react-native-community/geolocation';
import { PermissionsAndroid, Platform } from 'react-native';
import { getPositionCaptureTime } from './positionTimestamp';

export class FreshUavPositionError extends Error {}
export interface FreshUavPosition {
  latitude: number;
  longitude: number;
  accuracy: number | null;
  captured_at: string;
  client_timestamp_ms: number;
  capture_started_at_ms: number;
}
export interface FreshPositionOptions {
  signal?: AbortSignal;
  now?: () => number;
  timeoutMs?: number;
}

export const captureFreshUavPosition = async (
  options: FreshPositionOptions = {},
): Promise<FreshUavPosition> => {
  if (options.signal?.aborted) throw new FreshUavPositionError('Location capture was cancelled');
  if (Platform.OS === 'android') {
    const result = await PermissionsAndroid.request(PermissionsAndroid.PERMISSIONS.ACCESS_FINE_LOCATION);
    if (result !== PermissionsAndroid.RESULTS.GRANTED) {
      throw new FreshUavPositionError('Location permission was denied');
    }
  }
  if (options.signal?.aborted) throw new FreshUavPositionError('Location capture was cancelled');
  const now = options.now || Date.now;
  const started = now();
  const timeoutMs = options.timeoutMs ?? 15000;
  if (!Number.isSafeInteger(started) || started <= 0 || !Number.isFinite(timeoutMs) || timeoutMs <= 0) {
    throw new FreshUavPositionError('Invalid location request time');
  }
  return new Promise((resolve, reject) => {
    let settled = false;
    const finish = (error?: Error, value?: FreshUavPosition) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      options.signal?.removeEventListener('abort', abort);
      if (error) reject(error); else resolve(value!);
    };
    const abort = () => finish(new FreshUavPositionError('Location capture was cancelled'));
    const timer = setTimeout(() => finish(new FreshUavPositionError('A new location could not be obtained in time')), timeoutMs);
    options.signal?.addEventListener('abort', abort);
    try {
      Geolocation.getCurrentPosition(position => {
        if (settled) return;
        try {
          const captured = getPositionCaptureTime(position);
          const completed = now();
          if (!Number.isSafeInteger(completed) || captured.milliseconds < started || captured.milliseconds > completed) {
            throw new FreshUavPositionError('The location is not a new sample from this request');
          }
          const { latitude, longitude, accuracy } = position.coords;
          if (!Number.isFinite(latitude) || !Number.isFinite(longitude)
            || Math.abs(latitude) > 90 || Math.abs(longitude) > 180
            || (accuracy != null && (!Number.isFinite(accuracy) || accuracy < 0))) {
            throw new FreshUavPositionError('The new location is invalid');
          }
          finish(undefined, { latitude, longitude, accuracy: accuracy ?? null,
            captured_at: captured.iso, client_timestamp_ms: captured.milliseconds,
            capture_started_at_ms: started });
        } catch (error) {
          finish(error instanceof FreshUavPositionError ? error : new FreshUavPositionError('The new location is invalid'));
        }
      }, () => finish(new FreshUavPositionError('A new location is unavailable')), {
        enableHighAccuracy: true, maximumAge: 0, timeout: timeoutMs,
      });
    } catch {
      finish(new FreshUavPositionError('A new location is unavailable'));
    }
  });
};
