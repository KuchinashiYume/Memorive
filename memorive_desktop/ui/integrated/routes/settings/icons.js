/* User-supplied settings icons r0.3.3. SVG paths only; layout remains 60px. */
(() => {
  if(Memorive.settingsIcons)return;
  const shapes={"i-api":[{"tag":"circle","text":"","attributes":{"cx":"7.5","cy":"12","r":"3.25"}},{"tag":"path","text":"","attributes":{"d":"M10.75 12H20"}},{"tag":"path","text":"","attributes":{"d":"M15.8 12V9.2"}},{"tag":"path","text":"","attributes":{"d":"M18.7 12V9.2"}}],"i-cli":[{"tag":"path","text":"","attributes":{"d":"m6 7.5 4.5 4.5L6 16.5"}},{"tag":"path","text":"","attributes":{"d":"M13.5 16.5H19"}}],"i-local":[{"tag":"rect","text":"","attributes":{"x":"3.9","y":"7.2","width":"16.2","height":"9.6","rx":"1.5"}},{"tag":"circle","text":"","attributes":{"cx":"9","cy":"12","r":"2.15"}},{"tag":"circle","text":"","attributes":{"cx":"15","cy":"12","r":"2.15"}},{"tag":"path","text":"","attributes":{"d":"M20.1 9.6h1.4M20.1 12h1.4M20.1 14.4h1.4"}},{"tag":"path","text":"","attributes":{"d":"M6.2 16.8v1.6M8.5 16.8v1.6M10.8 16.8v1.6M13.1 16.8v1.6M15.4 16.8v1.6"}}],"i-flow":[{"tag":"rect","text":"","attributes":{"x":"9","y":"4.5","width":"6","height":"4","rx":"1"}},{"tag":"rect","text":"","attributes":{"x":"4","y":"15.5","width":"6","height":"4","rx":"1"}},{"tag":"rect","text":"","attributes":{"x":"14","y":"15.5","width":"6","height":"4","rx":"1"}},{"tag":"path","text":"","attributes":{"d":"M12 8.5v3.5"}},{"tag":"path","text":"","attributes":{"d":"M7 15.5V12h10v3.5"}}],"i-refine-c":[{"tag":"path","text":"","attributes":{"d":"M6.3 6.8h11.4"}},{"tag":"path","text":"","attributes":{"d":"M8.1 12h7.8"}},{"tag":"path","text":"","attributes":{"d":"M9.6 17.2h4.8"}},{"tag":"path","text":"","attributes":{"d":"m5.4 12 2.1-2.1"}},{"tag":"path","text":"","attributes":{"d":"m5.4 12 2.1 2.1"}},{"tag":"path","text":"","attributes":{"d":"m18.6 12-2.1-2.1"}},{"tag":"path","text":"","attributes":{"d":"m18.6 12-2.1 2.1"}}],"i-external":[{"tag":"path","text":"","attributes":{"d":"M7 17 17 7"}},{"tag":"path","text":"","attributes":{"d":"M10 7h7v7"}}],"i-source":[{"tag":"path","text":"","attributes":{"d":"M9.1 4.7v2.9M14.9 4.7v2.9"}},{"tag":"path","text":"","attributes":{"d":"M7.2 7.6h9.6v3.1a4.8 4.8 0 0 1-4.8 4.8 4.8 4.8 0 0 1-4.8-4.8Z"}},{"tag":"path","text":"","attributes":{"d":"M12 15.5v2.7"}},{"tag":"path","text":"","attributes":{"d":"M9.3 18.2h5.4"}}],"i-notify":[{"tag":"path","text":"","attributes":{"d":"M18 9a6 6 0 0 0-12 0c0 5-2 6.5-3 8h18c-1-1.5-3-3-3-8"}},{"tag":"path","text":"","attributes":{"d":"M10 20h4"}}],"i-behavior":[{"tag":"path","text":"","attributes":{"d":"M12.2 2h-.4a2 2 0 0 0-2 2v.2a2 2 0 0 1-1 1.7l-.4.3a2 2 0 0 1-2 0l-.2-.1a2 2 0 0 0-2.7.7l-.2.4A2 2 0 0 0 4 9.9l.2.1a2 2 0 0 1 1 1.7v.6a2 2 0 0 1-1 1.7l-.2.1a2 2 0 0 0-.7 2.7l.2.4a2 2 0 0 0 2.7.7l.2-.1a2 2 0 0 1 2 0l.4.3a2 2 0 0 1 1 1.7v.2a2 2 0 0 0 2 2h.4a2 2 0 0 0 2-2v-.2a2 2 0 0 1 1-1.7l.4-.3a2 2 0 0 1 2 0l.2.1a2 2 0 0 0 2.7-.7l.2-.4a2 2 0 0 0-.7-2.7l-.2-.1a2 2 0 0 1-1-1.7v-.6a2 2 0 0 1 1-1.7l.2-.1a2 2 0 0 0 .7-2.7l-.2-.4a2 2 0 0 0-2.7-.7l-.2.1a2 2 0 0 1-2 0l-.4-.3a2 2 0 0 1-1-1.7V4a2 2 0 0 0-2-2Z"}},{"tag":"circle","text":"","attributes":{"cx":"12","cy":"12","r":"3"}}],"i-network":[{"tag":"rect","text":"","attributes":{"x":"4.5","y":"5.5","width":"15","height":"5","rx":"1.4"}},{"tag":"rect","text":"","attributes":{"x":"4.5","y":"13.5","width":"15","height":"5","rx":"1.4"}},{"tag":"path","text":"","attributes":{"d":"M8 8h.01M8 16h.01"}},{"tag":"path","text":"","attributes":{"d":"M11 8h5.5M11 16h5.5"}}],"i-privacy":[{"tag":"path","text":"","attributes":{"d":"M12 3.4 18.6 6.2v5.2c0 4.2-2.6 7-6.6 8.8-4-1.8-6.6-4.6-6.6-8.8V6.2Z"}}],"i-appearance":[{"tag":"text","text":"A","attributes":{"x":"9.4","y":"12.2","font-size":"14.2"}},{"tag":"text","text":"a","attributes":{"x":"15.2","y":"12.95","font-size":"11.2"}}],"i-about":[{"tag":"circle","text":"","attributes":{"cx":"12","cy":"12","r":"7"}},{"tag":"path","text":"","attributes":{"d":"M12 10.5v5"}},{"tag":"path","text":"","attributes":{"d":"M12 8h.01"}}]};
  const mapping={api:'i-api',cli:'i-cli','local-models':'i-local',workflow:'i-flow',refinement:'i-refine-c',viewer:'i-external','external-data':'i-source',notice:'i-notify',behavior:'i-behavior',network:'i-network',privacy:'i-privacy',appearance:'i-appearance',update:'i-about'};
  function apply(){
    shapes['i-user']=[{tag:'circle',text:'',attributes:{cx:'12',cy:'7.5',r:'3.5'}},{tag:'path',text:'',attributes:{d:'M4.5 20v-1.5a7.5 7.5 0 0 1 15 0V20Z'}}];
    mapping.user='i-user';
    // Reuse the shipped search silhouette for the literature category.
    shapes['i-literature']=[{tag:'circle',text:'',attributes:{cx:'10.5',cy:'10.5',r:'6.5'}},{tag:'path',text:'',attributes:{d:'m16 16 4.5 4.5'}}];
    mapping.literature='i-literature';
    document.querySelectorAll('.desktop-settings-category').forEach(row=>{
      const holder=row.querySelector('.desktop-settings-cat-icon'),key=mapping[row.dataset.category];
      if(!holder||!key)return;
      const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
      svg.setAttribute('viewBox','0 0 24 24');
      svg.setAttribute('class','desktop-settings-icon');
      svg.setAttribute('aria-hidden','true');
      svg.setAttribute('data-settings-icon',key);
      for(const shape of shapes[key]){
        const child=document.createElementNS(svg.namespaceURI,shape.tag);
        for(const [k,v] of Object.entries(shape.attributes))child.setAttribute(k,v);
        if(shape.text)child.textContent=shape.text;
        svg.append(child);
      }
      holder.replaceChildren(svg);
    });
  }
  // Prefer the shared toolbar glyphs already shipped in Memorive. Only the
  // missing edit/confirm/cancel/export/preview shapes use the supplied r0.5 SVG.
  const actions={
    'settings-local-model-refresh':['refresh','发现本地模型'],
    'settings-local-model-add':['file-plus','添加本机服务'],
    'settings-add-api':['file-plus','添加 API 模型'],
    'settings-validate-draft':['save','安全保存新模型'],
    'settings-api-readiness-check':['search','检查调用准备'],
    'settings-browser-companion-setup':['sliders','配置只读桥接'],
    'memo-bridge-copy-folder':['copy','复制插件路径'],
    'memo-bridge-open-folder':['folder-open','打开插件文件夹'],
    'memo-bridge-check':['verify','检查插件连接'],
    'settings-browser-companion-test':['search','测试读取'],
    'settings-browser-companion-sync':['sync-incremental','增量同步'],
    'settings-browser-companion-full-sync':['sync-code','完整同步'],
    'settings-browser-companion-reload':['refresh','重新连接'],
    'settings-library-test':['search','检测文献库'],
    'settings-network-test':['search','检查网络'],
    'settings-notification-test':['notify','保存并发送测试通知'],
    'settings-open-log':['external','打开日志目录'],
    'settings-export-support':['export','导出诊断包'],
    'settings-export-settings':['export','导出设置'],
    'settings-import-settings':['import','导入设置'],
    'settings-shortcuts':['code','查看快捷键'],
    'settings-project-page':['external','在浏览器中打开'],
    'settings-adjust-model':['edit','调整模型'],
    'settings-adjust-fallback':['edit','调整备用模型'],
    'settings-node-test':['search','测试模型'],
    'settings-assistant-show':['preview','显示桌面助手'],
    'settings-assistant-hide':['close','隐藏桌面助手'],
    'settings-reset-layout':['restore','恢复默认布局'],
    'settings-exam-plan-cancel':['cancel','取消考试计划'],
    'settings-toast-close':['close','关闭提示']
  };
  const TEXT_ACTIONS={
    'settings-library-test':'检测文献库','settings-network-test':'检查网络',
    'settings-notification-test':'保存并发送测试通知',
    'settings-assistant-show':'显示桌面助手','settings-assistant-hide':'隐藏桌面助手',
    'settings-reset-layout':'恢复默认布局','settings-shortcuts':'查看快捷键'
  };
  function applyActions(){
    const root=document.getElementById('desktop-settings-preview');
    if(!root)return;
    const reusedRefreshClass='desktop-action-refresh';
    const sharedTrashClass='desktop-action-trash';
    for(const node of root.querySelectorAll('button')){
      if(node.getAttribute('role')==='switch')continue;
      if(TEXT_ACTIONS[node.id]){
        for(const cls of [...node.classList])if(cls.startsWith('desktop-action-')||['desktop-toolbar-icon','desktop-toolbar-soft','desktop-settings-action'].includes(cls))node.classList.remove(cls);
        node.classList.add('soft-button','desktop-settings-text-action');
        const label=Memorive.app?.translate?.(TEXT_ACTIONS[node.id])||TEXT_ACTIONS[node.id];
        node.dataset.desktopNoTranslate='';
        if(node.textContent!==label)node.textContent=label;
        if(node.getAttribute('aria-label')!==label)node.setAttribute('aria-label',label);
        node.removeAttribute('data-desktop-tip');
        node.removeAttribute('title');
        continue;
      }
      if(/^settings-(api|cli|workflow)-code-save$/.test(node.id)){
        node.hidden=true;
        node.setAttribute('aria-hidden','true');
        node.tabIndex=-1;
        continue;
      }
      let match=actions[node.id];
      if(node.matches('[data-validate-cli],[data-validate-api],[data-local-model-verify]'))match=['verify','验证模型'];
      const text=node.textContent.trim();
      if(text==='删除'||node.matches('[data-delete-model-service],[data-remove-cli-model],[data-delete-local-model],[data-research-delete-direction]'))match=['trash',node.getAttribute('aria-label')||'删除'];
      if(!match && /-code-format$/.test(node.id))match=['code','格式化代码'];
      if(!match && /-code-diff-toggle$/.test(node.id))match=['preview',text];
      if(!match && /-code-cancel$/.test(node.id))match=['cancel','取消代码修改'];
      if(!match && node.closest('[data-panel]')){
        if(['验证','再次验证','重新验证','测试','检测','保存并验证'].includes(text))match=['search',text];
        else if(['修改','编辑','调整'].includes(text))match=['edit',text];
        else if(['添加','＋ 添加','+ 添加'].includes(text))match=['file-plus',text];
        else if(['⟳','更新数据','刷新'].includes(text))match=['refresh',node.getAttribute('aria-label')||'更新数据'];
        else if(['打开','定位'].includes(text))match=['external',text];
      }
      if(!match)continue;
      if(match[0]==='search')match=['verify',match[1]];
      // Notification uses the existing message glyph, not a second bell asset.
      if(match[0]==='notify')match=['message',match[1]];
      for(const cls of [...node.classList])if(cls.startsWith('desktop-action-')&&cls!=='desktop-action-'+match[0])node.classList.remove(cls);
      node.classList.add('desktop-toolbar-icon','desktop-toolbar-soft','desktop-settings-action',match[0]==='trash'?sharedTrashClass:'desktop-action-'+match[0]);
      const label=Memorive.app?.translate?.(match[1])||match[1];
      if(node.getAttribute('aria-label')!==label)node.setAttribute('aria-label',label);
      if(node.dataset.desktopTip!==label)node.dataset.desktopTip=label;
      node.dataset.desktopPlacement='bottom';
      node.removeAttribute('title');
    }
  }
  Memorive.settingsIcons=Object.freeze({apply,applyActions,revision:'r0.3.3+actions-r0.5'});
  const root=document.getElementById('desktop-settings-preview');
  if(root)new MutationObserver(applyActions).observe(root,{childList:true,subtree:true});
  window.addEventListener('desktop:settings-locale-change',applyActions);
  applyActions();
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',apply,{once:true});else apply();
})();

