from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

PROJECT_ID = re.compile(r'^[a-z][a-z0-9-]{4,28}[a-z0-9]$')
LEGACY_HOST = re.compile(r'^([a-z][a-z0-9-]*)\.firebaseio\.com$')
REGIONAL_HOST = re.compile(r'^([a-z][a-z0-9-]*)\.([a-z0-9-]+)\.firebasedatabase\.app$')
THRESHOLDS = ('GS_T_LOCATION_UPDATE_SECONDS', 'GS_T_WAIT_SECONDS')


def clean_database_origin(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError('database_target_missing')
    candidate = value.strip()

    if any(ord(ch) <= 32 or ord(ch) == 127 for ch in candidate):
        raise ValueError('database_target_invalid')
    try:
        parsed = urlsplit(candidate)
        if (parsed.scheme != 'https' or parsed.username is not None or
                parsed.password is not None or parsed.port not in (None, 443) or
                parsed.path not in ('', '/') or parsed.query or parsed.fragment):
            raise ValueError('database_target_invalid')
        host = (parsed.hostname or '').lower()
    except ValueError:
        raise ValueError('database_target_invalid') from None
    if not (LEGACY_HOST.fullmatch(host) or REGIONAL_HOST.fullmatch(host)):
        raise ValueError('database_target_invalid')
    return 'https://' + host


def mobile_database_origin(options: dict) -> tuple[str, bool]:
    project = options.get('projectId')
    if not isinstance(project, str) or not PROJECT_ID.fullmatch(project.strip()):
        raise ValueError('mobile_project_invalid_or_missing')
    project = project.strip()
    configured = options.get('databaseURL')
    explicit = isinstance(configured, str) and bool(configured.strip())
    origin = clean_database_origin(configured if explicit else
                                   f'https://{project}-default-rtdb.firebaseio.com')
    host = urlsplit(origin).hostname
    match = LEGACY_HOST.fullmatch(host) or REGIONAL_HOST.fullmatch(host)
    if match.group(1) not in {project, project + '-default-rtdb'}:
        raise ValueError('mobile_target_project_mismatch')
    return origin, explicit


def check_deployment(mobile_options: dict, gs_environment: dict) -> dict:
    report = {
        'scope': 'local_configuration_only_no_network',
        'targets_match': None,
        'mobile_target_source': 'unresolved',
        'ground_target_source': 'GS_FIREBASE_DATABASE_URL',
        'thresholds_configured': False,
        'configuration_ready': False,
        'installed_build_verified': False,
        'errors': [],
    }
    mobile = ground = None
    try:
        mobile, explicit = mobile_database_origin(mobile_options)
        report['mobile_target_source'] = 'explicit_databaseURL' if explicit else 'derived_project_default'
        if not explicit:
            report['errors'].append('mobile_databaseURL_not_explicit')
    except (ValueError, TypeError, AttributeError):
        report['errors'].append('mobile_database_configuration_invalid')
    try:
        ground = clean_database_origin(gs_environment.get('GS_FIREBASE_DATABASE_URL'))
    except ValueError as exc:
        report['errors'].append('ground_' + str(exc))
    if mobile is not None and ground is not None:
        report['targets_match'] = mobile == ground
        if mobile != ground:
            report['errors'].append('mobile_ground_database_mismatch')
    missing_or_invalid = []
    for name in THRESHOLDS:
        value = gs_environment.get(name)
        try:
            number = float(value) if isinstance(value, str) and value.strip() else float('nan')
            valid = math.isfinite(number) and number > 0
            valid = valid and math.isfinite(number * 1000) and round(number * 1000) >= 1
        except (TypeError, ValueError, OverflowError):
            valid = False
        if not valid:
            missing_or_invalid.append(name)
    report['thresholds_configured'] = not missing_or_invalid
    report['threshold_configuration_errors'] = missing_or_invalid
    if missing_or_invalid:
        report['errors'].append('configured_thresholds_required')
    report['configuration_ready'] = not report['errors']
    return report


def read_static_mobile_options(path: Path) -> dict:


    text = path.read_text(encoding='utf-8')
    result = {}
    for key in ('projectId', 'databaseURL'):
        matches = re.findall(r'(?m)^\s*' + key + r'\s*:\s*([\'"])([^\r\n]*?)\1\s*[,}]', text)
        if len(matches) > 1:
            raise ValueError('mobile_configuration_requires_explicit_options')
        if matches:
            result[key] = matches[0][1]
    if 'projectId' not in result:
        raise ValueError('mobile_configuration_requires_explicit_options')

    if re.search(r'(?m)^\s*databaseURL\s*:', text) and 'databaseURL' not in result:
        raise ValueError('mobile_configuration_requires_explicit_options')
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mobile-options', type=Path,
                        help='JSON object containing only projectId and databaseURL; no credentials')
    parser.add_argument('--mobile-source', type=Path, default=Path(__file__).parent /
                        'FYP_alin1_SmartUAVRescueSystem_Mobile_APP-main/services/db/firebaseConfig.ts')
    args = parser.parse_args()
    try:
        if args.mobile_options:
            data = json.loads(args.mobile_options.read_text(encoding='utf-8'))
            if not isinstance(data, dict):
                raise ValueError('invalid_options')
            options = {key: data.get(key) for key in ('projectId', 'databaseURL')}
        else:
            options = read_static_mobile_options(args.mobile_source)
        report = check_deployment(options, dict(os.environ))
        report['mobile_inspection'] = 'explicit_options_file' if args.mobile_options else 'source_literals_not_installed_app'
    except (OSError, ValueError, TypeError):
        report = {'configuration_ready': False, 'scope': 'local_configuration_only_no_network',
                  'errors': ['cannot_resolve_mobile_configuration_use_explicit_options_file']}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['configuration_ready'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
