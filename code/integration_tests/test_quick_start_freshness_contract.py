from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
GS = ROOT / 'ground_station'
sys.path.insert(0, str(GS))
sys.path.insert(0, str(ROOT))
import rescue_event_manager as manager
import rescue_repository as repository
import priority_scheduler as scheduler
from deployment_preflight import check_deployment

BASE = 1_800_000_000_000
TEST_TIMEOUT_MS = 1_000
TEST_WAIT_MS = 600
USER = 'SYNTHETIC_USER'
SESSION = 'SYNTHETIC_SESSION'
TRIGGER = 'QUICK_START_LOCATION_TIMEOUT'
CONFIG = {'quick_start_timeout_ms': TEST_TIMEOUT_MS}


def iso(milliseconds):
    return datetime.fromtimestamp(milliseconds / 1000, timezone.utc).isoformat()


def point(milliseconds, latitude=22.3, longitude=114.2):
    return {'timestamp': milliseconds, 'timestampISO': iso(milliseconds),
            'latitude': latitude, 'longitude': longitude}


def user_record(points=None, status='ACTIVE'):
    return {'QuickStartSessions': {SESSION: {
        'status': status, 'startTime': iso(BASE - 100),
        'points': {'initial': point(BASE)} if points is None else copy.deepcopy(points),
    }}}


class MemoryReference:

    def __init__(self, database, path=()):
        self.database, self.path = database, path

    def child(self, name):
        return MemoryReference(self.database, self.path + (name,))

    def transaction(self, update):
        self.database.transactions += 1
        current = self.database.data
        for name in self.path:
            current = current[name]
        candidate = update(copy.deepcopy(current))
        self.database.callbacks += 1
        if self.database.retry_hook is not None:
            hook, self.database.retry_hook = self.database.retry_hook, None


            hook(current)
            candidate = update(copy.deepcopy(current))
            self.database.callbacks += 1
        if self.database.fail_commit:
            raise OSError('synthetic commit failure; no data committed')
        parent = self.database.data
        for name in self.path[:-1]:
            parent = parent[name]
        parent[self.path[-1]] = copy.deepcopy(candidate)
        return copy.deepcopy(candidate)


class MemoryDatabase:
    def __init__(self, user):
        self.data = {'users': {USER: copy.deepcopy(user)}}
        self.retry_hook = None
        self.fail_commit = False
        self.transactions = self.callbacks = 0

    def child(self, name):
        return MemoryReference(self, (name,))

    @property
    def user(self):
        return self.data['users'][USER]


