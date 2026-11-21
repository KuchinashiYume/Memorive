/* Settings › Agent and connections › integration config and plugins: the memo-research Skill shipped
   with Memorive, installed into the agents' own skills folders (Claude Code, Codex, Cursor, Gemini CLI,
   GitHub Copilot, OpenCode, DeepSeek Harness, CodeBuddy) or into a folder the person chooses.
   Compatibility code: the window methods memo_skill_targets, memo_skill_install, memo_skill_pick_folder
   and memo_skill_forget (desktop/api/research_memo.py, research/workspace/skill_install.py) do the work;
   rows reuse the developer clients' look (routes/settings/developer-clients.js). */
(() => {
 'use strict';
 const q=s=>document.querySelector(s),t=s=>Memorive.app?.translate?.(s)||s,api=()=>window.pywebview.api;
 const STATES={missing:'未安装',current:'已是最新',different:'与 Memorive 自带的版本不同'};
 const el=(tag,text,cls)=>{
   const n=document.createElement(tag);
   if(text!==null)n.textContent=t(text);
   if(cls)n.className=cls;
   return n;
 };
 function status(text,error=false){
   const n=q('#memo-skill-status');
   n.textContent=t(text);
   n.hidden=!text;
   n.dataset.error=String(error);
 }
 const fail=()=>status('没有装好，请检查这个文件夹能否写入后重试',true);
 function button(label,icon,name,action){
   const b=el('button',null,'icon-button desktop-toolbar-icon desktop-toolbar-soft desktop-action-'+icon);
   b.type='button';
   b.setAttribute('aria-label',t(label)+' · '+name);
   b.dataset.desktopTip=t(label);
   b.addEventListener('click',()=>Memorive.controls.busy(b,action).catch(fail));
   return b;
 }
 // A row: the agent (or the chosen folder), where the Skill goes, its state and what can be done.
 function row(name,path,state,actions){
   const r=el('div',null,'desktop-settings-control-row memo-dev-client'),text=el('span',null),title=el('strong',null);
   title.textContent=name;
   title.dataset.desktopNoTranslate='true';
   const where=el('small',null);
   where.append(Object.assign(document.createElement('span'),{textContent:path}),' · ',el('span',STATES[state]));
   where.firstChild.dataset.desktopNoTranslate='true';
   text.append(title,where);
   const inline=el('span',null,'desktop-settings-inline');
   inline.append(...actions);
   r.dataset.skillState=state;
   r.append(text,inline);
   return r;
 }
 function render(data){
   const list=q('#memo-skill-targets');
   list.replaceChildren();
   const done=name=>result=>{render(result);status(t('已安装')+' · '+name);};
   for(const a of data.agents){
     const install=a.state==='current'?[]:[button(a.state==='missing'?'安装 Skill':'更新 Skill',a.state==='missing'?'import':'refresh',a.name,
       async()=>done(a.name)(await api().memo_skill_install({agent:a.id})))];
     list.append(row(a.name,a.path,a.state,install));
   }
   for(const f of data.folders){
     const actions=[];
     if(f.state!=='current')actions.push(button(f.state==='missing'?'安装 Skill':'更新 Skill',f.state==='missing'?'import':'refresh',f.folder,
       async()=>done(f.folder)(await api().memo_skill_install({folder:f.folder}))));
     actions.push(button('从列表移除','trash',f.folder,async()=>{render(await api().memo_skill_forget({folder:f.folder}));status('');}));
     list.append(row(f.folder,f.path,f.state,actions));
   }
   if(!data.agents.length&&!data.folders.length)list.append(el('p','这台电脑上没有找到支持的 Agent；可以安装到其他文件夹','memo-dev-empty'));
 }
 const load=async()=>render(await api().memo_skill_targets());
 const init=()=>{
   if(!q('#memo-skill-targets'))return;
   q('#memo-skill-pick').addEventListener('click',()=>Memorive.controls.busy(q('#memo-skill-pick'),async()=>{
     const result=await api().memo_skill_pick_folder();
     if(!result.path)return;
     render(result);
     status(t('已安装')+' · '+result.path);
   }).catch(fail));
   q('#memo-agent-config-details').addEventListener('toggle',()=>{if(q('#memo-agent-config-details').open)load().catch(fail);});
 };
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
})();
