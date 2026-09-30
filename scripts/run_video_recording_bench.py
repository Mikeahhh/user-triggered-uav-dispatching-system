import argparse
import ast
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import traceback
import types
from datetime import datetime, timezone

from local_verification import ROOT, isolated_environment, new_result_directory


LAUNCHER = ROOT / 'code/search_uav/drone_system/launcher'
sys.path.insert(0, str(LAUNCHER))


def file_capture_guard(cv2, allowed):
    actual_capture = cv2.VideoCapture
    allowed = {str(Path(path).resolve()) for path in allowed}

    def open_file(source, *_args, **_kwargs):
        if not isinstance(source, str) or source not in allowed or not Path(source).is_file():
            raise ValueError('video bench forbids devices, URLs and unlisted files')
        return actual_capture(source, cv2.CAP_FFMPEG)

    cv2.VideoCapture = open_file
    blocked = []
    for source in (0, 6, '0', '/dev/video0', 'http://127.0.0.1/camera'):
        try:
            cv2.VideoCapture(source)
        except ValueError:
            blocked.append(str(source))
        else:
            raise AssertionError('capture guard accepted a forbidden source')
    return blocked


def recorder_worker(source, destination):
    import cv2
    import cam_recorder
    if os.environ.get('MASS26_LOCAL_VERIFICATION') != '1':
        raise RuntimeError('isolated test environment is required')
    source = str(Path(source).resolve(strict=True))
    blocked = file_capture_guard(cv2, [source])
    print('BENCH_CAPTURE_GUARD ' + json.dumps({'file_only': source, 'blocked': blocked}), flush=True)
    cam_recorder.build_arg_parser = lambda: types.SimpleNamespace(
        parse_args=lambda _argv: types.SimpleNamespace(
            device_index=source, output_path=str(destination), max_read_failures=1))
    return cam_recorder.main([])


