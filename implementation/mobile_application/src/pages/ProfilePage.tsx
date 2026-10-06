import React, { useCallback, useEffect, useContext, useState } from 'react';
import { CloudAccountContext, requireRenderedAccount } from '../services/cloudAccountContext';
import {
  View,
  Text,
  StyleSheet,
  TextInput,
  TouchableOpacity,
  ScrollView,
  KeyboardAvoidingView,
  Alert,
  ActivityIndicator,
  SafeAreaView,
} from 'react-native';
import { useTranslation } from 'react-i18next';


import { getCurrentUserProfile, saveCurrentUserProfile, LocalProfile, resetDb } from '../services/db/initDb';
import { assertCloudIdentity, BoundCloudIdentity } from '../services/mobileAuth';
import { readCloudProfile, writeCloudProfile } from '../services/persistentTracking';
import CloudAccount from '../components/CloudAccount';

interface EmergencyContact {
  name: string;
  phone: string;
}

const contactsFrom = (value: unknown): EmergencyContact[] => {
  try {
    const parsed = typeof value === 'string' ? JSON.parse(value) : value;
    return Array.isArray(parsed) ? parsed.filter(item => item && typeof item === 'object')
      .map(item => ({ name: String(item.name ?? ''), phone: String(item.phone ?? '') }))
      .filter(item => item.name.trim() || item.phone.trim()) : [];
  } catch { return []; }
};

