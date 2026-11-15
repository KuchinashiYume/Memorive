"""sample directed regressions. Local fixtures only; no production profile writes."""
import base64
import io
import json
from pathlib import Path
from copy import deepcopy
import pytest

ROOT=Path(__file__).resolve().parents[1]
def web(name):return (ROOT/'product/web'/name).read_text(encoding='utf8')


def test_bounded_research_edits_and_legacy_preservation():
    from memorive_research_runtime.common import defaults,validate_config_edit
    initial=defaults()
    for key,value in [('count',101),('count',0),('count',1.5),('interval_hours',12),('interval_hours',366*24)]:
        changed=dict(initial,**{key:value})
        with pytest.raises(ValueError):validate_config_edit(changed,initial)
    for count in [1,100]:validate_config_edit(dict(initial,count=count),initial)
    for share in [.01,.29,1]:
        changed=deepcopy(initial);changed['exposure_limits']['direction_share']=share
        validate_config_edit(changed,initial)
    for share in [.005,.295,float('nan')]:
        changed=deepcopy(initial);changed['exposure_limits']['direction_share']=share
        with pytest.raises(ValueError):validate_config_edit(changed,initial)
    legacy=dict(initial,count=1001,interval_hours=12)
    assert validate_config_edit(dict(legacy,push_enabled=False),legacy)['count']==1001

def test_new_preferences_are_private_atomic_and_revision_guarded(tmp_path):
    from memorive_settings.user_preferences import UserPreferences
    store=UserPreferences(tmp_path)
    assert store.get()['config']==dict(username='',avatar_png='',hide_expressions=False,answer_style='professional')  # answer_style added in v1.02
    assert not (tmp_path/'user_preferences.json').exists()
    value=store.save(config=dict(username='测试 User',avatar_png='',hide_expressions=True),expected_revision=0)
    assert value['revision']==1 and UserPreferences(tmp_path).get()==value
    with pytest.raises(ValueError):store.save(config=value['config'],expected_revision=0)
    before=(tmp_path/'user_preferences.json').read_bytes()
    with pytest.raises(ValueError):store.save(config=dict(value['config'],username='x'*65),expected_revision=1)
    assert (tmp_path/'user_preferences.json').read_bytes()==before
    for name in ['x\n','x\u202e','x'*65]:
        with pytest.raises(ValueError):store.save(config=dict(value['config'],username=name),expected_revision=1)

def test_avatar_server_rejects_forged_oversized_or_multiframe_payloads():
    from memorive_settings.user_preferences import normalize_avatar
    def png(size):
        import struct,zlib
        def chunk(kind,payload):return struct.pack('>I',len(payload))+kind+payload+struct.pack('>I',zlib.crc32(kind+payload))
        w,h=size
        data=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',w,h,8,6,0,0,0))+chunk(b'IDAT',zlib.compress((b'\x00'+b'\xff\x00\x00\xff'*w)*h))+chunk(b'IEND',b'')
        return base64.b64encode(data).decode()
    assert normalize_avatar(png((256,256)))
    for value in [base64.b64encode(b'<svg/>').decode(),png((257,1)),'A'*600000]:
        with pytest.raises(ValueError):normalize_avatar(value)
    raw=base64.b64decode(png((1,1)))
    for data in [raw[:-1],raw+b'ignored-tail',raw[:40]+b'broken'+raw[46:]]:
        with pytest.raises(ValueError):normalize_avatar(base64.b64encode(data).decode())

def test_corruption_is_not_overwritten(tmp_path):
    from memorive_settings.user_preferences import UserPreferences
    path=tmp_path/'user_preferences.json';path.write_bytes(b'corrupt-test-fixture')
    with pytest.raises(Exception):UserPreferences(tmp_path).get()
    assert path.read_bytes()==b'corrupt-test-fixture'


