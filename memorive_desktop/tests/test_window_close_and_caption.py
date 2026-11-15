"""Directed user-reported gap checks; runs against the copied predecessor first."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'product/desktop'))

def test_unified_caption_adapter_exists():
    assert (ROOT/'product/desktop/window_chrome.py').is_file()


def test_dirty_state_is_explicit_not_save_button_color():
    assert 'getEditState' in (ROOT/'product/web/settings_bridge.js').read_text(encoding='utf8')


import pytest
from itertools import product
from window_chrome import MainWindowChrome,close_projection,validate_edit_state,hit_test
from application_behavior import decide_close_action


class PolicyAPI:
    def __init__(self):
        self.config=dict(close_behavior='ASK',warn_on_close_running=True,
                         keep_tasks_in_background=True,active_job_count=0)
        self.calls=[]
    def get_close_policy(self,choice=None):
        return dict(self.config,status='READY',action=decide_close_action(**self.config,user_choice=choice))
    def exit_application(self,_):self.calls.append('EXIT');return {'status':'EXIT_SCHEDULED'}
    def hide_main_window(self):self.calls.append('HIDE');return {'status':'HIDDEN_TO_TRAY'}


CLEAN=dict(ready=True,dirty=False,saving=False)

@pytest.mark.parametrize('mode,warn,background,count,ready,dirty,saving',
    product(('ASK','EXIT','TRAY'),(False,True),(False,True),(0,2),(False,True),(False,True),(False,True)))
def test_close_projection_preserves_policy_and_edit_guard(mode,warn,background,count,ready,dirty,saving):
    config=dict(close_behavior=mode,warn_on_close_running=warn,keep_tasks_in_background=background,active_job_count=count)
    action=decide_close_action(**config)
    view=close_projection(dict(config,status='READY',action=action),dict(ready=ready,dirty=dirty,saving=saving))
    assert view['automatic_action']==(action if ready and not dirty and not saving else 'ASK')
    assert view['tray_allowed']==(not count or background)

@pytest.mark.parametrize('edit',[None,{},dict(CLEAN,unknown=True),dict(CLEAN,ready=1),dict(CLEAN,dirty='false')])
def test_invalid_edit_state_rejected(edit):
    with pytest.raises(ValueError):validate_edit_state(edit)

@pytest.mark.parametrize('choice',('EXIT','HIDE'))
def test_single_use_ticket(choice):
    api=PolicyAPI();chrome=MainWindowChrome(None,api)
    view=chrome.prepare_close(CLEAN);req=dict(ticket=view['ticket'],choice=choice,edit=CLEAN)
    assert chrome.commit_close(req)['status'] in {'EXIT_SCHEDULED','HIDDEN_TO_TRAY'}
    assert chrome.commit_close(req)['status']=='RECONFIRM'
    assert api.calls==[choice]

def test_cancel_invalidates_pending_intent():
    api=PolicyAPI();chrome=MainWindowChrome(None,api);view=chrome.prepare_close(CLEAN)
    chrome.cancel_close()
    assert chrome.commit_close(dict(ticket=view['ticket'],choice='EXIT',edit=CLEAN))['status']=='RECONFIRM'
    assert api.calls==[]

@pytest.mark.parametrize('field,value',[('active_job_count',1),('keep_tasks_in_background',False),('close_behavior','TRAY')])
def test_changed_policy_requires_reconfirmation(field,value):
    api=PolicyAPI();chrome=MainWindowChrome(None,api);view=chrome.prepare_close(CLEAN)
    api.config[field]=value
    assert chrome.commit_close(dict(ticket=view['ticket'],choice='EXIT',edit=CLEAN))['status']=='RECONFIRM'
    assert api.calls==[]

@pytest.mark.parametrize('edit',[dict(CLEAN,dirty=True),dict(CLEAN,ready=False),dict(CLEAN,saving=True)])
def test_edits_cannot_be_silently_discarded(edit):
    api=PolicyAPI();chrome=MainWindowChrome(None,api);view=chrome.prepare_close(CLEAN)
    assert chrome.commit_close(dict(ticket=view['ticket'],choice='EXIT',edit=edit))['status'] in {'RECONFIRM','BUSY'}
    assert api.calls==[]

def test_policy_change_between_reads_requires_reconfirmation():
    api=PolicyAPI();chrome=MainWindowChrome(None,api);view=chrome.prepare_close(CLEAN)
    original=api.get_close_policy
    def raced(choice=None):
        if choice:api.config['active_job_count']=1
        return original(choice)
    api.get_close_policy=raced
    assert chrome.commit_close(dict(ticket=view['ticket'],choice='EXIT',edit=CLEAN))['status']=='RECONFIRM'
    assert api.calls==[]

def test_background_off_cannot_hide_active_tasks():
    api=PolicyAPI();api.config.update(active_job_count=2,keep_tasks_in_background=False)
    chrome=MainWindowChrome(None,api);view=chrome.prepare_close(CLEAN)
    assert chrome.commit_close(dict(ticket=view['ticket'],choice='HIDE',edit=CLEAN))['status']=='RECONFIRM'
    assert api.calls==[]

def test_unknown_policy_cannot_close():
    api=PolicyAPI();chrome=MainWindowChrome(None,api)
    api.get_close_policy=lambda *_:{'status':'ERROR'}
    with pytest.raises(ValueError):chrome.prepare_close(CLEAN)
    assert api.calls==[]

def test_geometry_scaled_and_invalid_geometry_atomic():
    chrome=MainWindowChrome(None,None)
    chrome.update_regions(dict(scale=1.5,drag=[[0,0,800,48]],exclude=[],maximize=[[740,0,780,48]]))
    expected=chrome.regions.copy()
    assert expected['drag']==[(0,0,1200,72)]
    for scale in (0,-1,True,float('nan'),float('inf'),'1'):
        with pytest.raises(ValueError):chrome.update_regions(dict(scale=scale,drag=[],exclude=[],maximize=[]))
        assert chrome.regions==expected
    with pytest.raises(ValueError):chrome.update_regions(dict(scale=1,drag=[[20,10,0,0]],exclude=[],maximize=[]))
    assert chrome.regions==expected

@pytest.mark.parametrize('x,y,expected',[(-1,50,10),(800,50,11),(50,-1,12),(50,600,15),
    (-1,-1,13),(800,-1,14),(-1,600,16),(800,600,17),(750,20,9),(10,20,1),(400,20,2),(400,200,1)])
def test_native_resize_caption_and_button_hit_testing(x,y,expected):
    regions=dict(drag=[(0,0,800,48)],exclude=[(0,0,50,48),(740,0,800,48)],maximize=[(740,0,780,48)])
    assert hit_test(x,y,800,600,8,False,regions)==expected

def test_maximized_frame_has_no_resize_strip():
    assert hit_test(-1,100,800,600,8,True,{})==1
