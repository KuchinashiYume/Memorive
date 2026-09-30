"""Offline candidate EXE health: real IPC/controllers and hidden WebView2 shell."""
from pathlib import Path
import argparse,hashlib,json,logging,os,sys,threading,time,traceback

def main(root):
    p=argparse.ArgumentParser();p.add_argument('--evo-e2-health',type=Path,required=True);args=p.parse_args()
    target=args.evo_e2_health.resolve();target.mkdir(parents=True,exist_ok=False)
    result={'schema_version':'EVO-E2-ExecutableHealth-v1','status':'RUNNING','exe_sha256':hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
        'scope':'AUTHOR_MACHINE_EMPTY_PROFILE_REAL_IPC_AND_WEBVIEW2','model_semantic_quality':'NOT_ASSESSED','vm_validation':'NOT_RUN'}
    api=None;window=None;complete=threading.Event();result_path=target/'result.json';bridge_errors=[]
    class CaptureBridgeErrors(logging.Handler):
        def emit(self,record):
            if record.levelno>=logging.ERROR:bridge_errors.append(record.getMessage())
    capture=CaptureBridgeErrors();logging.getLogger('pywebview').addHandler(capture)
    def save():result_path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    try:
        from product_identity import BINDING
        if BINDING.get('package_id')!='EVO-E2-build009':raise ValueError('E2_HEALTH_PACKAGE_ID_MISMATCH')
        import uuid
        webview_storage=Path(os.environ.get('TEMP') or str(target)) / ('evo-e2-wv-'+uuid.uuid4().hex[:10]);webview_storage.mkdir()
        result['webview_storage']=str(webview_storage)
        for key,folder in [('APPDATA','appdata'),('LOCALAPPDATA','localappdata'),('TEMP','temp'),('TMP','temp')]:
            path=target/folder;path.mkdir(exist_ok=True);os.environ[key]=str(path)
        sources={name:target/'session-sources'/name for name in ('codex_home','claude_home')}
        for path in sources.values():path.mkdir(parents=True)
        from runtime import ProductApi
        api=ProductApi(target/'state',root/'source',root/'source/facade_method_binding.json',system_effects_enabled=False,
            session_source_roots=sources,session_visible_ui_reader={},test_fixture_mode=False)
        settings=api.call('settings.get_state',{})['settings'];nodes={n['node_id']:n for n in settings['workflow']['nodes']}
        assert nodes['data_review']['enabled'] is True and nodes['data_review']['profile_ref'] is None
        assert nodes['logic_review']['profile_ref'] is None
        assert api.call('review.list',{})==[]
        counts={method:len(api.call(method,{})[key]) for method,key in {'current_task.list':'tasks','messages.list':'rows','library.list_artifacts':'rows','work_log.list_entries':'rows'}.items()}
        assert not any(counts.values())
        result.update(settings_schema=settings['schema_version'],default_nodes=list(nodes),empty_counts=counts,ipc_review_methods='PASS')
        from .engine import analyze,evaluate
        from .exam import assets
        numeric=analyze('| unweighted count | denominator | percent (nearest) |\n| --- | --- | --- |\n| 8 | 20 | 40.0 |',source_id='packaged-local-health')
        assert numeric['color']=='GREEN' and numeric['external_model_calls']==0
        records={k:dict(record_id=k,raw=v,value=v,relation='',source={'kind':'RawMD'}) for k,v in [('statistic','2.00'),('p','0.046')]}
        tail=evaluate(dict(rule_id='tail',type='test_p',inputs={k:k for k in records},parameters=dict(rounding='nearest',distribution='normal',tail='two_sided',adjustment='none'),binding_source={'kind':'HEALTH_SYNTHETIC'}),records,checkpoint=lambda:None)
        assert tail['status']=='COMPATIBLE',tail
        _,pack,key,_=assets();assert len(pack['cases'])==len(key['answers'])==20
        result.update(packaged_numeric_engine='PASS',packaged_distribution_runtime='PASS',packaged_exam_members='HASH_VERIFIED_20_CASES')
        import webview
        webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER']=False
        window=webview.create_window('Memorive EVO-E2 local health',url=(root/'bundle_integrated.html').as_uri(),js_api=api,hidden=True,width=1600,height=1000,text_select=True)
        api.attach_window(window)
        from window_chrome import MainWindowChrome
        api._main_chrome=MainWindowChrome(window,api)
        window.events.before_show+=api._main_chrome.install
        def verify():
            try:
                deadline=time.monotonic()+45
                while time.monotonic()<deadline:
                    value=window.evaluate_js("({ready:!!window.__E2_REVIEW_UI__&&!!window.pywebview?.api?.call,data:!!document.querySelector('#settings-flow-list [data-step=\"data-review\"]'),logic:!!document.querySelector('#settings-flow-list [data-step=\"logic-review\"]')})")
                    if value and value.get('ready'):break
                    time.sleep(.2)
                assert value and value['ready'] and value['data'] and value['logic'],value
                window.evaluate_js("""window.__E2_NATIVE_HEALTH__={status:'RUNNING'};(async()=>{
                    const pages=[['settings','__Desktop_SETTINGS_BRIDGE__'],['inbox','__Desktop_INBOX_BRIDGE__'],['current-task','__Desktop_CURRENT_TASK_BRIDGE__'],['library','__Desktop_LIBRARY_BRIDGE__']];
                    for(const [route,bridge] of pages){window.__Desktop_INTEGRATED_APP__.activate(route);await window[bridge].bootstrap();}
                    const frame=await window.pywebview.api.get_window_state();
                    window.__E2_NATIVE_HEALTH__={status:'PASS',pages:pages.map(p=>p[0]),frame};
                })().catch(error=>{window.__E2_NATIVE_HEALTH__={status:'FAIL',error:String(error?.stack||error)};});void 0;""")
                deadline=time.monotonic()+45
                while time.monotonic()<deadline:
                    page_result=window.evaluate_js('window.__E2_NATIVE_HEALTH__')
                    if page_result and page_result.get('status')!='RUNNING':break
                    time.sleep(.2)
                assert page_result and page_result.get('status')=='PASS',page_result
                result['webview2']=value;result['native_pages']=page_result;result['effects']=api.call('settings.effect_metrics',{})
                assert result['effects']['external_model_calls']==0 and result['effects']['credential_value_reads']==0
                assert not bridge_errors,bridge_errors
                result['status']='PASS'
            except BaseException:result.update(status='FAIL',error=traceback.format_exc())
            finally:save();complete.set();window.destroy()
        save();webview.start(verify,gui='edgechromium',debug=False,http_server=False,private_mode=True,storage_path=str(webview_storage))
        if not complete.is_set():raise RuntimeError('E2_HEALTH_WINDOW_CLOSED_BEFORE_VERIFICATION')
    except BaseException:result.update(status='FAIL',error=traceback.format_exc())
    finally:
        if api is not None:
            try:api.close()
            except Exception:result.update(status='FAIL',close_error=traceback.format_exc())
        result['bridge_errors']=bridge_errors
        if bridge_errors:result['status']='FAIL'
        logging.getLogger('pywebview').removeHandler(capture)
        save()
    return 0 if result['status']=='PASS' else 1
