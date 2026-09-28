import SQLite, { SQLiteDatabase } from 'react-native-sqlite-storage';

SQLite.enablePromise(true);

const DB_NAME = 'location_tracker.db';
const DB_VERSION = 3;

let dbInstance: SQLiteDatabase | null = null;


const migrations = [
  async (db: SQLiteDatabase) => {
    console.log('🚀 Migration 1: Ensuring routes table has session_id');


    await db.executeSql('DROP TABLE IF EXISTS routes_new');
    await db.executeSql(`
      CREATE TABLE routes_new (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        start_time TEXT,
        session_id TEXT UNIQUE,
        status TEXT DEFAULT 'active'
      );
    `);


    try {
      await db.executeSql(`
        INSERT INTO routes_new (id, name, start_time, status)
        SELECT id, name, start_time, COALESCE(status, 'active') FROM routes;
      `);
    } catch (error) {
      console.log('No existing routes to migrate');
    }

    await db.executeSql('DROP TABLE IF EXISTS routes');
    await db.executeSql('ALTER TABLE routes_new RENAME TO routes');
  },

  async (db: SQLiteDatabase) => {
    console.log('🚀 Migration 2: Ensuring locations table has all columns');


    await db.executeSql('DROP TABLE IF EXISTS locations_new');
    await db.executeSql(`
      CREATE TABLE locations_new (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        route_id INTEGER,
        latitude REAL,
        longitude REAL,
        accuracy REAL,
        altitude REAL,
        speed REAL,
        heading REAL,
        timestamp TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (route_id) REFERENCES routes(id) ON DELETE CASCADE
      );
    `);


    try {
      await db.executeSql(`
        INSERT INTO locations_new (id, route_id, latitude, longitude, timestamp, created_at)
        SELECT id, route_id, latitude, longitude, timestamp, created_at FROM locations;
      `);
    } catch (error) {
      console.log('No existing locations to migrate');
    }

    await db.executeSql('DROP TABLE IF EXISTS locations');
    await db.executeSql('ALTER TABLE locations_new RENAME TO locations');
  }
];

export const initDb = async (): Promise<void> => {
  if (dbInstance) {
    console.log('📁 Database already initialized');
    return;
  }

  console.log(`🔥 initDb() called - Target version: ${DB_VERSION}`);

  try {

    dbInstance = await SQLite.openDatabase({
      name: DB_NAME,
      location: 'default',
    });
    console.log('✅ SQLite DB opened');


    await dbInstance.executeSql(`
      CREATE TABLE IF NOT EXISTS db_version (
        id INTEGER PRIMARY KEY,
        version INTEGER NOT NULL
      );
    `);


    let currentVersion = 0;
    const [versionResult] = await dbInstance.executeSql('SELECT version FROM db_version WHERE id = 1');
    if (versionResult.rows.length > 0) {
      currentVersion = versionResult.rows.item(0).version;
    } else {
      await dbInstance.executeSql('INSERT INTO db_version (id, version) VALUES (1, 0)');
      currentVersion = 0;
    }

    console.log(`📊 Current DB version: ${currentVersion}, Target: ${DB_VERSION}`);


    await dbInstance.executeSql(`
      CREATE TABLE IF NOT EXISTS user (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        first_name TEXT,
        last_name TEXT,
        gender TEXT,
        phone TEXT,
        email TEXT,
        medical_notes TEXT,
        emergency_contacts TEXT,
        created_at TEXT,
        updated_at TEXT
      );
    `);


    await dbInstance.executeSql(`
      CREATE TABLE IF NOT EXISTS routes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        start_time TEXT,
        session_id TEXT,
        status TEXT DEFAULT 'active'
      );
    `);

    await dbInstance.executeSql(`
      CREATE TABLE IF NOT EXISTS locations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        route_id INTEGER,
        latitude REAL,
        longitude REAL,
        timestamp TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
      );
    `);


    if (currentVersion < DB_VERSION) {
      console.log(`🔄 Running migrations ${currentVersion} → ${DB_VERSION}`);

      for (let v = currentVersion; v < DB_VERSION; v++) {
        const migrationIndex = v;
        if (migrationIndex < migrations.length) {
          console.log(`🔄 Applying migration ${v + 1}`);
          try {
            await migrations[migrationIndex](dbInstance);
            console.log(`✅ Migration ${v + 1} successful`);
          } catch (error) {
            console.error(`❌ Migration ${v + 1} failed:`, error);
          }
        }
      }


      await dbInstance.executeSql('UPDATE db_version SET version = ? WHERE id = 1', [DB_VERSION]);
      console.log(`✅ Database upgraded to version ${DB_VERSION}`);
    }


    await dbInstance.executeSql(`
      CREATE INDEX IF NOT EXISTS idx_locations_route_id ON locations(route_id)
    `);
    await dbInstance.executeSql(`
      CREATE INDEX IF NOT EXISTS idx_locations_timestamp ON locations(timestamp)
    `);

    console.log('✅ Database initialization complete');

  } catch (error) {
    console.error('❌ Database initialization failed:', error);
    dbInstance = null;
    throw error;
  }
};

export const getDb = (): SQLiteDatabase => {
  if (!dbInstance) {
    throw new Error('Database not initialized. Call initDb() first.');
  }
  return dbInstance;
};

export const resetDb = async (): Promise<void> => {
  if (!dbInstance) return;
  await dbInstance.executeSql('DELETE FROM user');
  console.log('🧹 User table cleared');
};

export const getRoutes = async (): Promise<any[]> => {
  const db = getDb();
  const [results] = await db.executeSql('SELECT * FROM routes ORDER BY start_time DESC');
  const routes = [];
  for (let i = 0; i < results.rows.length; i++) {
    routes.push(results.rows.item(i));
  }
  return routes;
};

export const getLocationsByRoute = async (routeId: number): Promise<any[]> => {
  const db = getDb();
  const [results] = await db.executeSql(
    'SELECT * FROM locations WHERE route_id = ? ORDER BY timestamp',
    [routeId]
  );
  const locations = [];
  for (let i = 0; i < results.rows.length; i++) {
    locations.push(results.rows.item(i));
  }
  return locations;
};

export const clearTrackingData = async (): Promise<void> => {
  const db = getDb();
  await db.executeSql('DELETE FROM locations');
  await db.executeSql('DELETE FROM routes');
  console.log('🧹 Tracking data cleared');
};

export const recreateDatabase = async (): Promise<boolean> => {
  if (dbInstance) {
    await dbInstance.close();
    dbInstance = null;
  }

  try {
    const SQLiteModule = require('react-native-sqlite-storage');
    await SQLiteModule.deleteDatabase({
      name: DB_NAME,
      location: 'default',
    });
    console.log('🗑️ Database file deleted');
    return true;
  } catch (error) {
    console.error('❌ Error deleting database:', error);
    return false;
  }
};