const ProfilePage = () => {
  const { t } = useTranslation();
  const renderedAccount = useContext(CloudAccountContext);

  const [firstName, setFirstName] = useState('');
  const [lastName, setLastName] = useState('');
  const [gender, setGender] = useState('');
  const [phoneNumber, setPhoneNumber] = useState('');
  const [email, setEmail] = useState('');
  const [medicalNotes, setMedicalNotes] = useState('');

  const [emergencyContacts, setEmergencyContacts] = useState<EmergencyContact[]>([]);

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [profileExists, setProfileExists] = useState(false);
  const [owner, setOwner] = useState<BoundCloudIdentity | null>(null);
  const [cloudNotice, setCloudNotice] = useState('');

  const loadProfile = useCallback(async () => {
    setLoading(true);
    try {
      const identity = await requireRenderedAccount(renderedAccount);
      setOwner(identity);
      setPhoneNumber(identity.phone);
      let data = await getCurrentUserProfile(identity);
      // Retain an existing local profile until the user explicitly saves it again.
      if (!data) {
        try {
          const remote = await readCloudProfile(identity);
          if (remote && typeof remote === 'object' && !Array.isArray(remote)) {
            const value = remote as Record<string, unknown>;
            data = {
              first_name: String(value.first_name ?? ''), last_name: String(value.last_name ?? ''),
              gender: String(value.gender ?? ''), phone: identity.phone,
              email: String(value.email ?? ''), medical_notes: String(value.medical_notes ?? ''),
              emergency_contacts: JSON.stringify(contactsFrom(value.emergency_contacts)),
            };
            await saveCurrentUserProfile(data, identity);
          }
        } catch { setCloudNotice(t('cloudAccount.cloudUnavailable')); }
      }
      await assertCloudIdentity(identity);
      if (data) {
        setFirstName(data.first_name || ''); setLastName(data.last_name || '');
        setGender(data.gender || ''); setEmail(data.email || '');
        setMedicalNotes(data.medical_notes || '');
        setEmergencyContacts(contactsFrom(data.emergency_contacts));
        setProfileExists(true);
      }
    } catch {
      setOwner(null);
    } finally { setLoading(false); }
  }, [t, renderedAccount]);

  useEffect(() => {
    loadProfile();
  }, [loadProfile]);

  const addEmergencyContact = () => {
    setEmergencyContacts((prev) => [...prev, { name: '', phone: '' }]);
  };

  const removeEmergencyContact = (index: number) => {
    setEmergencyContacts((prev) => prev.filter((_, i) => i !== index));
  };

  const updateEmergencyContact = (index: number, field: 'name' | 'phone', value: string) => {
    const updated = [...emergencyContacts];
    updated[index] = { ...updated[index], [field]: value };
    setEmergencyContacts(updated);
  };

  const saveProfile = async () => {
    if (!firstName.trim() || !lastName.trim() || !phoneNumber.trim()) {
      Alert.alert(
        t('profilePage.alert.validation.title'),
        t('profilePage.alert.validation.requiredFields')
      );
      return;
    }

    setSaving(true);

    const validContacts = emergencyContacts
      .map((c) => ({
        name: c.name.trim(),
        phone: c.phone.trim(),
      }))
      .filter((c) => c.name.length > 0 || c.phone.length > 0);

    const contactsJson = JSON.stringify(validContacts);

    const profileData: LocalProfile = {
      first_name: firstName.trim(),
      last_name: lastName.trim(),
      gender: gender.trim(),
      phone: owner?.phone || '',
      email: email.trim(),
      medical_notes: medicalNotes.trim(),
      emergency_contacts: contactsJson,
      updated_at: new Date().toISOString(),
    };

    try {
      if (!owner) throw new Error('A bound account is required.');
      await saveCurrentUserProfile(profileData, owner);
      setProfileExists(true);
      try {
        await writeCloudProfile(owner, profileData);
        setCloudNotice('');
        Alert.alert(t('profilePage.alert.saveSuccess.title'), t('cloudAccount.profileSynchronized'));
      } catch {
        setCloudNotice(t('cloudAccount.profileLocalOnly'));
        Alert.alert(t('profilePage.alert.saveSuccess.title'), t('cloudAccount.profileLocalOnly'));
      }
    } catch {
      Alert.alert(t('profilePage.alert.saveFailed.title'), t('profilePage.alert.saveFailed.message'));
    } finally { setSaving(false); }
  };

  const clearDatabase = async () => {
    Alert.alert(
      t('profilePage.alert.clearDatabase.title'),
      t('profilePage.alert.clearDatabase.message'),
      [
        { text: t('profilePage.button.cancel'), style: 'cancel' },
        {
          text: t('profilePage.button.clear'),
          style: 'destructive',
          onPress: async () => {
            try {
              if (!owner) return;
              await resetDb(owner);
              await assertCloudIdentity(owner);
              setFirstName('');
              setLastName('');
              setGender('');
              setPhoneNumber(owner.phone);
              setEmail('');
              setMedicalNotes('');
              setEmergencyContacts([]);
              setProfileExists(false);

              Alert.alert(t('profilePage.alert.clearSuccess.title'));
            } catch (err) {
              Alert.alert(t('profilePage.alert.clearFailed.title'), t('profilePage.alert.clearFailed.message'));
            }
          },
        },
      ]
    );
  };

  if (loading) {
    return (
      <SafeAreaView style={styles.loadingContainer}>
        <ActivityIndicator size="large" color="#007AFF" />
        <Text style={styles.loadingText}>{t('profilePage.loading')}</Text>
      </SafeAreaView>
    );
  }

  if (!owner) {
    return <SafeAreaView style={styles.safeArea}><ScrollView contentContainerStyle={styles.scrollContent}>
      <CloudAccount />
      <Text>{t('cloudAccount.bindingRequired')}</Text>
    </ScrollView></SafeAreaView>;
  }

  return (
    <SafeAreaView style={styles.safeArea}>
      <KeyboardAvoidingView
        style={styles.flex}
        behavior={'height'}
      >
        <ScrollView contentContainerStyle={styles.scrollContent}>
          <Text style={styles.title}>
            {profileExists ? t('profilePage.title.existing') : t('profilePage.title.new')}
          </Text>

          {!!cloudNotice && <Text>{cloudNotice}</Text>}
          <View style={styles.sectionCard}>
            <Text style={styles.sectionTitle}>{t('profilePage.section.personal')}</Text>

            <Input
              label={t('profilePage.form.firstName.label')}
              placeholder={t('profilePage.form.placeholder.firstName')}
              value={firstName}
              onChange={setFirstName}
              required
            />

            <Input
              label={t('profilePage.form.lastName.label')}
              placeholder={t('profilePage.form.placeholder.lastName')}
              value={lastName}
              onChange={setLastName}
              required
            />

            <Input
              label={t('profilePage.form.gender.label')}
              placeholder={t('profilePage.form.placeholder.gender')}
              value={gender}
              onChange={setGender}
            />

            <Input
              label={t('profilePage.form.phone.label')}
              placeholder={t('profilePage.form.placeholder.phoneNumber')}
              value={phoneNumber}
              onChange={setPhoneNumber}
              editable={false}
              keyboardType="phone-pad"
              required
            />

            <Input
              label={t('profilePage.form.email.label')}
              placeholder={t('profilePage.form.placeholder.email')}
              value={email}
              onChange={setEmail}
              keyboardType="email-address"
              autoCapitalize="none"
            />
          </View>

          <View style={styles.sectionCard}>
            <Text style={styles.sectionTitle}>{t('profilePage.section.medical')}</Text>
            <TextInput
              style={[styles.input, styles.textArea]}
              value={medicalNotes}
              onChangeText={setMedicalNotes}
              multiline
              placeholder={t('profilePage.form.medicalNotes.placeholder')}
              placeholderTextColor="#999"
            />
          </View>

          <View style={styles.sectionCard}>
            <View style={styles.sectionHeader}>
              <Text style={styles.sectionTitle}>{t('profilePage.emergencyContacts.title')}</Text>
              <TouchableOpacity onPress={addEmergencyContact}>
                <Text style={styles.addButton}>+ {t('profilePage.emergencyContacts.addButton')}</Text>
              </TouchableOpacity>
            </View>

            {emergencyContacts.length === 0 ? (
              <Text style={styles.emptyText}>
                {t('profilePage.emergencyContacts.empty') || '尚未新增緊急聯絡人'}
              </Text>
            ) : (
              emergencyContacts.map((contact, index) => (
                <View key={index} style={styles.contactCard}>
                  <Input
                    label={t('profilePage.emergencyContacts.name')}
                    placeholder={t('profilePage.form.placeholder.name')}
                    value={contact.name}
                    onChange={(v) => updateEmergencyContact(index, 'name', v)}
                  />

                  <Input
                    label={t('profilePage.emergencyContacts.phone')}
                    placeholder={t('profilePage.form.placeholder.phone')}
                    value={contact.phone}
                    onChange={(v) => updateEmergencyContact(index, 'phone', v)}
                    keyboardType="phone-pad"
                  />

                  <TouchableOpacity
                    style={styles.removeButton}
                    onPress={() => removeEmergencyContact(index)}
                  >
                    <Text style={styles.removeText}>
                      {t('profilePage.emergencyContacts.remove')}
                    </Text>
                  </TouchableOpacity>
                </View>
              ))
            )}
          </View>

          <TouchableOpacity
            style={[styles.saveButton, saving && styles.buttonDisabled]}
            onPress={saveProfile}
            disabled={saving}
          >
            {saving ? (
              <ActivityIndicator color="white" />
            ) : (
              <Text style={styles.buttonText}>
                {profileExists ? t('profilePage.button.update') : t('profilePage.button.save')}
              </Text>
            )}
          </TouchableOpacity>

          <TouchableOpacity onPress={clearDatabase} style={styles.dangerLink}>
            <Text style={styles.dangerText}>{t('profilePage.button.clearDatabase')}</Text>
          </TouchableOpacity>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
};

const Input = ({
  label,
  placeholder,
  value,
  onChange,
  keyboardType = 'default',
  autoCapitalize = 'words',
  required = false,
  editable = true,
}: {
  label: string;
  placeholder?: string;
  value: string;
  onChange: (v: string) => void;
  keyboardType?: 'default' | 'phone-pad' | 'email-address';
  autoCapitalize?: 'none' | 'words';
  required?: boolean;
  editable?: boolean;
}) => (
  <View style={styles.inputGroup}>
    <Text style={styles.inputLabel}>
      {label}
      {required && <Text style={styles.requiredStar}> *</Text>}
    </Text>
    <TextInput
      style={styles.input}
      placeholder={placeholder}
      placeholderTextColor="#999"
      value={value}
      onChangeText={onChange}
      keyboardType={keyboardType}
      autoCapitalize={autoCapitalize}
      editable={editable}
    />
  </View>
);

const styles = StyleSheet.create({
  safeArea: { flex: 1, backgroundColor: '#f8f9fa' },
  flex: { flex: 1 },
  scrollContent: { padding: 20, paddingBottom: 40 },

  loadingContainer: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  loadingText: { marginTop: 16, color: '#666', fontSize: 16 },

  title: {
    fontSize: 26,
    fontWeight: '700',
    color: '#000',
    textAlign: 'center',
    marginBottom: 24,
  },

  sectionCard: {
    backgroundColor: '#fff',
    borderRadius: 16,
    padding: 20,
    marginBottom: 20,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.1,
    shadowRadius: 8,
    elevation: 4,
  },
  sectionHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 16,
  },
  sectionTitle: { fontSize: 18, fontWeight: '600', color: '#333' },
  addButton: { color: '#007AFF', fontWeight: '600', fontSize: 15 },

  contactCard: {
    backgroundColor: '#f9f9f9',
    borderRadius: 12,
    padding: 16,
    marginBottom: 12,
  },

  inputGroup: { marginBottom: 16 },
  inputLabel: { fontSize: 14, color: '#555', marginBottom: 6, fontWeight: '500' },
  requiredStar: { color: '#FF3B30' },
  input: {
    borderWidth: 1,
    borderColor: '#ddd',
    borderRadius: 10,
    paddingHorizontal: 14,
    paddingVertical: 12,
    fontSize: 16,
    backgroundColor: '#fff',
  },
  textArea: { minHeight: 120, textAlignVertical: 'top' },

  removeButton: { alignSelf: 'flex-end', marginTop: 8 },
  removeText: { color: '#FF3B30', fontSize: 14 },

  saveButton: {
    backgroundColor: '#34C759',
    borderRadius: 12,
    paddingVertical: 16,
    alignItems: 'center',
    marginTop: 24,
    marginBottom: 16,
  },
  buttonDisabled: { backgroundColor: '#A8A8A8' },
  buttonText: { color: 'white', fontSize: 18, fontWeight: '600' },

  dangerLink: { alignItems: 'center', marginTop: 8 },
  dangerText: { color: '#FF3B30', fontSize: 16 },

  emptyText: {
    color: '#888',
    fontSize: 15,
    textAlign: 'center',
    paddingVertical: 20,
  },
});

export default ProfilePage;
