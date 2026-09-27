import React, { useEffect, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ScrollView,
  Alert,
  TextInput,
  Switch,
} from 'react-native';
import { useTranslation } from 'react-i18next';
import {
  DEFAULT_UAV_BASE_URL,
  DEFAULT_UAV_WIFI_SSID,
  getUavConnectionConfig,
  saveUavConnectionConfig,
  setUavSessionToken,
} from '../services/uavRescueClient';
import {
  setUavWifiSessionPassphrase,
  validateUavWifiSessionPassphrase,
} from '../services/uavWifiClient';
import { SYSTEM_RELEASE_ID } from '../services/appMetadata';

interface Props {
  onSelectPage: (page: number) => void;
}

interface SettingItemProps {
  title: string;
  subtitle?: string;
  onPress?: () => void;
}

const SettingItem: React.FC<SettingItemProps> = ({
  title,
  subtitle,
  onPress,
}) => (
  <TouchableOpacity
    style={styles.itemContainer}
    onPress={onPress}
    activeOpacity={0.7}
  >
    <View style={styles.itemContent}>
      <Text style={styles.itemTitle}>{title}</Text>
      {subtitle && <Text style={styles.itemSubtitle}>{subtitle}</Text>}
    </View>
    <Text style={styles.arrow}>{'>'}</Text>
  </TouchableOpacity>
);

const SettingPage: React.FC<Props> = ({ onSelectPage }) => {
  const { t, i18n } = useTranslation();
  const [uavBaseUrl, setUavBaseUrl] = useState(DEFAULT_UAV_BASE_URL);
  const [uavWifiSsid, setUavWifiSsid] = useState(DEFAULT_UAV_WIFI_SSID);
  const [uavWifiPassphrase, setUavWifiPassphrase] = useState('');
  const [uavToken, setUavToken] = useState('');
  const [uavTestMode, setUavTestMode] = useState(true);

  useEffect(() => {
    getUavConnectionConfig().then(config => {
      setUavBaseUrl(config.baseUrl);
      setUavWifiSsid(config.wifiSsid);
      setUavTestMode(config.testMode);
    }).catch(() => {
      Alert.alert(t('settingPage.uav.errorTitle'), t('settingPage.uav.reenterConfig'));
    });
  }, [t]);

  const currentLang = i18n.language === 'zh' ? '繁體中文' : 'English';
  const nextLang = i18n.language === 'en' ? 'zh' : 'en';

  const handleLanguageChange = () => {
    i18n.changeLanguage(nextLang);
  };

  const handleGoToProfile = () => {
    onSelectPage(6);
  };

  const handleAbout = () => {
    Alert.alert(
      t('settingPage.about.title'),
      t('settingPage.about.message')
    );
  };

  const handleTerms = () => {
    Alert.alert(
      t('settingPage.terms.title'),
      t('settingPage.terms.message')
    );
  };

  const handlePrivacy = () => {
    Alert.alert(
      t('settingPage.privacy.title'),
      t('settingPage.privacy.message')
    );
  };

  const handleSaveUav = async () => {
    try {
      validateUavWifiSessionPassphrase(uavWifiPassphrase);
      await saveUavConnectionConfig({
        baseUrl: uavBaseUrl,
        wifiSsid: uavWifiSsid,
        testMode: uavTestMode,
      });
      setUavSessionToken(uavToken);
      setUavWifiSessionPassphrase(uavWifiPassphrase);
      Alert.alert(
        t('settingPage.uav.savedTitle'),
        uavToken.trim()
          ? t('settingPage.uav.savedMessage')
          : t('settingPage.uav.savedWithoutToken'),
      );
    } catch {
      Alert.alert(
        t('settingPage.uav.errorTitle'),
        t('settingPage.uav.invalidConnection'),
      );
    }
  };

  return (
    <ScrollView style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.headerTitle}>
          {t('settingPage.title')}
        </Text>
      </View>

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>{t('settingPage.uav.title')}</Text>
        {uavTestMode && (
          <View testID="settings-uav-test-banner" style={styles.testModeBanner}>
            <Text style={styles.testModeText}>{t('settingPage.uav.testBanner')}</Text>
          </View>
        )}
        <View style={styles.switchRow}>
          <View style={styles.switchCopy}>
            <Text style={styles.switchTitle}>{t('settingPage.uav.testMode')}</Text>
            <Text style={styles.switchSubtitle}>
              {uavTestMode
                ? t('settingPage.uav.testModeOn')
                : t('settingPage.uav.testModeOff')}
            </Text>
          </View>
          <Switch
            testID="uav-test-mode-switch"
            value={uavTestMode}
            onValueChange={setUavTestMode}
          />
        </View>
        <Text style={styles.fieldLabel}>{t('settingPage.uav.url')}</Text>
        <TextInput
          testID="uav-base-url-input"
          style={styles.textInput}
          value={uavBaseUrl}
          onChangeText={setUavBaseUrl}
          autoCapitalize="none"
          autoCorrect={false}
          keyboardType="url"
        />
        <Text style={styles.fieldLabel}>{t('settingPage.uav.wifiSsid')}</Text>
        <Text style={styles.fieldHelp}>{t('settingPage.uav.wifiHelp')}</Text>
        <TextInput
          testID="uav-wifi-ssid-input"
          style={styles.textInput}
          value={uavWifiSsid}
          onChangeText={setUavWifiSsid}
          autoCapitalize="none"
          autoCorrect={false}
        />
        <Text style={styles.fieldLabel}>
          {t('settingPage.uav.wifiPassphrase')}
        </Text>
        <Text style={styles.fieldHelp}>
          {t('settingPage.uav.wifiPassphraseHelp')}
        </Text>
        <TextInput
          testID="uav-wifi-passphrase-input"
          style={styles.textInput}
          value={uavWifiPassphrase}
          onChangeText={setUavWifiPassphrase}
          autoCapitalize="none"
          autoCorrect={false}
          secureTextEntry
        />
        <Text style={styles.fieldLabel}>{t('settingPage.uav.token')}</Text>
        <Text style={styles.fieldHelp}>{t('settingPage.uav.tokenHelp')}</Text>
        <TextInput
          testID="uav-token-input"
          style={styles.textInput}
          value={uavToken}
          onChangeText={setUavToken}
          autoCapitalize="none"
          autoCorrect={false}
          secureTextEntry
        />
        <TouchableOpacity
          testID="save-uav-settings-button"
          style={styles.saveButton}
          onPress={handleSaveUav}
        >
          <Text style={styles.saveButtonText}>{t('settingPage.uav.save')}</Text>
        </TouchableOpacity>
      </View>

      <View style={styles.section}>
        <SettingItem
          title={t('settingPage.systemHealth')}
          subtitle={`${t('settingPage.systemHealthSubtitle')} • ${SYSTEM_RELEASE_ID}`}
          onPress={() => onSelectPage(7)}
        />
        <SettingItem
          title={t('settingPage.profile')}
          subtitle={t('settingPage.profileSubtitle')}
          onPress={handleGoToProfile}
        />

        <SettingItem
          title={t('settingPage.language')}
          subtitle={currentLang}
          onPress={handleLanguageChange}
        />

        <SettingItem
          title={t('settingPage.aboutApp')}
          onPress={handleAbout}
        />
      </View>

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>
          {t('settingPage.section.legal')}
        </Text>

        <SettingItem
          title={t('settingPage.terms.title')}
          onPress={handleTerms}
        />

        <SettingItem
          title={t('settingPage.privacy.title')}
          onPress={handlePrivacy}
        />
      </View>

      <Text style={styles.footerText}>
        MyDrone © {new Date().getFullYear()} • v1.2.0
      </Text>
    </ScrollView>
  );
};


