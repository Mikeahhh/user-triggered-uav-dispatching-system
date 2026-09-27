import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TextInput,
  ScrollView,
  Alert,
  TouchableOpacity,
  ActivityIndicator,
} from 'react-native';
import MapView, { Marker, Polyline } from 'react-native-maps';
import DatePicker from 'react-native-date-picker';
import { useTranslation } from 'react-i18next';
import { initDb, getDb } from '../services/db/initDb';
import { buildRealtimeDatabaseRestUrl } from '../services/db/firebaseRealtimeDatabase';
import {
  buildEventBookingRecord,
  estimateBookingTime,
  createTimeBasedEventId,
  parseLocalCalendarDate,
  parseLocalDateTime,
  type EventBookingItem,
  type EventBookingWaypoint,
} from '../services/eventBookingRecord';

type Waypoint = EventBookingWaypoint;
type EventItem = EventBookingItem;


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

const SHADOW_SM = {
  shadowColor: '#000',
  shadowOffset: { width: 0, height: 1 },
  shadowOpacity: 0.06,
  shadowRadius: 4,
  elevation: 2,
};

const SHADOW_MD = {
  shadowColor: '#000',
  shadowOffset: { width: 0, height: 4 },
  shadowOpacity: 0.1,
  shadowRadius: 8,
  elevation: 4,
};

