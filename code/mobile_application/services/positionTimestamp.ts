export const getPositionCaptureTime = (position: { timestamp?: unknown }): {
  milliseconds: number;
  iso: string;
} => {
  const milliseconds = position.timestamp;
  if (
    typeof milliseconds !== 'number' ||
    !Number.isSafeInteger(milliseconds) ||
    milliseconds <= 0 ||
    !Number.isFinite(new Date(milliseconds).getTime())
  ) {
    throw new Error('The location sample has no valid capture time; request a new location.');
  }
  return { milliseconds, iso: new Date(milliseconds).toISOString() };
};
