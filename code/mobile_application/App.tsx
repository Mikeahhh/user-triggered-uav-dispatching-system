import React, { useState, useEffect } from 'react';
import { SafeAreaView, StyleSheet, ActivityIndicator } from 'react-native';
import { Header, Footer, Content } from './components';
import './translations/i18n';
import { initDb } from './services/db/initDb';
import { SCREENSHOT_DEMO } from './services/buildMode';
import { pruneExpiredUavOutbox } from './services/uavRescueClient';

const App = () => {
  const [currentPage, setCurrentPage] = useState(SCREENSHOT_DEMO ? 7 : 1);
  const [dbInitialized, setDbInitialized] = useState(SCREENSHOT_DEMO);
  const [loading, setLoading] = useState(!SCREENSHOT_DEMO);

  useEffect(() => {
    const initializeDatabase = async () => {
      if (SCREENSHOT_DEMO) return;
      try {
        console.log('🔥 App starting — initializing database');
        await Promise.all([initDb(), pruneExpiredUavOutbox()]);
        console.log('✅ Database initialized successfully');
        setDbInitialized(true);
      } catch (error) {
        console.error('❌ Failed to initialize database:', error);
      } finally {
        setLoading(false);
      }
    };

    initializeDatabase();
  }, []);

  if (loading) {
    return (
      <SafeAreaView style={styles.loadingContainer}>
        <ActivityIndicator size="large" color="#0000ff" />
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.container}>
      <Header currentPage={currentPage} />
      {dbInitialized && (
        <Content
          currentPage={currentPage}
          onSelectPage={setCurrentPage}
          dbInitialized={dbInitialized}
        />
      )}
      <Footer
        currentPage={currentPage}
        onSelectPage={setCurrentPage}
      />
    </SafeAreaView>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#FFFFFF',
  },
  loadingContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
  },
});

export default App;
