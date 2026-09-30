import copy
import json
import os
import tempfile
import threading
from pathlib import Path

from execution_protocol import normalize_execution_payload
from mission_transfer_protocol import (CHUNK_POINTS, MAX_SOURCE_WAYPOINTS, MAX_TASK_BYTES,
                                       MAX_MESSAGE_BYTES, HASH, canonical, digest, validate_header)


class MissionTransferStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()

    def _directory(self, eid):
        directory = self.root / eid
        directory.mkdir(exist_ok=True, mode=0o700)
        return directory

    @staticmethod
    def _read(path, default=None):
        try:
            return json.loads(path.read_text())
        except FileNotFoundError:
            return default

    @staticmethod
    def _write(path, data):
        fd, temporary = tempfile.mkstemp(prefix='.transfer-', dir=str(path.parent))
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(canonical(data)); handle.flush(); os.fsync(handle.fileno())
            os.replace(temporary, path)
            descriptor = os.open(str(path.parent), os.O_RDONLY)
            try: os.fsync(descriptor)
            finally: os.close(descriptor)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)

    def _response(self, data, kind, **fields):
        return dict(schema_version=2, message_type=kind, execution_id=data['execution_id'],
                    content_fingerprint=data['content_fingerprint'], attempt=data['attempt'], request_kind=data['kind'], **fields)

    @staticmethod
    def _validate_manifest(manifest, execution_id):
        if not isinstance(manifest, dict): raise ValueError('invalid task manifest')
        count = manifest.get('waypoint_count'); chunks = manifest.get('chunk_count'); task = manifest.get('task')
        fingerprint = manifest.get('content_fingerprint')
        if (type(count) is not int or not 1 <= count <= MAX_SOURCE_WAYPOINTS
                or type(chunks) is not int or chunks != (count + CHUNK_POINTS - 1) // CHUNK_POINTS
                or not isinstance(task, dict) or task.get('execution_id') != execution_id
                or 'waypoints' in task or not isinstance(fingerprint, str) or not HASH.fullmatch(fingerprint)
                or len(canonical(manifest)) > MAX_MESSAGE_BYTES):
            raise ValueError('invalid task manifest')

    def _stored_manifest(self, directory):
        try:
            missing = object()
            manifest = self._read(directory / 'manifest.json', missing)
            if manifest is missing: return None
            self._validate_manifest(manifest, directory.name)
            return manifest
        except (ValueError, TypeError) as exc:
            raise ValueError('invalid stored manifest for ' + directory.name + '; operator recovery required') from exc

    def handle(self, data, engine, launch_fix=None, hover_seconds=5.0):
        validate_header(data)
        with self.lock:
            directory = self.root / data['execution_id']
            binding_path = directory / 'binding.json'
            binding = self._read(binding_path)
            if binding and binding['content_fingerprint'] != data['content_fingerprint']:
                raise ValueError('EXECUTION_CONFLICT')
            known = engine.snapshot(data['execution_id'])
            if known:
                if known.get('content_fingerprint') != data['content_fingerprint']:
                    raise ValueError('EXECUTION_CONFLICT')
                self._directory(data['execution_id'])
                if binding is None:
                    self._write(binding_path, dict(content_fingerprint=data['content_fingerprint']))
                decision = self._decision(directory, data, True, '', known['mission_id'])
                return decision, 'DUPLICATE', known
            decision = self._read(directory / 'decision.json')
            if decision and decision.get('accepted') is True:
                raise ValueError('accepted execution journal is unavailable; recovery required')
            manifest = self._stored_manifest(directory)
            kind = data['kind']
            if kind == 'MANIFEST':
                new = {key: data.get(key) for key in ('task', 'waypoint_count', 'chunk_count', 'content_fingerprint')}
                self._validate_manifest(new, data['execution_id'])
                if manifest is not None and manifest != new:
                    raise ValueError('manifest content conflict')
                if manifest is None:
                    incomplete = sum(1 for p in self.root.iterdir() if p.is_dir()
                                     and not (p / 'decision.json').exists() and self._stored_manifest(p) is not None)
                    if incomplete >= 64: raise ValueError('incomplete transfer capacity reached')
                    self._directory(data['execution_id'])
                    if binding is None:
                        self._write(binding_path, dict(content_fingerprint=data['content_fingerprint']))
                    self._write(directory / 'manifest.json', new)
                manifest = new
            elif kind == 'CHUNK':
                if manifest is None: raise ValueError('manifest required')
                index = data.get('index'); points = data.get('waypoints')
                if (type(index) is not int or not 0 <= index < manifest['chunk_count']
                        or data.get('start') != index * CHUNK_POINTS or not isinstance(points, list)
                        or len(points) != min(CHUNK_POINTS, manifest['waypoint_count'] - index * CHUNK_POINTS)
                        or digest(points) != data.get('chunk_sha256')):
                    raise ValueError('invalid task chunk')
                path = directory / ('chunk-%04d.json' % index)
                existing = self._read(path)
                if existing is not None and existing != points: raise ValueError('chunk content conflict')
                if existing is None: self._write(path, points)
            missing = [] if manifest is None else [i for i in range(manifest['chunk_count']) if not (directory / ('chunk-%04d.json' % i)).exists()]
            if kind == 'COMMIT':
                if decision and data['attempt'] <= decision['attempt']:
                    return decision, 'REJECTED', None
                if manifest is None or missing:
                    return self._response(data, 'TRANSFER', status='INCOMPLETE', missing_chunks=missing,
                                          manifest_required=manifest is None), None, None
                points = []
                for i in range(manifest['chunk_count']):
                    points.extend(self._read(directory / ('chunk-%04d.json' % i)))
                task = dict(manifest['task'], waypoints=points)
                if len(canonical(task)) > MAX_TASK_BYTES: raise ValueError('task exceeds 16 MiB')
                mission = normalize_execution_payload(task)
                if mission['content_fingerprint'] != data['content_fingerprint']:
                    raise ValueError('complete task fingerprint mismatch')
                self._write(directory / 'task.json', task)
                try:
                    result, snapshot = engine.admit(mission, launch_fix, hover_seconds=hover_seconds)
                except (ValueError, OSError) as exc:
                    known = engine.snapshot(data['execution_id'])
                    if known: raise
                    decision = self._decision(directory, data, False, str(exc), mission['mission_id'])
                    return decision, 'REJECTED', None
                return self._decision(directory, data, True, '', mission['mission_id']), result, snapshot
            if kind == 'QUERY' and decision:
                return decision, 'REJECTED', None
            return self._response(data, 'TRANSFER', status='TRANSFER_READY' if manifest is not None else 'NOT_RECEIVED',
                                  mission_id=manifest['task'].get('mission_id', '') if manifest else '',
                                  missing_chunks=missing, manifest_required=manifest is None), None, None

    def _decision(self, directory, data, accepted, reason, mission_id):
        path = directory / 'decision.json'; old = self._read(path)
        if old and old.get('accepted') is True: return old
        if old and old['attempt'] == data['attempt'] and old['accepted'] == accepted: return old
        result = self._response(data, 'ADMISSION', status='ACCEPTED' if accepted else 'REJECTED',
                                phase='MISSION_ACCEPTED' if accepted else 'MISSION_REJECTED',
                                mission_id=mission_id, accepted=accepted, reason=reason,
                                decision_seq=(old.get('decision_seq', 0) if old else 0) + 1)
        self._write(path, result)
        return copy.deepcopy(result)
