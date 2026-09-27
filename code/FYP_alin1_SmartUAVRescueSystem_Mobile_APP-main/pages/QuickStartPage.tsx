import React, { useState, useRef, useEffect } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  Alert,
  ScrollView,
  ActivityIndicator,
  Platform,
  PermissionsAndroid,
} from 'react-native';
import { useTranslation } from 'react-i18next';
import Geolocation, { GeolocationResponse } from '@react-native-community/geolocation';
import MapView, { Polyline } from 'react-native-maps';
import { initDb, getDb } from '../services/db/initDb';
import { buildRealtimeDatabaseRestUrl } from '../services/db/firebaseRealtimeDatabase';
import { getQuickStartSampleTime } from '../services/quickStartSample';
import {
  calculateQuickStartDistance,
  calculateQuickStartDuration,
  formatQuickStartTimestamp,
} from '../services/quickStartMetrics';
import { createQuickStartSessionId } from '../services/quickStartSessionId';


const COLORS = {
  primary: '#3B82F6',
  primaryDark: '#1D4ED8',
  success: '#10B981',
  danger: '#EF4444',
  background: '#F9FAFB',
  card: '#FFFFFF',
  text: '#111827',
  textSecondary: '#6B7280',
  border: '#E5E7EB',
  mapBorder: '#BFDBFE',
  lightBg: '#F0F9FF',
  header: '#000000',
};

const SHADOW_MD = {
  shadowColor: '#000',
  shadowOffset: { width: 0, height: 4 },
  shadowOpacity: 0.1,
  shadowRadius: 8,
  elevation: 4,
};


class LocationTrackerDB {
  static async addLocation(
    routeId: number,
    latitude: number,
    longitude: number,
    accuracy?: number | null,
    altitude?: number | null,
    speed?: number | null,
    heading?: number | null,
    capturedAt?: string
  ) {
    try {
      const db = getDb();
      const timestamp = capturedAt ?? new Date().toISOString();

      const result = await db.executeSql(
        'INSERT INTO locations (route_id, latitude, longitude, timestamp, accuracy, altitude, speed, heading) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        [routeId, latitude, longitude, timestamp, accuracy, altitude, speed, heading]
      );

      return {
        success: true,
        insertedId: result[0].insertId,
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
      };
    } catch (error: any) {
      console.error('Error saving location:', error);
      return { success: false, error: error.message };
    }
  }

  static async createRoute(name: string, sessionId: string, startTime: string) {
    try {
      const db = getDb();
      const [result] = await db.executeSql(
        'INSERT INTO routes (name, start_time, session_id) VALUES (?, ?, ?)',
        [name, startTime, sessionId]
      );

      return {
        id: result.insertId,
        name,
        startTime,
        sessionId
      };
    } catch (error: any) {
      console.error('Error creating route:', error);
      throw error;
    }
  }

  static async getStats() {
    try {
      const db = getDb();
      const [routes] = await db.executeSql('SELECT COUNT(*) as count FROM routes');
      const [locations] = await db.executeSql('SELECT COUNT(*) as count FROM locations');

      return {
        totalRoutes: routes.rows.item(0).count,
        totalLocations: locations.rows.item(0).count
      };
    } catch (error) {
      console.error('Error getting stats:', error);
      return { totalRoutes: 0, totalLocations: 0 };
    }
  }
}


type ActiveSession = {
  id: string;
  phone: string;
  routeId: number;
  latestSampleAtMs: number;
  nextPointNumber: number;
  acceptedPoints: number;
  acquiring: boolean;
  pendingUpload: Promise<void> | null;
};

type PendingStop = {
  session: ActiveSession;
  endTime: string;
  count: number;
  inProgress: boolean;
};

