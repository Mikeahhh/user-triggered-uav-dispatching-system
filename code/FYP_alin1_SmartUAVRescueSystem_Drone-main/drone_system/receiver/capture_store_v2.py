import copy
import errno
import fcntl
import hashlib
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
import capture_record_v2 as wire

class CaptureConflictError(RuntimeError):
    pass

class NoCollectionContext(RuntimeError):
    pass

class JournalCollectionContextProvider:

    def __init__(self,path):
        self.path=Path(path).expanduser()

    def __call__(self,user_id):
        wire.identifier(user_id,'user_id')
        fd=None
        try:
            fd=os.open(str(self.path)+'.lock',os.O_RDONLY)
            self._require_locked(fd)
            owner_before=self._owner(fd)
            with self.path.open(encoding='utf-8') as handle: journal=json.load(handle)
            owner_after=self._owner(fd)
            self._require_locked(fd)
            if owner_before!=owner_after or journal.get('owner')!=owner_before:
                raise NoCollectionContext('bridge owner changed')
            pid=owner_before.get('pid'); epoch=owner_before.get('instance_id')
            if type(pid) is not int or pid<=0 or not isinstance(epoch,str) or not re.fullmatch('[0-9a-f]{32}',epoch):
                raise NoCollectionContext('invalid bridge owner')
            os.kill(pid,0)
            context=journal.get('collection_context')
            if (not isinstance(context,dict) or type(context.get('schema_version')) is not int
                    or context['schema_version']!=2 or context.get('context_epoch')!=epoch
                    or context.get('collection_ready') is not True or context.get('arrival_observed') is not True):
                raise NoCollectionContext('no arrival-supported collection context')
            mid=wire.identifier(context.get('mission_id'),'carrier_mission_id',True)
            eid=wire.identifier(context.get('execution_id'),'carrier_execution_id')
            if mid.split('/')[0]!=user_id or journal.get('active_execution_id')!=eid:
                raise NoCollectionContext('no collection context for this user')
            record=journal.get('executions',{}).get(eid,{})
            if record.get('mission_id')!=mid or record.get('execution_id')!=eid:
                raise NoCollectionContext('journal execution/context mismatch')
            index=context.get('waypoint_index'); total=context.get('source_waypoint_total')
            if (record.get('phase')!=context.get('phase') or context.get('phase') not in ('NAVIGATING','HOVERING','WAITING_TARGET_ACCEPTANCE')
                    or record.get('search_area_arrival_observed') is not True or record.get('collection_controller_valid') is not True
                    or type(index) is not int or type(total) is not int or not 0<=index<total
                    or record.get('waypoint_index')!=index or record.get('source_waypoint_total')!=total):
                raise NoCollectionContext('collection header disagrees with execution facts')
            return copy.deepcopy(context)
        except (OSError,ValueError,TypeError,KeyError) as exc:
            raise NoCollectionContext('no live collection context') from exc
        finally:
            if fd is not None: os.close(fd)

    @staticmethod
    def _owner(fd):
        os.lseek(fd,0,os.SEEK_SET)
        raw=os.read(fd,4096)
        result=json.loads(raw.decode('utf-8'))
        if not isinstance(result,dict) or set(result)!={'pid','instance_id'}: raise NoCollectionContext('invalid lock owner')
        return result

    @staticmethod
    def _require_locked(fd):
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EAGAIN,errno.EACCES): return
            raise
        fcntl.flock(fd,fcntl.LOCK_UN)
        raise NoCollectionContext('bridge does not own journal lock')


