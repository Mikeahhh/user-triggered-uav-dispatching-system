import './firebaseConfig';
import { getApp } from 'firebase/app';
import type { FirebaseOptions } from 'firebase/app';

const PROJECT_ID_PATTERN = /^[a-z][a-z0-9-]{4,28}[a-z0-9]$/;
const FIREBASE_KEY_FORBIDDEN_CHARACTERS = '.#$[]/';

type RealtimeDatabaseOptions = Pick<
  FirebaseOptions,
  'databaseURL' | 'projectId'
>;

const requireProjectId = (projectId: unknown): string => {
  if (typeof projectId !== 'string') {
    throw new Error('Firebase projectId is required for Realtime Database');
  }

  const normalized = projectId.trim();
  if (!PROJECT_ID_PATTERN.test(normalized)) {
    throw new Error('Firebase projectId is invalid');
  }
  return normalized;
};

const isValidPathSegment = (value: string): boolean =>
  value.length > 0 &&
  Array.from(value).every(character => {
    const codePoint = character.codePointAt(0) ?? 0;
    return (
      !FIREBASE_KEY_FORBIDDEN_CHARACTERS.includes(character) &&
      codePoint > 31 &&
      codePoint !== 127
    );
  });


export const describeRealtimeDatabaseTarget = (
  options: RealtimeDatabaseOptions,
): { url: string; source: 'explicit' | 'derived' } => {
  const projectId = requireProjectId(options.projectId);
  const configuredUrl =
    typeof options.databaseURL === 'string' ? options.databaseURL.trim() : '';
  const candidate =
    configuredUrl || `https://${projectId}-default-rtdb.firebaseio.com`;

  if (Array.from(candidate).some(character => {
    const code = character.codePointAt(0)!;
    return /\s/u.test(character) || code <= 31 || code === 127;
  })) {
    throw new Error('Firebase databaseURL contains whitespace or control characters');
  }

  let parsed: URL;
  try {
    parsed = new URL(candidate);
  } catch {
    throw new Error('Firebase databaseURL is not a valid URL');
  }

  if (
    parsed.protocol !== 'https:' ||
    parsed.username ||
    parsed.password ||
    parsed.port ||
    (parsed.pathname !== '' && parsed.pathname !== '/') ||
    parsed.search ||
    parsed.hash
  ) {
    throw new Error('Firebase databaseURL must be a clean HTTPS origin');
  }

  const hostname = parsed.hostname.toLowerCase();
  const legacyMatch = hostname.match(/^([a-z][a-z0-9-]*)\.firebaseio\.com$/);
  const regionalMatch = hostname.match(
    /^([a-z][a-z0-9-]*)\.([a-z0-9-]+)\.firebasedatabase\.app$/,
  );
  const databaseId = legacyMatch?.[1] ?? regionalMatch?.[1];
  const allowedDefaultDatabaseIds = new Set([
    projectId,
    `${projectId}-default-rtdb`,
  ]);

  if (!databaseId || !allowedDefaultDatabaseIds.has(databaseId)) {
    throw new Error(
      'Firebase databaseURL host is not the configured project default',
    );
  }

  return { url: parsed.origin, source: configuredUrl ? 'explicit' : 'derived' };
};

export const resolveRealtimeDatabaseUrl = (
  options: RealtimeDatabaseOptions,
): string => describeRealtimeDatabaseTarget(options).url;

export const getRealtimeDatabaseUrl = (): string =>
  resolveRealtimeDatabaseUrl(getApp().options);

export const buildRealtimeDatabaseRestUrl = (
  ...pathSegments: Array<string | number>
): string => {
  if (pathSegments.length === 0) {
    throw new Error('Firebase Realtime Database path is required');
  }

  const encodedPath = pathSegments
    .map(segment => {
      const value = String(segment);
      if (!isValidPathSegment(value)) {
        throw new Error('Firebase Realtime Database path segment is invalid');
      }
      return encodeURIComponent(value);
    })
    .join('/');

  return `${getRealtimeDatabaseUrl()}/${encodedPath}.json`;
};
