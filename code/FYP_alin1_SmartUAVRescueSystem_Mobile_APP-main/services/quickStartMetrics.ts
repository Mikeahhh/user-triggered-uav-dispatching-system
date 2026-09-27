export interface QuickStartMetricPoint {
  lat: number;
  lng: number;
  timestamp: string;
}

export const calculateQuickStartDistance = (
  points: ReadonlyArray<Pick<QuickStartMetricPoint, 'lat' | 'lng'>>,
): string => {
  if (points.length < 2) return '0m';

  let totalMetres = 0;
  for (let index = 1; index < points.length; index += 1) {
    const previous = points[index - 1];
    const current = points[index];
    const latitudeDelta = (current.lat - previous.lat) * 111320;
    const longitudeDelta =
      (current.lng - previous.lng) *
      111320 *
      Math.cos((previous.lat * Math.PI) / 180);
    totalMetres += Math.sqrt(latitudeDelta ** 2 + longitudeDelta ** 2);
  }

  return totalMetres < 1000
    ? `${Math.round(totalMetres)}m`
    : `${(totalMetres / 1000).toFixed(1)}km`;
};

export const calculateQuickStartDuration = (
  points: ReadonlyArray<Pick<QuickStartMetricPoint, 'timestamp'>>,
): string => {
  if (points.length < 2) return '0 min';

  const startMs = Date.parse(points[0].timestamp);
  const endMs = Date.parse(points[points.length - 1].timestamp);
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs < startMs) {
    return '0 min';
  }

  return `${Math.round((endMs - startMs) / 60000)} min`;
};

export const formatQuickStartTimestamp = (timestamp: string): string => {
  const parsedMs = Date.parse(timestamp);
  if (!Number.isFinite(parsedMs)) return '--:--';
  return new Date(parsedMs).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
  });
};
