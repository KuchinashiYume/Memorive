/* Report mappings participate in the existing settings Save/Revert transaction. */
(()=>{
  if(Memorive.reportWorkflow)return;
  const clone=x=>JSON.parse(JSON.stringify(x));
  let saved=null,draft=null,loading=null;
  const call=(method,params={})=>Memorive.service.call(method,params);
  const dirty=()=>Boolean(saved&&JSON.stringify(saved.config)!==JSON.stringify(draft));
  const render=()=>Memorive.settingsView?.renderWorkflow();
  const sync=()=>Memorive.settingsBridge?.syncSaveState();
  const options=()=>(saved?.model_options||[]).map(row=>({profileRef:row.profile_ref,model:row.model,
    state:row.eligible?'green':'yellow',disabled:!row.eligible,note:row.eligible?'':'该模型尚未验证可用'}));
  async function load(force=false){
    if(loading)return loading;
    if(dirty()&&!force)return;
    loading=(async()=>{try{
      const data=await call('research.workflow_get');
      if(!dirty()||force){
        saved=data;
        draft=clone(data.config);
        render();
        sync();
      }
    }finally{loading=null;}})();
    return loading;
  }
  const validate=()=>Boolean(saved&&Object.values(draft).every(ref=>ref===null||saved.model_options.some(row=>row.profile_ref===ref&&row.eligible)));
  async function save(){
    if(!validate())throw new Error('REPORT_MODEL_UNAVAILABLE');
    const data=await call('research.workflow_save',{config:draft,expected_revision:saved.revision});
    saved=data;
    draft=clone(data.config);
    render();
    sync();
    return data;
  }
  function select(period,ref){
    if(!draft||!(period in draft))return;
    draft[period]=ref||null;
    sync();
  }
  Memorive.reportWorkflow=Object.freeze({load,save,select,options,inspect:()=>({saved,draft,dirty:dirty()})});
  function init(){
    Memorive.settingsBridge?.registerSaveParticipant('report-workflow',{isDirty:dirty,validate,save,discard:()=>load(true),afterUnlock:render});
    document.querySelector('[data-category="workflow"]')?.addEventListener('click',()=>load().catch(()=>{}));
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
})();

