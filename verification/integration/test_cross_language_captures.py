import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

ROOT = (Path(__file__).resolve().parents[2] / "code")
RECEIVER = ROOT / 'search_uav/drone_system/receiver'
GS = ROOT / 'ground_station/src'
sys.path.insert(0, str(RECEIVER))
import capture_record_v2 as uav
import test_cross_language_records


def load_ground_schema():
    spec = importlib.util.spec_from_file_location('gs_capture_schema_crosscheck', GS / 'capture_record_v2.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def capture_from_source(source, index=0, trigger='EVENT_BOOKING_TIMEOUT'):
    sample_ms = 1788912060123
    return dict(schema_version=2, capture_id=f'capture_{index}', request_id=source['request_id'],
                user_id=source['user_id'], context_id=f'context_{index}',
                carrier_mission_id=f"{source['user_id']}/{trigger}_event",
                carrier_execution_id=f'execution_{index}', source_request=copy.deepcopy(source),
                latitude=source['latitude'], longitude=source['longitude'], accuracy=source['accuracy'],
                captured_at='2026-09-09T00:01:00.123Z', client_timestamp_ms=sample_ms,
                capture_started_at_ms=sample_ms - 123, device='新采集设备-α🚁', test_mode=True)


class CrossLanguageCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ground = load_ground_schema()

    def mobile(self, export, arguments, capture_errors=False):
        result = subprocess.run(
            [shutil.which('node') or 'node', str(Path(__file__).with_name('mobile_export_probe.cjs'))],
            input=json.dumps({'module': 'src/services/uavCaptureV2.ts', 'export': export,
                              'arguments': arguments, 'captureErrors': capture_errors}, ensure_ascii=True),
            text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def vectors(self):
        sources = test_cross_language_records.CrossLanguageRecordHashTests().vectors()
        triggers = ['EVENT_BOOKING_TIMEOUT', 'QUICK_START_INACTIVITY', 'QUICK_START_LOCATION_TIMEOUT', 'SOS']
        return [capture_from_source(source, i, triggers[i % len(triggers)]) for i, source in enumerate(sources)]

    def test_canonical_bytes_and_hashes_match_all_three_real_modules(self):
        payloads = self.vectors()
        encoded = self.mobile('canonicalV2PayloadJson', [[value] for value in payloads])
        hashed = self.mobile('hashV2Payload', [[value] for value in payloads])
        for i, value in enumerate(payloads):
            with self.subTest(vector=i):
                on_uav = uav.validate_payload(value)
                on_ground = self.ground.validate_payload(value)
                self.assertEqual(encoded[i], uav.canonical(on_uav).decode('utf-8'))
                self.assertEqual(encoded[i], self.ground.canonical(on_ground).decode('utf-8'))
                self.assertEqual(hashed[i], uav.digest(on_uav))
                self.assertEqual(hashed[i], self.ground.digest(on_ground))

    def test_invalid_sample_identity_and_precision_are_rejected_by_all_modules(self):
        valid = self.vectors()[0]
        variants = []
        for name, value in [
            ('user_id', 'OTHER_USER'), ('request_id', 'another_request'),
            ('carrier_mission_id', 'OTHER_USER/booking'), ('carrier_execution_id', '../bad'),
            ('context_id', ''), ('capture_id', 'trailing\n'), ('latitude', True),
            ('longitude', 181), ('accuracy', -1), ('test_mode', 1), ('device', '\ud800'),
            ('capture_started_at_ms', valid['client_timestamp_ms'] + 1),
            ('client_timestamp_ms', valid['client_timestamp_ms'] + 1),
            ('captured_at', '2026-09-09T00:01:00.1230001Z'),
            ('captured_at', '2026-09-09 00:01:00.123Z'),
            ('captured_at', '20260909T000100.123Z'),
        ]:
            variants.append((f'{name}:{value!r}', {**valid, name: value}))
        for field in ('source_request', 'accuracy', 'context_id'):
            changed = copy.deepcopy(valid)
            changed.pop(field)
            variants.append((f'missing {field}', changed))
        for name, value in [('mission_id', 'TEST_USER/changed'), ('user_id', 'OTHER'),
                            ('captured_at', '2026-02-30T00:00:00Z')]:
            changed = copy.deepcopy(valid)
            changed['source_request'][name] = value
            variants.append((f'source {name}', changed))
        changed = copy.deepcopy(valid)
        changed['source_request']['gps_points'][0]['extra'] = 'invalid'
        variants.append(('unknown source point field', changed))
        mobile = self.mobile('validateCaptureV2Payload', [[p] for _, p in variants], True)
        for index, (label, payload) in enumerate(variants):
            with self.subTest(case=label):
                self.assertFalse(mobile[index]['accepted'])
                with self.assertRaises(ValueError):
                    uav.validate_payload(payload)
                with self.assertRaises(ValueError):
                    self.ground.validate_payload(payload)

    def test_python_receipt_is_validated_against_actual_mobile_content(self):
        payloads = self.vectors()[:3]
        arguments = []
        for payload in payloads:
            receipt = {key: payload[key] for key in ('capture_id', 'request_id', 'context_id',
                                                    'carrier_mission_id', 'carrier_execution_id')}
            receipt.update(schema_version=2, status='STORED',
                           payload_sha256=uav.digest(uav.validate_payload(payload)),
                           uav_received_at='2026-09-09T00:02:00.000Z', duplicate=False,
                           receiver_version='synthetic-source-test', system_release_id='synthetic')
            arguments.append([receipt, payload])
        accepted = self.mobile('validateCaptureReceipt', arguments)
        self.assertEqual(len(accepted), 3)
        altered = []
        for receipt, payload in arguments:
            altered += [[{**receipt, key: 'different'}, payload] for key in
                        ('capture_id', 'request_id', 'context_id', 'carrier_mission_id',
                         'carrier_execution_id', 'payload_sha256')]
        rejected = self.mobile('validateCaptureReceipt', altered, True)
        self.assertTrue(all(not value['accepted'] for value in rejected))


if __name__ == '__main__':
    unittest.main()
