import React, { useEffect, useRef, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  Alert,
  Linking,
  ScrollView,
  PermissionsAndroid,
} from 'react-native';
import Geolocation from '@react-native-community/geolocation';
import { useTranslation } from 'react-i18next';


import { initDb, getDb } from '../services/db/initDb';
import { persistCloudRecord } from '../services/persistentTracking';
import { getPositionCaptureTime } from '../services/positionTimestamp';
import { getCurrentSosRequest, restoreCurrentSosFromLegacy, saveCurrentSosRequest } from '../services/currentSosStore';
import { captureCurrentSosForUav, retrySavedUavCaptures, UavCaptureTransferError } from '../services/uavArrivalCaptureClient';
import { FreshUavPositionError } from '../services/freshUavPosition';
import {
  UavRescuePayload,
  flushPendingUavRescues,
  getUavConnectionConfig,
  getPendingUavRescues,
  queueRescueForUav,
} from '../services/uavRescueClient';
import {
  connectToUavWifi,
  disconnectFromUavWifi,
  subscribeToUavWifiLoss,
  supportsSystemUavWifiSelection,
} from '../services/uavWifiClient';

const SosPage = () => {
  const { t } = useTranslation();
  const [uavStatus, setUavStatus] = useState(t('sosPage.uav.notSent'));
  const [submitting, setSubmitting] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const submittingRef = useRef(false);
  const retryingRef = useRef(false);
  const activeCaptureAbort = useRef<AbortController | null>(null);
  useEffect(() => () => activeCaptureAbort.current?.abort(), []);

  useEffect(() => {
    getUavConnectionConfig()
      .catch(() => setUavStatus(t('sosPage.uav.configInvalid')));
  }, [t]);

  const ensureLocationPermission = async (): Promise<boolean> => {
    const result = await PermissionsAndroid.request(
      PermissionsAndroid.PERMISSIONS.ACCESS_FINE_LOCATION,
    );
    return result === PermissionsAndroid.RESULTS.GRANTED;
  };

  const callSOS = async () => {
    const number = '999';
    const url = `tel:${number}`;

    try {
      const supported = await Linking.canOpenURL(url);
      if (supported) {
        await Linking.openURL(url);
      } else {
        Alert.alert(t('sosPage.errorTitle'), t('sosPage.noPhoneApp'));
      }
    } catch {
      Alert.alert(t('sosPage.errorTitle'), t('sosPage.callFailed'));
    }
  };

  const requestRescue = async () => {
    if (submittingRef.current || retryingRef.current) return;
    submittingRef.current = true;
    setSubmitting(true);
    try {
      if (!(await ensureLocationPermission())) {
        Alert.alert(
          t('sosPage.errorTitle') || 'Error',
          t('sosPage.locationPermissionDenied'),
        );
        return;
      }

      const position: any = await new Promise((resolve, reject) => {
        Geolocation.getCurrentPosition(resolve, reject, {
          enableHighAccuracy: true,
          timeout: 15000,
          maximumAge: 10000,
        });
      });

      await initDb();
      const db = getDb();
      const result = await db.executeSql(
        'SELECT phone FROM user ORDER BY id DESC LIMIT 1',
      );
      if (result[0].rows.length === 0 || !result[0].rows.item(0).phone) {
        Alert.alert(
          t('sosPage.errorTitle') || '錯誤',
          t('sosPage.noPhoneStored') || '請先在個人資料頁設定電話號碼',
        );
        return;
      }

      const phone = result[0].rows.item(0).phone;
      const normalizedPhone = phone.replace(/[^0-9]/g, '');
      if (!normalizedPhone) {
        Alert.alert('錯誤', '電話號碼無效，請重新設定');
        return;
      }

      const { latitude, longitude, accuracy } = position.coords;
      const timestampMs = Date.now();
      const timestampKey = timestampMs.toString();
      const capturedAt = getPositionCaptureTime(position).iso;
      const rescueData = {
        latitude,
        longitude,
        status: 'PENDING',
        timestamp: timestampMs,
        device: 'android',
      };


      const cloudSave = persistCloudRecord(normalizedPhone, 'rescue_requests', timestampKey, rescueData)
        .then(saved => saved.stored)
        .catch(() => false);


      let config;
      try {
        config = await getUavConnectionConfig();
      } catch {

        setUavStatus(t('sosPage.uav.configInvalid'));
      }


      const uavPayload: UavRescuePayload = {
        schema_version: 1,
        request_id: timestampKey,
        mission_id: `${normalizedPhone}/${timestampKey}`,
        user_id: normalizedPhone,
        latitude,
        longitude,
        accuracy: Number.isFinite(accuracy) ? accuracy : null,
        captured_at: capturedAt,
        client_timestamp_ms: timestampMs,
        status: 'PENDING',
        device: 'android',
        gps_points: [{ latitude, longitude, captured_at: capturedAt }],
        test_mode: false,
      };

      const sourceAttempt = saveCurrentSosRequest(uavPayload).then(() => true).catch(() => false);
      const queueAttempt = config ? queueRescueForUav(uavPayload, { config })
        .then(() => true).catch(() => false) : Promise.resolve(false);
      const [cloudQueued, queued, sourceSaved] = await Promise.all([
        cloudSave, queueAttempt, sourceAttempt,
      ]);
      if (queued) {
        setUavStatus(t('sosPage.uav.queued'));
      } else {
        setUavStatus(t('sosPage.uav.notQueued'));
      }

      if (!sourceSaved) setUavStatus(t('sosPage.uav.sourceSaveFailed'));
      if (cloudQueued) {
        Alert.alert(t('sosPage.successTitle'), t('persistentTracking.recordQueued'));
      } else if (queued || sourceSaved) {
        Alert.alert(t('sosPage.errorTitle'), t('sosPage.savedLocallyOnly'));
      } else {
        Alert.alert(t('sosPage.errorTitle'), t('sosPage.requestFailed'));
      }
    } catch (error: any) {
      if (error?.code !== undefined) {
        Alert.alert(
          t('sosPage.errorTitle') || 'Error',
          t('sosPage.locationFailed', { code: error.code }),
        );
      } else {
        console.error('SOS request error:', error);
        Alert.alert(
          t('sosPage.errorTitle') || '錯誤',
          t('sosPage.requestFailed') || '發送失敗，請稍後再試',
        );
      }
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  };

  const transferArrivalLocation = async (newCapture: boolean) => {
    if (retryingRef.current || submittingRef.current) return;
    retryingRef.current = true;
    setRetrying(true);
    const controller = new AbortController();
    activeCaptureAbort.current = controller;
    let wifiStarted = false;
    let subscription: { remove: () => void } | null = null;
    let stage = 'preparing';
    try {
      await initDb();
      const rows = (await getDb().executeSql('SELECT phone FROM user ORDER BY id DESC LIMIT 1'))[0].rows;
      const userId = rows.length ? String(rows.item(0).phone || '').replace(/[^0-9]/g, '') : '';
      if (!userId) {
        Alert.alert(t('sosPage.errorTitle'), t('sosPage.noPhoneStored'));
        return;
      }
      let source: UavRescuePayload | null = null;
      if (newCapture) {
        source = await getCurrentSosRequest(userId);
        if (!source) source = await restoreCurrentSosFromLegacy(userId, (await getPendingUavRescues()).map(item => item.payload));
        if (!source) {
          setUavStatus(t('sosPage.uav.currentSosRequired'));
          Alert.alert(t('sosPage.errorTitle'), t('sosPage.uav.currentSosRequired'));
          return;
        }
      }
      const config = await getUavConnectionConfig();
      stage = 'connecting';
      setUavStatus(t('sosPage.uav.connectingWifi'));
      if (supportsSystemUavWifiSelection()) {
        if (!config.wifiSsid) {
          Alert.alert(t('sosPage.errorTitle'), t('sosPage.uav.wifiNotConfigured'));
          return;
        }
        wifiStarted = true;
        subscription = subscribeToUavWifiLoss(() => controller.abort());
        await connectToUavWifi(config.wifiSsid);
      } else {
        const confirmed = await new Promise<boolean>(resolve => {
          Alert.alert(t('sosPage.uav.manualWifiTitle'), t('sosPage.uav.manualWifiMessage'), [
            { text: t('sosPage.uav.cancel'), style: 'cancel', onPress: () => resolve(false) },
            { text: t('sosPage.uav.wifiConfirmed'), onPress: () => resolve(true) },
          ], { cancelable: true, onDismiss: () => resolve(false) });
        });
        if (!confirmed) return;
      }
      if (controller.signal.aborted) throw new UavCaptureTransferError('CANCELLED');
      const options = { wifiConfirmed: true, signal: controller.signal,
        onStage: (value: 'context' | 'location' | 'sending') => {
          stage = value;
          setUavStatus(t(`sosPage.uav.captureStage_${value}`));
        } };
      if (newCapture) {
        await captureCurrentSosForUav(source!, config, options);
        setUavStatus(t('sosPage.uav.captureStored'));
        Alert.alert(t('sosPage.uav.resultTitle'), t('sosPage.uav.captureStored'));
      } else {
        const result = await retrySavedUavCaptures(userId, config, options);
        const summary = t('sosPage.uav.captureRetrySummary', { ...result });
        setUavStatus(summary);
        Alert.alert(t('sosPage.uav.resultTitle'), summary);
      }
    } catch (error) {
      const codes: Record<string, string> = {
        NO_COLLECTION_CONTEXT: 'noCollectionContext', CURRENT_SOS_REQUIRED: 'currentSosRequired',
        CONTEXT_IDENTITY_CONFLICT: 'captureConflict', CAPTURE_CONTENT_CONFLICT: 'captureConflict',
        STORAGE_FAILURE: 'captureRetryFailed', STORAGE_INVALID: 'captureStorageInvalid', OUTBOX_FULL: 'captureOutboxFull',
      };
      const key = controller.signal.aborted ? 'wifiConnectionLost'
        : error instanceof FreshUavPositionError ? 'freshLocationFailed'
          : error instanceof UavCaptureTransferError ? (codes[error.code] || 'captureRetryFailed')
            : stage === 'connecting' ? 'wifiConnectionFailed' : 'captureRetryFailed';
      setUavStatus(t(`sosPage.uav.${key}`));
      Alert.alert(t('sosPage.errorTitle'), t(`sosPage.uav.${key}`));
    } finally {
      subscription?.remove();
      if (wifiStarted) {
        try { await disconnectFromUavWifi(); } catch {
          setUavStatus(t('sosPage.uav.disconnectFailed'));
          Alert.alert(t('sosPage.errorTitle'), t('sosPage.uav.disconnectFailed'));
        }
      }
      activeCaptureAbort.current = null;
      retryingRef.current = false;
      setRetrying(false);
    }
  };

  const retryUavTransfer = async () => {
    if (retryingRef.current || submittingRef.current) return;
    retryingRef.current = true;
    setRetrying(true);
    let wifiRequestStarted = false;
    let wifiLossSubscription: { remove: () => void } | null = null;
    let transferAbortController: AbortController | null = null;
    let stage: 'preparing' | 'connecting' | 'sending' = 'preparing';
    try {
      const pending = await getPendingUavRescues();
      if (pending.length === 0) {
        setUavStatus(t('sosPage.uav.empty'));
        Alert.alert(t('sosPage.uav.resultTitle'), t('sosPage.uav.empty'));
        return;
      }

      const config = await getUavConnectionConfig();
      if (supportsSystemUavWifiSelection()) {
        if (!config.wifiSsid) {
          setUavStatus(t('sosPage.uav.wifiNotConfigured'));
          Alert.alert(
            t('sosPage.errorTitle'),
            t('sosPage.uav.wifiNotConfigured'),
          );
          return;
        }
        stage = 'connecting';
        setUavStatus(t('sosPage.uav.connectingWifi'));
        wifiRequestStarted = true;
        transferAbortController = new AbortController();
        wifiLossSubscription = subscribeToUavWifiLoss(() => {
          transferAbortController?.abort();
        });
        await connectToUavWifi(config.wifiSsid);
      }

      stage = 'sending';
      setUavStatus(t('sosPage.uav.sending'));
      const result = await flushPendingUavRescues({
        config,
        signal: transferAbortController?.signal,
      });
      if (transferAbortController?.signal.aborted) {
        setUavStatus(t('sosPage.uav.wifiConnectionLost'));
        Alert.alert(
          t('sosPage.errorTitle'),
          t('sosPage.uav.wifiConnectionLost'),
        );
        return;
      }
      const summary = t('sosPage.uav.retrySummary', {
        sent: result.sent.length,
        failed: result.failed,
        expired: result.expired,
        differentReceiver: result.differentReceiver,
        differentWifi: result.differentWifi,
        remaining: result.remaining,
      });
      if (
        result.failed > 0 ||
        result.differentReceiver > 0 ||
        result.differentWifi > 0
      ) {
        setUavStatus(t('sosPage.uav.partial', { remaining: result.remaining }));
        Alert.alert(t('sosPage.uav.resultTitle'), summary);
      } else if (result.sent.length > 0) {
        const last = result.sent[result.sent.length - 1];
        setUavStatus(t('sosPage.uav.stored', { requestId: last.request_id }));
        Alert.alert(t('sosPage.uav.resultTitle'), summary);
      } else {
        setUavStatus(t('sosPage.uav.expired', { count: result.expired }));
        Alert.alert(t('sosPage.uav.resultTitle'), summary);
      }
    } catch {
      const messageKey =
        transferAbortController?.signal.aborted
          ? 'sosPage.uav.wifiConnectionLost'
          : stage === 'connecting'
          ? 'sosPage.uav.wifiConnectionFailed'
          : stage === 'preparing'
            ? 'sosPage.uav.configInvalid'
            : 'sosPage.uav.retryFailed';
      setUavStatus(t(messageKey));
      Alert.alert(t('sosPage.errorTitle'), t(messageKey));
    } finally {
      wifiLossSubscription?.remove();
      if (wifiRequestStarted) {
        try {
          await disconnectFromUavWifi();
        } catch {
          setUavStatus(t('sosPage.uav.disconnectFailed'));
          Alert.alert(
            t('sosPage.errorTitle'),
            t('sosPage.uav.disconnectFailed'),
          );
        }
      }
      retryingRef.current = false;
      setRetrying(false);
    }
  };

  return (
    <ScrollView
      testID="sos-scroll-view"
      style={styles.container}
      contentContainerStyle={styles.contentContainer}
      showsVerticalScrollIndicator={false}
    >
      <View style={styles.warningContainer}>
        <Text style={styles.warningIcon}>!</Text>
        <Text style={styles.warningText}>{t('sosPage.legalWarning')}</Text>
      </View>
      <Text style={styles.confirmText}>{t('sosPage.confirmText')}</Text>

      <TouchableOpacity
        testID="sos-button"
        style={[
          styles.sosButton,
          (submitting || retrying) && styles.disabledButton,
        ]}
        onPress={requestRescue}
        disabled={submitting || retrying}
      >
        <Text style={styles.sosButtonText}>{t('sosPage.sosButton')}</Text>
      </TouchableOpacity>

      <TouchableOpacity
        testID="capture-uav-button"
        style={[styles.uavButton, (retrying || submitting) && styles.disabledButton]}
        onPress={() => transferArrivalLocation(true)}
        disabled={retrying || submitting}
      >
        <Text style={styles.uavButtonText}>{t('sosPage.uav.captureButton')}</Text>
      </TouchableOpacity>
      <TouchableOpacity
        testID="retry-capture-uav-button"
        style={[styles.uavButton, (retrying || submitting) && styles.disabledButton]}
        onPress={() => transferArrivalLocation(false)}
        disabled={retrying || submitting}
      >
        <Text style={styles.uavButtonText}>{t('sosPage.uav.retryCaptureButton')}</Text>
      </TouchableOpacity>
      <Text style={styles.uavStatusText}>{t('sosPage.uav.captureExplanation')}</Text>

      <TouchableOpacity
        testID="sync-uav-button"
        style={[
          styles.uavButton,
          (retrying || submitting) && styles.disabledButton,
        ]}
        onPress={retryUavTransfer}
        disabled={retrying || submitting}
      >
        <Text style={styles.uavButtonText}>{t('sosPage.uav.retryButton')}</Text>
      </TouchableOpacity>
      <Text testID="uav-transfer-status" style={styles.uavStatusText}>
        {uavStatus}
      </Text>
      <TouchableOpacity
        testID="call-999-button"
        style={styles.callButton}
        onPress={callSOS}
      >
        <Text style={styles.callButtonText}>{t('sosPage.callButton')}</Text>
      </TouchableOpacity>
      <View testID="sos-slogan" style={styles.footerLogo}>
        <Text style={styles.sloganText}>{t('sosPage.slogan')}</Text>
      </View>
    </ScrollView>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#000000',
  },
  contentContainer: {
    flexGrow: 1,
    justifyContent: 'center',
    alignItems: 'center',
    paddingHorizontal: 30,
    paddingVertical: 24,
  },

  warningContainer: {
    alignItems: 'center',
    marginBottom: 24,
  },
  warningIcon: {
    fontSize: 60,
    color: '#FFEB3B',
    fontWeight: 'bold',
    marginBottom: 10,
  },
  warningText: {
    color: '#FFEB3B',
    fontSize: 18,
    fontWeight: 'bold',
    textAlign: 'center',
  },

  confirmText: {
    color: '#FFFFFF',
    fontSize: 20,
    textAlign: 'center',
    marginBottom: 32,
    lineHeight: 28,
  },

  sosButton: {
    backgroundColor: '#D32F2F',
    width: 200,
    height: 200,
    borderRadius: 100,
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: 28,
    elevation: 8,
    shadowColor: '#D32F2F',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.6,
    shadowRadius: 8,
  },
  sosButtonText: {
    color: '#FFFFFF',
    fontSize: 48,
    fontWeight: 'bold',
  },

  callButton: {
    backgroundColor: '#FF6F00',
    paddingVertical: 15,
    paddingHorizontal: 40,
    borderRadius: 30,
    marginBottom: 16,
  },
  uavButton: {
    backgroundColor: '#1565C0',
    paddingVertical: 12,
    paddingHorizontal: 24,
    borderRadius: 24,
    marginBottom: 8,
  },
  uavButtonText: {
    color: '#FFFFFF',
    fontSize: 16,
    fontWeight: 'bold',
  },
  disabledButton: {
    opacity: 0.5,
  },
  uavStatusText: {
    color: '#90CAF9',
    fontSize: 13,
    textAlign: 'center',
    marginBottom: 18,
  },
  callButtonText: {
    color: '#FFFFFF',
    fontSize: 20,
    fontWeight: 'bold',
  },

  footerLogo: {
    alignItems: 'center',
    marginBottom: 8,
  },
  logoText: {
    color: '#FFFFFF',
    fontSize: 36,
    fontWeight: 'bold',
    marginBottom: 8,
  },
  sloganText: {
    color: '#BBBBBB',
    fontSize: 16,
    fontStyle: 'italic',
  },
});

export default SosPage;