const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#f8f9fa',
  },
  header: {
    paddingHorizontal: 20,
    paddingTop: 50,
    paddingBottom: 20,
    backgroundColor: '#ffffff',
    borderBottomWidth: 1,
    borderBottomColor: '#e0e0e0',
  },
  headerTitle: {
    fontSize: 28,
    fontWeight: '700',
    color: '#111827',
  },
  section: {
    marginTop: 16,
    backgroundColor: '#ffffff',
    borderRadius: 12,
    marginHorizontal: 12,
    overflow: 'hidden',
    borderWidth: 1,
    borderColor: '#e5e7eb',
  },
  sectionTitle: {
    fontSize: 13,
    fontWeight: '600',
    color: '#6b7280',
    paddingHorizontal: 16,
    paddingTop: 16,
    paddingBottom: 8,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  itemContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingVertical: 16,
    paddingHorizontal: 16,
    borderBottomWidth: 1,
    borderBottomColor: '#f3f4f6',
  },
  itemContent: {
    flex: 1,
  },
  itemTitle: {
    fontSize: 16,
    color: '#111827',
    fontWeight: '500',
  },
  itemSubtitle: {
    fontSize: 13,
    color: '#6b7280',
    marginTop: 4,
  },
  arrow: {
    fontSize: 18,
    color: '#9ca3af',
    marginLeft: 8,
  },
  footerText: {
    textAlign: 'center',
    color: '#9ca3af',
    fontSize: 12,
    marginVertical: 24,
    marginBottom: 40,
  },
  fieldLabel: {
    fontSize: 13,
    color: '#4b5563',
    marginHorizontal: 16,
    marginTop: 8,
  },
  fieldHelp: {
    fontSize: 12,
    color: '#6b7280',
    marginHorizontal: 16,
    marginTop: 4,
  },
  textInput: {
    marginHorizontal: 16,
    marginTop: 6,
    borderWidth: 1,
    borderColor: '#d1d5db',
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
    color: '#111827',
  },
  saveButton: {
    backgroundColor: '#1565C0',
    margin: 16,
    borderRadius: 8,
    paddingVertical: 12,
    alignItems: 'center',
  },
  saveButtonText: {
    color: '#ffffff',
    fontWeight: '700',
  },
  testModeBanner: {
    backgroundColor: '#F9A825',
    marginHorizontal: 16,
    marginBottom: 10,
    paddingHorizontal: 12,
    paddingVertical: 9,
    borderRadius: 8,
  },
  testModeText: {
    color: '#000000',
    fontWeight: '700',
    textAlign: 'center',
  },
  switchRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingBottom: 10,
  },
  switchCopy: {
    flex: 1,
    paddingRight: 12,
  },
  switchTitle: {
    color: '#111827',
    fontSize: 15,
    fontWeight: '600',
  },
  switchSubtitle: {
    color: '#6b7280',
    fontSize: 12,
    marginTop: 3,
  },
});

export default SettingPage;
