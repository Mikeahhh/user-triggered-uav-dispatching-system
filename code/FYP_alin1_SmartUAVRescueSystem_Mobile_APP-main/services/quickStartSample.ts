import { getPositionCaptureTime } from './positionTimestamp';


export const getQuickStartSampleTime = (
  position: { timestamp?: unknown },
  requestStartedAtMs: number,
  receivedAtMs: number,
  previousSampleAtMs: number | null = null,
): { milliseconds: number; iso: string } => {
  const capture = getPositionCaptureTime(position);
  if (
    !Number.isSafeInteger(requestStartedAtMs) || requestStartedAtMs <= 0 ||
    !Number.isSafeInteger(receivedAtMs) || receivedAtMs < requestStartedAtMs
  ) {
    throw new Error('The location request clock window is invalid; request a new location.');
  }
  if (capture.milliseconds < requestStartedAtMs || capture.milliseconds > receivedAtMs) {
    throw new Error('The location sample was not captured during this request; request a fresh location.');
  }
  if (previousSampleAtMs !== null && capture.milliseconds <= previousSampleAtMs) {
    throw new Error('The location sample is repeated or older than the last accepted sample.');
  }
  return capture;
};
