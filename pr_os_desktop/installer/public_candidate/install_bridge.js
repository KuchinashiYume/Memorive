(() => {
  'use strict';
  const $=(s,r=document)=>r.querySelector(s), $$=(s,r=document)=>Array.from(r.querySelectorAll(s));
  const app=$('#installerWindow'),stage=$('#previewStage'),viewName=$('#windowViewName'),liveRegion=$('#liveRegion'),toast=$('#toast'),tooltip=$('#tooltip'),detailDialog=$('#detailDialog'),cancelDialog=$('#cancelDialog'),customCliDialog=$('#customCliDialog');
  const clamp=(v,a,b)=>Math.min(b,Math.max(a,v));
  const escapeHtml=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let seq=0,facts={space:'正在读取安装清单'},failureMessage='',busy=false;
  const pending=new Map();
  function call(action,args={}) {return new Promise((resolve,reject)=>{
    const id=++seq,timeout=['browse','install','launch'].includes(action)?0:15000;
    const finish=(fn,value)=>{const p=pending.get(id);if(!p)return;clearTimeout(p.timer);pending.delete(id);fn(value);};
    const timer=timeout?setTimeout(()=>finish(reject,new Error('安装器未及时响应，请重试；若仍无响应，请关闭并重新打开安装器。')),timeout):null;
    pending.set(id,{resolve:value=>finish(resolve,value),reject:error=>finish(reject,error),timer});
    try{window.chrome.webview.postMessage({id,action,args});}catch(e){finish(reject,e);}
  });}
  const state={page:1,scenario:'ready',programPath:'',dataPath:'',shortcut:false,planRevision:1,installMode:'idle',installStep:0,progress:0,completed:[],customTools:[],customToolSequence:1};
  const builtInTools=[];
  const pageMeta={1:{view:'安装向导 · 欢迎'},2:{view:'安装向导 · 系统检查'},3:{view:'安装向导 · 安装位置'},4:{view:'安装向导 · 外部工具检测'},5:{view:'安装向导 · 准备安装'},6:{view:'安装向导 · 安装与完成'}};
  const installSteps=[['验证安装文件'],['准备必要组件'],['安装 Memorive'],['创建系统入口'],['首次启动检查']];
  function effectiveCheckScenario(){return facts.webview?'ready':'standard';}
  function updatePlanHash(){return '';} // Native installer rechecks and binds the exact package/paths.
  function invalidatePlan(){state.planRevision++;}
  function setPage(page){state.page=Number(page);app.dataset.page=String(page);$$('.wizard-page').forEach(n=>n.hidden=Number(n.dataset.page)!==state.page);viewName.textContent=pageMeta[state.page].view;renderStepRail();if(page===2)refreshChecks();if(page===4)refreshTools();if(page===5)renderPlan();if(page===6)renderInstallPage();$(`.wizard-page[data-page="${page}"] h2`)?.focus({preventScroll:true});call('uiState',{page:state.page,mode:state.installMode}).catch(()=>{});}
  /* REFERENCE_RENDER_FUNCTIONS */
  function renderChecks(){
    const checks=facts.checks||[];
    $('#checkList').innerHTML=checks.map(([status,name,result])=>`<div class="check-row compact-check-row status-${status}"><span class="status-symbol"><svg class="icon" aria-hidden="true"><use href="#${status==='pass'?'i-check':status==='error'?'i-x':'i-alert'}"></use></svg></span><span class="check-name">${escapeHtml(name)}</span><span class="check-result">${escapeHtml(result)}</span></div>`).join('');
    const errors=checks.some(r=>r[0]==='error'),warning=checks.some(r=>r[0]==='warning');state.scenario=errors?'blocked':'ready';
    $('#systemNext').disabled=errors;$('#checkSummary').textContent=errors?'无法安装':warning?'需要补充组件':'检查通过';
    const c=$('#checkCallout');c.className='compact-status '+(errors?'error':warning?'warning':'success');c.innerHTML=`<svg class="icon" aria-hidden="true"><use href="#${errors?'i-x':warning?'i-alert':'i-check-circle'}"></use></svg><span>${escapeHtml(errors?'当前系统不满足安装条件。':warning?'安装时补充所需组件。':'必要系统运行环境已满足；其余程序组件由安装包自带。')}</span>`;
    renderStepRail();
  }
  async function refreshChecks(){const b=$('#recheckSystem');b.disabled=true;try{Object.assign(facts,await call('checks',{root:$('#programPath').value}));renderChecks();}catch(e){showToast(e.message);}finally{b.disabled=false;}}
  async function refreshTools(){const b=$('#recheckTools');b.disabled=true;try{const rows=await call('tools',{custom:state.customTools});builtInTools.splice(0,builtInTools.length,...rows.filter(r=>r.support!=='CUSTOM'));state.customTools=rows.filter(r=>r.support==='CUSTOM');renderTools();}catch(e){showToast(e.message);}finally{b.disabled=false;}}
  function applyPaths(p){$('#programPath').value=p.root;$('#dataPath').value=p.data;facts.existing=p.existing;['programPath','dataPath'].forEach(id=>$('#'+id).readOnly=p.existing);$$('[data-browse]').forEach(b=>b.disabled=p.existing);$('#locationNote').textContent=p.existing?'检测到已有安装，本次更新或修复沿用原位置。更换程序位置须先卸载并保留数据，再重新安装并选择原数据目录；此安装器暂不支持数据迁移。':'首次安装：可输入完整路径，或浏览选择最终文件夹。程序与数据分开保存，卸载时默认保留数据。';validatePaths();invalidatePlan();}
  async function nextPage(n){if(n===4){if(!validatePaths())return;$('#locationNext').disabled=true;try{applyPaths(await call('paths',{root:state.programPath,data:state.dataPath}));}catch(e){const error=$('#pathError');error.textContent=e.message;error.hidden=false;error.scrollIntoView({block:'nearest'});return;}finally{$('#locationNext').disabled=false;}}setPage(n);}
  function openCancelDialog(){if(busy&&state.progress>=94)return;$('#cancelDialogSubtitle').textContent=busy?'将在当前安全步骤结束后停止；保留诊断记录。':'未开始安装时不会修改程序或用户数据。';$('#cancelDialogMessage').textContent=busy?'确定要停止安装吗？':'确定要退出安装向导吗？';if(!cancelDialog.open)cancelDialog.showModal();}
  async function confirmCancel(){const button=$('#confirmCancel');if(button.disabled)return;button.disabled=true;
    try{if(busy){await call('cancel');cancelDialog.close();}else await call('close');}
    catch(e){$('#cancelDialogMessage').textContent='退出未完成：'+e.message;}
    finally{button.disabled=false;}
  }
  async function runInstall(){if(busy||!validatePaths())return;busy=true;state.installMode='installing';state.installStep=0;state.completed=[];state.progress=0;setPage(6);try{await call('install',{root:state.programPath,data:state.dataPath,desktop:state.shortcut});state.installMode='complete';state.progress=100;renderInstallPage();renderStepRail();}catch(e){failureMessage=e.message;state.installMode='rolledback';renderInstallPage();}finally{busy=false;call('uiState',{page:6,mode:state.installMode}).catch(()=>{});}}
  function bindDynamicInstallEvents(){
    $('#finishWizard')?.addEventListener('click',async()=>{try{if($('#launchAfterFinish')?.checked)await call('launch');await call('close');}catch(e){showToast(e.message);}});
    $('#returnToPlan')?.addEventListener('click',()=>{state.installMode='idle';setPage(5);});
    $$('[data-action="cancel"]',$('#page6Footer')).forEach(b=>b.addEventListener('click',openCancelDialog));
    if(busy&&state.installMode==='installing'&&state.progress>=94)$$('button',$('#page6Footer')).forEach(b=>b.disabled=true);
  }
  async function addCustomCli(e){e.preventDefault();const name=$('#customCliName').value.trim(),command=$('#customCliCommand').value.trim(),error=$('#customCliError');if(!name||!command){error.textContent='请填写名称和命令或路径。';error.hidden=false;return;}if([...builtInTools,...state.customTools].some(t=>t.name.toLowerCase()===name.toLowerCase())){error.textContent='已经存在同名检测项。';error.hidden=false;return;}try{const t=await call('custom',{name,command});t.id='custom-'+state.customToolSequence++;state.customTools.push(t);customCliDialog.close();renderTools();}catch(e){error.textContent=e.message;error.hidden=false;}}
  window.chrome.webview.addEventListener('message',event=>{const m=event.data;if(m.event==='progress'){state.progress=m.percent;state.installStep=m.step;state.completed=m.completed;renderInstallPage();return;}if(m.event==='closeRequest'){openCancelDialog();return;}const p=pending.get(m.id);if(!p)return;m.ok?p.resolve(m.value):p.reject(new Error(m.error));});
  $$('[data-next]').forEach(b=>b.addEventListener('click',()=>nextPage(Number(b.dataset.next))));
  $$('[data-back]').forEach(b=>b.addEventListener('click',()=>setPage(Number(b.dataset.back))));
  $$('[data-action="cancel"]').forEach(b=>b.addEventListener('click',openCancelDialog));
  $$('[data-dialog]').forEach(b=>b.addEventListener('click',()=>openDetailDialog(b.dataset.dialog)));
  $('#recheckSystem').addEventListener('click',refreshChecks);$('#recheckTools').addEventListener('click',refreshTools);$('#addCustomCli').addEventListener('click',openCustomCliDialog);$('#startInstall').addEventListener('click',runInstall);$('#confirmCancel').addEventListener('click',confirmCancel);$('#customCliForm').addEventListener('submit',addCustomCli);
  $$('[data-close-dialog]').forEach(b=>b.addEventListener('click',()=>detailDialog.close()));$$('[data-close-cancel]').forEach(b=>b.addEventListener('click',()=>cancelDialog.close()));$$('[data-close-custom]').forEach(b=>b.addEventListener('click',()=>customCliDialog.close()));
  ['programPath','dataPath'].forEach(id=>$('#'+id).addEventListener('input',()=>{validatePaths();invalidatePlan();}));
  $('#desktopShortcut').addEventListener('change',e=>{state.shortcut=e.target.checked;invalidatePlan();});
  $$('[data-browse]').forEach(b=>b.addEventListener('click',async()=>{if(b.disabled)return;const id=b.dataset.browse==='program'?'programPath':'dataPath';b.disabled=true;try{const path=await call('browse',{kind:b.dataset.browse,current:$('#'+id).value});if(path){$('#'+id).value=path;validatePaths();invalidatePlan();call('uiState',{page:state.page,stage:'browse-selection',kind:b.dataset.browse}).catch(()=>{});}}catch(e){showToast(e.message);}finally{b.disabled=Boolean(facts.existing);$('#'+id).focus();}}));
  $('#closeInstallerWindow').addEventListener('click',openCancelDialog);
  document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!document.querySelector('dialog[open]'))openCancelDialog();});
  call('init').then(f=>{facts=f;applyPaths(f);state.shortcut=f.desktop;$('#desktopShortcut').checked=f.desktop;const note=document.createElement('span');note.className='footer-note';note.textContent='Memorive · v1.01';$('.wizard-page[data-page="1"] .footer-left').append(note);validatePaths();renderChecks();setPage(1);}).catch(e=>{failureMessage=e.message;state.installMode='rolledback';setPage(6);});
})();
