import { initDb, getDb } from './initDb';
export { DB_NAME } from './initDb';

export const getDB = async () => {
  await initDb();
  return getDb();
};
