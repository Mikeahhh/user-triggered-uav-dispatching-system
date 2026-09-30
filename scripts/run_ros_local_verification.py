import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import sys
from datetime import datetime, timezone

from local_verification import ROOT, new_result_directory


def hashes(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob('*')) if path.is_file() and '__pycache__' not in path.parts}


def main():
    parser = argparse.ArgumentParser(description='Build real ROS Noetic packages and run local synthetic ROS scenarios in an offline container.')
    parser.add_argument('--output', required=True)
    parser.add_argument('--flight-source', required=True, type=Path)
    parser.add_argument('--socket', default=str(Path.home() / '.orbstack/run/docker.sock'))
    parser.add_argument('--base-image', default='ros:noetic-ros-base-focal')
    args = parser.parse_args()
    output = new_result_directory(args.output)
    report = {'all_passed': False, 'started_at': datetime.now(timezone.utc).isoformat(), 'steps': []}
    docker = shutil.which('docker')
    endpoint = Path(args.socket).expanduser().resolve(strict=True)
    if not stat.S_ISSOCK(endpoint.stat().st_mode):
        raise ValueError('Docker endpoint must be a local Unix socket')
    command = [docker, '--host', 'unix://' + str(endpoint)]
    env = dict(os.environ)
    for name in ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH'):
        env.pop(name, None)

    def run(arguments, name, capture=False):
        with (output / (name + '.log')).open('w') as log:
            result = subprocess.run(command + arguments, text=True, env=env, stdout=subprocess.PIPE if capture else log,
                                    stderr=log, check=False)
            if capture:
                log.write(result.stdout)
        report['steps'].append({'name': name, 'return_code': result.returncode, 'arguments': arguments})
        (output / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
        result.check_returncode()
        return result.stdout if capture else None

    try:
        runtime = json.loads(run(['info', '--format', '{{json .}}'], 'docker-local-info', True))
        if runtime.get('OperatingSystem') != 'OrbStack' or runtime.get('Name') != 'orbstack':
            raise ValueError('This verification requires the inspected local OrbStack runtime')
        report['local_runtime'] = {key: runtime.get(key) for key in ('OperatingSystem', 'Name', 'Architecture', 'ServerVersion')}
        report['docker_endpoint'] = 'unix://' + str(endpoint)
        image = json.loads(run(['image', 'inspect', args.base_image], 'ros-base-image', True))[0]
        base = image['RepoDigests'][0]
        report['base_image_digest'] = base
        workspace = output / 'workspace/src'
        workspace.mkdir(parents=True)
        sources = {'rescue_bridge': ROOT / 'code/search_uav/catkin_ws/src/rescue_bridge',
                   'quadrotor_msgs': args.flight_source / 'src/utils/quadrotor_msgs',
                   'cmake_utils': args.flight_source / 'src/utils/cmake_utils'}
        provenance = {}
        for name, source in sources.items():
            source = source.resolve(strict=True)
            shutil.copytree(source, workspace / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.DS_Store'))
            provenance[name] = {'source': str(source), 'sha256': hashes(source)}
        (output / 'source-provenance.json').write_text(json.dumps(provenance, indent=2) + '\n')
        tag = 'metacom26-ros-local:' + output.name.lower()
        run(['build', '--build-arg', 'ROS_IMAGE=' + base, '-t', tag, str(ROOT / 'scripts/ros_local')], 'image-build')
        report['verification_image'] = json.loads(run(['image', 'inspect', tag], 'verification-image', True))[0]['Id']
        name = 'metacom26-ros-' + str(os.getpid())
        report['runtime_isolation'] = {'network': 'none', 'published_ports': [], 'devices': [],
                                       'capabilities': 'ALL dropped', 'privileged': False,
                                       'writable_mount': str(output), 'read_only_root': True,
                                       'uid': os.getuid(), 'gid': os.getgid()}
        run(['run', '--rm', '--name', name, '--network', 'none', '--cap-drop', 'ALL',
             '--security-opt', 'no-new-privileges', '--read-only', '--tmpfs', '/tmp:rw,nosuid,size=256m',
             '--user', str(os.getuid()) + ':' + str(os.getgid()), '--env', 'HOME=/tmp/local-ros-home',
             '--mount', 'type=bind,src=' + str(output) + ',dst=/results',
             '--mount', 'type=bind,src=' + str(ROOT / 'scripts/ros_local') + ',dst=/verification,readonly',
             tag, '/verification/build_and_run.sh'], 'ros-build-and-runtime')
        runtime_report = json.loads((output / 'runtime/result.json').read_text())
        report['runtime_report'] = 'runtime/result.json'
        report['all_passed'] = runtime_report['all_passed']
        report['scope'] = 'Real ROS Noetic/catkin, rescue_bridge C++ and Python nodes, generated messages and original quadrotor_msgs/cmake_utils. Synthetic GPS/odometry actuation only; no Fast-Drone planner, px4ctrl, Pixhawk, physics-qualified flight or physical device validation.'
    except Exception as exc:
        report.update(error_type=type(exc).__name__, error=str(exc))
    report['completed_at'] = datetime.now(timezone.utc).isoformat()
    report['verification_source_sha256'] = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                                          for path in [Path(__file__).resolve(), *sorted((ROOT / 'scripts/ros_local').glob('*'))] if path.is_file()}
    (output / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'all_passed': report['all_passed'], 'report': str(output / 'result.json')}))
    return 0 if report['all_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
