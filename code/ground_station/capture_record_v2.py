import copy
import hashlib
import hmac
import json
import math
import re
from datetime import datetime, timezone

ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
MISSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}/[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
HASH_RE = re.compile(r"[0-9a-f]{64}")
SOURCE_FIELDS = frozenset(('schema_version','request_id','mission_id','user_id','latitude','longitude','accuracy','captured_at','client_timestamp_ms','status','device','gps_points','test_mode'))
PAYLOAD_FIELDS = frozenset(('schema_version','capture_id','request_id','user_id','context_id','carrier_mission_id','carrier_execution_id','source_request','latitude','longitude','accuracy','captured_at','client_timestamp_ms','capture_started_at_ms','device','test_mode'))
RECORD_FIELDS = PAYLOAD_FIELDS | {'uav_received_at','payload_sha256','storage_state'}
ENVELOPE_FIELDS = frozenset(('schema_version','delivery_state','forward_trigger','trigger_semantics','forwarded_at','record','envelope_sha256'))
ACK_FIELDS = frozenset(('schema_version','capture_id','request_id','context_id','carrier_mission_id','carrier_execution_id','payload_sha256','envelope_sha256','status','acknowledged_at'))
TRIGGERS = {
 'MISSION_QUEUE_COMPLETE_LAND_REQUESTED': 'Mission queue complete and LAND command requested; physical touchdown is not confirmed.',
 'GROUND_STATION_SYNC_REQUEST': 'Recovery replay requested by Ground Station; this is not landing evidence.',
}

class CaptureValidationError(ValueError):
    pass


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')


def exact(value, fields, label):
    if not isinstance(value,dict) or set(value)!=set(fields):
        raise CaptureValidationError('invalid '+label+' fields')


def identifier(value, label, mission=False):
    if not isinstance(value,str) or not (MISSION_RE if mission else ID_RE).fullmatch(value):
        raise CaptureValidationError('invalid '+label)
    return value


def timestamp(value, label, *, strict=True):
    if not isinstance(value,str) or not value.endswith('Z'):
        raise CaptureValidationError('invalid '+label)
    if strict and not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z', value):
        raise CaptureValidationError('invalid '+label)
    try:
        result=datetime.fromisoformat(value[:-1]+'+00:00')
    except ValueError as exc:
        raise CaptureValidationError('invalid '+label) from exc
    if result.tzinfo is None or result.utcoffset()!=timezone.utc.utcoffset(result):
        raise CaptureValidationError('invalid '+label)
    return result


def integer(value,label):
    if type(value) is not int or value<=0 or value>9007199254740991:
        raise CaptureValidationError('invalid '+label)
    return value


def number(value,label):
    if isinstance(value,bool) or not isinstance(value,(int,float)):
        raise CaptureValidationError('invalid '+label)
    try: result=float(value)
    except (ValueError,OverflowError): raise CaptureValidationError('invalid '+label)
    if not math.isfinite(result): raise CaptureValidationError('invalid '+label)
    return 0.0 if result==0 else result


def gps(data):
    lat,lon=number(data.get('latitude'),'latitude'),number(data.get('longitude'),'longitude')
    if not -90<=lat<=90 or not -180<=lon<=180: raise CaptureValidationError('GPS out of range')
    accuracy=data.get('accuracy')
    if accuracy is not None:
        accuracy=number(accuracy,'accuracy')
        if accuracy<0: raise CaptureValidationError('accuracy out of range')
    return lat,lon,accuracy


def device_fields(data):
    value=data.get('device')
    if not isinstance(value,str) or not value or len(value)>32: raise CaptureValidationError('invalid device')
    try: value.encode('utf-8')
    except UnicodeEncodeError as exc: raise CaptureValidationError('invalid device Unicode') from exc
    if type(data.get('test_mode')) is not bool: raise CaptureValidationError('invalid test_mode')


def validate_source(data):
    if not isinstance(data,dict): raise CaptureValidationError('current SOS source_request required')
    exact(data,SOURCE_FIELDS,'source_request')
    if type(data['schema_version']) is not int or data['schema_version']!=1: raise CaptureValidationError('source_request must be schema v1')
    uid=identifier(data['user_id'],'source user'); rid=identifier(data['request_id'],'source request')
    mid=identifier(data['mission_id'],'source mission',True)
    if mid!=uid+'/'+rid: raise CaptureValidationError('source mission/user/request mismatch')
    lat,lon,accuracy=gps(data)
    timestamp(data['captured_at'],'source captured_at',strict=False);integer(data['client_timestamp_ms'],'source timestamp')
    if data['status'] not in ('PENDING','ACCEPTED'): raise CaptureValidationError('invalid source status')
    device_fields(data)
    points=data['gps_points']
    if not isinstance(points,list) or len(points)>1000: raise CaptureValidationError('invalid source gps_points')
    result=copy.deepcopy(data); result.update(latitude=lat,longitude=lon,accuracy=accuracy)
    for point in result['gps_points']:
        exact(point,{'latitude','longitude','captured_at'},'source GPS point')
        point['latitude'],point['longitude'],_=gps(point)
        timestamp(point['captured_at'],'source GPS captured_at',strict=False)
    return result


def validate_payload(data):
    if not isinstance(data,dict) or not isinstance(data.get('source_request'),dict):
        raise CaptureValidationError('current SOS source_request required')
    exact(data,PAYLOAD_FIELDS,'capture payload')
    if type(data['schema_version']) is not int or data['schema_version']!=2: raise CaptureValidationError('unsupported capture schema')
    for name in ('capture_id','request_id','user_id','context_id','carrier_execution_id'): identifier(data[name],name)
    carrier=identifier(data['carrier_mission_id'],'carrier_mission_id',True)
    source=validate_source(data['source_request'])
    if data['request_id']!=source['request_id'] or data['user_id']!=source['user_id'] or carrier.split('/')[0]!=data['user_id']:
        raise CaptureValidationError('capture/source/carrier user mismatch')
    lat,lon,accuracy=gps(data)
    sample=integer(data['client_timestamp_ms'],'client_timestamp_ms')
    started=integer(data['capture_started_at_ms'],'capture_started_at_ms')
    if started>sample: raise CaptureValidationError('GPS sample predates capture start')
    captured=timestamp(data['captured_at'],'captured_at')
    fraction=data['captured_at'].partition('.')[2][:-1] if '.' in data['captured_at'] else ''
    if any(c!='0' for c in fraction[3:]): raise CaptureValidationError('captured_at contains submillisecond time')
    delta=captured-datetime(1970,1,1,tzinfo=timezone.utc)
    captured_us=(delta.days*86400+delta.seconds)*1000000+delta.microseconds
    if captured_us!=sample*1000: raise CaptureValidationError('captured_at does not match GPS timestamp')
    device_fields(data)
    result=copy.deepcopy(data)
    result.update(source_request=source,latitude=lat,longitude=lon,accuracy=accuracy)
    return result


def validate_record(record):
    exact(record,RECORD_FIELDS,'capture record')
    payload=validate_payload({k:record[k] for k in PAYLOAD_FIELDS})
    if record['storage_state']!='STORED': raise CaptureValidationError('invalid capture storage state')
    timestamp(record['uav_received_at'],'uav_received_at')
    if not isinstance(record['payload_sha256'],str) or not HASH_RE.fullmatch(record['payload_sha256']) or not hmac.compare_digest(digest(payload),record['payload_sha256']):
        raise CaptureValidationError('capture payload hash mismatch')

    if canonical(payload)!=canonical({k:record[k] for k in PAYLOAD_FIELDS}): raise CaptureValidationError('capture payload is not normalized')
    return copy.deepcopy(record)


def envelope_hash(envelope):
    return digest({k:v for k,v in envelope.items() if k!='envelope_sha256'})


def build_envelope(record,trigger,forwarded_at=None):
    record=validate_record(record)
    if trigger not in TRIGGERS: raise CaptureValidationError('invalid forward trigger')
    result=dict(schema_version=2,delivery_state='FORWARDED_TO_GS',forward_trigger=trigger,
                trigger_semantics=TRIGGERS[trigger],forwarded_at=forwarded_at or utc_now(),record=record)
    result['envelope_sha256']=envelope_hash(result)
    return result


def parse_envelope(envelope):
    exact(envelope,ENVELOPE_FIELDS,'capture envelope')
    if type(envelope['schema_version']) is not int or envelope['schema_version']!=2: raise CaptureValidationError('unsupported capture envelope schema')
    if envelope['delivery_state']!='FORWARDED_TO_GS': raise CaptureValidationError('invalid delivery state')
    trigger=envelope['forward_trigger']
    if trigger not in TRIGGERS or envelope['trigger_semantics']!=TRIGGERS[trigger]: raise CaptureValidationError('invalid trigger semantics')
    record=validate_record(envelope['record'])
    if timestamp(envelope['forwarded_at'],'forwarded_at')<timestamp(record['uav_received_at'],'uav_received_at'): raise CaptureValidationError('forwarding precedes receipt')
    value=envelope['envelope_sha256']
    if not isinstance(value,str) or not HASH_RE.fullmatch(value) or not hmac.compare_digest(value,envelope_hash(envelope)):
        raise CaptureValidationError('capture envelope hash mismatch')
    return copy.deepcopy(envelope)


def validate_ack(ack):
    exact(ack,ACK_FIELDS,'capture ACK')
    if type(ack['schema_version']) is not int or ack['schema_version']!=2: raise CaptureValidationError('unsupported capture ACK schema')
    for name in ('capture_id','request_id','context_id','carrier_execution_id'): identifier(ack[name],name)
    identifier(ack['carrier_mission_id'],'carrier_mission_id',True)
    for name in ('payload_sha256','envelope_sha256'):
        if not isinstance(ack[name],str) or not HASH_RE.fullmatch(ack[name]): raise CaptureValidationError('invalid '+name)
    if ack['status']!='ACKNOWLEDGED_BY_GS': raise CaptureValidationError('invalid ACK status')
    timestamp(ack['acknowledged_at'],'acknowledged_at')
    return copy.deepcopy(ack)


def build_ack(envelope):
    env=parse_envelope(envelope); record=env['record']
    ack={k:record[k] for k in ('capture_id','request_id','context_id','carrier_mission_id','carrier_execution_id','payload_sha256')}
    ack.update(schema_version=2,envelope_sha256=env['envelope_sha256'],status='ACKNOWLEDGED_BY_GS',acknowledged_at=utc_now())
    return validate_ack(ack)
