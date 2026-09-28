import { initializeApp } from 'firebase/app';
import { getFirestore } from 'firebase/firestore';


const app = initializeApp({
  apiKey: 'compile-only-placeholder',
  authDomain: 'compile-only.invalid',
  projectId: 'compile-only-placeholder',
  databaseURL:
    'https://compile-only-placeholder-default-rtdb.firebaseio.com',
  storageBucket: 'compile-only.invalid',
  messagingSenderId: '000000000000',
  appId: '1:000000000000:web:compileonly',
});

export const db = getFirestore(app);
