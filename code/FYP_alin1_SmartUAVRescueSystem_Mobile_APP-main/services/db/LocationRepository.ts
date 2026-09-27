import { getDB } from './database';

export const LocationRepository = {
  async createRoute(id: number, name: string) {
    const db = await getDB();
    await db.executeSql(
      'INSERT INTO routes (id, name, start_time) VALUES (?, ?, ?)',
      [id, name, new Date().toISOString()]
    );
  },

  async insertLocation(
    latitude: number,
    longitude: number,
    routeId: number
  ) {
    const db = await getDB();
    await db.executeSql(
      `INSERT INTO locations (latitude, longitude, timestamp, route_id)
       VALUES (?, ?, ?, ?)`,
      [latitude, longitude, new Date().toISOString(), routeId]
    );
  },

  async getLocationsByRoute(routeId: number) {
    const db = await getDB();
    const [result] = await db.executeSql(
      'SELECT * FROM locations WHERE route_id = ? ORDER BY timestamp ASC',
      [routeId]
    );
    return result.rows.raw();
  },

  async getUnsyncedLocations() {
    const db = await getDB();
    const [result] = await db.executeSql(
      'SELECT * FROM locations WHERE synced = 0'
    );
    return result.rows.raw();
  },

  async markLocationsSynced(ids: number[]) {
    if (!ids.length) return;
    const db = await getDB();
    const placeholders = ids.map(() => '?').join(',');
    await db.executeSql(
      `UPDATE locations SET synced = 1 WHERE id IN (${placeholders})`,
      ids
    );
  },
};
