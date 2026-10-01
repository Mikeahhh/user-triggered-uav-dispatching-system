from __future__ import annotations

import importlib.util
import json
import re
import unittest
from pathlib import Path


CODE_ROOT = (Path(__file__).resolve().parents[2] / "code")
MOBILE = CODE_ROOT / "mobile_application"
GROUND = CODE_ROOT / "ground_station"
DRONE = CODE_ROOT / "search_uav"
RELEASE_ID = "MASS26-20260806"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load {}".format(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CrossComponentReleaseAlignmentTests(unittest.TestCase):
    def test_mobile_release_metadata_is_1_2_0_build_3(self):
        package = json.loads((MOBILE / "package.json").read_text(encoding="utf-8"))
        metadata = (MOBILE / "src" / "services" / "appMetadata.ts").read_text(encoding="utf-8")
        rescue_client = (MOBILE / "src" / "services" / "uavRescueClient.ts").read_text(
            encoding="utf-8"
        )
        gradle = (MOBILE / "android" / "app" / "build.gradle").read_text(encoding="utf-8")
        self.assertEqual(package["version"], "1.2.0")
        self.assertIn("MOBILE_APP_VERSION = '1.2.0'", metadata)
        self.assertIn("ANDROID_VERSION_CODE = 3", metadata)
        self.assertNotIn("IOS_BUILD_NUMBER", metadata)
        self.assertIn("SYSTEM_RELEASE_ID = 'MASS26-20260806'", metadata)
        self.assertIn("UAV_RESCUE_PROTOCOL = 'SOS schema v1'", metadata)
        self.assertIn("UAV_OUTBOX_STORAGE_VERSION = 3", metadata)
        self.assertIn("schema_version: 1;", rescue_client)
        self.assertIn("uav-rescue-outbox-v3", rescue_client)
        self.assertIn("OUTBOX_STORAGE_SCHEMA_VERSION = 3", rescue_client)
        self.assertRegex(gradle, r"\bversionCode\s+3\b")
        self.assertRegex(gradle, r'\bversionName\s+"1\.2\.0"')
        self.assertFalse((MOBILE / "ios").exists())
        self.assertNotIn("ios", package.get("scripts", {}))

    def test_ground_station_release_metadata_is_v7_0_4_0(self):
        metadata = load_module("mass26_ground_metadata", GROUND / "src/app_metadata.py")
        version_info = (GROUND / "packaging/windows_version_info_v7.txt").read_text(
            encoding="utf-8"
        )
        v5_spec = (GROUND / "packaging/ground_station_V5.spec").read_text(encoding="utf-8")
        v6_spec = (GROUND / "packaging/ground_station_V6.spec").read_text(encoding="utf-8")
        self.assertEqual(metadata.APP_VERSION, "0.4.0")
        self.assertEqual(metadata.WINDOWS_RELEASE_NAME, "ground_station_V7")
        self.assertEqual(metadata.SYSTEM_RELEASE_ID, RELEASE_ID)
        self.assertEqual(metadata.PROTOCOL_SCHEMA_VERSION, 1)
        self.assertIn("filevers=(0, 4, 0, 0)", version_info)
        self.assertIn("StringStruct('OriginalFilename', 'ground_station_V7.exe')", version_info)
        self.assertIn("StringStruct('Comments', 'MASS26-20260806; protocol schema v1')", version_info)
        for legacy_spec in (v5_spec, v6_spec):
            self.assertIn("raise SystemExit", legacy_spec)
            self.assertIn("ground_station_V7.spec", legacy_spec)

    def test_uav_release_metadata_is_1_2_0(self):
        protocol = load_module(
            "mass26_uav_protocol",
            DRONE / "catkin_ws" / "src" / "rescue_bridge" / "src" / "mission_protocol.py",
        )
        receiver_text = (
            DRONE / "drone_system" / "receiver" / "phone_sos_receiver.py"
        ).read_text(encoding="utf-8")
        package_xml = (
            DRONE / "catkin_ws" / "src" / "rescue_bridge" / "package.xml"
        ).read_text(encoding="utf-8")
        self.assertEqual(protocol.UAV_SOFTWARE_VERSION, "1.2.0")
        self.assertEqual(protocol.SYSTEM_RELEASE_ID, RELEASE_ID)
        self.assertIn("<version>1.2.0</version>", package_xml)
        self.assertIsNotNone(
            re.search(r'^SCHEMA_VERSION\s*=\s*1$', receiver_text, re.MULTILINE)
        )
        self.assertIsNotNone(
            re.search(r'^RECEIVER_VERSION\s*=\s*"1\.2\.0"', receiver_text, re.MULTILINE)
        )
        self.assertIsNotNone(
            re.search(r'^UAV_SOFTWARE_VERSION\s*=\s*"1\.2\.0"', receiver_text, re.MULTILINE)
        )
        self.assertIn('SYSTEM_RELEASE_ID = "MASS26-20260806"', receiver_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
