/* The user panel in settings and the user's name and picture in the navigation: user name, avatar, the answer style,
   and hiding the assistant's expressions (settings.user_get, settings.user_save). Takes part in the page's shared
   save and revert. Publishes window.__Desktop_LOCAL_USER__. */
(() => {
  'use strict';
  if(window.__Desktop_LOCAL_USER__)return;
  const defaults=()=>({username:'',avatar_png:'',hide_expressions:false,answer_style:'professional'});
  let saved=null,draft=defaults(),loading=false,decoding=false,ready=false;
  const q=selector=>document.querySelector(selector);
  const t=s=>window.__Desktop_INTEGRATED_APP__?.translate?.(s)||s;
  const call=(method,params={})=>window.__Desktop_SERVICE__.call(method,params);
  const config=()=>({...draft,username:q('#settings-username')?.value??draft.username});
  const dirty=()=>saved!==null&&JSON.stringify(config())!==JSON.stringify(saved.config);
  const sync=()=>window.__Desktop_SETTINGS_BRIDGE__?.syncSaveState();
  const status=s=>{
    const node=q('#settings-user-status');
    if(node)node.textContent=t(s);
  };
  function titles(){
    const name=(saved?.config.username||'').trim();
    const lang=document.documentElement.lang||'zh-CN';
    for(const node of document.querySelectorAll('.desktop-app-title')){
      const view=node.parentElement.querySelector('[id$="view-name"]');
      const suffix=view?.textContent.trim()?(lang.startsWith('ja')?'の':lang.startsWith('en')?'’s':'的'):'';
      const text=name?name+suffix:(window.__MEMO_PRODUCT_NAME__||'Memorive');
      node.dataset.desktopNoTranslate='true';
      node.dataset.personalized=String(Boolean(name));
      if(node.textContent!==text)node.textContent=text;
      if(node.title!==text)node.title=text;
    }
    navigationIdentity();
  }
  function navigationIdentity(){
    const value=saved?.config||defaults(),name=value.username.trim()||t('用户');
    for(const nav of document.querySelectorAll('.desktop-navigation')){
      let entry=nav.querySelector('.desktop-nav-user');
      if(!entry){
        entry=document.createElement('button');
        entry.type='button';
        entry.className='desktop-nav-user';
        entry.dataset.desktopNoTranslate='true';
        const avatar=document.createElement('span');
        avatar.className='desktop-nav-avatar';
        // Identity is page-independent; never inherit a settings-only class.
        const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
        svg.setAttribute('viewBox','0 0 24 24');
        svg.setAttribute('aria-hidden','true');
        svg.innerHTML='<circle cx="12" cy="7.5" r="3.5"/><path d="M4.5 20v-1.5a7.5 7.5 0 0 1 15 0V20Z"/>';
        avatar.append(svg);
        const img=document.createElement('img');
        img.alt='';
        img.hidden=true;
        avatar.append(img);
        const caption=document.createElement('span');
        caption.className='desktop-nav-username';
        entry.append(avatar,caption);
        nav.append(entry);
        entry.addEventListener('click',async()=>{
          await window.__Desktop_INTEGRATED_APP__?.activate('settings');
          if(q('[data-panel="user"]')?.hidden)q('[data-category="user"]')?.click();
        });
      }
      const caption=entry.querySelector('.desktop-nav-username'),
        image=entry.querySelector('img'),
        svg=entry.querySelector('svg');
      if(caption.textContent!==name)caption.textContent=name;
      const label=t('用户')+' · '+name;
      if(entry.getAttribute('aria-label')!==label)entry.setAttribute('aria-label',label);
      entry.removeAttribute('title');
      entry.removeAttribute('data-hint');
      entry.removeAttribute('data-tooltip');
      image.hidden=!value.avatar_png;
      if(svg)svg.style.display=value.avatar_png?'none':'';
      if(value.avatar_png){
        const uri='data:image/png;base64,'+value.avatar_png;
        if(image.getAttribute('src')!==uri)image.src=uri;
      }
      else image.removeAttribute('src');
    }
  }
  function presentation(value){
    document.documentElement.classList.toggle('desktop-hide-expressions',value.hide_expressions);
    document.documentElement.classList.remove('desktop-user-loading');
    window.DesktopDecorations?.applyHidden?.(value.hide_expressions);
    titles();
  }
  function render(){
    const name=q('#settings-username'),
      toggle=q('#settings-hide-expressions'),
      image=q('#settings-avatar-preview img'),
      icon=q('#settings-avatar-preview svg');
    if(name){
      name.value=draft.username;
      name.disabled=!saved;
    }
    for(const radio of document.querySelectorAll('[name=answer-style]')){
      radio.checked=radio.value===draft.answer_style;
      radio.disabled=!saved;
    }
    if(toggle){
      toggle.setAttribute('aria-checked',String(draft.hide_expressions));
      toggle.disabled=!saved;
    }
    if(image){
      image.hidden=!draft.avatar_png;
      if(draft.avatar_png)image.src='data:image/png;base64,'+draft.avatar_png;else image.removeAttribute('src');
    }
    if(icon){if(draft.avatar_png)icon.setAttribute('hidden','');else icon.removeAttribute('hidden');}
    if(q('#settings-avatar-upload'))q('#settings-avatar-upload').disabled=!saved||decoding;
    if(q('#settings-avatar-remove'))q('#settings-avatar-remove').disabled=!saved||!draft.avatar_png||decoding;
  }
  function validate(){
    const name=config().username;
    const valid=!decoding&&[...name].length<=64&&!/[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}]/u.test(name);
    q('#settings-username')?.setAttribute('aria-invalid',String(!valid));
    if(!valid)status(decoding?'正在处理头像':'用户名最多 64 个字符，不能包含控制字符');
    return valid;
  }
  // Check encoded dimensions before allocating a decoded bitmap.
  function imageSize(bytes,type){
    const v=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);
    if(type==='image/png'&&bytes.length>=24&&v.getUint32(0)===0x89504e47)return [v.getUint32(16),v.getUint32(20)];
    if(type==='image/jpeg'&&v.byteLength>4&&v.getUint16(0)===0xffd8){
      let p=2;
      while(p+4<=bytes.length){
        if(bytes[p++]!==255)break;
        while(bytes[p]===255)p++;
        const marker=bytes[p++];
        if(marker===0xd9||marker===0xda)break;
        if(marker===0x01||(marker>=0xd0&&marker<=0xd7))continue;
        const length=v.getUint16(p);
        if(length<2||p+length>bytes.length)break;
        if([0xc0,0xc1,0xc2,0xc3,0xc5,0xc6,0xc7,0xc9,0xca,0xcb,0xcd,0xce,0xcf].includes(marker)&&length>=7)return [v.getUint16(p+5),v.getUint16(p+3)];
        p+=length;
      }
    }
    if(type==='image/webp'&&bytes.length>=30&&v.getUint32(0)===0x52494646&&v.getUint32(8)===0x57454250){
      const kind=v.getUint32(12);
      if(kind===0x56503858)return [1+bytes[24]+bytes[25]*256+bytes[26]*65536,1+bytes[27]+bytes[28]*256+bytes[29]*65536];
      if(kind===0x56503820&&bytes[23]===0x9d&&bytes[24]===1&&bytes[25]===0x2a)return [v.getUint16(26,true)&16383,v.getUint16(28,true)&16383];
      if(kind===0x5650384c&&bytes[20]===0x2f){
        const n=v.getUint32(21,true);
        return [(n&16383)+1,((n>>>14)&16383)+1];
      }
    }
    throw new Error('AVATAR_FORMAT');
  }
  async function avatar(file){
    if(!file||!['image/png','image/jpeg','image/webp'].includes(file.type)||file.size>5*1024*1024)throw new Error('AVATAR_FORMAT_OR_SIZE');
    const bytes=new Uint8Array(await file.arrayBuffer()),[width,height]=imageSize(bytes,file.type);
    if(width<1||height<1||width>4096||height>4096||width*height>16*1024*1024)throw new Error('AVATAR_DIMENSIONS');
    const bitmap=await createImageBitmap(file);
    try{
      const canvas=document.createElement('canvas');
      canvas.width=256;
      canvas.height=256;
      const size=Math.min(bitmap.width,bitmap.height);
      canvas.getContext('2d').drawImage(bitmap,(bitmap.width-size)/2,(bitmap.height-size)/2,size,size,0,0,256,256);
      const result=canvas.toDataURL('image/png').split(',')[1];
      if(result.length>524288)throw new Error('AVATAR_PAYLOAD');
      return result;
    }finally{bitmap.close();}
  }
  async function load(){
    if(loading||!window.pywebview?.api?.call)return;
    loading=true;
    try{
      const result=await call('settings.user_get');
      saved=result;
      draft=structuredClone(result.config);
      render();
      presentation(draft);
      status('');
      sync();
    }catch(_){
      status('用户设置读取失败，请重新打开设置');
      presentation(defaults());
    }
    finally{loading=false;}
  }
  function initialize(){
    if(ready)return;
    ready=true;
    const bridge=window.__Desktop_SETTINGS_BRIDGE__;
    bridge?.registerSaveParticipant('local-user',{
      isDirty:dirty,validate,
      save:async()=>{
        const result=await call('settings.user_save',{config:config(),expected_revision:saved.revision});
        saved=result;
        draft=structuredClone(result.config);
        render();
        presentation(draft);
        status('已保存');
      },
      discard:async()=>{
        draft=structuredClone(saved.config);
        render();
        presentation(draft);
        status('');
      },
      afterUnlock:render
    });
    q('#settings-username')?.addEventListener('input',()=>{
      draft.username=q('#settings-username').value;
      validate();
      sync();
    });
    q('#settings-hide-expressions')?.addEventListener('click',event=>{
      event.preventDefault();
      draft.hide_expressions=!draft.hide_expressions;
      render();
      presentation(draft);
      sync();
    });
    for(const radio of document.querySelectorAll('[name=answer-style]'))radio.addEventListener('change',()=>{
      draft.answer_style=radio.value;
      sync();
    });
    q('#settings-avatar-upload')?.addEventListener('click',()=>q('#settings-avatar-file').click());
    q('#settings-avatar-remove')?.addEventListener('click',()=>{
      draft.avatar_png='';
      render();
      sync();
    });
    q('#settings-avatar-file')?.addEventListener('change',async event=>{
      const file=event.target.files?.[0];
      if(!file)return;
      decoding=true;
      render();
      sync();
      status('正在处理头像');
      try{
        draft.avatar_png=await avatar(file);
        status('');
      }
      catch(_){status('请选择 PNG、JPEG 或 WebP，最大 5 MB，单边不超过 4096 像素');}
      finally{
        decoding=false;
        event.target.value='';
        render();
        sync();
      }
    });
    const help=window.__Desktop_SETTINGS_CONTROLS__?.help;
    if(help&&q('#settings-username-label'))help(q('#settings-username-label'),'用户名','用于工作区标题；留空显示 Memorive。最多 64 个字符，仅保存在本机。');
    if(help&&q('#settings-avatar-label'))help(q('#settings-avatar-label'),'头像','显示在导航栏底部。上传后居中裁为 256×256，移除图片元数据；不会上传到外部服务。');
    const headerObserver=new MutationObserver(titles);
    for(const node of document.querySelectorAll('.desktop-app-title'))headerObserver.observe(node.parentElement,{childList:true,subtree:true,characterData:true});
    new MutationObserver(titles).observe(document.documentElement,{attributes:true,attributeFilter:['lang']});
    window.addEventListener('desktop:settings-locale-change',titles);
    window.addEventListener('pywebviewready',load,{once:true});
    q('[data-category="user"]')?.addEventListener('click',()=>{if(!saved)void load();});
    if(window.pywebview?.api?.call)void load();
    else if(!window.chrome?.webview)presentation(defaults());
  }
  window.__Desktop_LOCAL_USER__=Object.freeze({load,avatar,imageSize,presentation,titles,getState:()=>({saved,draft:config(),dirty:dirty(),decoding})});
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',initialize,{once:true});else initialize();
})();