const EventBookingPage = () => {
  const { t } = useTranslation();

  const [events, setEvents] = useState<EventItem[]>([]);
  const [loading, setLoading] = useState(true);

  const [showForm, setShowForm] = useState(false);
  const [editingEventId, setEditingEventId] = useState<string | null>(null);
  const [title, setTitle] = useState('');
  const [selectedDate, setSelectedDate] = useState(new Date());
  const [startTime, setStartTime] = useState(new Date());
  const [waypoints, setWaypoints] = useState<Waypoint[]>([]);

  const [dateOpen, setDateOpen] = useState(false);
  const [startTimeOpen, setStartTimeOpen] = useState(false);

  const mapRef = useRef<MapView>(null);

  const getUserPhone = useCallback(async () => {
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
      console.error('Error getting phone:', err);
      return null;
    }
  }, []);

  const loadEventsFromFirebase = useCallback(async () => {
    try {
      setLoading(true);
      const phone = await getUserPhone();
      if (!phone) {
        Alert.alert(
          t('eventBookingPage.alert.noPhone.title'),
          t('eventBookingPage.alert.noPhone.message')
        );
        setEvents([]);
        setLoading(false);
        return;
      }

      const response = await fetch(
        buildRealtimeDatabaseRestUrl('users', phone, 'booked_events')
      );
      const data = await response.json();

      if (data) {
        const loadedEvents: EventItem[] = Object.entries(data).map(([id, val]: [string, any]) => ({
          id,
          ...val,
        }));
        setEvents(loadedEvents.sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime()));
      } else {
        setEvents([]);
      }
    } catch (err) {
      console.error('Failed to load events:', err);
      Alert.alert(
        t('eventBookingPage.alert.loadFailed.title'),
        t('eventBookingPage.alert.loadFailed.message')
      );
    } finally {
      setLoading(false);
    }
  }, [getUserPhone, t]);

  useEffect(() => {
    loadEventsFromFirebase();
  }, [loadEventsFromFirebase]);

  const saveEventToFirebase = async () => {
    if (!title.trim()) {
      Alert.alert(
        t('eventBookingPage.alert.validation.title'),
        t('eventBookingPage.alert.validation.titleRequired')
      );
      return;
    }
    if (waypoints.length < 2) {
      Alert.alert(
        t('eventBookingPage.alert.validation.title'),
        t('eventBookingPage.alert.validation.waypointsRequired')
      );
      return;
    }

    const phone = await getUserPhone();
    if (!phone) {
      Alert.alert(
        t('eventBookingPage.alert.noPhone.title'),
        t('eventBookingPage.alert.noPhone.message')
      );
      return;
    }

    let eventId: string;
    let createdAt: string;

    if (editingEventId) {
      eventId = editingEventId;
      const existingEvent = events.find(e => e.id === eventId);
      createdAt = existingEvent?.createdAt || new Date().toISOString();
    } else {
      const createdAtMs = Date.now();
      eventId = createTimeBasedEventId(
        createdAtMs,
        events.map(event => event.id),
      );
      createdAt = new Date(createdAtMs).toISOString();
    }

    try {
      const updatedEvent = buildEventBookingRecord({
      title,
      selectedDate,
      startTime,
      waypoints,
      createdAt,
      });

      const response = await fetch(
        buildRealtimeDatabaseRestUrl(
          'users',
          phone,
          'booked_events',
          eventId,
        ),
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(updatedEvent),
        }
      );

      if (response.ok) {
        setEvents(prev => {
          if (editingEventId) {
            return prev.map(e => (e.id === eventId ? { id: eventId, ...updatedEvent } : e));
          } else {
            return [{ id: eventId, ...updatedEvent }, ...prev];
          }
        });

        Alert.alert(
          t('eventBookingPage.alert.saveSuccess.title'),
          editingEventId
            ? t('eventBookingPage.alert.saveSuccess.updated')
            : t('eventBookingPage.alert.saveSuccess.message')
        );

        resetForm();
      } else {
        throw new Error('Save failed');
      }
    } catch (err) {
      console.error('Failed to save event:', err);
      Alert.alert(
        t('eventBookingPage.alert.saveFailed.title'),
        t('eventBookingPage.alert.saveFailed.message')
      );
    }
  };

  const deleteEvent = async (eventId: string) => {
    Alert.alert(
      t('eventBookingPage.alert.deleteConfirm.title'),
      t('eventBookingPage.alert.deleteConfirm.message'),
      [
        { text: t('eventBookingPage.button.cancel'), style: 'cancel' },
        {
          text: t('eventBookingPage.button.delete'),
          style: 'destructive',
          onPress: async () => {
            const phone = await getUserPhone();
            if (!phone) return;

            try {
              const response = await fetch(
                buildRealtimeDatabaseRestUrl(
                  'users',
                  phone,
                  'booked_events',
                  eventId,
                ),
                {
                  method: 'DELETE',
                }
              );

              if (response.ok) {
                setEvents(prev => prev.filter(e => e.id !== eventId));
                Alert.alert(
                  t('eventBookingPage.alert.deleteSuccess.title'),
                  t('eventBookingPage.alert.deleteSuccess.message')
                );
              } else {
                throw new Error('Delete failed');
              }
            } catch (err) {
              console.error('Failed to delete event:', err);
              Alert.alert(
                t('eventBookingPage.alert.deleteFailed.title'),
                t('eventBookingPage.alert.deleteFailed.message')
              );
            }
          },
        },
      ]
    );
  };

  const editEvent = (event: EventItem) => {
    setEditingEventId(event.id);
    setTitle(event.title);
    setSelectedDate(parseLocalCalendarDate(event.date));
    setStartTime(parseLocalDateTime(event.date, event.startTime));
    setWaypoints(event.waypoints);
    setShowForm(true);
  };

  const resetForm = () => {
    setEditingEventId(null);
    setTitle('');
    setSelectedDate(new Date());
    setStartTime(new Date());
    setWaypoints([]);
    setShowForm(false);
  };

  const handleMapLongPress = (e: any) => {
    const { coordinate } = e.nativeEvent;
    setWaypoints(prev => [...prev, coordinate]);
  };

  const removeLastWaypoint = () => {
    setWaypoints(prev => prev.slice(0, -1));
  };

  const formatDate = (d: Date) => d.toLocaleDateString('zh-TW');
  const formatTime = (d: Date) => d.toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit' });

  let estimatedEndLabel = t('eventBookingPage.form.routeNeeded');
  try {
    const estimate = estimateBookingTime(selectedDate, startTime, waypoints);
    const end = new Date(estimate.expectedEndAtMs);
    estimatedEndLabel = `${formatDate(end)} ${formatTime(end)}`;
  } catch {
    estimatedEndLabel = t('eventBookingPage.form.routeNeeded');
  }

  return (
    <ScrollView style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.title}>{t('eventBookingPage.title')}</Text>
        <Text style={styles.subtitle}>{t('eventBookingPage.subtitle')}</Text>
      </View>
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>
          {t('eventBookingPage.events.title', { count: events.length })}
        </Text>

        {loading ? (
          <View style={styles.loadingContainer}>
            <ActivityIndicator size="small" color={COLORS.primary} />
            <Text style={styles.loadingText}>{t('eventBookingPage.loading')}</Text>
          </View>
        ) : events.length === 0 ? (
          <Text style={styles.emptyText}>{t('eventBookingPage.events.empty')}</Text>
        ) : (
          events.map(event => (
            <View key={event.id} style={[styles.eventCard, SHADOW_SM]}>
              <Text style={styles.eventTitle}>{event.title}</Text>
              <Text style={styles.eventDetail}>{t('eventBookingPage.events.date')}: {event.date}</Text>
              <Text style={styles.eventDetail}>
                {t('eventBookingPage.events.time')}: {event.startTime} ~ {event.endDate && event.endDate !== event.date ? `${event.endDate} ` : ''}{event.endTime}
              </Text>
              <Text style={styles.eventDetail}>{t('eventBookingPage.events.waypoints')}: {event.waypoints.length}</Text>

              <View style={styles.eventActions}>
                <TouchableOpacity onPress={() => editEvent(event)}>
                  <Text style={styles.editButton}>{t('eventBookingPage.button.edit')}</Text>
                </TouchableOpacity>
                <TouchableOpacity onPress={() => deleteEvent(event.id)}>
                  <Text style={styles.deleteButton}>{t('eventBookingPage.button.delete')}</Text>
                </TouchableOpacity>
              </View>
            </View>
          ))
        )}
      </View>
      <View style={styles.controlSection}>
        <TouchableOpacity
          style={[
            styles.actionButton,
            showForm ? styles.buttonCancel : styles.buttonAdd,
            SHADOW_MD,
          ]}
          onPress={() => {
            if (showForm) resetForm();
            else setShowForm(true);
          }}
          activeOpacity={0.85}
        >
          <Text style={styles.actionButtonText}>
            {showForm
              ? (editingEventId
                  ? t('eventBookingPage.button.cancelEdit')
                  : t('eventBookingPage.button.cancelAdd'))
              : t('eventBookingPage.button.addNew')}
          </Text>
        </TouchableOpacity>
      </View>
      {showForm && (
        <View style={[styles.formContainer, SHADOW_MD]}>
          <Text style={styles.formTitle}>
            {editingEventId ? t('eventBookingPage.form.editTitle') : t('eventBookingPage.form.addTitle')}
          </Text>

          <TextInput
            style={styles.input}
            placeholder={t('eventBookingPage.form.titlePlaceholder')}
            value={title}
            onChangeText={setTitle}
          />
          <TouchableOpacity style={styles.pickerRow} onPress={() => setDateOpen(true)}>
            <Text style={styles.pickerText}>{t('eventBookingPage.form.date')}: {formatDate(selectedDate)}</Text>
          </TouchableOpacity>
          <DatePicker
            modal
            open={dateOpen}
            date={selectedDate}
            onConfirm={(date) => {
              setDateOpen(false);
              setSelectedDate(date);
            }}
            onCancel={() => setDateOpen(false)}
            mode="date"
            minimumDate={new Date()}
          />
          <TouchableOpacity style={styles.pickerRow} onPress={() => setStartTimeOpen(true)}>
            <Text style={styles.pickerText}>{t('eventBookingPage.form.startTime')}: {formatTime(startTime)}</Text>
          </TouchableOpacity>
          <DatePicker
            modal
            open={startTimeOpen}
            date={startTime}
            onConfirm={(time) => {
              setStartTimeOpen(false);
              setStartTime(time);
            }}
            onCancel={() => setStartTimeOpen(false)}
            mode="time"
          />
          <View style={styles.pickerRow}>
            <Text style={styles.pickerText}>{t('eventBookingPage.form.estimatedEnd')}: {estimatedEndLabel}</Text>
            <Text style={styles.pickerText}>{t('eventBookingPage.form.estimateMethod')}</Text>
          </View>
          <View style={styles.mapSection}>
            <Text style={styles.sectionTitle}>
              {t('eventBookingPage.form.map.title', { count: waypoints.length })}
            </Text>

            <View style={[styles.mapContainer, SHADOW_MD]}>
              <MapView
                ref={mapRef}
                style={StyleSheet.absoluteFillObject}
                initialRegion={{
                  latitude: 22.387,
                  longitude: 114.195,
                  latitudeDelta: 0.08,
                  longitudeDelta: 0.08,
                }}
                onLongPress={handleMapLongPress}
                showsUserLocation={true}
                showsMyLocationButton={true}
                showsCompass={true}
              >
                {waypoints.map((wp, idx) => (
                  <Marker
                    key={idx}
                    coordinate={wp}
                    title={t('eventBookingPage.form.map.marker', { index: idx + 1 })}
                    pinColor={idx === 0 ? 'green' : idx === waypoints.length - 1 ? 'red' : 'orange'}
                  />
                ))}

                {waypoints.length >= 2 && (
                  <Polyline
                    coordinates={waypoints}
                    strokeColor={COLORS.primary}
                    strokeWidth={5}
                    lineCap="round"
                    lineJoin="round"
                  />
                )}
              </MapView>
            </View>

            {waypoints.length > 0 && (
              <TouchableOpacity
                style={styles.removeButton}
                onPress={removeLastWaypoint}
                activeOpacity={0.85}
              >
                <Text style={styles.removeButtonText}>{t('eventBookingPage.button.removeLast')}</Text>
              </TouchableOpacity>
            )}
          </View>
          <TouchableOpacity
            style={[
              styles.saveButton,
              (waypoints.length < 2 || !title.trim()) && styles.disabledSaveButton,
              SHADOW_MD,
            ]}
            onPress={saveEventToFirebase}
            disabled={waypoints.length < 2 || !title.trim()}
            activeOpacity={0.85}
          >
            <Text style={styles.saveButtonText}>
              {editingEventId ? t('eventBookingPage.button.update') : t('eventBookingPage.button.save')}
            </Text>
          </TouchableOpacity>
        </View>
      )}

      <View style={styles.bottomSpacer} />
    </ScrollView>
  );
};

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.background },
  bottomSpacer: { height: 40 },
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

  section: {
    marginHorizontal: 20,
    marginTop: 24,
    marginBottom: 16,
  },
  sectionTitle: {
    fontSize: 18,
    fontWeight: '700',
    color: COLORS.text,
    marginBottom: 12,
  },
  loadingContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 16,
  },
  loadingText: {
    marginLeft: 10,
    color: COLORS.textSecondary,
    fontSize: 15,
  },
  emptyText: {
    textAlign: 'center',
    color: COLORS.textSecondary,
    padding: 20,
    fontSize: 15,
  },
  eventCard: {
    backgroundColor: COLORS.lightBg,
    padding: 16,
    borderRadius: 16,
    marginBottom: 12,
    borderLeftWidth: 5,
    borderLeftColor: COLORS.primary,
  },
  eventTitle: {
    fontSize: 18,
    fontWeight: '600',
    color: COLORS.primary,
    marginBottom: 8,
  },
  eventDetail: {
    fontSize: 14,
    color: COLORS.textSecondary,
    marginBottom: 4,
  },
  eventActions: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    marginTop: 8,
  },
  editButton: {
    color: COLORS.primary,
    marginRight: 16,
    fontWeight: '600',
    fontSize: 14,
  },
  deleteButton: {
    color: COLORS.danger,
    fontWeight: '600',
    fontSize: 14,
  },

  controlSection: {
    marginHorizontal: 20,
    marginBottom: 20,
  },
  actionButton: {
    paddingVertical: 18,
    borderRadius: 16,
    alignItems: 'center',
    justifyContent: 'center',
  },
  buttonAdd: {
    backgroundColor: COLORS.success,
  },
  buttonCancel: {
    backgroundColor: COLORS.danger,
  },
  actionButtonText: {
    color: 'white',
    fontSize: 18,
    fontWeight: '700',
  },

  formContainer: {
    marginHorizontal: 20,
    marginBottom: 32,
    padding: 20,
    borderRadius: 20,
    backgroundColor: 'white',
  },
  formTitle: {
    fontSize: 20,
    fontWeight: '700',
    color: COLORS.text,
    marginBottom: 16,
  },
  input: {
    borderWidth: 1,
    borderColor: COLORS.border,
    borderRadius: 12,
    padding: 16,
    marginBottom: 16,
    fontSize: 16,
    backgroundColor: COLORS.lightBg,
  },
  pickerRow: {
    padding: 16,
    backgroundColor: COLORS.lightBg,
    borderRadius: 12,
    marginBottom: 12,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  pickerText: {
    fontSize: 15,
    color: COLORS.text,
  },

  mapSection: { marginTop: 20 },
  mapContainer: {
    height: 340,
    borderRadius: 20,
    overflow: 'hidden',
    borderWidth: 1,
    borderColor: COLORS.mapBorder,
    backgroundColor: COLORS.lightBg,
  },
  removeButton: {
    marginTop: 12,
    backgroundColor: '#F59E0B',
    paddingVertical: 14,
    borderRadius: 12,
    alignItems: 'center',
  },
  removeButtonText: {
    color: 'white',
    fontSize: 16,
    fontWeight: '600',
  },

  saveButton: {
    backgroundColor: COLORS.success,
    paddingVertical: 18,
    borderRadius: 16,
    alignItems: 'center',
    marginTop: 24,
  },
  disabledSaveButton: {
    backgroundColor: COLORS.border,
  },
  saveButtonText: {
    color: 'white',
    fontSize: 18,
    fontWeight: '700',
  },
});

export default EventBookingPage;
