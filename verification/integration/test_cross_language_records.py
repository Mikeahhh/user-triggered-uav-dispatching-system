import hashlib
import json
from pathlib import Path
import random
import shutil
import subprocess
import sys
import unittest

ROOT = (Path(__file__).resolve().parents[2] / "code")
sys.path.insert(0, str(ROOT / 'search_uav/drone_system/receiver'))
from phone_sos_receiver import canonical_json, validate_payload


def sample(index, latitude, longitude, accuracy):
    return {
        'schema_version': 1, 'request_id': f'cross_{index}', 'mission_id': f'TEST_USER/cross_{index}',
        'user_id': 'TEST_USER', 'latitude': latitude, 'longitude': longitude, 'accuracy': accuracy,
        'captured_at': '2026-09-09T00:00:00.000Z', 'client_timestamp_ms': 1788912000000,
        'status': 'PENDING', 'device': '测试设备-α', 'test_mode': True,
        'gps_points': [{'latitude': latitude, 'longitude': longitude,
                        'captured_at': '2026-09-09T00:00:00.000Z'}],
    }


class CrossLanguageRecordHashTests(unittest.TestCase):
    def call_mobile(self, export, payloads):
        result = subprocess.run(
            [shutil.which('node') or 'node', str(Path(__file__).with_name('mobile_export_probe.cjs'))],
            input=json.dumps({'module': 'src/services/rescueRecordHash.ts', 'export': export,
                              'arguments': [[p] for p in payloads]}, ensure_ascii=False),
            text=True, capture_output=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def vectors(self):
        explicit = [
            (22, 114, 3), (0.0, -0.0, 0), (1e-7, -1e-7, 1e20),
            (1e-4, -1e-5, 1e16), (1e-6, 1e-8, 1e-9),
            (1.0000000000000002, 114.00000000000001, 1e15),
            (90.0, -180.0, None), (-90.0, 180.0, 1e100),
            (5e-324, -5e-324, 0.00009999999999999999),
        ]
        random_source = random.Random(20260909)
        for _ in range(90):
            explicit.append((random_source.uniform(-90, 90), random_source.uniform(-180, 180),
                             10 ** random_source.uniform(-20, 25)))
        return [sample(i, *values) for i, values in enumerate(explicit)]

    def test_canonical_bytes_match_real_python_normalizer_on_numeric_edges(self):
        payloads = self.vectors()
        actual = self.call_mobile('canonicalV1PayloadJson', payloads)
        for index, payload in enumerate(payloads):
            expected = canonical_json(validate_payload(payload)).decode('utf-8')
            self.assertEqual(actual[index], expected, f'canonical mismatch in synthetic vector {index}')

    def test_content_sha256_matches_real_python_for_every_vector(self):
        payloads = self.vectors()
        actual = self.call_mobile('hashV1Payload', payloads)
        expected = [hashlib.sha256(canonical_json(validate_payload(p))).hexdigest() for p in payloads]
        self.assertEqual(actual, expected)


if __name__ == '__main__':
    unittest.main()