class CaptureStoreV2:
    def __init__(self,root,write_atomic,write_exclusive,mkdir_durable,source_lookup=None):
        self.root=Path(root)
        self.records_dir=self.root/'captures_v2'
        self.outbox_dir=self.root/'capture_outbox_v2'
        self.contexts_dir=self.root/'capture_contexts_v2'
        self.sources_dir=self.root/'capture_sources_v2'
        self.triggers_dir=self.root/'capture_land_requests_v2'
        for path in (self.records_dir,self.outbox_dir,self.contexts_dir,self.sources_dir,self.triggers_dir): mkdir_durable(path)
        self.write_atomic=write_atomic; self.write_exclusive=write_exclusive
        self.source_lookup=source_lookup
        self.lock=threading.RLock()
        self.bad_entries=set()

    @staticmethod
    def read(path):
        with path.open(encoding='utf-8') as handle: return json.load(handle)

    @staticmethod
    def _path(directory,capture_id):
        return directory/(wire.identifier(capture_id,'capture_id')+'.json')

    def issue_context(self,user_id,request_id,capture_id,provider):
        for value,name in ((user_id,'user_id'),(request_id,'request_id'),(capture_id,'capture_id')): wire.identifier(value,name)
        path=self._path(self.contexts_dir,capture_id)
        with self.lock:
            if path.exists():
                saved=self._read_context(capture_id)
                if saved['response']['user_id']!=user_id or saved['response']['request_id']!=request_id:
                    raise CaptureConflictError('CONTEXT_IDENTITY_CONFLICT')
                if provider is None: raise NoCollectionContext('collection context unavailable')
                live=provider(user_id)
                if (not isinstance(live,dict) or live.get('collection_ready') is not True or live.get('arrival_observed') is not True
                        or any(live.get(k)!=saved['bridge_context'].get(k) for k in ('mission_id','execution_id','context_epoch'))):
                    raise NoCollectionContext('capture intent no longer belongs to live eligible execution')
                return saved['response']
            if provider is None: raise NoCollectionContext('collection context unavailable')
            bridge=provider(user_id)
            if (not isinstance(bridge,dict) or bridge.get('collection_ready') is not True
                    or bridge.get('arrival_observed') is not True): raise NoCollectionContext('collection not ready')
            mid=wire.identifier(bridge.get('mission_id'),'carrier_mission_id',True)
            eid=wire.identifier(bridge.get('execution_id'),'carrier_execution_id')
            kind=wire.identifier(bridge.get('mission_type'),'carrier_mission_type')
            if mid.split('/')[0]!=user_id: raise NoCollectionContext('no context for this user')
            response=dict(schema_version=2,status='CONTEXT_ISSUED',context_id=uuid.uuid4().hex,
                          capture_id=capture_id,request_id=request_id,user_id=user_id,
                          carrier_mission_id=mid,carrier_execution_id=eid,carrier_mission_type=kind,
                          phase=bridge.get('phase',''),issued_at=wire.utc_now())
            saved=dict(response=response,bridge_context=copy.deepcopy(bridge))
            try: self.write_exclusive(path,saved)
            except FileExistsError:
                winner_saved=self._read_context(capture_id); winner=winner_saved['response']
                if winner['user_id']!=user_id or winner['request_id']!=request_id: raise CaptureConflictError('CONTEXT_IDENTITY_CONFLICT')
                if any(bridge.get(k)!=winner_saved['bridge_context'].get(k) for k in ('mission_id','execution_id','context_epoch')):
                    raise NoCollectionContext('capture intent belongs to another execution epoch')
                return winner
            return response

    def _read_context(self,capture_id):
        saved=self.read(self._path(self.contexts_dir,capture_id))
        wire.exact(saved,{'response','bridge_context'},'saved context')
        response=saved['response']
        wire.exact(response,{'schema_version','status','context_id','capture_id','request_id','user_id','carrier_mission_id','carrier_execution_id','carrier_mission_type','phase','issued_at'},'context response')
        if type(response['schema_version']) is not int or response['schema_version']!=2 or response['status']!='CONTEXT_ISSUED' or response['capture_id']!=capture_id: raise wire.CaptureValidationError('invalid saved context identity')
        for field in ('capture_id','request_id','user_id','context_id','carrier_execution_id','carrier_mission_type'): wire.identifier(response[field],field)
        wire.identifier(response['carrier_mission_id'],'carrier_mission_id',True)
        wire.timestamp(response['issued_at'],'issued_at')
        bridge=saved['bridge_context']
        if (not isinstance(bridge,dict) or bridge.get('collection_ready') is not True or bridge.get('arrival_observed') is not True
                or bridge.get('mission_id')!=response['carrier_mission_id'] or bridge.get('execution_id')!=response['carrier_execution_id']
                or bridge.get('mission_type')!=response['carrier_mission_type'] or bridge.get('phase')!=response['phase']
                or response['carrier_mission_id'].split('/')[0]!=response['user_id']):
            raise wire.CaptureValidationError('invalid saved bridge context')
        return saved

    def read_record(self,capture_id):
        record=wire.validate_record(self.read(self._path(self.records_dir,capture_id)))
        if record['capture_id']!=capture_id: raise wire.CaptureValidationError('capture filename mismatch')
        return record

    def read_delivery(self,capture_id):
        return self.read(self._path(self.outbox_dir,capture_id))

    def _ensure_outbox(self,record):
        path=self._path(self.outbox_dir,record['capture_id'])
        if path.exists(): return
        state={k:record[k] for k in ('capture_id','request_id','context_id','carrier_mission_id','carrier_execution_id','payload_sha256')}
        state.update(schema_version=2,state='STORED_ONBOARD',delivery_attempts=0,updated_at=record['uav_received_at'])
        try: self.write_exclusive(path,state)
        except FileExistsError: pass

    def _preserve_source(self,source):
        if self.source_lookup is not None:
            try: original=self.source_lookup(source['request_id'])
            except FileNotFoundError: original=None
            if original is not None and wire.canonical(wire.validate_source({k:original[k] for k in wire.SOURCE_FIELDS}))!=wire.canonical(source):
                raise CaptureConflictError('SOURCE_REQUEST_CONFLICT')
        key=hashlib.sha256(source['mission_id'].encode('utf-8')).hexdigest()
        path=self.sources_dir/(key+'.json')
        try: self.write_exclusive(path,source)
        except FileExistsError:
            if wire.canonical(self.read(path))!=wire.canonical(source): raise CaptureConflictError('SOURCE_REQUEST_CONFLICT')

    def store(self,raw):
        payload=wire.validate_payload(raw); capture_id=payload['capture_id']; hashed=wire.digest(payload)
        path=self._path(self.records_dir,capture_id)
        with self.lock:
            if path.exists():
                existing=self.read_record(capture_id)
                if existing['payload_sha256']!=hashed: raise CaptureConflictError('CAPTURE_CONTENT_CONFLICT')
                self._ensure_outbox(existing)
                return existing,True
            try: context=self._read_context(capture_id)['response']
            except FileNotFoundError as exc: raise NoCollectionContext('capture intent has no context') from exc
            for name in ('capture_id','request_id','user_id','context_id','carrier_mission_id','carrier_execution_id'):
                if payload[name]!=context[name]: raise CaptureConflictError('CONTEXT_IDENTITY_CONFLICT')

            self._preserve_source(payload['source_request'])
            record=dict(payload,uav_received_at=wire.utc_now(),payload_sha256=hashed,storage_state='STORED')
            try: self.write_exclusive(path,record)
            except FileExistsError:
                existing=self.read_record(capture_id)
                if existing['payload_sha256']!=hashed: raise CaptureConflictError('CAPTURE_CONTENT_CONFLICT')
                self._ensure_outbox(existing); return existing,True
            self._ensure_outbox(record)
            return record,False

    def update_delivery(self,capture_id,**updates):
        with self.lock:
            path=self._path(self.outbox_dir,capture_id); value=self.read(path)
            value.update(updates,updated_at=wire.utc_now()); self.write_atomic(path,value); return value

    def finish_attempt(self,capture_id,result,error=None):
        with self.lock:
            state=self.read_delivery(capture_id)
            if state.get('state')=='ACKNOWLEDGED_BY_GS': return state
            return self.update_delivery(capture_id,last_attempt_result=result,last_delivery_error=error)

    def pending_records(self,execution_id=None):
        output=[]
        with self.lock:
            for path in sorted(self.records_dir.glob('*.json')):
                try:
                    record=self.read_record(path.stem); self._ensure_outbox(record); state=self.read_delivery(path.stem)
                    if type(state.get('schema_version')) is not int or state['schema_version']!=2: raise wire.CaptureValidationError('outbox schema')
                    for key in ('capture_id','request_id','context_id','carrier_mission_id','carrier_execution_id','payload_sha256'):
                        if state.get(key)!=record[key]: raise wire.CaptureValidationError('outbox identity')
                    if state.get('state') not in ('STORED_ONBOARD','FORWARDED_TO_GS','ACKNOWLEDGED_BY_GS'): raise wire.CaptureValidationError('outbox state')
                    if state['state']=='ACKNOWLEDGED_BY_GS' or execution_id and record['carrier_execution_id']!=execution_id: continue
                    output.append(record)
                except (OSError,ValueError,TypeError,KeyError): self.bad_entries.add(path.name)
        return output

    def remember_land_request(self,mission_id,execution_id):
        wire.identifier(mission_id,'carrier_mission_id',True);wire.identifier(execution_id,'carrier_execution_id')
        self.write_atomic(self._path(self.triggers_dir,execution_id),dict(schema_version=2,carrier_mission_id=mission_id,carrier_execution_id=execution_id,observed_at=wire.utc_now()))

    def has_land_request(self,record):
        try:
            value=self.read(self._path(self.triggers_dir,record['carrier_execution_id']))
            return value.get('schema_version')==2 and value.get('carrier_mission_id')==record['carrier_mission_id'] and value.get('carrier_execution_id')==record['carrier_execution_id']
        except (OSError,ValueError): return False

    def prepare_delivery(self,record,trigger):
        with self.lock:
            state=self.read_delivery(record['capture_id']); saved=state.get('delivery_envelope')
            if saved is not None:
                envelope=wire.parse_envelope(saved)
                if envelope['record']!=record: raise wire.CaptureValidationError('saved envelope record mismatch')
                return envelope
            envelope=wire.build_envelope(record,trigger)
            self.update_delivery(record['capture_id'],delivery_envelope=envelope)
            return envelope

    def acknowledge(self,ack):
        ack=wire.validate_ack(ack); capture_id=ack['capture_id']
        with self.lock:
            state=self.read_delivery(capture_id)
            if state.get('state') not in ('FORWARDED_TO_GS','ACKNOWLEDGED_BY_GS'): raise wire.CaptureValidationError('capture not awaiting ACK')
            for name in ('capture_id','request_id','context_id','carrier_mission_id','carrier_execution_id','payload_sha256','envelope_sha256'):
                if ack[name]!=state.get(name): raise wire.CaptureValidationError('capture ACK identity/hash mismatch')
            if state.get('state')=='ACKNOWLEDGED_BY_GS':
                if state.get('ground_ack')!=ack: raise wire.CaptureValidationError('repeated ACK differs from stored ACK')
                return state
            return self.update_delivery(capture_id,state='ACKNOWLEDGED_BY_GS',ground_ack=ack,acknowledged_at=ack['acknowledged_at'],last_attempt_result='ACKNOWLEDGED')

    def health_summary(self):
        values=[self.read_delivery(record['capture_id']) for record in self.pending_records()]
        return dict(captures=len(list(self.records_dir.glob('*.json'))),pending=len(values),unreadable_entries=len(self.bad_entries))


