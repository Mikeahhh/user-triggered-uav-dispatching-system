export const formatQuickStartTimestamp = (timestamp: string): string => {
  const parsedMs = Date.parse(timestamp);
  if (!Number.isFinite(parsedMs)) return '--:--';
  return new Date(parsedMs).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
  });
};
