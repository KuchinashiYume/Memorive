from pathlib import Path
from datetime import datetime, timezone
import json, hashlib, os, uuid
from threading import RLock

# Windows readers do not share delete access. Keep our brief local reads and
# atomic replacements mutually exclusive; never retry a finished research run.
_FILE_IO_LOCK = RLock()

def now():
    return datetime.now(timezone.utc).isoformat()

def encoded(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf8')

def sha(value):
    return hashlib.sha256(value if isinstance(value,bytes) else encoded(value)).hexdigest()

def write(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
    payload=encoded(value)
    with _FILE_IO_LOCK:
        with tmp.open('xb') as f:
            f.write(payload);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)

def sealed(value):
    value={k:v for k,v in value.items() if k!='sha256'}
    return dict(value,sha256=sha(value))

def read(path):
    with _FILE_IO_LOCK:
        value=json.loads(Path(path).read_text(encoding='utf8'))
    if value.get('sha256')!=sha({k:v for k,v in value.items() if k!='sha256'}):
        raise ValueError('RESEARCH_RECORD_HASH_MISMATCH')
    return value

CHANNELS=('CORE','ADJACENT','BRIDGE','HORIZON','COVERAGE_REPAIR')
def defaults():
    return {'topic':'','count':10,'queries':{c:'' for c in CHANNELS},
            'weights':{c:(100 if c=='CORE' else 0) for c in CHANNELS},'include_seen':False,
            'passive_enabled':False,'push_enabled':True,'interval_hours':24,'matching_preference':50,
            'directions':[],'use_recent_interests':False,
            'exposure_limits':{'author':None,'institution':None,'journal':None,'direction':None,'direction_share':None}}

def validate_config(value):
    if not isinstance(value,dict) or set(value)!=set(defaults()):raise ValueError('RESEARCH_CONFIG_FIELDS_INVALID')
    if not isinstance(value['topic'],str) or len(value['topic'])>240 or any(ord(c)<32 for c in value['topic']):raise ValueError('RESEARCH_TOPIC_INVALID')
    if type(value['count']) is not int or value['count']<1:raise ValueError('RESEARCH_COUNT_INVALID')
    if type(value['include_seen']) is not bool:raise ValueError('RESEARCH_CONFIG_INVALID')
    if type(value['matching_preference']) is not int or not 0<=value['matching_preference']<=100:raise ValueError('RESEARCH_MATCHING_PREFERENCE_INVALID')
    if any(type(value[k]) is not bool for k in ('passive_enabled','push_enabled')):raise ValueError('RESEARCH_PREFERENCE_INVALID')
    # Retain hours on disk for compatibility; the UI edits days, including fractions.
    if type(value['interval_hours']) not in (int,float) or not __import__('math').isfinite(value['interval_hours']) or value['interval_hours'] < 1:raise ValueError('RESEARCH_INTERVAL_INVALID')
    if not isinstance(value['queries'],dict) or set(value['queries'])!=set(CHANNELS):raise ValueError('RESEARCH_CHANNELS_INVALID')
    if any(not isinstance(v,str) or len(v)>240 or any(ord(c)<32 for c in v) for v in value['queries'].values()):raise ValueError('RESEARCH_QUERY_INVALID')
    if not isinstance(value['weights'],dict) or set(value['weights'])!=set(CHANNELS) or any(type(v) is not int or not 0<=v<=1000 for v in value['weights'].values()) or sum(value['weights'].values())==0:raise ValueError('RESEARCH_WEIGHTS_INVALID')
    if type(value['use_recent_interests']) is not bool:raise ValueError('RESEARCH_INTEREST_PREFERENCE_INVALID')
    if not isinstance(value['directions'],list):raise ValueError('RESEARCH_DIRECTIONS_INVALID')
    ids=set()
    for direction in value['directions']:
        if not isinstance(direction,dict) or set(direction)!={'id','revision','name','query','channels','keywords','seed_ids'}:raise ValueError('RESEARCH_DIRECTION_FIELDS_INVALID')
        if not isinstance(direction['id'],str) or not __import__('re').fullmatch(r'direction-[a-zA-Z0-9-]+',direction['id']) or direction['id'] in ids:raise ValueError('RESEARCH_DIRECTION_ID_INVALID')
        ids.add(direction['id'])
        if type(direction['revision']) is not int or direction['revision']<1:raise ValueError('RESEARCH_DIRECTION_REVISION_INVALID')
        if any(not isinstance(direction[k],str) or not direction[k].strip() or any(ord(ch)<32 for ch in direction[k]) for k in ('name','query')):raise ValueError('RESEARCH_DIRECTION_TEXT_INVALID')
        if not isinstance(direction['channels'],list) or not direction['channels'] or len(set(direction['channels']))!=len(direction['channels']) or not set(direction['channels']).issubset(CHANNELS):raise ValueError('RESEARCH_DIRECTION_CHANNEL_INVALID')
        for key in ('keywords','seed_ids'):
            if not isinstance(direction[key],list) or any(not isinstance(x,str) or not x.strip() for x in direction[key]):raise ValueError('RESEARCH_DIRECTION_EVIDENCE_INVALID')
    limits=value['exposure_limits']
    if not isinstance(limits,dict) or set(limits)!=set(defaults()['exposure_limits']):raise ValueError('RESEARCH_EXPOSURE_LIMITS_INVALID')
    for key,v in limits.items():
        if v is None:continue
        if key=='direction_share':
            if type(v) not in (int,float) or not 0<v<=1:raise ValueError('RESEARCH_EXPOSURE_LIMIT_INVALID')
        elif type(v) is not int or v<1:raise ValueError('RESEARCH_EXPOSURE_LIMIT_INVALID')
    return json.loads(encoded(value))

def validate_config_edit(value, previous):
    """Constrain edited UI preferences, without invalidating frozen/legacy runs."""
    accepted=validate_config(value)
    for key,minimum,maximum,unit in (('count',1,100,1),('interval_hours',24,365*24,24)):
        current=accepted[key]
        if current!=previous.get(key) and (not minimum<=current<=maximum or current%unit):
            raise ValueError('RESEARCH_EDIT_'+key.upper()+'_RANGE')
    prior=previous.get('exposure_limits',{})
    for key,current in accepted['exposure_limits'].items():
        if current is None or current==prior.get(key):continue
        if key=='direction_share':
            if not .01<=current<=1 or abs(current*100-round(current*100))>1e-8:
                raise ValueError('RESEARCH_EDIT_SHARE_RANGE')
        elif not 1<=current<=100:
            raise ValueError('RESEARCH_EDIT_EXPOSURE_RANGE')
    return accepted
