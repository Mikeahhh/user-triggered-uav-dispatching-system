import SQLite from 'react-native-sqlite-storage';

SQLite.enablePromise(true);

export const DB_NAME = 'location_tracker.db';

export const getDB = async () => {
  const db = await SQLite.openDatabase({
    name: DB_NAME,
    location: 'default',
  });

  console.log('✅ SQLite DB opened');

  await db.executeSql(`
    CREATE TABLE IF NOT EXISTS routes (
      id INTEGER PRIMARY KEY,
      name TEXT,
      start_time TEXT
    );
  `);

  await db.executeSql(`
    CREATE TABLE IF NOT EXISTS locations (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      latitude REAL,
      longitude REAL,
      timestamp TEXT,
      route_id INTEGER,
      synced INTEGER DEFAULT 0
    );
  `);

  console.log('✅ SQLite tables ensured');

  return db;
};
