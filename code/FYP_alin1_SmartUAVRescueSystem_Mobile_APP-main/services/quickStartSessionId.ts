const QUICK_START_SESSION_PREFIX = 'session_';

export const createQuickStartSessionIdGenerator = () => {
  let lastAllocatedTimestampMs = -1;

  return (nowMs: number = Date.now()): string => {
    if (!Number.isSafeInteger(nowMs) || nowMs < 0) {
      throw new Error(
        'Quick Start session timestamp must be a non-negative safe integer',
      );
    }

    const timestampMs = Math.max(nowMs, lastAllocatedTimestampMs + 1);
    if (!Number.isSafeInteger(timestampMs)) {
      throw new Error('Unable to allocate a unique Quick Start session ID');
    }

    lastAllocatedTimestampMs = timestampMs;
    return `${QUICK_START_SESSION_PREFIX}${timestampMs}`;
  };
};

export const createQuickStartSessionId =
  createQuickStartSessionIdGenerator();
