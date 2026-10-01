import React, { useCallback, useEffect, useState } from 'react';
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


import { initDb, getDb, resetDb } from '../services/db/initDb';
import { buildRealtimeDatabaseRestUrl } from '../services/db/firebaseRealtimeDatabase';

interface EmergencyContact {
  name: string;
  phone: string;
}

const normalizePhoneForKey = (phone: string) => phone.replace(/[^0-9]/g, '');

const ProfilePage = () => {
  const { t } = useTranslation();

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
  const [profileId, setProfileId] = useState<number | null>(null);

  const loadProfile = useCallback(async () => {
    setLoading(true);
    try {
      await initDb();
      const db = getDb();

      const result = await db.executeSql('SELECT * FROM user ORDER BY id DESC LIMIT 1');

      let localData = null;
      if (result[0].rows.length > 0) {
        localData = result[0].rows.item(0);

        setFirstName(localData.first_name || '');
        setLastName(localData.last_name || '');
        setGender(localData.gender || '');
        setPhoneNumber(localData.phone || '');
        setEmail(localData.email || '');
        setMedicalNotes(localData.medical_notes || '');
        setProfileId(localData.id);
        setProfileExists(true);


        let parsedContacts: EmergencyContact[] = [];
        if (localData.emergency_contacts) {
          try {
            let raw = localData.emergency_contacts;
            if (typeof raw === 'string') {
              raw = JSON.parse(raw);
            }
            if (Array.isArray(raw)) {
              parsedContacts = raw
                .filter((item): item is Record<string, unknown> => typeof item === 'object' && item !== null)
                .map((item) => ({
                  name: String((item as Record<string, unknown>).name ?? ''),
                  phone: String((item as Record<string, unknown>).phone ?? ''),
                }))
                .filter((c) => c.name.trim() || c.phone.trim());
            }
          } catch (e) {
            console.warn('本地 emergency_contacts 解析失敗:', e);
          }
        }
        setEmergencyContacts(parsedContacts);
      } else {
        setProfileExists(false);
        setEmergencyContacts([]);
      }


      if (localData?.phone) {
        const normalized = normalizePhoneForKey(localData.phone);
        try {
          const res = await fetch(
            buildRealtimeDatabaseRestUrl('users', normalized, 'profile')
          );

          if (!res.ok) {
            console.warn('Firebase fetch 失敗:', res.status);
            return;
          }

          const fbData = await res.json();

          if (fbData && typeof fbData === 'object') {

            setFirstName(fbData.first_name || localData.first_name || '');
            setLastName(fbData.last_name || localData.last_name || '');
            setGender(fbData.gender || localData.gender || '');
            setPhoneNumber(fbData.phone || localData.phone || '');
            setEmail(fbData.email || localData.email || '');
            setMedicalNotes(fbData.medical_notes || localData.medical_notes || '');


            let fbContacts: EmergencyContact[] = [];
            let fbEmergency = fbData.emergency_contacts;

            if (typeof fbEmergency === 'string') {
              try {
                fbEmergency = JSON.parse(fbEmergency);
              } catch (parseErr) {
                console.warn('雲端 emergency_contacts 字串解析失敗:', parseErr);
                fbEmergency = [];
              }
            }

            if (Array.isArray(fbEmergency)) {
              fbContacts = fbEmergency
                .filter((item): item is Record<string, unknown> => typeof item === 'object' && item !== null)
                .map((item) => ({
                  name: String((item as Record<string, unknown>).name ?? ''),
                  phone: String((item as Record<string, unknown>).phone ?? ''),
                }))
                .filter((c) => c.name.trim() || c.phone.trim());
            }

            setEmergencyContacts(fbContacts);


            await db.executeSql(
              `UPDATE user SET first_name=?, last_name=?, gender=?, phone=?, email=?, medical_notes=?, emergency_contacts=?, updated_at=datetime('now') WHERE id=?`,
              [
                fbData.first_name || localData.first_name,
                fbData.last_name || localData.last_name,
                fbData.gender || localData.gender,
                fbData.phone || localData.phone,
                fbData.email || localData.email,
                fbData.medical_notes || localData.medical_notes,
                JSON.stringify(fbContacts),
                localData.id,
              ]
            );
          }
        } catch (err) {
          console.warn('Firebase 同步失敗:', err);
        }
      }
    } catch (error) {
      console.error('loadProfile 失敗:', error);
      Alert.alert(t('profilePage.alert.loadFailed.title'), t('profilePage.alert.loadFailed.message'));
    } finally {
      setLoading(false);
    }
  }, [t]);

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

    const normalizedPhone = normalizePhoneForKey(phoneNumber);

    const validContacts = emergencyContacts
      .map((c) => ({
        name: c.name.trim(),
        phone: c.phone.trim(),
      }))
      .filter((c) => c.name.length > 0 || c.phone.length > 0);

    const contactsJson = JSON.stringify(validContacts);

    const profileData = {
      first_name: firstName.trim(),
      last_name: lastName.trim(),
      gender: gender.trim(),
      phone: phoneNumber.trim(),
      email: email.trim(),
      medical_notes: medicalNotes.trim(),
      emergency_contacts: contactsJson,
      updated_at: new Date().toISOString(),
    };

    try {
      await initDb();
      const db = getDb();

      if (profileExists && profileId) {
        await db.executeSql(
          `UPDATE user SET first_name=?, last_name=?, gender=?, phone=?, email=?, medical_notes=?, emergency_contacts=?, updated_at=datetime('now') WHERE id=?`,
          [
            profileData.first_name,
            profileData.last_name,
            profileData.gender,
            profileData.phone,
            profileData.email,
            profileData.medical_notes,
            profileData.emergency_contacts,
            profileId,
          ]
        );
      } else {
        const result = await db.executeSql(
          `INSERT INTO user (first_name, last_name, gender, phone, email, medical_notes, emergency_contacts, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))`,
          [
            profileData.first_name,
            profileData.last_name,
            profileData.gender,
            profileData.phone,
            profileData.email,
            profileData.medical_notes,
            profileData.emergency_contacts,
          ]
        );
        setProfileId(result[0].insertId ?? null);
        setProfileExists(true);
      }

      const fbRes = await fetch(
        buildRealtimeDatabaseRestUrl('users', normalizedPhone, 'profile'),
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(profileData),
        }
      );

      if (!fbRes.ok) {
        const errText = await fbRes.text();
        console.warn('Firebase 上傳失敗:', fbRes.status, errText);
        Alert.alert('警告', '本地儲存成功，但雲端同步失敗，請檢查網路');
      }

      Alert.alert(
        t('profilePage.alert.saveSuccess.title'),
        t('profilePage.alert.saveSuccess.message')
      );
    } catch (error: any) {
      console.error('儲存失敗:', error);
      Alert.alert(t('profilePage.alert.saveFailed.title'), t('profilePage.alert.saveFailed.message'));
    } finally {
      setSaving(false);
    }
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
              await resetDb();
              setFirstName('');
              setLastName('');
              setGender('');
              setPhoneNumber('');
              setEmail('');
              setMedicalNotes('');
              setEmergencyContacts([]);
              setProfileExists(false);
              setProfileId(null);
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
}: {
  label: string;
  placeholder?: string;
  value: string;
  onChange: (v: string) => void;
  keyboardType?: 'default' | 'phone-pad' | 'email-address';
  autoCapitalize?: 'none' | 'words';
  required?: boolean;
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
