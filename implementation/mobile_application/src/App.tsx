import React, { useState, useEffect } from 'react';
import { SafeAreaView, StyleSheet, ActivityIndicator } from 'react-native';
import { Header, Footer, Content } from './components';
import './translations/i18n';
import { initDb } from './services/db/initDb';
import { pruneExpiredUavOutbox } from './services/uavRescueClient';
import { CloudIdentity, cloudIdentityKey, readCloudIdentity, subscribeCloudIdentity } from './services/mobileAuth';
import { CloudAccountContext } from './services/cloudAccountContext';

const App = () => {
  const [currentPage, setCurrentPage] = useState(1);
  const [dbInitialized, setDbInitialized] = useState(false);
  const [loading, setLoading] = useState(true);
  const [account, setAccount] = useState<CloudIdentity>({ status: 'SIGNED_OUT' });

  useEffect(() => {
    let mounted = true;
    const update = (identity: Parameters<typeof cloudIdentityKey>[0]) => {
      if (mounted) setAccount(identity);
    };
    let receivedEvent = false;
    const unsubscribe = subscribeCloudIdentity(identity => { receivedEvent = true; update(identity); });
    readCloudIdentity().then(identity => { if (!receivedEvent) update(identity); }).catch(() => {});
    return () => { mounted = false; unsubscribe(); };
  }, []);

  useEffect(() => {
    const initializeDatabase = async () => {
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
        <CloudAccountContext.Provider value={account}>
          <Content
            key={cloudIdentityKey(account)}
            currentPage={currentPage}
            onSelectPage={setCurrentPage}
            dbInitialized={dbInitialized}
          />
        </CloudAccountContext.Provider>
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
