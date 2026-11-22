/* Settings › 外观与辅助功能 › 表情包: the expression packs on this computer and the one in use; installing a pack from
   a folder, using one, removing one (a person's pack goes to the Recycle Bin; the kaomoji pack shipped with Memorive
   can be installed again), the folder of installed packs and the pack makers' guide. The window methods are in
   desktop/api/expression_packs.py; after a change the main window reads its faces again
   (shared/expression-pack.js reload) and the desk assistant redraws its own. Rows reuse the look of the Skill rows
   (routes/settings/skill-install.js). */
(() => {
 'use strict';
 const q=s=>document.querySelector(s),t=s=>Memorive.app?.translate?.(s)||s,api=()=>window.pywebview.api;
 // Why a folder is not a usable pack: the codes of memorive/desktop/settings/expression_packs.py.
 const REASONS={
   PACK_JSON_MISSING:'文件夹里没有 pack.json',
   PACK_JSON_INVALID:'pack.json 不是有效的 JSON',
   PACK_FORMAT_UNSUPPORTED:'pack.json 的 format 应为 MemoriveExpressionPack-v1',
   PACK_ID_INVALID:'id 只能用小写字母、数字和 . _ -，最长 64 个字符',
   PACK_FIELD_INVALID:'这一项写法不对',
   PACK_IDLE_MISSING:'没有写常驻表情 idle',
   PACK_LOOK_INVALID:'这个表情位写法不对',
   PACK_TEXT_TOO_LONG:'这个表情位的文字超过 40 个字',
   PACK_FILE_OUTSIDE:'图片不在表情包文件夹里',
   PACK_FILE_MISSING:'找不到这张图片',
   PACK_FILE_TYPE:'不支持这种图片格式',
   PACK_FILE_TOO_LARGE:'图片超过 16 MB',
   PACK_ID_TAKEN:'已有同一 id 的表情包',
   PACK_NOT_FOUND:'找不到这个表情包'
 };
 const reason=error=>t(REASONS[error.code]||'这个表情包不能用')+(error.detail?' · '+error.detail:'');
 const el=(tag,text,cls)=>{
   const n=document.createElement(tag);
   if(text!==null)n.textContent=t(text);
   if(cls)n.className=cls;
   return n;
 };
 const plain=text=>Object.assign(document.createElement('span'),{textContent:text});
 function status(text,error=false){
   const n=q('#settings-expression-pack-status');
   n.textContent=text;
   n.hidden=!text;
   n.dataset.error=String(error);
 }
 const fail=()=>status(t('没有完成，请重试'),true);
 function button(label,icon,name,action){
   const b=el('button',null,'icon-button desktop-toolbar-icon desktop-toolbar-soft desktop-action-'+icon);
   b.type='button';
   b.setAttribute('aria-label',t(label)+' · '+name);
   b.dataset.desktopTip=t(label);
   b.addEventListener('click',()=>Memorive.controls.busy(b,action).catch(fail));
   return b;
 }
 // The pack's resting face, in the pack's own text style.
 function preview(pack){
   const face=el('span',null,'desktop-expression-pack-preview');
   face.setAttribute('aria-hidden','true');
   if(pack.text_style?.font)face.style.setProperty('--expression-font',pack.text_style.font);
   if(pack.text_style?.color)face.style.setProperty('--expression-color',pack.text_style.color);
   Memorive.expressionPack.paint(face,pack.preview,pack.id);
   return face;
 }
 // A row: preview, name, who made it, version and state, and what can be done.
 function row(pack,actions){
   const r=el('div',null,'desktop-settings-control-row memo-dev-client desktop-expression-pack'),text=el('span',null),title=el('strong',null);
   title.textContent=pack.name||pack.folder;
   title.dataset.desktopNoTranslate='true';
   const about=el('small',null);
   if(pack.error){
     about.append(el('span',reason(pack.error)));
     about.dataset.error='true';
   }else{
     const parts=[pack.author,pack.version].filter(Boolean).map(plain);
     parts.forEach(part=>{part.dataset.desktopNoTranslate='true';});
     if(pack.builtin)parts.push(el('span','随 Memorive 提供'));
     if(!pack.installed)parts.push(el('span','未安装'));
     if(pack.active)parts.push(el('span','使用中'));
     parts.forEach((part,index)=>about.append(...(index?[' · ',part]:[part])));
   }
   text.append(title,about);
   const inline=el('span',null,'desktop-settings-inline');
   inline.append(...actions);
   r.dataset.packState=pack.error?'error':pack.active?'active':pack.installed?'installed':'removed';
   r.append(preview(pack),text,inline);
   return r;
 }
 async function changed(result,done){
   if(result.status==='REFUSED'){
     status(reason(result),true);
     return;
   }
   render(result);
   status(done);
   await Memorive.expressionPack.reload();
 }
 function render(state){
   const list=q('#settings-expression-packs');
   list.replaceChildren();
   for(const pack of state.packs){
     if(pack.error){
       list.append(row(pack,[]));
       continue;
     }
     const actions=[];
     if(!pack.installed)actions.push(button('安装','import',pack.name,async()=>changed(await api().expression_pack_use({id:pack.id}),t('已安装')+' · '+pack.name)));
     else{
       if(!pack.active)actions.push(button('使用','confirm',pack.name,async()=>changed(await api().expression_pack_use({id:pack.id}),t('使用中')+' · '+pack.name)));
       actions.push(button(pack.builtin?'卸载':'卸载（移到回收站）','trash',pack.name,
         async()=>changed(await api().expression_pack_remove({id:pack.id}),t('已卸载')+' · '+pack.name)));
     }
     list.append(row(pack,actions));
   }
   if(!state.packs.length)list.append(el('p','还没有表情包，可以从文件夹安装','memo-dev-empty'));
 }
 const load=async()=>render(await api().expression_packs_state());
 const init=()=>{
   if(!q('#settings-expression-packs'))return;
   q('#settings-expression-pack-install').addEventListener('click',()=>Memorive.controls.busy(q('#settings-expression-pack-install'),async()=>{
     const result=await api().expression_pack_install();
     if(result.status==='CANCELLED')return;
     const name=result.packs?.find(pack=>pack.id===result.installed)?.name||'';
     await changed(result,t('已安装')+' · '+name);
   }).catch(fail));
   q('#settings-expression-pack-folder').addEventListener('click',()=>api().expression_pack_open_folder().catch(fail));
   q('#settings-expression-pack-guide').addEventListener('click',()=>api().expression_pack_open_guide().catch(fail));
   q('#settings-expression-packs-details').addEventListener('toggle',()=>{if(q('#settings-expression-packs-details').open)load().catch(fail);});
 };
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
})();
