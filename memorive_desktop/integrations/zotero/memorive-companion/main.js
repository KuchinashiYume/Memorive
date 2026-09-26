"use strict";
var Memorive={
 windows:new Map(),known:new Map(),sourceDocs:new Map(),menus:[],
 pref(key,value){const name="extensions.memorive."+key;if(value!==undefined)Zotero.Prefs.set(name,value,true);return Zotero.Prefs.get(name,true);},
 async init(info){
  Object.assign(this,info);Zotero.MemoriveCompanion=this;
  for(const target of ["main/library/item","main/menubar/tools"])this.menus.push(Zotero.MenuManager.registerMenu({menuID:"memo-"+target.replaceAll("/","-"),pluginID:info.id,target,menus:[
   {menuType:"menuitem",onShowing:(e,c)=>{c.menuElem.label="Memo 研究";},onCommand:()=>this.open(Zotero.getMainWindow())},
   {menuType:"menuitem",onShowing:(e,c)=>{c.menuElem.label="送往 Memo（预览）";},onCommand:()=>this.previewSelected(Zotero.getMainWindow()).catch(e=>this.error(e))}]}));
  this.observer=Zotero.Notifier.registerObserver({notify:async(event,type,ids)=>{
   if(type!=="item"||!["add","modify","delete","trash"].includes(event))return;
   const affected=new Set();for(const id of ids){for(const source of this.known.get(id)||[])affected.add(source);const item=Zotero.Items.get(id);if(item?.parentID)for(const source of this.known.get(item.parentID)||[])affected.add(source);}
   if(affected.size)try{await this.validateSources(affected);}catch(e){Zotero.debug("Memo source notification failed: "+e.message);}
  }},"item","memorive");
 },
 async invoke(method,params){
  const helper=this.pref("helper"),workspace=this.pref("workspace");if(!helper||!workspace)throw Error("请先在 Zotero 设置 → Memorive 连接中填写路径");
  const {Subprocess}=ChromeUtils.importESModule("resource://gre/modules/Subprocess.sys.mjs");
  const proc=await Subprocess.call({command:helper,arguments:["--memo-agent","--workspace",workspace,"--connector-call",method],stderr:"pipe"});
  const {setTimeout,clearTimeout}=ChromeUtils.importESModule("resource://gre/modules/Timer.sys.mjs"),timer=setTimeout(()=>proc.kill(),90000);
  const read=async pipe=>{let text="",chunk;while((chunk=await pipe.readString())){text+=chunk;if(text.length>8*1024*1024){proc.kill();throw Error("Memo 响应超出大小限制");}}return text;};
  try{await proc.stdin.write(JSON.stringify(params));await proc.stdin.close();const [out,err,result]=await Promise.all([read(proc.stdout),read(proc.stderr),proc.wait()]);
   if(result.exitCode)throw Error(err||"Memo 工具未完成");const value=JSON.parse(out);if(value.error)throw Error(value.error);return value;
  }finally{clearTimeout(timer);}
 },
 call(method,params={}){const connection_id=this.pref("connection");if(!connection_id)throw Error("请先选择 Memo 连接");return this.invoke("connector."+method,{connection_id,...params});},
 el(root,tag,text,cls){const e=root.ownerDocument.createElementNS("http://www.w3.org/1999/xhtml",tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;root.append(e);return e;},
 button(root,text,fn,cls){const b=this.el(root,"button",text,cls);b.onclick=async()=>{b.disabled=true;try{await fn();}catch(e){this.error(e,root.ownerDocument.defaultView);}finally{b.disabled=false;}};return b;},
 error(e,win=Zotero.getMainWindow()){const v=this.windows.get(win);if(v)v.status.textContent=e.message||String(e);else Zotero.alert(win,"Memo",e.message||String(e));},
 label(s){return ({COMPLETE:"已完成",PARTIAL:"部分完成",ERROR:"需要处理",QUEUED:"等待 Memo",RUNNING:"处理中",DISPATCHED:"已提交",INTERRUPTED:"已中断",active:"可检索",changed:"来源已变化",unavailable:"来源不可用"})[s]||s;},
 addToWindow(win){if(!win.ZoteroPane||win.document.getElementById("memo-z-style"))return;const link=this.el(win.document.documentElement,"link");link.id="memo-z-style";link.rel="stylesheet";link.href=this.rootURI+"style.css";},
 removeFromWindow(win){const v=this.windows.get(win);if(v){win.clearInterval(v.timer);v.panel.remove();this.windows.delete(win);}win.document.getElementById("memo-z-style")?.remove();},
 shutdown(){for(const w of Zotero.getMainWindows())this.removeFromWindow(w);if(this.observer)Zotero.Notifier.unregisterObserver(this.observer);for(const id of this.menus)Zotero.MenuManager.unregisterMenu(id);delete Zotero.MemoriveCompanion;},
 async selectedRecords(win){
  const parents=new Map();for(let i of win.ZoteroPane.getSelectedItems()){if(i.parentID)i=Zotero.Items.get(i.parentID);if(i?.isRegularItem())parents.set(i.id,i);}
  if(!parents.size)throw Error("请选择文献条目或其 PDF");return this.records(parents);
 },
 async records(parents){
  const rows=[];
  for(const i of parents.values()){
   if(i.libraryID!==Zotero.Libraries.userLibraryID)throw Error("本版支持个人文献库；群组库请导出书目与有权使用的附件");
   const base={item_key:i.key,library_id:String(i.libraryID),title:i.getField("title")||"未命名文献",authors:i.getCreators().map(a=>a.name||[a.firstName,a.lastName].filter(Boolean).join(" ")),year:i.getField("date")||"",doi:i.getField("DOI")||"",abstract:i.getField("abstractNote")||"",version:String(i.version||0)};
   const attachments=Zotero.Items.get(i.getAttachments()).filter(a=>a.isPDFAttachment());
   if(!attachments.length)rows.push({...base,source_key:i.key,annotations:[]});
   for(const a of attachments){const file=await a.getFilePathAsync();if(!file)throw Error("附件尚未下载："+a.getField("title"));
    const annotations=[];for(const n of a.getAnnotations()){let page=null;try{page=JSON.parse(n.annotationPosition).pageIndex+1;}catch(e){}annotations.push({id:n.key,text:n.annotationText||"",comment:n.annotationComment||"",page});}
    rows.push({...base,source_key:i.key+":"+a.key,attachment_key:a.key,path:file,annotations});
   }
  }return rows;
 },
 signature(r){return JSON.stringify([r.source_key,r.title,r.authors,r.year,r.doi,r.abstract,(r.path||"").replaceAll("\\","/"),[...(r.annotations||[])].sort((a,b)=>a.id.localeCompare(b.id)).map(a=>[a.id,a.text,a.comment,a.page])]);},
 bindSources(documents){
  this.known.clear();this.sourceDocs=new Map(documents.map(d=>[d.id,d]));const bind=(id,source)=>{if(!this.known.has(id))this.known.set(id,new Set());this.known.get(id).add(source);};
  for(const d of documents){const i=Zotero.Items.getByLibraryAndKey(Number(d.library_id),d.item_key);if(!i)continue;bind(i.id,d.id);for(const id of i.getAttachments()){bind(id,d.id);for(const a of Zotero.Items.get(id).getAnnotations?.()||[])bind(a.id,d.id);}}
 },
 async validateSources(ids){
  const changed=[];for(const id of ids){const d=this.sourceDocs.get(id);if(!d||d.state!=="active")continue;const parent=Zotero.Items.getByLibraryAndKey(Number(d.library_id),d.item_key);
   if(!parent||parent.deleted){changed.push(id);continue;}const rows=await this.records(new Map([[parent.id,parent]])),current=rows.find(r=>r.source_key===d.source_key);
   if(!current||this.signature(current)!==this.signature(d))changed.push(id);
  }if(changed.length){await this.call("source_changed",{source_ids:changed});for(const id of changed)this.sourceDocs.get(id).state="changed";}return changed;
 },
 async previewSelected(win){
  const items=await this.selectedRecords(win),p=await this.call("preview",{items}),v=await this.open(win);v.detail.replaceChildren();this.el(v.detail,"h3","预览选中文献");this.el(v.detail,"p","新增 "+p.counts.new+" · 更新 "+p.counts.updated+" · 未变化 "+p.counts.unchanged);const choices=[];
  for(const row of p.items){const label=this.el(v.detail,"label"),c=this.el(label,"input");c.type="checkbox";c.checked=true;this.el(label,"span",row.title);choices.push([c,row.source_id]);this.el(v.detail,"small",row.path||"仅书目，无可处理的 PDF");}
  this.button(v.detail,"确认导入",async()=>{const r=await this.call("commit",{preview_id:p.id,preview_hash:p.preview_hash,selected_ids:choices.filter(([c])=>c.checked).map(([,id])=>id)});v.detail.replaceChildren();await this.refresh(v);v.status.textContent=this.label(r.status);},"memo-primary");return p;
 },
 async open(win){
  if(this.windows.has(win))return this.windows.get(win);this.addToWindow(win);const panel=this.el(win.document.documentElement,"section",undefined,"memo-z-panel");panel.id="memo-z-panel";panel.setAttribute("aria-label","Memo 研究");
  const v={win,panel,threadId:null,lastChat:"",busy:false};this.windows.set(win,v);this.el(panel,"h2","Memo 研究");const bar=this.el(panel,"div");
  this.button(bar,"关闭",()=>{win.clearInterval(v.timer);panel.remove();this.windows.delete(win);});this.button(bar,"发送选中文献",()=>this.previewSelected(win));this.button(bar,"刷新",()=>this.refresh(v));
  v.status=this.el(panel,"p","","memo-status");v.detail=this.el(panel,"div");v.threads=this.el(panel,"select");v.threads.setAttribute("aria-label","共享 Memo 会话");v.threads.onchange=()=>{v.threadId=v.threads.value||null;v.lastChat="";this.refresh(v).catch(e=>this.error(e,win));};
  v.question=this.el(panel,"textarea");v.question.rows=3;v.question.placeholder="检索项目证据或简短提问";
  const ask=this.el(panel,"div");this.button(ask,"检索证据",()=>this.search(v));this.button(ask,"简短提问",async()=>{const a=await this.call("queue",{kind:"ask",question:v.question.value,thread_id:v.threadId,request_id:Zotero.Utilities.randomString(24)});v.lastAction=a.id;v.status.textContent="已提交，由 Memo 使用保存的问答设置执行";},"memo-primary");
  this.button(ask,"交给 Agent",async()=>{const a=await this.call("queue",{kind:"agent",question:v.question.value,thread_id:v.threadId,request_id:Zotero.Utilities.randomString(24)});v.lastAction=a.id;v.status.textContent="已提交，结果将回到同一会话";});
  v.results=this.el(panel,"div");v.chat=this.el(panel,"div");this.el(panel,"h3","文献与结果");v.docs=this.el(panel,"div");this.el(panel,"h3","导入与任务");v.jobs=this.el(panel,"div");
  try{await this.refresh(v);}catch(e){this.error(e,win);}v.timer=win.setInterval(()=>this.refresh(v).catch(e=>this.error(e,win)),3000);return v;
 },
 async refresh(v){
  if(v.busy)return;v.busy=true;
  try{let state=await this.call("state");this.bindSources(state.documents);if((await this.validateSources(this.sourceDocs.keys())).length)state=await this.call("state");v.status.textContent=state.desktop_online?"Memo 已连接":"资料可检索；问答和主线任务等待 Memo 打开";
   const action=state.actions.find(a=>a.id===v.lastAction);if(action?.result?.thread_id){v.threadId=action.result.thread_id;v.lastAction=null;}if(action?.error)v.status.textContent=action.error;
   const chats=await this.call("chat");v.threads.replaceChildren();this.el(v.threads,"option","新会话").value="";for(const t of chats.threads)this.el(v.threads,"option",t.title).value=t.id;v.threads.value=v.threadId||"";
   if(v.threadId){const t=await this.call("chat",{thread_id:v.threadId});if(v.lastChat!==String(t.revision)){v.chat.replaceChildren();for(const m of t.messages.slice(-8)){const card=this.el(v.chat,"div",undefined,"memo-z-card");this.el(card,"strong",m.role==="user"?"你":"Memo");this.el(card,"p",m.text);}v.lastChat=String(t.revision);}}
   v.docs.replaceChildren();for(const d of state.documents){
    const card=this.el(v.docs,"div",undefined,"memo-z-card");this.el(card,"strong",d.title);this.el(card,"small",this.label(d.state)+(d.processing?" · 主线："+d.processing.state:""));
    this.button(card,"结果 / 回写",()=>this.showOutputs(v,d));if(d.path&&!d.processing)this.button(card,"主线处理",()=>this.call("queue",{kind:"process",source_id:d.id,request_id:Zotero.Utilities.randomString(24)}));
    if(d.processing?.state==="FAILED")this.button(card,"只重试失败步骤",()=>this.call("queue",{kind:"process_retry",source_id:d.id,request_id:Zotero.Utilities.randomString(24)}));
   }
   v.jobs.replaceChildren();for(const j of state.jobs){const card=this.el(v.jobs,"div",undefined,"memo-z-card");this.el(card,"p",this.label(j.status)+" · "+j.steps.filter(s=>s.status==="COMPLETE").length+"/"+j.steps.length+" · "+(j.elapsed_ms||0)+" ms");for(const s of j.steps.filter(s=>s.error))this.el(card,"small",s.error);if(["ERROR","PARTIAL","INTERRUPTED"].includes(j.status))this.button(card,"重试未完成资料",async()=>{await this.call("retry",{job_id:j.id});await this.refresh(v);});}
   for(const a of state.actions.filter(a=>["ERROR","INTERRUPTED","QUEUED"].includes(a.status))){const card=this.el(v.jobs,"div",undefined,"memo-z-card");this.el(card,"p",this.label(a.status)+" · "+a.kind);if(a.error)this.el(card,"small",a.error);if(a.status!=="QUEUED")this.button(card,"重试此请求",()=>this.call("action_retry",{action_id:a.id}));}
  }finally{v.busy=false;}
 },
 async search(v){
  const r=await this.call("search",{query:v.question.value,limit:8});v.results.replaceChildren();for(const ref of r.results){const card=this.el(v.results,"div",undefined,"memo-z-card");this.el(card,"strong",ref.title);this.el(card,"p",ref.text);this.el(card,"small",ref.page?"第 "+ref.page+" 页":"第 "+ref.line_start+"–"+ref.line_end+" 行");
   this.button(card,"打开出处",async()=>{const x=await this.call("read",{evidence_id:ref.id,expected_hash:ref.content_hash});if(!x.source_uri?.startsWith("zotero://"))throw Error("此来源属于其他资料库，请在 Memo 中查看");Zotero.launchURL(x.source_uri);});
   this.button(card,"复制带出处引用",async()=>{const x=await this.call("read",{evidence_id:ref.id,expected_hash:ref.content_hash});Zotero.Utilities.Internal.copyTextToClipboard(x.text+"\n\n"+x.title+" · "+x.id+" · SHA256 "+x.content_hash+"\n"+(x.source_uri||""));v.status.textContent="引用已复制";});
  }v.status.textContent=r.results.length+" 条证据";
 },
 async showOutputs(v,d){
  const out=await this.call("outputs",{source_id:d.id});v.detail.replaceChildren();this.el(v.detail,"h3",d.title);const choices=[];
  for(const i of out.items){const label=this.el(v.detail,"label"),c=this.el(label,"input");c.type="checkbox";c.checked=true;this.el(label,"span",i.title+" · "+i.kind);choices.push([c,i.id]);this.el(v.detail,"pre",i.markdown);}
  const selected=()=>choices.filter(([c])=>c.checked).map(([,id])=>id);
  this.button(v.detail,"回写到专属子笔记",async()=>{const r=await this.writeResult(d,selected());if(r.status==="CONFLICT"){this.el(v.detail,"p","检测到结果笔记被修改，原内容已保留。");this.button(v.detail,"另存新的子笔记",async()=>{await this.writeResult(d,selected(),true);v.status.textContent="新结果笔记已创建";});}else{v.status.textContent="结果已回写";v.detail.replaceChildren();}},"memo-primary");
 },
 async writeResult(d,output_ids,saveAs=false){
  const parent=Zotero.Items.getByLibraryAndKey(Number(d.library_id),d.item_key);if(!parent)throw Error("原文献不存在");
  const pref="note."+this.pref("connection")+"."+d.id,stored=this.pref(pref);let note=!saveAs&&stored?Zotero.Items.getByLibraryAndKey(parent.libraryID,stored):null;
  if(note&&(!note.isNote()||note.parentID!==parent.id))throw Error("结果笔记归属发生变化，请另存");
  const fresh=!note;if(!note){note=new Zotero.Item("note");note.libraryID=parent.libraryID;note.parentID=parent.id;}
  const current=fresh?"":note.getNote(),external=fresh?parent.key+":new:"+Zotero.Utilities.randomString(24):note.key;
  const p=await this.call("writeback_prepare",{source_id:d.id,external_note_id:external,existing_text:current,output_ids,format:"html"});if(p.status==="CONFLICT")return p;
  if(!fresh&&note.getNote()!==current)throw Error("笔记刚刚被修改，请重试");
  await Zotero.DB.executeTransaction(async()=>{note.setNote(p.text);await note.save();});
  const written=note.getNote();await this.call("writeback_ack",{plan_id:p.id,text_hash:p.text_hash,host_text:written,host_note_id:note.key});this.pref(pref,note.key);return {status:"SYNCED",note_key:note.key,note_id:note.id};
 }
};