class FreshnessContracts(unittest.TestCase):
    def setUp(self):
        for target in ('socket.socket.connect', 'socket.create_connection'):
            guard = patch(target, side_effect=AssertionError('network forbidden in this contract'))
            guard.start()
            self.addCleanup(guard.stop)

    def reconcile(self, database, now, config=None):
        return repository.reconcile_user(database, USER, now, CONFIG if config is None else config)

    def monitor(self, database):
        return database.user['quick_start_monitoring'][SESSION]

    def alerts(self, database):
        return database.user.get('rescue_alerts', {})

    def only_alert(self, database):
        alerts = self.alerts(database)
        self.assertEqual(len(alerts), 1)
        return next(iter(alerts.values()))

    def add_point(self, database, key, milliseconds, **coordinates):
        database.user['QuickStartSessions'][SESSION]['points'][key] = point(milliseconds, **coordinates)

    def mobile_times(self, arguments):
        result = subprocess.run(
            [shutil.which('node') or 'node', str(Path(__file__).with_name('mobile_export_probe.cjs'))],
            input=json.dumps({'module': 'services/quickStartSample.ts',
                              'export': 'getQuickStartSampleTime',
                              'arguments': arguments, 'captureErrors': True}),
            text=True, capture_output=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def make_timed_out(self, now=BASE + TEST_TIMEOUT_MS):
        database = MemoryDatabase(user_record())
        self.reconcile(database, now)
        self.assertEqual(self.monitor(database)['monitoring_status'], 'TIMED_OUT')
        self.only_alert(database)
        return database

    def resolve_and_convert(self, database, now):
        alert = self.only_alert(database)


        contacted = manager.record_user_contact(alert, manager.SAFETY_UNCONFIRMED, True)
        contacted = manager.record_emergency_contact(contacted, manager.SAFETY_UNCONFIRMED, True)
        updated, event = manager.convert_alert_to_rescue_event(contacted, now, True,
                                                             database.user.get('rescue_events'))
        database.user['rescue_alerts'][updated['alert_id']] = updated
        database.user.setdefault('rescue_events', {})[event['event_id']] = event
        return event

    def test_g01_g02_real_mobile_gate_then_stationary_or_moving_samples_stay_fresh(self):
        samples = [BASE, BASE + 700, BASE + 1400, BASE + 2100]
        values = self.mobile_times([
            [{'timestamp': sample}, sample - 20, sample + 10, samples[i - 1] if i else None]
            for i, sample in enumerate(samples)
        ])
        self.assertTrue(all(value['accepted'] for value in values), values)
        for moving in (False, True):
            with self.subTest(moving=moving):
                database = MemoryDatabase(user_record(points={}))
                for index, value in enumerate(values):
                    actual = value['value']['milliseconds']
                    self.add_point(database, str(index), actual,
                                   latitude=22.3 + index / 100 if moving else 22.3)
                    _, observed = self.reconcile(database, actual + 10)
                    self.assertEqual(self.monitor(database)['latest_sample_at_ms'], actual)
                    self.assertEqual(self.monitor(database)['monitoring_status'], 'FRESH')
                    self.assertEqual(observed['alerts'], {})
                    self.assertEqual(observed['events'], {})

    def test_g04_g05_g07_g08_real_mobile_gate_rejects_cached_duplicate_and_invalid_samples(self):
        arguments = [
            [{'timestamp': BASE - 1}, BASE, BASE + 20, None],
            [{'timestamp': BASE}, BASE - 10, BASE + 20, BASE],
            [{'timestamp': BASE - 1}, BASE - 10, BASE + 20, BASE],
            [{'timestamp': BASE + 21}, BASE, BASE + 20, None],
            [{}, BASE, BASE + 20, None],
            [{'timestamp': str(BASE)}, BASE, BASE + 20, None],
            [{'timestamp': True}, BASE, BASE + 20, None],
            [{'timestamp': BASE}, BASE + 20, BASE, None],
        ]
        values = self.mobile_times(arguments)
        self.assertEqual([value['accepted'] for value in values], [False] * len(arguments))
        self.assertTrue(all(value.get('error') for value in values))

    def test_g03_threshold_is_inclusive_and_uses_sample_plus_timeout(self):
        for offset, expected in ((999, 'FRESH'), (1000, 'TIMED_OUT'), (1001, 'TIMED_OUT')):
            with self.subTest(offset=offset):
                database = MemoryDatabase(user_record())
                _, observed = self.reconcile(database, BASE + offset)
                self.assertEqual(self.monitor(database)['monitoring_status'], expected)
                self.assertEqual(observed['events'], {})
                if offset < TEST_TIMEOUT_MS:
                    self.assertEqual(self.alerts(database), {})
                else:
                    alert = self.only_alert(database)
                    self.assertEqual(alert['trigger_type'], TRIGGER)
                    self.assertEqual(alert['sample_at_ms'], BASE)
                    self.assertEqual(alert['abnormal_since_ms'], BASE + TEST_TIMEOUT_MS)
                    self.assertEqual(alert['created_at_ms'], BASE + offset)

    def test_config_does_not_fall_back_to_old_inactivity_parameters(self):
        result = manager.scan_user_records(USER, user_record(), BASE + 100_000,
                                           movement_threshold_m=1, inactivity_threshold_ms=1)
        self.assertEqual(result['alerts'], {})
        self.assertEqual(result['events'], {})
        self.assertTrue(result['issues'])
        for timeout in (None, 0, -1, True, float('inf'), 'not-a-time'):
            with self.subTest(timeout=timeout):
                database = MemoryDatabase(user_record())
                _, observed = self.reconcile(database, BASE + 100_000,
                    {'quick_start_timeout_ms': timeout, 'd_move_meters': 1, 'inactive_threshold_ms': 1})
                self.assertEqual(observed['alerts'], {})
                self.assertTrue(observed['issues'])

    def test_g04_g07_g12_duplicate_and_reordered_points_do_not_regress_or_refresh_a(self):
        database = self.make_timed_out()
        before = copy.deepcopy(self.monitor(database))
        self.add_point(database, 'different_key_same_sample', BASE)
        self.add_point(database, 'late_older_sample', BASE - 50)
        for now in (BASE + 1100, BASE + 1300):
            _, observed = self.reconcile(database, now)
            state = self.monitor(database)
            self.assertEqual(state['latest_sample_at_ms'], BASE)
            self.assertEqual(state['monitoring_status'], 'TIMED_OUT')
            self.assertEqual(state['episode_id'], before['episode_id'])
            self.assertEqual(state['episode_first_onset_ms'], before['episode_first_onset_ms'])
            self.assertEqual(observed['alerts'], {})
            self.assertEqual(observed['events'], {})
        self.only_alert(database)

    def test_g06_expired_backlog_advances_a_but_does_not_restart_timeout_episode(self):
        database = self.make_timed_out()
        initial = copy.deepcopy(self.monitor(database))
        alert_id = self.only_alert(database)['alert_id']
        self.add_point(database, 'backlog', BASE + 200)
        self.reconcile(database, BASE + 2000)
        state, alert = self.monitor(database), self.only_alert(database)
        self.assertEqual(state['latest_sample_at_ms'], BASE + 200)
        self.assertEqual(state['monitoring_status'], 'TIMED_OUT')
        self.assertEqual(state['episode_id'], initial['episode_id'])
        self.assertEqual(state['episode_first_onset_ms'], initial['episode_first_onset_ms'])
        self.assertEqual(state['episode_count'], initial['episode_count'])
        self.assertEqual(alert['alert_id'], alert_id)
        self.assertEqual(alert['abnormal_since_ms'], BASE + 200 + TEST_TIMEOUT_MS)

    def test_g08_future_sample_cannot_age_into_freshness_after_restart_or_key_change(self):
        future = BASE + 10_000
        database = MemoryDatabase(user_record(points={'good': point(BASE), 'future': point(future)}))
        self.reconcile(database, BASE + TEST_TIMEOUT_MS)
        self.assertEqual(self.monitor(database)['latest_sample_at_ms'], BASE)
        self.assertTrue(self.monitor(database)['rejected_future_samples'])

        restarted = MemoryDatabase(json.loads(json.dumps(database.user)))
        restarted.user['QuickStartSessions'][SESSION]['points'] = {'new-key': point(future)}
        self.reconcile(restarted, future + 1)
        self.assertEqual(self.monitor(restarted)['latest_sample_at_ms'], BASE)
        self.assertEqual(self.monitor(restarted)['monitoring_status'], 'TIMED_OUT')
        self.assertEqual(len(self.alerts(restarted)), 1)


        restarted.user['QuickStartSessions'][SESSION]['points'] = {'new-key': point(future - 1)}
        self.reconcile(restarted, future + 1)
        self.assertEqual(self.monitor(restarted)['latest_sample_at_ms'], future - 1)
        self.assertEqual(self.monitor(restarted)['monitoring_status'], 'FRESH')

    def test_g08_bad_point_does_not_mask_timeout_and_has_a_diagnostic(self):
        invalid = [
            {'latitude': 22.3, 'longitude': 114.2},
            {'latitude': 22.3, 'longitude': 114.2, 'timestamp': 'wrong'},
            point(BASE + 100_000),
            point(BASE + 100, latitude=91),
        ]
        for bad in invalid:
            with self.subTest(bad=bad):
                database = MemoryDatabase(user_record(points={'good': point(BASE), 'bad': bad}))
                _, observed = self.reconcile(database, BASE + TEST_TIMEOUT_MS)
                self.assertEqual(self.monitor(database)['latest_sample_at_ms'], BASE)
                self.assertEqual(self.monitor(database)['monitoring_status'], 'TIMED_OUT')
                self.only_alert(database)
                self.assertTrue(observed['issues'] or self.monitor(database).get('ignored_gps_point_count', 0) > 0)

    def test_g09_g10_no_valid_current_session_point_is_not_ready(self):
        for points in ({}, {'old-session': point(BASE - 101)},
                       {'future': point(BASE + 100_000)}, {'missing': {'latitude': 22.3, 'longitude': 114.2}}):
            with self.subTest(points=points):
                database = MemoryDatabase(user_record(points=points))
                self.reconcile(database, BASE + 10_000)
                self.assertEqual(self.monitor(database)['monitoring_status'], 'NOT_READY')
                self.assertIsNone(self.monitor(database).get('latest_sample_at_ms'))
                self.assertEqual(self.alerts(database), {})
                self.assertEqual(database.user.get('rescue_events', {}), {})

    def test_g10_different_session_does_not_inherit_previous_a(self):
        database = self.make_timed_out()
        database.user['QuickStartSessions'][SESSION]['status'] = 'STOPPED'
        database.user['QuickStartSessions']['NEW_SESSION'] = {
            'status': 'ACTIVE', 'startTime': iso(BASE + 2000), 'points': {}}
        _, observed = self.reconcile(database, BASE + 2000)
        newest = database.user['quick_start_monitoring']['NEW_SESSION']
        self.assertEqual(newest['monitoring_status'], 'NOT_READY')
        self.assertIsNone(newest.get('latest_sample_at_ms'))
        self.assertEqual(self.monitor(database)['monitoring_status'], 'STOPPED')
        self.assertEqual(observed['alerts'], {})

    def test_g11_stopped_session_never_creates_new_alert(self):
        database = MemoryDatabase(user_record(status='STOPPED'))
        self.reconcile(database, BASE + 10_000)
        self.assertEqual(self.alerts(database), {})


        database = MemoryDatabase(user_record())
        self.reconcile(database, BASE + 10)
        database.user['QuickStartSessions'][SESSION]['status'] = 'STOPPED'
        self.reconcile(database, BASE + 10_000)
        self.assertEqual(self.alerts(database), {})
        self.assertEqual(self.monitor(database)['monitoring_status'], 'STOPPED')

    def test_g11_g12_retry_rechecks_concurrent_stop_before_commit(self):
        database = MemoryDatabase(user_record())
        self.reconcile(database, BASE + 10)
        before_callbacks = database.callbacks
        database.retry_hook = lambda user: user['QuickStartSessions'][SESSION].update(status='STOPPED')
        committed, observed = self.reconcile(database, BASE + TEST_TIMEOUT_MS)
        self.assertEqual(database.callbacks - before_callbacks, 2)
        self.assertEqual(committed['QuickStartSessions'][SESSION]['status'], 'STOPPED')
        self.assertEqual(observed['alerts'], {})
        self.assertEqual(self.alerts(database), {})
        self.assertEqual(self.monitor(database)['monitoring_status'], 'STOPPED')

    def test_g12_retry_preserves_concurrent_human_resolution(self):
        database = self.make_timed_out()
        alert_id = self.only_alert(database)['alert_id']
        self.add_point(database, 'fresh', BASE + 1200)
        def concurrent_resolution(user):
            user['rescue_alerts'][alert_id] = manager.record_user_contact(
                user['rescue_alerts'][alert_id], manager.SAFE_CONFIRMED, False)
            user['rescue_alerts'][alert_id]['operator_note'] = 'synthetic manual decision'
        database.retry_hook = concurrent_resolution
        _, observed = self.reconcile(database, BASE + 1201)
        actual = self.only_alert(database)
        self.assertEqual(actual['status'], manager.CLOSED_SAFE)
        self.assertEqual(actual['stage'], manager.RESOLUTION)
        self.assertEqual(actual['user_contact_result'], manager.SAFE_CONFIRMED)
        self.assertEqual(actual['operator_note'], 'synthetic manual decision')
        self.assertEqual(observed['alerts'], {})
        self.assertEqual(observed['events'], {})

    def test_g12_failed_cloud_commit_has_no_local_persistence_or_success_result(self):
        database = MemoryDatabase(user_record())
        before = copy.deepcopy(database.user)
        database.fail_commit = True
        with self.assertRaises(OSError):
            self.reconcile(database, BASE + TEST_TIMEOUT_MS)
        self.assertEqual(database.user, before)
        database.fail_commit = False
        self.reconcile(database, BASE + TEST_TIMEOUT_MS)
        self.only_alert(database)
        _, observed = self.reconcile(database, BASE + TEST_TIMEOUT_MS + 1)
        self.assertEqual(observed['alerts'], {})

    def test_g13_fresh_recovery_preserves_pending_human_state_and_retimeout_reuses_alert(self):
        database = self.make_timed_out()
        original = self.only_alert(database)
        alert_id = original['alert_id']
        contacted = manager.record_user_contact(original, manager.SAFETY_UNCONFIRMED, True)
        contacted['operator_note'] = 'synthetic pending contact'
        database.user['rescue_alerts'][alert_id] = contacted
        self.add_point(database, 'recovered', BASE + 1500)
        self.reconcile(database, BASE + 1501)
        recovered = self.only_alert(database)
        for field in ('status', 'stage', 'user_contact_result', 'emergency_contact_result', 'operator_note'):
            self.assertEqual(recovered[field], contacted[field])
        self.assertEqual(recovered['location_status'], 'RECOVERED')
        self.assertEqual(recovered['abnormal_since_ms'], BASE + TEST_TIMEOUT_MS)
        self.assertEqual(self.monitor(database)['monitoring_status'], 'FRESH')
        _, observed = self.reconcile(database, BASE + 2500)
        later = self.only_alert(database)
        self.assertEqual(later['alert_id'], alert_id)
        self.assertEqual(later['location_status'], 'TIMED_OUT')
        self.assertEqual(later['abnormal_since_ms'], BASE + 2500)
        self.assertEqual(later['stage'], manager.CONTACT_EMERGENCY)
        self.assertEqual(observed['alerts'], {})
        self.assertEqual(observed['events'], {})

    def test_g13_recovery_does_not_prevent_explicit_operator_conversion(self):
        database = self.make_timed_out()
        self.add_point(database, 'recovered', BASE + 1500)
        self.reconcile(database, BASE + 1501)
        event = self.resolve_and_convert(database, BASE + 1502)
        self.assertEqual(event['abnormal_since_ms'], BASE + TEST_TIMEOUT_MS)
        self.assertEqual(event['created_at_ms'], BASE + 1502)

    def test_g13_closed_alert_requires_actual_recovery_before_a_new_alert(self):
        database = self.make_timed_out()
        alert = self.only_alert(database)
        closed = manager.record_user_contact(alert, manager.SAFE_CONFIRMED, False)
        database.user['rescue_alerts'][alert['alert_id']] = closed
        self.add_point(database, 'expired-backlog', BASE + 100)
        self.reconcile(database, BASE + 2000)
        self.assertEqual(database.user['rescue_alerts'], {alert['alert_id']: closed})
        self.add_point(database, 'genuine-recovery', BASE + 2100)
        self.reconcile(database, BASE + 2101)
        _, repeated = self.reconcile(database, BASE + 3100)
        self.assertEqual(len(repeated['alerts']), 1)
        self.assertEqual(len(self.alerts(database)), 2)
        self.assertEqual(self.alerts(database)[alert['alert_id']], closed)
        later = next(iter(repeated['alerts'].values()))
        self.assertNotEqual(later['alert_id'], alert['alert_id'])
        self.assertEqual(later['abnormal_since_ms'], BASE + 3100)
        _, rescanned = self.reconcile(database, BASE + 3101)
        self.assertEqual(rescanned['alerts'], {})

    def test_g08_future_only_record_does_not_start_tracking_when_clock_catches_up(self):
        future = BASE + 2000
        database = MemoryDatabase(user_record(points={'future': point(future)}))
        self.reconcile(database, BASE)
        self.assertEqual(self.monitor(database)['monitoring_status'], 'NOT_READY')
        restarted = MemoryDatabase(json.loads(json.dumps(database.user)))
        self.reconcile(restarted, future + 1)
        self.assertEqual(self.monitor(restarted)['monitoring_status'], 'NOT_READY')
        self.assertIsNone(self.monitor(restarted).get('latest_sample_at_ms'))
        self.assertEqual(self.alerts(restarted), {})

    def test_threshold_change_cannot_recover_an_episode_without_a_new_fresh_sample(self):
        database = self.make_timed_out()
        before = copy.deepcopy(self.monitor(database))
        changed = {'quick_start_timeout_ms': 10 * TEST_TIMEOUT_MS}
        self.reconcile(database, BASE + 1100, changed)
        current = self.monitor(database)
        self.assertEqual(current['configured_timeout_ms'], 10 * TEST_TIMEOUT_MS)
        self.assertEqual(current['effective_timeout_ms'], TEST_TIMEOUT_MS)
        self.assertTrue(current['configuration_pending'])
        self.assertEqual(current['monitoring_status'], 'TIMED_OUT')
        self.assertEqual(current['episode_id'], before['episode_id'])
        restarted = MemoryDatabase(json.loads(json.dumps(database.user)))
        self.add_point(restarted, 'fresh', BASE + 1200)
        self.reconcile(restarted, BASE + 1201, changed)
        self.assertEqual(self.monitor(restarted)['monitoring_status'], 'FRESH')
        self.assertEqual(self.monitor(restarted)['effective_timeout_ms'], 10 * TEST_TIMEOUT_MS)
        self.assertGreater(self.monitor(restarted)['policy_epoch'], before['policy_epoch'])
        self.assertEqual(self.only_alert(restarted)['status'], manager.PENDING)

    def test_scan_is_pure_and_only_repository_commit_can_publish_detection(self):
        user = user_record()
        original = copy.deepcopy(user)
        result = manager.scan_user_records(USER, user, BASE + TEST_TIMEOUT_MS,
                                           quick_start_timeout_ms=TEST_TIMEOUT_MS)
        self.assertEqual(user, original)
        self.assertEqual(len(result['alerts']), 1)
        self.assertEqual(result['events'], {})
        self.assertEqual(result['monitoring'][SESSION]['monitoring_status'], 'TIMED_OUT')

    def test_g14_recovery_does_not_cancel_or_mutate_converted_or_dispatched_event(self):
        for event_status in (manager.PENDING, manager.DISPATCHED):
            with self.subTest(event_status=event_status):
                database = self.make_timed_out()
                event = self.resolve_and_convert(database, BASE + 1100)
                event['status'] = event_status
                before_events = copy.deepcopy(database.user['rescue_events'])
                self.add_point(database, 'recovered', BASE + 1500)
                _, observed = self.reconcile(database, BASE + 1501)
                self.assertEqual(database.user['rescue_events'], before_events)
                self.assertEqual(self.only_alert(database)['status'], manager.CONVERTED)
                self.assertEqual(self.monitor(database)['monitoring_status'], 'FRESH')
                self.assertEqual(observed['events'], {})
                self.reconcile(database, BASE + 2500)
                self.assertEqual(database.user['rescue_events'], before_events)
                self.assertEqual(len(self.alerts(database)), 1)

    def test_g15_sample_alert_operator_event_queue_chain_uses_distinct_time_origins(self):
        database = self.make_timed_out(now=BASE + 1200)
        alert = self.only_alert(database)
        with self.assertRaises(manager.RescueAlertTransitionError):
            manager.convert_alert_to_rescue_event(alert, BASE + 1300, True)
        self.assertEqual(database.user.get('rescue_events', {}), {})
        event = self.resolve_and_convert(database, BASE + 5000)
        metrics = scheduler.schedule_metrics(event, BASE + 5000, TEST_WAIT_MS)
        self.assertEqual(metrics['queue_waiting_time_ms'], 0)
        self.assertEqual(metrics['abnormal_duration_ms'], 4000)
        self.assertEqual(metrics['effective_priority'], scheduler.NORMAL_PRIORITY)
        self.assertEqual(scheduler.effective_priority(event, BASE + 5599, TEST_WAIT_MS), scheduler.NORMAL_PRIORITY)
        self.assertEqual(scheduler.effective_priority(event, BASE + 5600, TEST_WAIT_MS), scheduler.HIGH_PRIORITY)
        sos = {'event_id': 'SYNTHETIC_SOS', 'user_id': USER, 'status': manager.PENDING,
               'trigger_type': manager.SOS, 'created_at_ms': BASE + 5050, 'abnormal_since_ms': None}
        booking = {'event_id': 'SYNTHETIC_BOOKING', 'user_id': USER, 'status': manager.PENDING,
                   'trigger_type': manager.EVENT_BOOKING_TIMEOUT, 'created_at_ms': BASE + 5060,
                   'abnormal_since_ms': BASE + 2000}
        records = [booking, event, sos]
        original = copy.deepcopy(records)
        ordered = scheduler.order_pending_events(records, BASE + 5100, TEST_WAIT_MS)
        self.assertEqual([item['event_id'] for item in ordered], [event['event_id'], booking['event_id'], sos['event_id']])
        self.assertEqual(records, original)
        ordered = scheduler.order_pending_events(list(reversed(records)), BASE + 5600, TEST_WAIT_MS)
        self.assertEqual(ordered[0]['event_id'], event['event_id'])

    def test_g16_legacy_pending_alert_with_missing_detection_fields_is_preserved(self):
        database = MemoryDatabase(user_record())
        legacy = {'alert_id': 'quick_inactivity__SYNTHETIC_SESSION__1800000000000',
                  'user_id': USER, 'trigger_type': manager.QUICK_START_INACTIVITY,
                  'primary_record_type': manager.QUICK_START_SESSIONS,
                  'primary_record_id': SESSION, 'status': manager.PENDING,
                  'stage': manager.CONTACT_USER}
        database.user['rescue_alerts'] = {legacy['alert_id']: copy.deepcopy(legacy)}
        _, observed = self.reconcile(database, BASE + 10_000)
        self.assertEqual(database.user['rescue_alerts'], {legacy['alert_id']: legacy})
        self.assertEqual(observed['alerts'], {})
        self.assertEqual(observed['events'], {})

    def test_g16_legacy_event_is_schedulable_and_never_duplicated(self):
        database = MemoryDatabase(user_record())
        legacy = {'event_id': 'SYNTHETIC_LEGACY_EVENT', 'user_id': USER,
                  'trigger_type': manager.QUICK_START_INACTIVITY,
                  'primary_record_type': manager.QUICK_START_SESSIONS,
                  'primary_record_id': SESSION, 'status': manager.PENDING,
                  'created_at_ms': BASE + 500, 'abnormal_since_ms': BASE}
        database.user['rescue_events'] = {legacy['event_id']: copy.deepcopy(legacy)}
        _, observed = self.reconcile(database, BASE + 10_000)
        self.assertEqual(database.user['rescue_events'], {legacy['event_id']: legacy})
        self.assertEqual(observed['alerts'], {})
        self.assertEqual(observed['events'], {})
        self.assertEqual(scheduler.order_pending_events([legacy], BASE + 10_000, TEST_WAIT_MS), [legacy])

    def test_g16_malformed_legacy_identity_reports_issue_without_crash_or_duplicate(self):
        database = MemoryDatabase(user_record())
        legacy = {'trigger_type': manager.QUICK_START_INACTIVITY,
                  'primary_record_type': manager.QUICK_START_SESSIONS,
                  'primary_record_id': SESSION, 'status': manager.PENDING}
        database.user['rescue_alerts'] = {'SYNTHETIC_INCOMPLETE_HISTORY': legacy}
        before = copy.deepcopy(database.user['rescue_alerts'])
        _, observed = self.reconcile(database, BASE + 10_000)
        self.assertEqual(database.user['rescue_alerts'], before)
        self.assertEqual(observed['alerts'], {})
        self.assertEqual(observed['events'], {})
        self.assertTrue(observed['issues'])

    def test_g18_booking_and_sos_survive_invalid_quick_start_data(self):
        user = user_record(points={'bad': {'timestamp': 'invalid'}})
        user['booked_events'] = {'SYNTHETIC_BOOKING': {'status': 'ACTIVE', 'expectedEndAtMs': BASE}}
        user['rescue_requests'] = {'SYNTHETIC_SOS': {'status': manager.PENDING}}
        database = MemoryDatabase(user)
        _, observed = self.reconcile(database, BASE + TEST_TIMEOUT_MS)
        self.assertEqual([item['trigger_type'] for item in observed['alerts'].values()], [manager.EVENT_BOOKING_TIMEOUT, manager.SOS])
        self.assertEqual([item['trigger_type'] for item in observed['events'].values()], [])
        _, repeated = self.reconcile(database, BASE + TEST_TIMEOUT_MS + 1)
        self.assertEqual(repeated['alerts'], {})
        self.assertEqual(repeated['events'], {})

    def test_preflight_demands_new_explicit_timeout_and_separate_queue_threshold(self):
        options = {'projectId': 'synthetic-test', 'databaseURL': 'https://synthetic-test-default-rtdb.firebaseio.com'}
        base = {'GS_FIREBASE_DATABASE_URL': options['databaseURL'], 'GS_T_WAIT_SECONDS': '0.6'}
        legacy = {**base, 'GS_D_MOVE_METERS': '1', 'GS_T_INACTIVE_SECONDS': '1'}
        self.assertFalse(check_deployment(options, legacy)['configuration_ready'])
        fresh = {**base, 'GS_T_LOCATION_UPDATE_SECONDS': '1'}
        report = check_deployment(options, fresh)
        self.assertTrue(report['configuration_ready'], report)
        self.assertFalse(report['installed_build_verified'])
        fresh.pop('GS_T_WAIT_SECONDS')
        self.assertFalse(check_deployment(options, fresh)['configuration_ready'])


if __name__ == '__main__':
    unittest.main()
