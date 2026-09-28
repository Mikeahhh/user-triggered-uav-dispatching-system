import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deployment_preflight import check_deployment, clean_database_origin, mobile_database_origin, read_static_mobile_options

TARGET = 'https://audit-example-default-rtdb.firebaseio.com'


class DeploymentPreflightTests(unittest.TestCase):
    def environment(self, target=TARGET):

        return {'GS_FIREBASE_DATABASE_URL': target,
                'GS_T_LOCATION_UPDATE_SECONDS': '20', 'GS_T_WAIT_SECONDS': '60'}

    def options(self, target=TARGET):
        return {'projectId': 'audit-example', 'databaseURL': target}

    def test_same_explicit_target_and_valid_settings_pass_without_deployment_claim(self):
        result = check_deployment(self.options(TARGET + '/'), self.environment())
        self.assertTrue(result['configuration_ready'])
        self.assertTrue(result['targets_match'])
        self.assertFalse(result['installed_build_verified'])

    def test_different_target_fails_without_printing_target_or_credentials(self):
        result = check_deployment(self.options(), self.environment(
            'https://other-example-default-rtdb.firebaseio.com'))
        self.assertFalse(result['configuration_ready'])
        self.assertFalse(result['targets_match'])
        output = json.dumps(result)
        self.assertNotIn('firebaseio', output)
        self.assertNotIn('other-example', output)

    def test_same_derived_default_does_not_masquerade_as_explicit_configuration(self):
        result = check_deployment({'projectId': 'audit-example'}, self.environment())
        self.assertTrue(result['targets_match'])
        self.assertEqual(result['mobile_target_source'], 'derived_project_default')
        self.assertFalse(result['configuration_ready'])

    def test_invalid_mobile_urls_are_rejected_and_not_echoed(self):
        for url in ['http://audit-example-default-rtdb.firebaseio.com',
                    TARGET + '?auth=synthetic-secret', TARGET + '/users',
                    'https://other-example-default-rtdb.firebaseio.com',
                    'https://user:synthetic-secret@audit-example.firebaseio.com',
                    TARGET.replace('audit-', 'audit-\n')]:
            with self.subTest(case='invalid_origin'):
                result = check_deployment(self.options(url), self.environment())
                self.assertFalse(result['configuration_ready'])
                self.assertNotIn('synthetic-secret', json.dumps(result))

    def test_missing_and_bad_thresholds_do_not_enable_dispatch(self):
        for value in ['', '0', '-1', 'nan', 'inf', '0.00001']:
            environment = self.environment()
            environment['GS_T_WAIT_SECONDS'] = value
            result = check_deployment(self.options(), environment)
            self.assertFalse(result['configuration_ready'])
            self.assertIn('GS_T_WAIT_SECONDS', result['threshold_configuration_errors'])
        result = check_deployment(self.options(), {})
        self.assertFalse(result['configuration_ready'])
        self.assertIsNone(result['targets_match'])

    def test_static_reader_rejects_dynamic_target_instead_of_guessing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'firebaseConfig.ts'
            path.write_text("const c = {\n projectId: 'audit-example',\n databaseURL: injectedValue,\n};\n")
            with self.assertRaises(ValueError):
                read_static_mobile_options(path)
            path.write_text("const c = {\n projectId: 'audit-example',\n databaseURL: '" + TARGET + "',\n};\n")
            self.assertEqual(read_static_mobile_options(path), self.options())

    def test_real_mobile_resolver_and_preflight_agree_on_supported_origins(self):
        cases = [self.options(), self.options(TARGET + '/'),
                 self.options(TARGET + ':443/'),
                 self.options('https://audit-example-default-rtdb.asia-southeast1.firebasedatabase.app'),
                 {'projectId': 'audit-example'}]
        completed = subprocess.run(
            [shutil.which('node') or 'node', str(Path(__file__).with_name('mobile_export_probe.cjs'))],
            input=json.dumps({'module': 'services/db/firebaseRealtimeDatabase.ts',
                              'export': 'resolveRealtimeDatabaseUrl',
                              'arguments': [[case] for case in cases]}),
            text=True, capture_output=True, timeout=20, check=True,
        )
        self.assertEqual(json.loads(completed.stdout),
                         [mobile_database_origin(case)[0] for case in cases])

    def test_real_ground_config_and_preflight_agree_without_sdk_initialization(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] /
                             'ground_station'))
        from firebase_runtime_config import load_firebase_runtime_config
        for origin in [TARGET, TARGET + '/', TARGET + ':443/',
                       'https://audit-example-default-rtdb.asia-southeast1.firebasedatabase.app']:
            with self.subTest(origin_kind='synthetic'):
                config = load_firebase_runtime_config(self.environment(origin))
                self.assertTrue(config['ready'], config['errors'])
                self.assertEqual(config['database_url'], clean_database_origin(origin))
        self.assertFalse(load_firebase_runtime_config({})['ready'])


if __name__ == '__main__':
    unittest.main()
