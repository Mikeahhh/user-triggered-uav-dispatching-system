import SQLite, { SQLiteDatabase } from 'react-native-sqlite-storage';
import { initializePersistentTracking, matchesPersistentTrackingDatabase, clearPersistentTrackingHistory } from '../persistentTracking';
import { assertCloudIdentity, BoundCloudIdentity, requireCloudIdentity } from '../mobileAuth';

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

export type LocalProfile = {
  id?: number; first_name: string; last_name: string; gender: string; phone: string;
  email: string; medical_notes: string; emergency_contacts: string; updated_at?: string;
};

export const getCurrentUserProfile = async (owner: BoundCloudIdentity): Promise<LocalProfile | null> => {
  await initDb();
  await assertCloudIdentity(owner);
  const [result] = await getDb().executeSql(
    'SELECT * FROM user WHERE owner_uid=? AND owner_project=? AND owner_target=? AND phone=? ORDER BY id DESC LIMIT 1',
    [owner.uid, owner.projectId, owner.target, owner.phone],
  );
  await assertCloudIdentity(owner);
  return result.rows.length ? result.rows.item(0) : null;
};

export const saveCurrentUserProfile = async (data: LocalProfile, owner: BoundCloudIdentity): Promise<void> => {
  if (data.phone !== owner.phone) throw new Error('The profile phone must match the administrator binding.');
  const previous = await getCurrentUserProfile(owner);
  await assertCloudIdentity(owner);
  const fields = [data.first_name, data.last_name, data.gender, data.phone, data.email,
    data.medical_notes, data.emergency_contacts];
  if (previous?.id) {
    await getDb().executeSql(
      "UPDATE user SET first_name=?,last_name=?,gender=?,phone=?,email=?,medical_notes=?,emergency_contacts=?,updated_at=datetime('now') WHERE id=? AND owner_uid=? AND owner_project=? AND owner_target=? AND phone=?",
      [...fields, previous.id, owner.uid, owner.projectId, owner.target, owner.phone],
    );
  } else {
    await getDb().executeSql(
      "INSERT INTO user(first_name,last_name,gender,phone,email,medical_notes,emergency_contacts,owner_uid,owner_project,owner_target,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))",
      [...fields, owner.uid, owner.projectId, owner.target],
    );
  }
  await assertCloudIdentity(owner);
};

export const resetDb = async (owner: BoundCloudIdentity): Promise<void> => {
  await initDb();
  await assertCloudIdentity(owner);
  await getDb().executeSql('DELETE FROM user WHERE owner_uid=? AND owner_project=? AND owner_target=? AND phone=?',
    [owner.uid, owner.projectId, owner.target, owner.phone]);
};

export const getRoutes = async (): Promise<any[]> => {
  const owner = await requireCloudIdentity();
  await initDb();
  const [result] = await getDb().executeSql('SELECT * FROM routes WHERE owner_uid=? AND owner_project=? AND target=? AND owner_phone=? ORDER BY start_time DESC',
    [owner.uid, owner.projectId, owner.target, owner.phone]);
  await assertCloudIdentity(owner);
  return Array.from({ length: result.rows.length }, (_, index) => result.rows.item(index));
};

export const getLocationsByRoute = async (routeId: number): Promise<any[]> => {
  const owner = await requireCloudIdentity();
  await initDb();
  const [result] = await getDb().executeSql('SELECT l.* FROM locations l JOIN routes r ON r.id=l.route_id WHERE l.route_id=? AND r.owner_uid=? AND r.owner_project=? AND r.target=? AND r.owner_phone=? ORDER BY l.timestamp',
    [routeId, owner.uid, owner.projectId, owner.target, owner.phone]);
  await assertCloudIdentity(owner);
  return Array.from({ length: result.rows.length }, (_, index) => result.rows.item(index));
};

export const clearTrackingData = async (): Promise<void> => {
  await clearPersistentTrackingHistory();
};
