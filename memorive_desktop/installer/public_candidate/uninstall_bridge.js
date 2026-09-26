(() => {
  'use strict';
  const $=id=>document.getElementById(id),app=$('uninstallerWindow'),stage=$('previewStage'),primary=$('primaryAction'),secondary=$('secondaryAction'),closeButton=$('closeWindow');
  const views=['welcomeView','dataView','progressView','blockedView','failedView','completeView'];
  const state={view:'welcome',policy:'keep',checking:false,finalizing:false,progress:0,note:'',closed:false};
  let seq=0,busy=false;const pending=new Map();
  function call(action,args={}){return new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});window.chrome.webview.postMessage({id,action,args});});}
  function announce(text){$('liveRegion').textContent=text;}
  function focusHeading(){$('pageTitle').focus({preventScroll:true});}
  /* REFERENCE_RENDER_FUNCTIONS */
  async function showData(){try{await call('removalCheck');state.policy='keep';state.note='';show('data');}catch(e){showError(e,true);}}
  function showError(e,blocked=false){state.finalizing=false;show(blocked?'blocked':'failed');const v=$(blocked?'blockedView':'failedView');v.querySelector('.message-primary').textContent=blocked?'无法开始卸载':'Memorive 未能完全卸载。';let p=v.querySelector('.message-secondary');if(!p){p=document.createElement('p');p.className='message-secondary';v.append(p);}p.textContent=e.message;}
  async function start(){if(busy)return;try{const plan=await call('removalPlan',{policy:state.policy});if(state.policy==='delete'){const confirmed=await call('confirmDeletion',{hash:plan.hash});if(!confirmed){state.policy='keep';state.note='未确认删除；个人数据将保留。';reflectPolicy();return;}}busy=true;state.finalizing=false;setProgress(0);show('working');await call('remove',{hash:plan.hash,policy:state.policy});setProgress(100);state.finalizing=false;show('complete');}catch(e){if(e.message==='卸载已取消，程序和个人数据均未更改。'){state.policy='keep';state.note=e.message;show('data');}else showError(e);}finally{busy=false;call('uiState',{view:state.view}).catch(()=>{});}}
  async function close(){if(busy){if(!state.finalizing)await call('cancel');return;}await call('close');}
  primary.addEventListener('click',()=>{if(primary.disabled)return;if(state.view==='welcome'||state.view==='blocked'||state.view==='failed')showData();else if(state.view==='data')start();else if(state.view==='complete')close();});
  secondary.addEventListener('click',()=>{if(secondary.disabled)return;if(state.view==='data')show('welcome');else close();});
  closeButton.addEventListener('click',close);app.addEventListener('keydown',e=>{if(e.key==='Escape'){e.preventDefault();close();}});
  ['keepData','deleteData'].forEach(id=>$(id).addEventListener('change',()=>{if(state.view==='data'){state.policy=$('deleteData').checked?'delete':'keep';state.note='';reflectPolicy();}}));
  window.chrome.webview.addEventListener('message',event=>{const m=event.data;if(m.event==='progress'){state.finalizing=m.finalizing;setProgress(m.percent);render(false);$('progressLabel').textContent=m.message;return;}if(m.event==='closeRequest'){close();return;}const p=pending.get(m.id);if(!p)return;pending.delete(m.id);m.ok?p.resolve(m.value):p.reject(new Error(m.error));});
  call('init').then(()=>{render(false);return call('uiState',{view:'welcome'});}).catch(e=>showError(e,true));
})();