const QuickStartPage = () => {
  const { t } = useTranslation();

  const [locations, setLocations] = useState<{ lat: number; lng: number; timestamp: string }[]>([]);
  const intervalRef = useRef<NodeJS.Timeout | null>(null);
  const currentSessionRef = useRef<ActiveSession | null>(null);
  const mountedRef = useRef(true);
  const pendingStopsRef = useRef(new Map<string, PendingStop>());
  const [pendingStops, setPendingStops] = useState<PendingStop[]>([]);
  const [tracking, setTracking] = useState(false);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string>('');
  const startingRef = useRef(false);
  const startAttemptRef = useRef(0);
  const [currentRoute, setCurrentRoute] = useState<{ id: number; name: string; sessionId: string } | null>(null);
  const [stats, setStats] = useState({ totalPoints: 0, distance: '0m', sessionDuration: '0 min' });
  const [lastUpdateTime, setLastUpdateTime] = useState<string>('');
  const [sessionId, setSessionId] = useState<string>('');
  const [appReady, setAppReady] = useState(false);
  const [firebaseUploadCount, setFirebaseUploadCount] = useState(0);
  const [lastFirebaseUpload, setLastFirebaseUpload] = useState<string>('');

  const UPDATE_INTERVAL = 5000;


  useEffect(() => {
    mountedRef.current = true;
    const initialize = async () => {
      try {
        await initDb();
        setAppReady(true);
      } catch (err) {
        console.error('Failed to initialize database', err);
        Alert.alert(
          t('quickStartPage.alert.initFailed.title'),
          t('quickStartPage.alert.initFailed.message')
        );
        setAppReady(true);
      }
    };

    initialize();

    return () => {
      mountedRef.current = false;
      if (intervalRef.current) clearInterval(intervalRef.current);
      currentSessionRef.current = null;
      startingRef.current = false;
      startAttemptRef.current += 1;
    };
  }, [t]);


  const updateStats = (locs: typeof locations) => {
    setStats({
      totalPoints: locs.length,
      distance: calculateQuickStartDistance(locs),
      sessionDuration: calculateQuickStartDuration(locs)
    });
  };

  const requestLocationPermission = async (): Promise<boolean> => {
    if (Platform.OS === 'ios') return true;

    try {
      const granted = await PermissionsAndroid.request(
        PermissionsAndroid.PERMISSIONS.ACCESS_FINE_LOCATION,
        {
          title: t('quickStartPage.permission.title'),
          message: t('quickStartPage.permission.message'),
          buttonNeutral: t('quickStartPage.permission.neutral'),
          buttonNegative: t('quickStartPage.permission.negative'),
          buttonPositive: t('quickStartPage.permission.positive')
        }
      );
      return granted === PermissionsAndroid.RESULTS.GRANTED;
    } catch (err) {
      console.warn('Permission request error', err);
      return false;
    }
  };


  const getUserPhone = async () => {
    try {
      await initDb();
      const db = getDb();
      const result = await db.executeSql(
        'SELECT phone FROM user ORDER BY id DESC LIMIT 1'
      );

      if (result[0].rows.length > 0) {
        const phone = result[0].rows.item(0).phone;
        return phone ? phone.replace(/[^0-9]/g, '') : null;
      }
      return null;
    } catch (err) {
      console.error('Error getting phone from SQLite:', err);
      return null;
    }
  };

  const uploadLocationToFirebase = async (
    session: ActiveSession,
    pos: GeolocationResponse,
    capturedAtMs: number,
    capturedAt: string,
  ): Promise<void> => {
    if (currentSessionRef.current !== session) return;
    const { latitude, longitude, accuracy, altitude, speed, heading } = pos.coords;
    const data = {
      latitude, longitude,
      accuracy: accuracy ?? null,
      altitude: altitude ?? null,
      speed: speed ?? null,
      heading: heading ?? null,
      timestamp: capturedAtMs,
      timestampISO: capturedAt,
    };
    const pointKey = `point_${session.nextPointNumber++}`;
    try {
      const res = await fetch(
        buildRealtimeDatabaseRestUrl('users', session.phone, 'QuickStartSessions', session.id, 'points', pointKey),
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(data),
        },
      );
      if (!res.ok) throw new Error(`Location upload failed (${res.status}).`);
      if (currentSessionRef.current !== session) return;
      setFirebaseUploadCount(c => c + 1);
      setLastFirebaseUpload(
        new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
      );
    } catch (err) {
      console.error('Firebase upload error:', err);
      if (currentSessionRef.current === session) {
        Alert.alert(t('quickStartPage.alert.uploadFailed.title'), t('quickStartPage.alert.uploadFailed.message'));
      }
    }
  };

  const getValidLocation = async (previousSampleAtMs: number | null = null): Promise<{
    position: GeolocationResponse;
    milliseconds: number;
    iso: string;
  }> => {
    const hasPerm = await requestLocationPermission();
    if (!hasPerm) throw new Error(t('quickStartPage.alert.locationError.permission'));


    const requestStartedAtMs = Date.now();
    return new Promise((resolve, reject) => {
      Geolocation.getCurrentPosition(
        pos => {
          const receivedAtMs = Date.now();
          const { latitude, longitude } = pos.coords;
          if (!Number.isFinite(latitude) || !Number.isFinite(longitude) ||
              Math.abs(latitude) > 90 || Math.abs(longitude) > 180) {
            reject(new Error(t('quickStartPage.alert.locationError.invalid')));
            return;
          }
          try {
            const capture = getQuickStartSampleTime(pos, requestStartedAtMs, receivedAtMs, previousSampleAtMs);
            resolve({ position: pos, ...capture });
          } catch (err) {
            reject(new Error(t('quickStartPage.alert.locationError.freshSample')));
          }
        },
        err => {
          let msg = t('quickStartPage.alert.locationError.default');
          if (err.code === 1) msg = t('quickStartPage.alert.locationError.permission');
          if (err.code === 2) msg = t('quickStartPage.alert.locationError.unavailable');
          if (err.code === 3) msg = t('quickStartPage.alert.locationError.timeout');
          reject(new Error(msg));
        },
        { enableHighAccuracy: true, timeout: 20000, maximumAge: 0 },
      );
    });
  };

  const showLocation = (pos: GeolocationResponse, capturedAt: string) => {
    const newLoc = {
      lat: pos.coords.latitude,
      lng: pos.coords.longitude,
      timestamp: capturedAt,
    };
    setLocations(prev => {
      const updated = [...prev, newLoc];
      updateStats(updated);
      return updated;
    });
    setLastUpdateTime(formatQuickStartTimestamp(capturedAt));
  };

  const acquireLocation = async (session: ActiveSession) => {
    if (session.acquiring || currentSessionRef.current !== session) return;
    session.acquiring = true;
    try {
      const { position: pos, milliseconds: capturedAtMs, iso: capturedAt } =
        await getValidLocation(session.latestSampleAtMs);
      if (currentSessionRef.current !== session) return;
      const { latitude, longitude, accuracy, altitude, speed, heading } = pos.coords;
      const saved = await LocationTrackerDB.addLocation(
        session.routeId, latitude, longitude, accuracy, altitude, speed, heading, capturedAt,
      );
      if (currentSessionRef.current !== session) return;
      if (!saved.success) throw new Error(t('quickStartPage.alert.startFailed.message'));
      session.latestSampleAtMs = capturedAtMs;
      session.acceptedPoints += 1;
      showLocation(pos, capturedAt);
      session.pendingUpload = uploadLocationToFirebase(session, pos, capturedAtMs, capturedAt);
      await session.pendingUpload;
    } catch (err: any) {
      if (currentSessionRef.current !== session) return;
      Alert.alert(t('quickStartPage.alert.locationError.title'), err.message);
    } finally {
      session.pendingUpload = null;
      session.acquiring = false;
    }
  };

  const startTracking = async () => {
    if (currentSessionRef.current || startingRef.current || !appReady) return;

    startingRef.current = true;
    const attempt = ++startAttemptRef.current;
    setStarting(true);
    setStartError('');
    setLocations([]);
    updateStats([]);
    setCurrentRoute(null);
    setLastUpdateTime('');
    setFirebaseUploadCount(0);
    setLastFirebaseUpload('');

    try {

      const { position: pos, milliseconds: capturedAtMs, iso: capturedAt } = await getValidLocation();
      if (attempt !== startAttemptRef.current) return;
      const phone = await getUserPhone();
      if (attempt !== startAttemptRef.current) return;
      if (!phone) {
        throw new Error(t('quickStartPage.alert.startFailed.missingPhone'));
      }

      const sid = createQuickStartSessionId();
      const route = await LocationTrackerDB.createRoute(`Session-${sid}`, sid, capturedAt);
      if (attempt !== startAttemptRef.current) return;
      const { latitude, longitude, accuracy, altitude, speed, heading } = pos.coords;
      const saved = await LocationTrackerDB.addLocation(
        route.id, latitude, longitude, accuracy, altitude, speed, heading, capturedAt
      );
      if (attempt !== startAttemptRef.current) return;
      if (!saved.success) {
        throw new Error(t('quickStartPage.alert.startFailed.message'));
      }


      const res = await fetch(
        buildRealtimeDatabaseRestUrl('users', phone, 'QuickStartSessions', sid),
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            startTime: capturedAt,
            status: 'ACTIVE',
            points: {
              point_1: {
                latitude, longitude,
                accuracy: accuracy ?? null,
                altitude: altitude ?? null,
                speed: speed ?? null,
                heading: heading ?? null,
                timestamp: capturedAtMs,
                timestampISO: capturedAt,
              },
            },
          }),
        }
      );
      if (attempt !== startAttemptRef.current) return;
      if (!res.ok) {
        throw new Error(t('quickStartPage.alert.startFailed.upload'));
      }

      const session: ActiveSession = {
        id: sid, phone, routeId: route.id,
        latestSampleAtMs: capturedAtMs,
        nextPointNumber: 2,
        acceptedPoints: 1,
        acquiring: false,
        pendingUpload: null,
      };
      currentSessionRef.current = session;
      setSessionId(sid);
      setCurrentRoute(route);
      showLocation(pos, capturedAt);
      setFirebaseUploadCount(1);
      setLastFirebaseUpload(
        new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
      );
      setTracking(true);
      intervalRef.current = setInterval(() => {
        acquireLocation(session);
      }, UPDATE_INTERVAL);
      Alert.alert(
        t('quickStartPage.alert.trackingStarted.title'),
        t('quickStartPage.alert.trackingStarted.message', { sessionId: sid })
      );
    } catch (err: any) {
      if (attempt !== startAttemptRef.current) return;
      setTracking(false);
      currentSessionRef.current = null;
      setSessionId('');
      const message = err.message || t('quickStartPage.alert.startFailed.message');
      setStartError(message);
      Alert.alert(
        t('quickStartPage.alert.startFailed.title'),
        `${t('quickStartPage.status.notReady')}\n${message}\n${t('quickStartPage.retryHint')}`
      );
    } finally {
      if (attempt === startAttemptRef.current) {
        startingRef.current = false;
        setStarting(false);
      }
    }
  };

  const refreshPendingStops = () => {
    if (mountedRef.current) setPendingStops([...pendingStopsRef.current.values()]);
  };

  const completeSessionStop = async (stop: PendingStop) => {
    if (stop.inProgress) return;
    stop.inProgress = true;
    refreshPendingStops();
    try {


      await stop.session.pendingUpload;
      const res = await fetch(
        buildRealtimeDatabaseRestUrl('users', stop.session.phone, 'QuickStartSessions', stop.session.id),
        {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ endTime: stop.endTime, status: 'COMPLETED' }),
        },
      );
      if (!res.ok) throw new Error(`Session completion failed (${res.status}).`);
      pendingStopsRef.current.delete(stop.session.id);
      if (mountedRef.current && !currentSessionRef.current && !startingRef.current) {
        Alert.alert(
          t('quickStartPage.alert.trackingStopped.title'),
          t('quickStartPage.alert.trackingStopped.message', { count: stop.count }),
        );
      }
    } catch (err) {
      console.error('Failed to finalize session on Firebase:', err);
      if (mountedRef.current) {
        Alert.alert(t('quickStartPage.alert.stopFailed.title'), t('quickStartPage.stopPending.message'));
      }
    } finally {
      stop.inProgress = false;
      refreshPendingStops();
    }
  };

  const stopTracking = async () => {
    const session = currentSessionRef.current;
    if (!session) return;
    currentSessionRef.current = null;
    setTracking(false);
    setSessionId('');
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
    const stop: PendingStop = {
      session,
      endTime: new Date().toISOString(),
      count: session.acceptedPoints,
      inProgress: false,
    };
    pendingStopsRef.current.set(session.id, stop);
    await completeSessionStop(stop);
  };

  const polylineCoords = locations.map(loc => ({
    latitude: loc.lat,
    longitude: loc.lng
  }));

  return (
    <ScrollView style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.title}>{t('quickStartPage.title')}</Text>
        <Text style={styles.subtitle}>{t('quickStartPage.subtitle')}</Text>
        {!appReady && (
          <View style={styles.initializingContainer}>
            <ActivityIndicator size="small" color="white" />
            <Text style={styles.initializingText}>{t('quickStartPage.initializing')}</Text>
          </View>
        )}
      </View>
      <View style={[styles.statusCard, tracking ? styles.statusActive : styles.statusInactive, SHADOW_MD]}>
        <View style={styles.statusRow}>
          <View style={[
            styles.statusLight,
            { backgroundColor: tracking ? COLORS.success : COLORS.danger }
          ]} />
          <Text style={styles.statusText}>
            {starting ? t('quickStartPage.status.locating') :
              tracking ? t('quickStartPage.status.active') :
                startError ? t('quickStartPage.status.notReady') : t('quickStartPage.status.inactive')}
          </Text>
        </View>

        {!!startError && (
          <Text style={styles.routeInfo}>
            {startError}{'\n'}{t('quickStartPage.retryHint')}
          </Text>
        )}

        <View style={styles.sessionInfoRow}>
          <Text style={styles.sessionLabel}>{t('quickStartPage.session.label')}</Text>
          <Text style={styles.sessionValue}>
            {sessionId || (tracking ? t('quickStartPage.session.generating') : '—')}
          </Text>
        </View>

        {currentRoute && (
          <Text style={styles.routeInfo}>
            Route #{currentRoute.id}
          </Text>
        )}

        <View style={styles.uploadStat}>
          <Text style={styles.uploadText}>
            Firebase 上傳: {firebaseUploadCount}
            {lastFirebaseUpload ? ` • 最後: ${lastFirebaseUpload}` : ''}
          </Text>
        </View>
      </View>

      {pendingStops.map(stop => (
        <View key={stop.session.id} style={[styles.statusCard, styles.statusInactive]}>
          <Text style={styles.routeInfo}>{t('quickStartPage.stopPending.message')}</Text>
          <Text style={styles.routeInfo}>{stop.session.id}</Text>
          <TouchableOpacity
            testID="quick-start-retry-stop"
            style={[styles.actionButton, styles.buttonStart, stop.inProgress && styles.buttonDisabled]}
            disabled={stop.inProgress}
            onPress={() => completeSessionStop(stop)}
          >
            <Text style={styles.buttonText}>
              {stop.inProgress ? t('quickStartPage.stopPending.sending') : t('quickStartPage.stopPending.retry')}
            </Text>
          </TouchableOpacity>
        </View>
      ))}
      <View style={[styles.metricsCard, SHADOW_MD]}>
        <View style={styles.metricsGrid}>
          <View style={styles.metricItem}>
            <Text style={styles.metricLabel}>{t('quickStartPage.stats.points')}</Text>
            <Text style={[styles.metricValue, { color: COLORS.primary }]}>{stats.totalPoints}</Text>
          </View>
          <View style={styles.metricDivider} />
          <View style={styles.metricItem}>
            <Text style={styles.metricLabel}>{t('quickStartPage.stats.distance')}</Text>
            <Text style={[styles.metricValue, { color: COLORS.primary }]}>{stats.distance}</Text>
          </View>
          <View style={styles.metricDivider} />
          <View style={styles.metricItem}>
            <Text style={styles.metricLabel}>{t('quickStartPage.stats.duration')}</Text>
            <Text style={[styles.metricValue, { color: COLORS.primary }]}>{stats.sessionDuration}</Text>
          </View>
        </View>

        {lastUpdateTime && (
          <Text style={styles.lastUpdateText}>
            最後更新：{lastUpdateTime}
          </Text>
        )}
      </View>
      <View style={styles.controlSection}>
        <TouchableOpacity
          testID="quick-start-toggle"
          style={[
            styles.actionButton,
            tracking ? styles.buttonStop : styles.buttonStart,
            (!appReady || starting) && styles.buttonDisabled,
            SHADOW_MD,
          ]}
          onPress={tracking ? stopTracking : startTracking}
          disabled={!appReady || starting}
          activeOpacity={0.85}
        >
          <Text style={styles.buttonText}>
            {starting ? t('quickStartPage.button.locating') :
              tracking ? t('quickStartPage.button.stop') : t('quickStartPage.button.start')}
          </Text>
        </TouchableOpacity>
      </View>
      <View style={styles.mapSection}>
        <Text style={styles.sectionTitle}>{t('quickStartPage.map.title')}</Text>
        <View style={[styles.mapContainer, SHADOW_MD]}>
          {locations.length > 1 ? (
            <MapView
              style={StyleSheet.absoluteFillObject}
              initialRegion={{
                latitude: locations[0].lat,
                longitude: locations[0].lng,
                latitudeDelta: 0.018,
                longitudeDelta: 0.018,
              }}
              showsUserLocation={true}
              showsMyLocationButton={true}
              showsCompass={true}
            >
              <Polyline
                coordinates={polylineCoords}
                strokeColor={COLORS.primary}
                strokeWidth={5}
                lineCap="round"
                lineJoin="round"
              />
            </MapView>
          ) : (
            <View style={styles.mapPlaceholder}>
              <Text style={styles.placeholderMain}>
                {tracking ? '等待定位資料...' : '點擊「開始」以記錄軌跡'}
              </Text>
              <Text style={styles.placeholderSub}>
                {t('quickStartPage.map.noData')}
              </Text>
            </View>
          )}
        </View>
      </View>
      {locations.length > 0 && (
        <View style={[styles.historyCard, SHADOW_MD]}>
          <Text style={styles.sectionTitle}>最近位置</Text>
          {locations.slice(-4).reverse().map((loc, i) => (
            <View key={i} style={styles.historyItem}>
              <Text style={styles.historyTime}>
                {formatQuickStartTimestamp(loc.timestamp)}
              </Text>
              <Text style={styles.historyCoords}>
                {loc.lat.toFixed(6)}, {loc.lng.toFixed(6)}
              </Text>
            </View>
          ))}
        </View>
      )}
    </ScrollView>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: COLORS.background,
  },
  header: {
    paddingVertical: 32,
    paddingHorizontal: 24,
    backgroundColor: COLORS.header,
    alignItems: 'center',
  },
  title: {
    fontSize: 28,
    fontWeight: '700',
    color: 'white',
    marginBottom: 6,
  },
  subtitle: {
    fontSize: 15,
    color: 'rgba(255,255,255,0.88)',
    textAlign: 'center',
  },
  initializingContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: 16,
  },
  initializingText: {
    color: 'white',
    marginLeft: 10,
    fontSize: 14,
  },


  statusCard: {
    marginHorizontal: 20,
    marginTop: 24,
    marginBottom: 16,
    padding: 20,
    borderRadius: 20,
  },
  statusActive: {
    backgroundColor: '#F0FDF4',
    borderLeftWidth: 5,
    borderLeftColor: COLORS.success,
  },
  statusInactive: {
    backgroundColor: '#FEF2F2',
    borderLeftWidth: 5,
    borderLeftColor: COLORS.danger,
  },
  statusRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 16,
  },
  statusLight: {
    width: 18,
    height: 18,
    borderRadius: 9,
    marginRight: 12,
    borderWidth: 3,
    borderColor: 'white',
  },
  statusText: {
    fontSize: 18,
    fontWeight: '600',
    color: COLORS.text,
  },
  sessionInfoRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 10,
  },
  sessionLabel: {
    fontSize: 14,
    color: COLORS.textSecondary,
    marginRight: 10,
  },
  sessionValue: {
    fontSize: 16,
    fontWeight: '700',
    color: COLORS.primary,
    fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace',
  },
  routeInfo: {
    fontSize: 13,
    color: COLORS.textSecondary,
    marginBottom: 12,
  },
  uploadStat: {
    marginTop: 8,
    paddingTop: 12,
    borderTopWidth: 1,
    borderTopColor: COLORS.border,
  },
  uploadText: {
    fontSize: 13,
    color: COLORS.textSecondary,
    fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace',
  },


  metricsCard: {
    marginHorizontal: 20,
    marginBottom: 24,
    padding: 24,
    borderRadius: 20,
    backgroundColor: 'white',
  },
  metricsGrid: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginBottom: 16,
  },
  metricItem: {
    flex: 1,
    alignItems: 'center',
  },
  metricLabel: {
    fontSize: 13,
    color: COLORS.textSecondary,
    marginBottom: 6,
  },
  metricValue: {
    fontSize: 24,
    fontWeight: '700',
  },
  metricDivider: {
    width: 1,
    backgroundColor: COLORS.border,
    marginHorizontal: 12,
  },
  lastUpdateText: {
    fontSize: 13,
    color: COLORS.textSecondary,
    textAlign: 'center',
    marginTop: 8,
  },


  controlSection: {
    marginHorizontal: 20,
    marginBottom: 28,
  },
  actionButton: {
    paddingVertical: 18,
    borderRadius: 16,
    alignItems: 'center',
    justifyContent: 'center',
  },
  buttonStart: {
    backgroundColor: COLORS.success,
  },
  buttonStop: {
    backgroundColor: COLORS.danger,
  },
  buttonDisabled: {
    opacity: 0.55,
  },
  buttonText: {
    color: 'white',
    fontSize: 18,
    fontWeight: '700',
  },


  mapSection: {
    marginHorizontal: 20,
    marginBottom: 28,
  },
  sectionTitle: {
    fontSize: 18,
    fontWeight: '700',
    color: COLORS.text,
    marginBottom: 12,
  },
  mapContainer: {
    height: 340,
    borderRadius: 20,
    overflow: 'hidden',
    borderWidth: 1,
    borderColor: COLORS.mapBorder,
    backgroundColor: COLORS.lightBg,
  },
  mapPlaceholder: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    paddingHorizontal: 40,
  },
  placeholderMain: {
    fontSize: 18,
    fontWeight: '600',
    color: '#4B5563',
    textAlign: 'center',
    marginBottom: 8,
  },
  placeholderSub: {
    fontSize: 14,
    color: '#9CA3AF',
    textAlign: 'center',
  },


  historyCard: {
    marginHorizontal: 20,
    marginBottom: 40,
    padding: 20,
    borderRadius: 20,
    backgroundColor: 'white',
  },
  historyItem: {
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: COLORS.border,
  },
  historyTime: {
    fontSize: 13,
    color: COLORS.textSecondary,
    marginBottom: 4,
  },
  historyCoords: {
    fontSize: 15,
    fontWeight: '600',
    color: COLORS.text,
    fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace',
  },
});

export default QuickStartPage;