class CaptureDeliveryCoordinator:
    def __init__(self,store,publish,topic,clock=time.time):
        self.store=store;self.publish=publish;self.topic=topic;self.clock=clock

    def forward(self,record,trigger):
        capture_id=record['capture_id']
        try:
            with self.store.lock:
                state=self.store.read_delivery(capture_id)
                if state.get('state')=='ACKNOWLEDGED_BY_GS': return False
                deadline=state.get('retry_after_epoch')
                if deadline is not None:
                    if wire.number(deadline,'retry deadline')>self.clock(): return False
                attempts=state.get('delivery_attempts',0)
                if type(attempts) is not int or attempts<0: raise wire.CaptureValidationError('invalid delivery attempts')
                envelope=self.store.prepare_delivery(record,trigger)
                self.store.update_delivery(capture_id,state='FORWARDED_TO_GS',envelope_sha256=envelope['envelope_sha256'],
                     forward_trigger=envelope['forward_trigger'],forwarded_at=envelope['forwarded_at'],
                     delivery_attempts=attempts+1,last_attempt_trigger=envelope['forward_trigger'],
                     retry_after_epoch=self.clock()+min(60.,2.*2**min(attempts,8)),last_attempt_result='IN_PROGRESS')
            self.publish(self.topic,wire.canonical(envelope).decode('utf-8'))
            self.store.finish_attempt(capture_id,'PUBLISHED')
            return True
        except Exception as exc:
            try: self.store.finish_attempt(capture_id,'DEFERRED',type(exc).__name__)
            except Exception: pass
            return False

    def retry_pending(self):
        count=0
        for record in self.store.pending_records():
            try: state=self.store.read_delivery(record['capture_id'])
            except (OSError,ValueError,TypeError): continue
            saved=state.get('delivery_envelope')
            trigger=saved.get('forward_trigger') if isinstance(saved,dict) else None
            if trigger not in wire.TRIGGERS and self.store.has_land_request(record): trigger='MISSION_QUEUE_COMPLETE_LAND_REQUESTED'
            if trigger in wire.TRIGGERS: count+=self.forward(record,trigger)
        return count

    def handle_status(self,data):
        if (not isinstance(data,dict) or type(data.get('schema_version')) is not int or data['schema_version']!=2
                or data.get('phase')!='LAND_REQUESTED' or data.get('status') not in ('LANDING','LAND_REQUESTED')
                or data.get('land_command_requested') is not True): return 0
        mid=data.get('mission_id');eid=data.get('execution_id')
        try: wire.identifier(mid,'carrier_mission_id',True);wire.identifier(eid,'carrier_execution_id')
        except ValueError: return 0
        if data.get('active_execution_id') not in (None,eid): return 0
        self.store.remember_land_request(mid,eid)
        return sum(self.forward(record,'MISSION_QUEUE_COMPLETE_LAND_REQUESTED') for record in self.store.pending_records(eid) if record['carrier_mission_id']==mid)

    def sync(self):
        return sum(self.forward(record,'GROUND_STATION_SYNC_REQUEST') for record in self.store.pending_records())
