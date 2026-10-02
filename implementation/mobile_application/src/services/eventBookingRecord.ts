export interface EventBookingWaypoint {
  latitude: number;
  longitude: number;
}

export interface EventBookingRecord {
  title: string;
  date: string;
  startTime: string;
  endTime: string;
  waypoints: EventBookingWaypoint[];
  createdAt: string;
  expectedEndAtMs?: number;
  endDate?: string;
  routeDistanceM?: number;
  estimatedDurationMinutes?: number;
  walkingSpeedKmh?: number;
  estimationMethod?: string;
}

export interface EventBookingItem extends EventBookingRecord {
  id: string;
}

export type NewEventBookingRecord = EventBookingRecord & {
  expectedEndAtMs: number;
};

interface BuildEventBookingRecordInput {
  title: string;
  selectedDate: Date;
  startTime: Date;
  waypoints: ReadonlyArray<EventBookingWaypoint>;
  createdAt: string;
}

const DATE_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/;
const TIME_PATTERN = /^(\d{2}):(\d{2})$/;
const EVENT_ID_PREFIX = 'event_';

const requireValidDate = (value: Date, label: string): void => {
  if (!Number.isFinite(value.getTime())) {
    throw new Error(`${label} must be a valid Date`);
  }
};

const padTwoDigits = (value: number): string => String(value).padStart(2, '0');

export const formatLocalCalendarDate = (value: Date): string => {
  requireValidDate(value, 'selectedDate');
  return [
    String(value.getFullYear()).padStart(4, '0'),
    padTwoDigits(value.getMonth() + 1),
    padTwoDigits(value.getDate()),
  ].join('-');
};

export const formatLocalClockTime = (value: Date): string => {
  requireValidDate(value, 'time');
  return `${padTwoDigits(value.getHours())}:${padTwoDigits(
    value.getMinutes(),
  )}`;
};

const buildStrictLocalDate = (
  year: number,
  monthIndex: number,
  day: number,
  hour: number,
  minute: number,
): Date => {
  const value = new Date(year, monthIndex, day, hour, minute, 0, 0);
  if (
    value.getFullYear() !== year ||
    value.getMonth() !== monthIndex ||
    value.getDate() !== day ||
    value.getHours() !== hour ||
    value.getMinutes() !== minute ||
    value.getSeconds() !== 0 ||
    value.getMilliseconds() !== 0
  ) {
    throw new Error('Local booking date and time are invalid');
  }
  return value;
};

const parseDateParts = (date: string): [number, number, number] => {
  const match = DATE_PATTERN.exec(date);
  if (!match) {
    throw new Error('Booking date must use YYYY-MM-DD');
  }
  return [Number(match[1]), Number(match[2]) - 1, Number(match[3])];
};

const parseTimeParts = (time: string): [number, number] => {
  const match = TIME_PATTERN.exec(time);
  if (!match) {
    throw new Error('Booking time must use HH:mm');
  }
  return [Number(match[1]), Number(match[2])];
};

export const parseLocalCalendarDate = (date: string): Date => {
  const [year, monthIndex, day] = parseDateParts(date);
  return buildStrictLocalDate(year, monthIndex, day, 12, 0);
};

export const parseLocalDateTime = (date: string, time: string): Date => {
  const [year, monthIndex, day] = parseDateParts(date);
  const [hour, minute] = parseTimeParts(time);
  return buildStrictLocalDate(year, monthIndex, day, hour, minute);
};

export const buildExpectedEndAtMs = (
  selectedDate: Date,
  endTime: Date,
): number => {
  requireValidDate(selectedDate, 'selectedDate');
  requireValidDate(endTime, 'endTime');

  return buildStrictLocalDate(
    selectedDate.getFullYear(),
    selectedDate.getMonth(),
    selectedDate.getDate(),
    endTime.getHours(),
    endTime.getMinutes(),
  ).getTime();
};

export const WALKING_SPEED_KMH = 4;

export const routeDistanceMetres = (
  waypoints: ReadonlyArray<EventBookingWaypoint>,
): number => {
  if (waypoints.length < 2) {
    throw new Error('A route needs at least two coordinates');
  }
  waypoints.forEach(point => {
    if (!Number.isFinite(point.latitude) || Math.abs(point.latitude) > 90 ||
        !Number.isFinite(point.longitude) || Math.abs(point.longitude) > 180) {
      throw new Error('Route coordinates are invalid');
    }
  });
  const rad = Math.PI / 180;
  let distance = 0;
  for (let i = 1; i < waypoints.length; i += 1) {
    const a = waypoints[i - 1];
    const b = waypoints[i];
    const lat = (b.latitude - a.latitude) * rad;
    const lon = (b.longitude - a.longitude) * rad;
    const h = Math.sin(lat / 2) ** 2 + Math.cos(a.latitude * rad) *
      Math.cos(b.latitude * rad) * Math.sin(lon / 2) ** 2;
    distance += 2 * 6371008.8 * Math.asin(Math.sqrt(Math.min(1, Math.max(0, h))));
  }
  if (distance <= 0) {
    throw new Error('The planned route must have a positive distance');
  }
  return distance;
};

export const estimateBookingTime = (
  selectedDate: Date,
  startTime: Date,
  waypoints: ReadonlyArray<EventBookingWaypoint>,
) => {
  requireValidDate(selectedDate, 'selectedDate');
  requireValidDate(startTime, 'startTime');
  const startAtMs = buildExpectedEndAtMs(selectedDate, startTime);
  const routeDistanceM = routeDistanceMetres(waypoints);
  const estimatedDurationMinutes = Math.ceil(routeDistanceM / (WALKING_SPEED_KMH * 1000) * 60);
  const expectedEndAtMs = startAtMs + estimatedDurationMinutes * 60000;
  requireValidDate(new Date(expectedEndAtMs), 'estimatedEnd');
  return {
    expectedEndAtMs,
    routeDistanceM,
    estimatedDurationMinutes,
    walkingSpeedKmh: WALKING_SPEED_KMH,
    estimationMethod: 'route_distance_fixed_speed_v1',
  };
};

export const buildEventBookingRecord = ({
  title,
  selectedDate,
  startTime,
  waypoints,
  createdAt,
}: BuildEventBookingRecordInput): NewEventBookingRecord => {
  const estimate = estimateBookingTime(selectedDate, startTime, waypoints);
  const end = new Date(estimate.expectedEndAtMs);
  return {
    title: title.trim(),
    date: formatLocalCalendarDate(selectedDate),
    startTime: formatLocalClockTime(startTime),
    endDate: formatLocalCalendarDate(end),
    endTime: formatLocalClockTime(end),
    waypoints: waypoints.map(point => ({...point})),
    createdAt,
    ...estimate,
  };
};

export const createTimeBasedEventId = (
  nowMs: number,
  existingIds: ReadonlyArray<string>,
): string => {
  if (!Number.isSafeInteger(nowMs) || nowMs < 0) {
    throw new Error('Event ID timestamp must be a non-negative safe integer');
  }

  const occupiedIds = new Set(existingIds);
  let candidateMs = nowMs;
  while (occupiedIds.has(`${EVENT_ID_PREFIX}${candidateMs}`)) {
    if (candidateMs === Number.MAX_SAFE_INTEGER) {
      throw new Error('Unable to allocate a unique Event Booking ID');
    }
    candidateMs += 1;
  }

  return `${EVENT_ID_PREFIX}${candidateMs}`;
};
