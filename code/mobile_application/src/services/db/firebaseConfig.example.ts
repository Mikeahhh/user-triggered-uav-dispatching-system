import { initializeApp } from 'firebase/app';
import { getFirestore } from 'firebase/firestore';


const firebaseConfig = {
  apiKey: 'EXTERNAL_FIREBASE_API_KEY',
  authDomain: 'EXTERNAL_FIREBASE_AUTH_DOMAIN',
  projectId: 'EXTERNAL_FIREBASE_PROJECT_ID',
  databaseURL: 'https://EXTERNAL_FIREBASE_PROJECT_ID-default-rtdb.firebaseio.com',
  storageBucket: 'EXTERNAL_FIREBASE_STORAGE_BUCKET',
  messagingSenderId: 'EXTERNAL_FIREBASE_MESSAGING_SENDER_ID',
  appId: 'EXTERNAL_FIREBASE_APP_ID',
};

const app = initializeApp(firebaseConfig);
export const db = getFirestore(app);
