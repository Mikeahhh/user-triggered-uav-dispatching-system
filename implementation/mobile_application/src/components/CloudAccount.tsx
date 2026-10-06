import React, { useEffect, useState } from 'react';
import { ActivityIndicator, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { useTranslation } from 'react-i18next';
import {
  CloudIdentity, readCloudIdentity, refreshCloudBinding, signInToCloud,
  signOutOfCloud, subscribeCloudIdentity,
} from '../services/mobileAuth';

const CloudAccount = () => {
  const { t } = useTranslation();
  const [identity, setIdentity] = useState<CloudIdentity>({ status: 'SIGNED_OUT' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    let mounted = true;
    let receivedEvent = false;
    const unsubscribe = subscribeCloudIdentity(value => { receivedEvent = true; if (mounted) setIdentity(value); });
    readCloudIdentity().then(value => { if (mounted && !receivedEvent) setIdentity(value); })
      .catch(() => { if (mounted) setError(t('cloudAccount.unavailable')); });
    return () => { mounted = false; unsubscribe(); };
  }, [t]);

  const perform = async (operation: () => Promise<unknown>) => {
    if (busy) return;
    setBusy(true); setError('');
    try { await operation(); setIdentity(await readCloudIdentity()); }
    catch { setError(t('cloudAccount.operationFailed')); }
    finally { setBusy(false); }
  };

  return (
    <View style={styles.card}>
      <Text style={styles.title}>{t('cloudAccount.title')}</Text>
      <Text style={styles.body}>{t(`cloudAccount.${identity.status}`)}</Text>
      {!!identity.email && <Text style={styles.body}>{identity.email}</Text>}
      {!!identity.phone && <Text style={styles.body}>{t('cloudAccount.boundPhone', { phone: identity.phone })}</Text>}
      {!!identity.uid && (
        <View>
          <Text style={styles.body}>{t('cloudAccount.accountId')}</Text>
          <Text selectable style={styles.identifier}>{identity.uid}</Text>
        </View>
      )}
      {!!error && <Text accessibilityRole="alert" style={styles.error}>{error}</Text>}
      {busy && <ActivityIndicator />}
      {identity.status === 'SIGNED_OUT' ? (
        <TouchableOpacity testID="google-sign-in" accessibilityRole="button" style={styles.button}
          disabled={busy} onPress={() => perform(signInToCloud)}>
          <Text style={styles.buttonText}>{t('cloudAccount.signIn')}</Text>
        </TouchableOpacity>
      ) : (
        <View>
          <TouchableOpacity testID="refresh-account-binding" accessibilityRole="button" style={styles.button}
            disabled={busy} onPress={() => perform(refreshCloudBinding)}>
            <Text style={styles.buttonText}>{t('cloudAccount.refresh')}</Text>
          </TouchableOpacity>
          <TouchableOpacity testID="google-sign-out" accessibilityRole="button" style={styles.secondaryButton}
            disabled={busy} onPress={() => perform(signOutOfCloud)}>
            <Text style={styles.body}>{t('cloudAccount.signOut')}</Text>
          </TouchableOpacity>
          <Text style={styles.hint}>{t('cloudAccount.signOutHint')}</Text>
        </View>
      )}
    </View>
  );
};

const styles = StyleSheet.create({
  card: { margin: 12, padding: 16, backgroundColor: '#ffffff', borderRadius: 12, borderWidth: 1, borderColor: '#e5e7eb' },
  title: { fontSize: 18, fontWeight: '600', color: '#111827', marginBottom: 8 },
  body: { fontSize: 14, color: '#111827', marginVertical: 4 },
  identifier: { fontSize: 13, color: '#111827', marginBottom: 8 },
  hint: { fontSize: 12, color: '#4b5563', marginTop: 4 },
  error: { fontSize: 14, color: '#b91c1c', marginVertical: 8 },
  button: { padding: 12, backgroundColor: '#1565c0', borderRadius: 8, alignItems: 'center', marginTop: 12 },
  buttonText: { color: '#ffffff', fontWeight: '600' },
  secondaryButton: { padding: 8, alignItems: 'center', marginTop: 8 },
});

export default CloudAccount;
