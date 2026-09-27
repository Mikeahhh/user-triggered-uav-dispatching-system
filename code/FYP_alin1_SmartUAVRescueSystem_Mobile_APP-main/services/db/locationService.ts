import { initDb } from './initDb';
import { db } from './firebaseConfig';
import { collection, addDoc } from 'firebase/firestore';
import { Alert } from 'react-native';

interface Location {
  latitude: number;
  longitude: number;
}


export const saveLocationSQLite = async (_userId: string, _location: Location): Promise<void> => {
  try {
    await initDb();

    console.log("Location saved to SQLite.");
  } catch (error) {
    console.error("Error saving location to SQLite:", error);
    Alert.alert("Error", "Unable to save location. Please try again.");
  }
};


export const syncLocationToFirebase = async (userId: string, location: Location): Promise<void> => {
  try {
    const locationRef = collection(db, 'locations');
    await addDoc(locationRef, { userId, ...location });
    console.log("Location synchronized to Firebase.");
  } catch (error) {
    console.error("Error synchronizing location to Firebase:", error);
    Alert.alert("Error", "Unable to sync location. Please try again.");
  }
};
