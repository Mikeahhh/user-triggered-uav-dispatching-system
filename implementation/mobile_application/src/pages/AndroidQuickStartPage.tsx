import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, AppState, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { useTranslation } from 'react-i18next';
import MapView, { Polyline } from 'react-native-maps';
import { getDb, initDb } from '../services/db/initDb';
import {
  readTrackingSnapshot, resumePersistentTracking, retryPersistentSync,
  startPersistentTracking, stopPersistentTracking, TrackingSnapshot,
} from '../services/persistentTracking';
import { formatQuickStartTimestamp } from '../services/quickStartMetrics';

const AndroidQuickStartPage = () => {
  const { t } = useTranslation();
  const [snapshot, setSnapshot] = useState<TrackingSnapshot | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const mounted = useRef(false);
  const updating = useRef(false);
  const refresh = useCallback(async () => {
    if (updating.current) return;
    updating.current = true;
    try {
      const value = await readTrackingSnapshot();
      if (mounted.current) setSnapshot(value);
    } catch (failure) {
      if (mounted.current) setError(failure instanceof Error ? failure.message : String(failure));
    } finally { updating.current = false; }
  }, []);

  useEffect(() => {
    mounted.current = true;
    initDb().then(refresh).catch(failure => {
      if (mounted.current) setError(String(failure));
    });
    const timer = setInterval(refresh, 1000);
    const subscription = AppState.addEventListener('change', state => {
      if (state === 'active') refresh();
    });
    return () => {
      mounted.current = false;
      clearInterval(timer);
      subscription.remove();
    };
  }, [refresh]);

  const perform = async (operation: () => Promise<unknown>) => {
    if (busy) return;
    setBusy(true);
    setError('');
    try { await operation(); await refresh(); }
    catch (failure) {
      const message = failure instanceof Error ? failure.message : String(failure);
      if (mounted.current) {
        setError(message);
        Alert.alert(t('quickStartPage.alert.startFailed.title'), message);
      }
    } finally { if (mounted.current) setBusy(false); }
  };

  const start = async () => {
    await initDb();
    const [result] = await getDb().executeSql('SELECT phone FROM user ORDER BY id DESC LIMIT 1');
    const phone = result.rows.length ? String(result.rows.item(0).phone ?? '').replace(/[^0-9]/g, '') : '';
    if (!phone) throw new Error(t('quickStartPage.alert.startFailed.missingPhone'));
    await startPersistentTracking(phone);
  };

  const session = snapshot?.session;
  const interrupted = !!session && (session.state === 'INTERRUPTED' || !snapshot?.serviceRunning);
  const points = snapshot?.points ?? [];
  const stateKey = interrupted ? 'persistentTracking.interrupted' :
    session?.state === 'STARTING' ? 'quickStartPage.status.locating' :
      session ? 'quickStartPage.status.active' : (snapshot?.totalPoints ?? 0) > 0
        ? 'persistentTracking.stopped' : 'quickStartPage.status.inactive';

  return (
    <ScrollView style={styles.page}>
      <View style={styles.header}>
        <Text style={styles.title}>{t('quickStartPage.title')}</Text>
        <Text style={styles.subtitle}>{t('persistentTracking.backgroundDescription')}</Text>
      </View>
      <View style={styles.card}>
        <Text style={styles.status}>{t(stateKey)}</Text>
        {!!error && <Text style={styles.error}>{error}</Text>}
        {!!session?.last_error && <Text style={styles.error}>{session.last_error}</Text>}
        <Text>{t('quickStartPage.session.label')}: {session?.session_id || snapshot?.displaySessionId || '—'}</Text>
        <Text>{t('quickStartPage.stats.points')}: {snapshot?.totalPoints ?? 0}</Text>
        <Text>{t('persistentTracking.uploaded')}: {snapshot?.uploadedPoints ?? 0}</Text>
        <Text>{t('persistentTracking.pending')}: {snapshot?.pendingCount ?? 0}</Text>
        {!!snapshot?.syncState && <Text>{t(`persistentTracking.sync.${snapshot.syncState}`)}</Text>}
      </View>
      {interrupted && (
        <TouchableOpacity testID="quick-start-resume" style={styles.button} disabled={busy}
          onPress={() => perform(resumePersistentTracking)}>
          <Text style={styles.buttonText}>{t('persistentTracking.resume')}</Text>
        </TouchableOpacity>
      )}
      <TouchableOpacity testID="quick-start-toggle"
        style={[styles.button, session && styles.stop]} disabled={busy || !snapshot}
        onPress={() => perform(session ? stopPersistentTracking : start)}>
        <Text style={styles.buttonText}>
          {t(session ? 'quickStartPage.button.stop' : 'quickStartPage.button.start')}
        </Text>
      </TouchableOpacity>
      {(snapshot?.pendingCount ?? 0) > 0 && (
        <View style={styles.card}>
          <Text>{t('persistentTracking.savedLocally')}</Text>
          {!!snapshot?.latestSyncError && <Text style={styles.error}>{snapshot.latestSyncError}</Text>}
          {snapshot?.pendingStops.map(stop => (
            <View key={stop.sessionId}>
              <Text>{t('quickStartPage.stopPending.message')} {stop.sessionId}</Text>
              {!!stop.error && <Text style={styles.error}>{stop.error}</Text>}
            </View>
          ))}
          <TouchableOpacity testID="quick-start-retry-stop" style={styles.button}
            disabled={busy} onPress={() => perform(retryPersistentSync)}>
            <Text style={styles.buttonText}>{t('quickStartPage.stopPending.retry')}</Text>
          </TouchableOpacity>
        </View>
      )}
      {points.length > 1 && (
        <MapView style={styles.map} initialRegion={{ ...points[0], latitudeDelta: 0.018, longitudeDelta: 0.018 }}>
          <Polyline coordinates={points} strokeColor="#2563EB" strokeWidth={4} />
        </MapView>
      )}
      <View style={styles.card}>
        <Text>{t('persistentTracking.previewLimit')}</Text>
        {points.slice(-4).reverse().map(point => (
          <Text key={point.timestamp}>
            {formatQuickStartTimestamp(point.timestamp)}: {point.latitude.toFixed(6)}, {point.longitude.toFixed(6)}
          </Text>
        ))}
        {(snapshot?.legacyUnboundRoutes ?? 0) > 0 && (
          <Text>{t('persistentTracking.legacyPreserved')}</Text>
        )}
      </View>
    </ScrollView>
  );
};

const styles = StyleSheet.create({
  page: { flex: 1, backgroundColor: '#F9FAFB' },
  header: { padding: 24, backgroundColor: '#111827' },
  title: { color: '#FFFFFF', fontSize: 28, fontWeight: '700' },
  subtitle: { color: '#FFFFFF', marginTop: 12 },
  card: { padding: 18, margin: 12, backgroundColor: '#FFFFFF', borderRadius: 12, gap: 10 },
  status: { fontSize: 20, fontWeight: '600' },
  error: { color: '#B91C1C' },
  button: { margin: 12, padding: 16, backgroundColor: '#2563EB', borderRadius: 10, alignItems: 'center' },
  stop: { backgroundColor: '#DC2626' },
  buttonText: { color: '#FFFFFF', fontWeight: '600' },
  map: { height: 240, margin: 12 },
});

export default AndroidQuickStartPage;
