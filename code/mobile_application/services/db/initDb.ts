import SQLite, { SQLiteDatabase } from 'react-native-sqlite-storage';
import { initializePersistentTracking, matchesPersistentTrackingDatabase, clearPersistentTrackingHistory } from '../persistentTracking';

SQLite.enablePromise(true);
export const DB_NAME = 'location_tracker.db';
let dbInstance: SQLiteDatabase | null = null;
let initialization: Promise<void> | null = null;

const initializeDbOnce = async (): Promise<void> => {
  await initializePersistentTracking();
  const opened = await SQLite.openDatabase({ name: DB_NAME, location: 'default' });
  const [databases] = await opened.executeSql('PRAGMA database_list');
  let actualPath = '';
  for (let index = 0; index < databases.rows.length; index += 1) {
    const entry = databases.rows.item(index);
    if (entry.name === 'main') actualPath = entry.file;
  }
  if (!actualPath || !(await matchesPersistentTrackingDatabase(actualPath))) {
    await opened.close();
    throw new Error('Android recording and the application opened different databases.');
  }
  dbInstance = opened;
};

export const initDb = (): Promise<void> => {
  if (dbInstance) return Promise.resolve();
  if (!initialization) initialization = initializeDbOnce().finally(() => { initialization = null; });
  return initialization;
};

export const getDb = (): SQLiteDatabase => {
  if (!dbInstance) throw new Error('Database not initialized. Call initDb() first.');
  return dbInstance;
};

export const resetDb = async (): Promise<void> => {
  await initDb();
  await getDb().executeSql('DELETE FROM user');
};

export const getRoutes = async (): Promise<any[]> => {
  const [result] = await getDb().executeSql('SELECT * FROM routes ORDER BY start_time DESC');
  return Array.from({ length: result.rows.length }, (_, index) => result.rows.item(index));
};

export const getLocationsByRoute = async (routeId: number): Promise<any[]> => {
  const [result] = await getDb().executeSql('SELECT * FROM locations WHERE route_id = ? ORDER BY timestamp', [routeId]);
  return Array.from({ length: result.rows.length }, (_, index) => result.rows.item(index));
};

export const clearTrackingData = async (): Promise<void> => {
  await clearPersistentTrackingHistory();
};
