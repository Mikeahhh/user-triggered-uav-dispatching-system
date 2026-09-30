import React, { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { useTranslation } from 'react-i18next';
import {
  ANDROID_VERSION_CODE,
  MOBILE_APP_VERSION,
  MOBILE_STACK_SUMMARY,
  SYSTEM_RELEASE_ID,
  UAV_OUTBOX_STORAGE_VERSION,
  UAV_RESCUE_PROTOCOL,
} from '../services/appMetadata';
import {
  UAV_OUTBOX_MAX_ITEMS,
  UAV_OUTBOX_TTL_MS,
  getPendingUavRescues,
  getUavConnectionConfig,
} from '../services/uavRescueClient';
import { supportsSystemUavWifiSelection } from '../services/uavWifiClient';
import {
  SCREENSHOT_DEMO,
  SCREENSHOT_DEMO_BANNER,
} from '../services/buildMode';

interface Props {
  onSelectPage: (page: number) => void;
}

interface HealthState {
  loading: boolean;
  available: boolean;
  pendingCount: number;
  receiverConfigured: boolean;
  wifiConfigured: boolean;
  testMode: boolean;
}

const initialHealth: HealthState = {
  loading: true,
  available: false,
  pendingCount: 0,
  receiverConfigured: false,
  wifiConfigured: false,
  testMode: true,
};

const StatusRow = ({ label, value }: { label: string; value: string }) => (
  <View style={styles.statusRow}>
    <Text style={styles.statusLabel}>{label}</Text>
    <Text style={styles.statusValue}>{value}</Text>
  </View>
);

const SystemHealthPage: React.FC<Props> = ({ onSelectPage }) => {
  const { t } = useTranslation();
  const [health, setHealth] = useState(initialHealth);

  useEffect(() => {
    let mounted = true;
    Promise.all([getUavConnectionConfig(), getPendingUavRescues()])
      .then(([config, pending]) => {
        if (!mounted) return;
        setHealth({
          loading: false,
          available: true,
          pendingCount: pending.length,
          receiverConfigured: config.baseUrl.trim().length > 0,
          wifiConfigured: config.wifiSsid.trim().length > 0,
          testMode: config.testMode,
        });
      })
      .catch(() => {
        if (mounted) setHealth({ ...initialHealth, loading: false });
      });
    return () => {
      mounted = false;
    };
  }, []);

  const yes = t('systemHealth.value.yes');
  const no = t('systemHealth.value.no');
  const ttlHours = UAV_OUTBOX_TTL_MS / (60 * 60 * 1000);

  return (
    <ScrollView
      testID="system-health-page"
      style={styles.container}
      contentContainerStyle={styles.content}
    >
      <View style={styles.hero}>
        {SCREENSHOT_DEMO && (
          <View testID="local-demo-banner" style={styles.demoBanner}>
            <Text style={styles.demoBannerText}>{SCREENSHOT_DEMO_BANNER}</Text>
          </View>
        )}
        <Text style={styles.eyebrow}>{t('systemHealth.eyebrow')}</Text>
        <Text style={styles.title}>{t('systemHealth.title')}</Text>
        <Text style={styles.disclaimer}>{t('systemHealth.disclaimer')}</Text>
      </View>

      <View testID="release-metadata-card" style={styles.card}>
        <Text style={styles.cardTitle}>{t('systemHealth.release.title')}</Text>
        <StatusRow label={t('systemHealth.release.app')} value={MOBILE_APP_VERSION} />
        <StatusRow
          label={t('systemHealth.release.androidBuild')}
          value={String(ANDROID_VERSION_CODE)}
        />
        <StatusRow
          label={t('systemHealth.release.releaseId')}
          value={SYSTEM_RELEASE_ID}
        />
        <StatusRow
          label={t('systemHealth.release.protocol')}
          value={UAV_RESCUE_PROTOCOL}
        />
        <StatusRow
          label={t('systemHealth.release.outboxSchema')}
          value={`v${UAV_OUTBOX_STORAGE_VERSION}`}
        />
        {MOBILE_STACK_SUMMARY.map(item => (
          <Text key={item} style={styles.stackText}>{item}</Text>
        ))}
      </View>

      <View testID="runtime-health-card" style={styles.card}>
        <Text style={styles.cardTitle}>{t('systemHealth.runtime.title')}</Text>
        {health.loading ? (
          <ActivityIndicator color="#1565C0" />
        ) : (
          <>
            <StatusRow
              label={t('systemHealth.runtime.storage')}
              value={health.available ? yes : t('systemHealth.value.unavailable')}
            />
            <StatusRow
              label={t('systemHealth.runtime.pending')}
              value={`${health.pendingCount}/${UAV_OUTBOX_MAX_ITEMS}`}
            />
            <StatusRow
              label={t('systemHealth.runtime.retention')}
              value={t('systemHealth.runtime.hours', { count: ttlHours })}
            />
            <StatusRow
              label={t('systemHealth.runtime.receiverConfigured')}
              value={health.receiverConfigured ? yes : no}
            />
            <StatusRow
              label={t('systemHealth.runtime.wifiConfigured')}
              value={health.wifiConfigured ? yes : no}
            />
            <StatusRow
              label={t('systemHealth.runtime.androidSelector')}
              value={supportsSystemUavWifiSelection() ? yes : no}
            />
            <StatusRow
              label={t('systemHealth.runtime.mode')}
              value={health.testMode ? 'TEST' : 'PRODUCTION'}
            />
          </>
        )}
        <Text style={styles.privacyNote}>{t('systemHealth.runtime.redaction')}</Text>
      </View>

      <View testID="sync-workflow-card" style={styles.card}>
        <Text style={styles.cardTitle}>{t('systemHealth.workflow.title')}</Text>
        {(['one', 'two', 'three', 'four', 'five'] as const).map((step, index) => (
          <View key={step} style={styles.stepRow}>
            <View style={styles.stepNumber}>
              <Text style={styles.stepNumberText}>{index + 1}</Text>
            </View>
            <Text style={styles.stepText}>{t(`systemHealth.workflow.${step}`)}</Text>
          </View>
        ))}
      </View>

      <TouchableOpacity
        testID="system-health-back-button"
        style={styles.backButton}
        onPress={() => onSelectPage(3)}
      >
        <Text style={styles.backButtonText}>{t('systemHealth.back')}</Text>
      </TouchableOpacity>
    </ScrollView>
  );
};

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#F3F6FA' },
  content: { padding: 16, paddingBottom: 36 },
  hero: {
    backgroundColor: '#0B1F33',
    borderRadius: 16,
    padding: 20,
    marginBottom: 14,
  },
  eyebrow: {
    color: '#77BDFB',
    fontSize: 12,
    fontWeight: '700',
    letterSpacing: 1,
  },
  demoBanner: {
    backgroundColor: '#FFD54F',
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: 8,
    marginBottom: 12,
  },
  demoBannerText: {
    color: '#2B2100',
    fontSize: 13,
    fontWeight: '900',
    textAlign: 'center',
  },
  title: { color: '#FFFFFF', fontSize: 25, fontWeight: '800', marginTop: 6 },
  disclaimer: { color: '#CFD8E3', fontSize: 13, lineHeight: 19, marginTop: 9 },
  card: {
    backgroundColor: '#FFFFFF',
    borderRadius: 14,
    borderWidth: 1,
    borderColor: '#DCE4EC',
    padding: 16,
    marginBottom: 14,
  },
  cardTitle: { color: '#102A43', fontSize: 17, fontWeight: '800', marginBottom: 10 },
  statusRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: 7,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: '#E6ECF2',
  },
  statusLabel: { color: '#52606D', fontSize: 13, flex: 1, paddingRight: 12 },
  statusValue: { color: '#102A43', fontSize: 13, fontWeight: '700' },
  stackText: { color: '#627D98', fontSize: 12, marginTop: 7 },
  privacyNote: {
    color: '#52606D',
    backgroundColor: '#EEF5FB',
    borderRadius: 8,
    padding: 10,
    marginTop: 12,
    fontSize: 12,
    lineHeight: 18,
  },
  stepRow: { flexDirection: 'row', alignItems: 'flex-start', marginBottom: 11 },
  stepNumber: {
    width: 24,
    height: 24,
    borderRadius: 12,
    backgroundColor: '#1565C0',
    alignItems: 'center',
    justifyContent: 'center',
    marginRight: 10,
  },
  stepNumberText: { color: '#FFFFFF', fontSize: 12, fontWeight: '800' },
  stepText: { flex: 1, color: '#334E68', fontSize: 13, lineHeight: 19 },
  backButton: {
    backgroundColor: '#1565C0',
    borderRadius: 10,
    paddingVertical: 13,
    alignItems: 'center',
  },
  backButtonText: { color: '#FFFFFF', fontSize: 15, fontWeight: '800' },
});

export default SystemHealthPage;
