/* Browser profiles share the settings save transaction and the existing editors. */
(() => {
  'use strict';
  const root=document.getElementById('desktop-settings-preview');
  const grid=root?.querySelector('.desktop-settings-browser-provider-grid');
  if(!grid)return;
  const bridge=()=>window.__Desktop_SETTINGS_BRIDGE__;
  const call=(method,...args)=>window.pywebview.api[method](...args);
  const el=(tag,text,cls)=>{
    const n=document.createElement(tag);
    if(text!=null)n.textContent=text;
    if(cls)n.className=cls;
    return n;
  };
  const text=(zh,en,ja)=>{
    const lang=document.querySelector('#settings-language')?.value||'zh-CN';
    return lang==='en-US'?en:lang==='ja-JP'?ja:zh;
  };
  const notify=message=>bridge()?.notify(message);
  let state=null,draft=[],baseline='',saving=false,busy=false,invalidCode=false,projection=null;
  const shape=rows=>rows.map((r,i)=>({slot:i+1,name:r.name,url:r.url,enabled:r.enabled,adapter:r.adapter,selectors:{...r.selectors}}));
  const dirty=()=>invalidCode||JSON.stringify(shape(draft))!==baseline;
  const section=el('section',null,'desktop-settings-refinement-section');
  section.id='memo-browser-sites';
  grid.closest('.desktop-settings-browser-setup').after(section);
  const head=el('header',null,'desktop-settings-refinement-section-head'),
    title=el('strong',text('网站','Websites','ウェブサイト'));
  const add=el('button',text('添加网页','Add website','ウェブサイトを追加'),'soft-button');
  add.type='button';
  add.id='memo-browser-site-add';
  add.classList.add('desktop-toolbar-icon','desktop-toolbar-soft','desktop-action-file-plus');
  add.setAttribute('aria-label',add.textContent);
  add.dataset.desktopTip=add.textContent;
  add.dataset.desktopPlacement='bottom';
  head.append(title,add);
  section.append(head,grid);
  const status=el('p',null,'desktop-settings-code-status');
  status.setAttribute('role','status');
  section.append(status);
  const work=el('section',null,'desktop-settings-code-workbench');
  work.id='memo-browser-code-workbench';
  work.hidden=true;
  const codeHead=el('header',null,'desktop-settings-code-head');
  codeHead.append(el('strong',text('网页读取配置','Website reading configuration','ウェブ読み取り設定')),el('span','JSON','badge'));
  const editor=el('div',null,'desktop-settings-code-editor'),
    lines=el('pre','1','desktop-settings-code-line-numbers'),
    stage=el('div',null,'desktop-settings-code-stage'),
    highlight=el('pre',null,'desktop-settings-code-highlight'),
    code=el('textarea',null,'desktop-settings-code-input');
  code.id='memo-browser-sites-code';
  code.spellcheck=false;
  code.setAttribute('aria-label',text('网页读取配置 JSON','Website configuration JSON','ウェブ設定 JSON'));
  lines.setAttribute('aria-hidden','true');
  highlight.setAttribute('aria-hidden','true');
  stage.append(highlight,code);
  editor.append(lines,stage);
  const actions=el('footer',null,'desktop-settings-code-actions'),
    format=el('button',text('格式化','Format','整形'),'soft-button');
  format.type='button';
  actions.append(format);
  work.append(codeHead,editor,actions);
  grid.after(work);
  const repaintCode=()=>bridge()?.renderCodeEditor({input:code,highlight,lines});
  function sync(){
    bridge()?.syncSaveState();
    status.textContent=invalidCode?text('JSON 格式或字段无效，修改仍保留','Invalid JSON or fields; edits retained','JSON または項目が無効です。編集は保持されています'):dirty()?text('未保存','Unsaved','未保存'):'';
  }
  function identify(raw){
    try{
      const url=new URL(raw);
      if(raw!==raw.trim()||/[\u0000-\u0020\u007f\\]/.test(raw)||url.protocol!=='https:'||url.username||url.password||url.port||/[%\u0080-\uffff]/.test(raw.split('/')[2]||'')||url.hostname.endsWith('.'))return null;
      return state?.official_site_catalog?.find(row=>row.hosts.includes(url.hostname));
    }catch(_){return null;}
  }
  const valid=()=>!invalidCode&&draft.length<=12&&draft.every(r=>typeof r.name==='string'&&r.name.trim()&&typeof r.enabled==='boolean'&&identify(r.url)&&r.selectors&&Object.values(r.selectors).every(v=>typeof v==='string'&&v.trim()));
  function mode(){
    const shell=root.querySelector('.desktop-settings-shell');
    return shell?.dataset.modeScope==='refinement'&&shell.dataset.settingsMode==='developer';
  }
  function renderMode(){
    const developer=mode()||invalidCode;
    grid.hidden=developer;
    work.hidden=!developer;
    add.hidden=developer;
    if(developer&&!invalidCode){
      code.value=JSON.stringify(shape(draft),null,2);
      repaintCode();
    }
  }
  function changed(){
    invalidCode=false;
    sync();
  }
  function build(){
    const openSlots=new Set([...grid.querySelectorAll('details[open]')].map(n=>n.dataset.slot));
    grid.replaceChildren();
    draft.forEach((site,index)=>{
      const card=el('details',null,'desktop-settings-browser-site desktop-settings-api-card');
      card.dataset.slot=String(index+1);
      card.open=openSlots.has(String(index+1))||!site.url;
      const header=el('summary'),
        titleBox=el('span'),
        nameLine=el('span',null,'desktop-settings-browser-site-title'),
        dot=el('i',null,'status-dot yellow'),
        nameText=el('strong',site.name||text('新网页','New website','新しいウェブサイト')),
        label=el('small','');
      nameLine.append(dot,nameText);
      titleBox.append(nameLine,label);
      const controls=el('span',null,'desktop-settings-source-head-actions'),toggle=el('button',null,'switch');
      toggle.type='button';
      toggle.setAttribute('role','switch');
      toggle.setAttribute('aria-label',text('启用 ','Enable ','有効化 ')+(site.name||text('新网页','new website','新しいウェブサイト')));
      toggle.setAttribute('aria-checked',String(site.enabled));
      toggle.addEventListener('click',event=>{
        event.preventDefault();
        event.stopPropagation();
        site.enabled=!site.enabled;
        toggle.setAttribute('aria-checked',String(site.enabled));
        changed();
        renderStatus();
      });
      controls.append(toggle,el('span','›','desktop-settings-disclosure-icon'));
      header.append(titleBox,controls);
      card.append(header);
      const fields=el('div',null,'desktop-settings-browser-site-fields desktop-settings-form-grid');
      for(const [key,caption,type] of [['name',text('名称','Name','名前'),'text'],['url',text('网址','URL','URL'),'url']]){
        const box=el('label',caption,'field-label'),input=el('input',null,'field');
        input.type=type;
        input.value=site[key];
        input.autocomplete='off';
        input.dataset.siteField=key;
        input.maxLength=key==='name'?60:512;
        box.append(input);
        fields.append(box);
        input.addEventListener('input',()=>{
          site[key]=input.value;
          if(key==='name')nameText.textContent=input.value||text('新网页','New website','新しいウェブサイト');else{
            const identity=identify(input.value);
            site.adapter=identity?.adapter||'generic';
            input.setAttribute('aria-invalid',String(!identity));
          }
          changed();
          renderStatus();
        });
      }
      const footer=el('div',null,'desktop-settings-source-actions'),
        open=el('button',text('打开网页','Open website','ウェブサイトを開く'),'soft-button'),
        remove=el('button',text('删除','Remove','削除'),'soft-button');
      open.type=remove.type='button';
      open.classList.add('desktop-toolbar-icon','desktop-toolbar-soft','desktop-action-external');
      open.setAttribute('aria-label',open.textContent);
      open.dataset.desktopTip=open.textContent;
      open.dataset.desktopPlacement='bottom';
      open.addEventListener('click',async event=>{
        event.preventDefault();
        event.stopPropagation();
        try{
          await call('open_browser_companion_site',site.url,root.querySelector('#settings-browser-companion-browser')?.value||null);
        }catch(_){
          notify(text('无法打开：请检查网址和浏览器选择','Unable to open: check the URL and selected browser','開けません。URL とブラウザーを確認してください'));
        }
      });
      remove.addEventListener('click',event=>{
        event.stopPropagation();
        draft.splice(index,1);
        build();
        changed();
      });
      controls.prepend(open);
      footer.append(remove);
      fields.append(footer);
      card.append(fields);
      grid.append(card);
    });
    renderStatus();
    renderMode();
    unlock();
  }
  function renderStatus(){draft.forEach((site,index)=>{
    const card=grid.querySelector(`[data-slot="${index+1}"]`);
    if(!card)return;
    const identity=identify(site.url),
      saved=state?.sites?.find(r=>r.url===site.url),
      row=projection?.provider_sync?.[saved?.provider_id]||{};
    const label=card.querySelector('small');
    label.textContent=!identity?text('仅支持通过网址筛选的模型官网','Only verified model websites are supported','URL 検証済みのモデル公式サイトのみ対応'):!site.enabled?text('已关闭','Disabled','無効'):busy?text('正在读取','Reading','読み取り中'):row.status==='COMPLETE'&&!Number(row.failed_count||0)?text(`已读取 ${Number(row.captured_count||0)} 条`,`Read ${Number(row.captured_count||0)} chats`,`${Number(row.captured_count||0)} 件読み取り済み`):row.error_code==='PROVIDER_HUMAN_VERIFICATION_REQUIRED'?text('请在网页完成验证','Complete verification on the website','ウェブサイトで認証してください'):['SESSION_INDEX_NODES_NOT_FOUND','SESSION_INDEX_NODES_NOT_VISIBLE','SESSION_INDEX_LINKS_NOT_FOUND'].includes(row.error_code)?text('历史列表未加载，请重试读取','History did not load; retry reading','履歴が未読み込みです。再試行してください'):['ERROR','FAILED','NOT_OPEN'].includes(row.status)||Number(row.failed_count||0)>0?text('读取未完成，请重试','Read incomplete; retry','読み取り未完了。再試行してください'):text('待连接／测试','Awaiting connection / test','接続・テスト待ち');
    card.dataset.state=!site.enabled?'disabled':!identity||(!busy&&(['ERROR','FAILED','NOT_OPEN'].includes(row.status)||Number(row.failed_count||0)>0))?'error':!busy&&row.status==='COMPLETE'?'complete':'pending';
    const dot=card.querySelector('.status-dot');
    dot.hidden=!site.enabled;
    dot.className='status-dot '+(card.dataset.state==='error'?'red':card.dataset.state==='complete'?'green':'yellow');
    label.title=row.error_code||'';
  });}
  function unlock(){
    section.querySelectorAll('input,textarea,button').forEach(n=>n.disabled=saving||busy);
    add.disabled=saving||busy||draft.length>=12;
  }
  add.addEventListener('click',event=>{
    event.stopPropagation();
    if(!state)return;
    draft.push({slot:draft.length+1,name:'',url:'',enabled:true,adapter:'generic',selectors:{path_prefix:'/chat/',history:'nav a[href], aside a[href]',user:'[data-message-author-role="user"], [data-role="user"]',assistant:'[data-message-author-role="assistant"], [data-role="assistant"]'}});
    build();
    changed();
    grid.lastElementChild.querySelector('input').focus();
  });
  code.addEventListener('input',()=>{
    try{
      const next=JSON.parse(code.value);
      if(!Array.isArray(next)||next.some(r=>!r||typeof r!=='object'||Array.isArray(r)||typeof r.name!=='string'||typeof r.url!=='string'||typeof r.enabled!=='boolean'||!r.selectors))throw Error();
      draft=shape(next);
      invalidCode=false;
    }catch(_){invalidCode=true;}
    repaintCode();
    sync();
  });
  code.addEventListener('scroll',()=>{
    highlight.scrollTop=code.scrollTop;
    highlight.scrollLeft=code.scrollLeft;
    lines.scrollTop=code.scrollTop;
  });
  format.addEventListener('click',()=>{
    if(invalidCode)return;
    code.value=JSON.stringify(shape(draft),null,2);
    repaintCode();
  });
  for(const event of ['change','input','click'])section.addEventListener(event,e=>e.stopPropagation());
  async function save(){
    if(saving)return;
    if(!valid())throw Error('BROWSER_SITE_FIELDS_INVALID');
    saving=true;
    unlock();
    try{
      await call('save_browser_companion_sites',shape(draft),state.revision);
      const latest=await call('browser_companion_status');
      state=null;
      baseline='';
      render(latest,false,true);
      window.dispatchEvent(new CustomEvent('desktop:browser-companion-updated',{detail:latest}));
    }
    catch(error){
      notify(text('网页设置未保存，请检查网址、读取规则或重新打开配置','Website settings not saved; check the URL, reading rules, or reload settings','ウェブ設定を保存できません。URL・読み取りルールを確認してください'));
      throw error;
    }
    finally{
      saving=false;
      unlock();
      sync();
    }
  }
  function render(value,isBusy,force=false){
    projection=value;
    busy=!!isBusy;
    const settings=value?.site_settings;
    if(!settings)return;
    if(!state||force||(!dirty()&&state.profile_sha256!==settings.profile_sha256)){
      state=settings;
      draft=shape(settings.sites);
      baseline=JSON.stringify(draft);
      invalidCode=false;
      build();
    }
    renderStatus();
    unlock();
  }
  bridge()?.registerSaveParticipant('browser-sites',{isDirty:()=>!!state&&dirty(),validate:valid,save,discard:async()=>render(await call('browser_companion_status'),false,true),afterUnlock:unlock});
  window.addEventListener('desktop:settings-mode-changed',()=>{if(!mode()&&!invalidCode)build();else renderMode();});
  window.addEventListener('desktop:settings-mode-scope-changed',renderMode);
  window.addEventListener('desktop:settings-locale-change',()=>{
    title.textContent=text('网站','Websites','ウェブサイト');
    const addLabel=text('添加网页','Add website','ウェブサイトを追加');
    add.textContent=addLabel;
    add.setAttribute('aria-label',addLabel);
    add.dataset.desktopTip=addLabel;
    codeHead.querySelector('strong').textContent=text('网页读取配置','Website reading configuration','ウェブ読み取り設定');
    code.setAttribute('aria-label',text('网页读取配置 JSON','Website configuration JSON','ウェブ設定 JSON'));
    format.textContent=text('格式化','Format','整形');
    build();
    sync();
  });
  window.__Desktop_BROWSER_SITES__=Object.freeze({render,isDirty:dirty});
})();