def console_methods():
    from recording_state import RecordingStateError, recording_result_error
    source = LAUNCHER / 'drone_console.py'
    tree = ast.parse(source.read_text())
    console = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'DroneConsole')
    names = {'_rec_stdout_reader', '_rec_send', '_journal_transition', '_stop_recording'}
    selected = [node for node in console.body if isinstance(node, ast.FunctionDef) and node.name in names]
    if len(selected) != len(names):
        raise AssertionError('production recording method not found')
    namespace = dict(os=os, json=json, time=time, RecordingStateError=RecordingStateError,
                     recording_result_error=recording_result_error, RECORDER_EVENT_PREFIX='RECORDER_EVENT ')
    exec(compile(ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[])), str(source), 'exec'), namespace)
    return {name: namespace[name] for name in names}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run_bench(output, setup_log=None):
    import cv2
    import numpy as np
    from recording_state import RecordingJournal, matches_recording_status
    source = output / 'synthetic-input.avi'
    destination = output / 'recorded-output.avi'
    journal_path = output / 'recording-manifest.json'
    if setup_log is not None:
        shutil.copyfile(setup_log, output / 'dependency-setup.log')
    writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*'MJPG'), 20., (320, 240))
    if not writer.isOpened():
        raise RuntimeError('synthetic input writer could not open')
    for index in range(120):
        frame = np.zeros((240, 320, 3), dtype=np.uint8)
        frame[:, :, 0] = index * 2
        frame[:, :, 1] = np.arange(320, dtype=np.uint16).astype(np.uint8)
        cv2.rectangle(frame, ((index * 3) % 250, 80), ((index * 3) % 250 + 60, 140), (0, 255, 255), -1)
        cv2.putText(frame, 'SYNTHETIC {:03d}'.format(index), (15, 40), cv2.FONT_HERSHEY_SIMPLEX, .6, (255, 255, 255), 2)
        writer.write(frame)
    writer.release()
    checks = {'synthetic_input_nonempty': source.is_file() and source.stat().st_size > 0}
    blocked = file_capture_guard(cv2, [source, destination])
    journal = RecordingJournal(journal_path)
    mission_id, execution_id = 'VIDEO_AUDIT/synthetic-flight', 'video-execution-20260930'
    journal.start('video-bench-20260930', mission_id, destination.name,
                  'SYNTHETIC_VIDEO_BENCH', execution_id=execution_id)
    ready = threading.Event()
    console_log = []

    def log(category, message, **_kwargs):
        console_log.append({'category': category, 'message': message})
        if message.startswith('recorder READY '):
            ready.set()

    proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--record-worker',
                             str(source), str(destination)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1,
                            env=isolated_environment(output, sys.executable))
    raw_output = proc.stdout
    with (output / 'recorder.log').open('w') as recorder_log:
        class LoggedOutput:
            def readline(self):
                line = raw_output.readline()
                recorder_log.write(line)
                recorder_log.flush()
                return line

            def close(self):
                raw_output.close()

        proc.stdout = LoggedOutput()
        console = types.SimpleNamespace(_rec_proc=proc, _rec_journal=journal, _rec_filename=str(destination),
                                        _recording=False, _rec_starting=True, _rec_stopping=False,
                                        _rec_paused=False, root=types.SimpleNamespace(after=lambda *_args: None),
                                        _update_rec_ui=lambda: None, _log=log)
        for name, method in console_methods().items():
            setattr(console, name, types.MethodType(method, console))
        reader = threading.Thread(target=console._rec_stdout_reader,
                                  args=(proc, journal, str(destination)), daemon=True)
        reader.start()
        try:
            if not ready.wait(15):
                raise RuntimeError('production recorder did not become ready')
            time.sleep(1.2)
            console._stop_recording(reason='SYNTHETIC_BENCH_NORMAL_STOP')
            reader.join(15)
            if reader.is_alive():
                raise RuntimeError('production recorder did not stop')
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)
            reader.join(5)
            if proc.stdin:
                proc.stdin.close()
    (output / 'console-events.json').write_text(json.dumps(console_log, indent=2) + '\n')
    state = journal.load()
    checks.update(
        recorder_exit_zero=proc.returncode == 0,
        production_journal_completed=state['status'] == 'COMPLETED',
        normal_stop_observed=any(event['status'] == 'STOP_REQUESTED' for event in state['events']),
        positive_reported_frames=type(state.get('frames_written')) is int and state['frames_written'] > 0,
        correct_mission_and_execution=state['mission_id'] == mission_id and state['execution_id'] == execution_id,
        correct_output_binding=state['output_file'] == destination.name,
        own_status_matches=matches_recording_status(mission_id, execution_id, {'mission_id': mission_id, 'execution_id': execution_id}),
        other_execution_rejected=not matches_recording_status(mission_id, execution_id, {'mission_id': mission_id, 'execution_id': 'other-flight'}),
        no_partial_output=not destination.with_name('recorded-output.part.avi').exists(),
        device_and_url_inputs_blocked=len(blocked) == 5)
    capture = cv2.VideoCapture(str(destination))
    if not capture.isOpened():
        raise RuntimeError('recorded video cannot be opened for decoding')
    decoded, frame_hashes, dimensions = 0, set(), set()
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        decoded += 1
        frame_hashes.add(hashlib.sha256(frame.tobytes()).hexdigest())
        dimensions.add(tuple(frame.shape))
        if decoded == 1:
            cv2.imwrite(str(output / 'decoded-first-frame.png'), frame)
    capture.release()
    checks.update(output_decodes_with_frames=decoded > 0,
                  decoded_count_matches_report=decoded == state.get('frames_written'),
                  frames_have_motion=len(frame_hashes) > 1,
                  frame_dimensions_correct=dimensions == {(240, 320, 3)})
    ffprobe = shutil.which('ffprobe') or '/opt/homebrew/bin/ffprobe'
    probe = None
    if Path(ffprobe).is_file():
        result = subprocess.run([ffprobe, '-v', 'error', '-count_frames', '-show_entries',
                                 'stream=codec_name,width,height,nb_read_frames', '-of', 'json', str(destination)],
                                text=True, capture_output=True, timeout=30, check=True)
        probe = json.loads(result.stdout)
        (output / 'ffprobe.json').write_text(result.stdout)
        checks['independent_ffprobe_frames_match'] = int(probe['streams'][0]['nb_read_frames']) == decoded
    artifacts = {path.name: {'sha256': sha256(path), 'bytes': path.stat().st_size}
                 for path in output.iterdir() if path.is_file()}
    sources = [LAUNCHER / 'cam_recorder.py', LAUNCHER / 'recording_state.py', LAUNCHER / 'drone_console.py',
               Path(__file__).resolve(), ROOT / 'scripts/requirements-video-verification.txt']
    return dict(all_passed=all(checks.values()), checks=checks, decoded_frames=decoded,
                unique_decoded_frames=len(frame_hashes), recorded_frames=state.get('frames_written'),
                mission_id=mission_id, execution_id=execution_id,
                opencv_version=cv2.__version__, opencv_package=importlib.metadata.version('opencv-python-headless'),
                numpy_version=np.__version__, python_version=sys.version,
                completed_at=datetime.now(timezone.utc).isoformat(), artifacts=artifacts,
                source_sha256={str(path.relative_to(ROOT)): sha256(path) for path in sources},
                scope='Synthetic local file input; actual production recorder main, OpenCV VideoWriter, console event handling and RecordingJournal. No camera/device, ROS, field imagery, real cloud or flight verification.',
                network_policy='Loopback-only Python subprocess guard; capture allowlist rejects devices and URLs.',
                input_injection='Only the recorder argument provider is replaced to supply the allowlisted synthetic file instead of an integer camera index.')


def main():
    if len(sys.argv) == 4 and sys.argv[1] == '--record-worker':
        return recorder_worker(sys.argv[2], sys.argv[3])
    parser = argparse.ArgumentParser(description='Encode and decode a synthetic video through the production recorder without a camera.')
    parser.add_argument('--output', required=True)
    parser.add_argument('--setup-log')
    parser.add_argument('--isolated', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not args.isolated:
        return subprocess.run([sys.executable, str(Path(__file__).resolve()), *sys.argv[1:], '--isolated'],
                              env=isolated_environment(args.output, sys.executable)).returncode
    output = new_result_directory(args.output)
    try:
        report = run_bench(output, args.setup_log)
    except Exception as exc:
        report = {'all_passed': False, 'error': str(exc), 'error_type': type(exc).__name__}
        (output / 'failure.log').write_text(traceback.format_exc())
    (output / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'all_passed': report['all_passed'], 'report': str(output / 'result.json'),
                      'decoded_frames': report.get('decoded_frames')}))
    return 0 if report['all_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
