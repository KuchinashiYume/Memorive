/* The existing workflow model picker, parameterized for other settings without workflow writes. */
(() => {
  if (Memorive.modelPicker) return;
  function bind({button,menu,label=null,dot=null,onSelect=null,manageEvents=true}) {
    let options=[],renderSignature=null;
    const setOpen=open=>{
      const next=Boolean(open)&&!button.disabled;
      menu.dataset.open=String(next);
      menu.setAttribute('aria-hidden',String(!next));
      button.setAttribute('aria-expanded',String(next));
    };
    const render=(values,currentRef='')=>{
      const nextOptions=Array.isArray(values)?values:[];
      const signature=JSON.stringify([nextOptions,currentRef]);
      if(signature===renderSignature)return;
      renderSignature=signature;
      options=nextOptions;
      menu.replaceChildren();
      options.forEach((option,index)=>{
        const item=document.createElement('button');
        item.type='button';
        item.setAttribute('role','menuitemradio');
        item.setAttribute('aria-checked',String((option.profileRef||'')===currentRef));
        item.dataset.model=option.model;
        item.dataset.profileRef=option.profileRef||'';
        item.dataset.modelKey=option.modelKey||'';
        // Resolve test scores through the workflow's existing frozen/history binding.
        item.dataset.testScore='';
        item.dataset.state=option.state;
        item.dataset.pickerIndex=String(index);
        if(option.risk)item.dataset.risk='true';
        item.disabled=Boolean(option.disabled);
        const mark=document.createElement('span');
        mark.className='status-dot '+option.state;
        const name=document.createElement('span');
        name.textContent=option.model;
        const note=document.createElement('small');
        note.textContent=option.note;
        item.append(mark,name,note);
        menu.append(item);
      });
      const current=options.find(option=>(option.profileRef||'')===currentRef);
      if(label){
        label.textContent=current?.model||'—';
        label.title=current?.model||'';
      }
      if(dot)dot.className='status-dot '+(current?.state||'yellow');
    };
    const click=event=>{
      const item=event.target.closest('button[data-picker-index]');
      if(!item||item.disabled||!menu.contains(item))return;
      const option=options[Number(item.dataset.pickerIndex)];
      if(!option)return;
      setOpen(false);
      onSelect?.(option);
      button.focus();
    };
    const toggle=()=>setOpen(menu.dataset.open!=='true');
    const outside=event=>{if(!button.contains(event.target)&&!menu.contains(event.target))setOpen(false);};
    const keys=event=>{
      if(event.key==='Escape'){
        event.preventDefault();
        setOpen(false);
        button.focus();
        return;
      }
      if(event.key==='Tab'){
        setOpen(false);
        return;
      }
      if(!['ArrowDown','ArrowUp','Home','End'].includes(event.key))return;
      event.preventDefault();
      setOpen(true);
      const items=[...menu.querySelectorAll('button:not(:disabled)')];
      if(!items.length)return;
      const active=items.indexOf(document.activeElement);
      const next=event.key==='Home'?0:event.key==='End'?items.length-1:
        event.key==='ArrowUp'?(active<=0?items.length-1:active-1):(active+1)%items.length;
      items[next].focus();
    };
    if(manageEvents){
      button.addEventListener('click',toggle);
      button.addEventListener('keydown',keys);
      menu.addEventListener('click',click);
      menu.addEventListener('keydown',keys);
      document.addEventListener('pointerdown',outside);
    }
    return {render,setOpen,destroy(){
      setOpen(false);
      if(manageEvents){
        button.removeEventListener('click',toggle);
        button.removeEventListener('keydown',keys);
        menu.removeEventListener('click',click);
        menu.removeEventListener('keydown',keys);
        document.removeEventListener('pointerdown',outside);
      }
    }};
  }
  Memorive.modelPicker=Object.freeze({bind});
})();

  (() => {
    const root = document.getElementById('desktop-settings-preview');
    const preview = Memorive.frames.settings;
    if (!root || !preview) return;
    const q = (selector) => root.querySelector(selector);
    const qa = (selector) => [...root.querySelectorAll(selector)];
    const workarea = q('#settings-workarea');
    const categories = qa('.desktop-settings-category');
    const primary = q('#settings-primary');
    const primaryResizer = q('#settings-resize-primary');
    const nodePanel = q('#settings-node-settings');
    const pageSave = q('#settings-save');
    const shell = q('.desktop-settings-shell');
    const modelMenu = q('#settings-model-menu');
    const modelButton = q('#settings-adjust-model');
    const workflowModelPicker = Memorive.modelPicker.bind({button:modelButton,menu:modelMenu,manageEvents:false});
    const shortcutPopover = q('#settings-shortcut-popover');
    const flowList = q('#settings-flow-list');
    const flowStage = q('#settings-flow-stage');
    const bypassLayer = q('#settings-bypass-layer');
    const bypassTitle = q('#settings-bypass-title');
    const bypassRoutes = new Map(qa('[data-bypass-route]').map((route) => [route.dataset.bypassRoute, route]));
    const routeCopy = q('#settings-route-copy');
    const bypassWarning = q('#settings-node-bypass-warning');
    const bypassWarningCopy = q('#settings-node-bypass-copy');
    const workflowKind = q('#settings-workflow-kind');
    const workflowViewLabel = q('#settings-workflow-view-label');
    const workflowFlow = q('#settings-workflow-flow');
    const workflowNote = q('#settings-flow-note');
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    const motionMs = (value) => reducedMotion.matches ? 0 : Math.min(value,180);
    const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    const nextFrame = () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    let pageDirty = false;
    let pageError = false;
    let nodeError = false;
    let retryError = false;
    let networkError = false;
    let activeRisk = false;
    let selectedNode = null;
    let confirmedRetries = 2;
    let confirmedModel = '尚未映射已配置模型';
    let confirmedModelState = 'green';
    let retryTimer = 0;
    let modelTimer = 0;
    let categoryVersion = 0;
    let nodeVersion = 0;
    let apiDisclosureVersion = 0;
    let lastBypassKeys = new Set();
    let toastTimer = 0;
    let toastFadeTimer = 0;
    let toastVersion = 0;
    let activePrimaryResize = null;
    let rememberedCategoryPrimarySize = null;
    const categoryScrollPositions = new Map();

    const clampPaneSize = (value, min, max) => Math.min(max, Math.max(min, value));
    // Larger font sizes widen the category column; smaller ones keep the 100% width (its icons and padding stay).
    const fontScale = () => Math.max(1, Number.parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--desktop-font-factor')) || 1);
    const defaultCategoryPrimarySize = () => Math.round(
      Math.max(0, workarea.getBoundingClientRect().width - 260 * fontScale() - 12)
    );
    const getPrimarySize = () => {
      const inlineValue = Number.parseFloat(workarea.style.getPropertyValue('--desktop-settings-primary-size'));
      if (Number.isFinite(inlineValue)) return inlineValue;
      return defaultCategoryPrimarySize();
    };
    const getPrimaryLimits = () => {
      const width = workarea.getBoundingClientRect().width;
      const max = Math.max(96, width - 190 * fontScale() - 12);
      return { min: Math.min(360, max), max };
    };
    const preferredPrimarySize = () => {
      if (Number.isFinite(rememberedCategoryPrimarySize)) return rememberedCategoryPrimarySize;
      return defaultCategoryPrimarySize();
    };
    const updatePrimaryResizerAria = (limits, value) => {
      const rounded = Math.round(value);
      primaryResizer.setAttribute('aria-valuemin', String(Math.round(limits.min)));
      primaryResizer.setAttribute('aria-valuemax', String(Math.round(limits.max)));
      primaryResizer.setAttribute('aria-valuenow', String(rounded));
      primaryResizer.setAttribute('aria-valuetext', `${rounded} 像素`);
    };
    // When the detail panel would be narrower than 480px (times the font size), the category column shows its
    // icons only, as while a node is open (.is-compact shares the .has-node styles).
    const updateCompact = () => {
      const width = workarea.getBoundingClientRect().width;
      workarea.classList.toggle('is-compact', workarea.classList.contains('has-category') && width - 260 * fontScale() - 12 < 480 * fontScale());
    };
    const setPrimarySize = (value, announce = false, remember = true) => {
      updateCompact();
      const limits = getPrimaryLimits();
      const next = clampPaneSize(value, limits.min, limits.max);
      workarea.style.setProperty('--desktop-settings-primary-size', `${Math.round(next)}px`);
      if (remember) rememberedCategoryPrimarySize = next;
      updatePrimaryResizerAria(limits, next);
      if (announce) preview.announce(`设置详情边栏宽度 ${Math.round(next)} 像素`);
      return next;
    };
    const updatePrimaryResizerBounds = () => {
      const limits = getPrimaryLimits();
      const size = preferredPrimarySize();
      if (workarea.classList.contains('has-category')) setPrimarySize(size, false, false);
      else updatePrimaryResizerAria(limits, clampPaneSize(size, limits.min, limits.max));
    };
    const beginPrimaryResize = (event) => {
      if (event.button !== 0 || !workarea.classList.contains('has-category')) return;
      event.preventDefault();
      activePrimaryResize = event.pointerId;
      workarea.dataset.resizing = 'primary';
      primaryResizer.setPointerCapture(event.pointerId);
    };
    const movePrimaryResize = (event) => {
      if (activePrimaryResize !== event.pointerId) return;
      setPrimarySize(workarea.getBoundingClientRect().right - event.clientX);
    };
    const endPrimaryResize = (event) => {
      if (activePrimaryResize !== event.pointerId) return;
      if (primaryResizer.hasPointerCapture(event.pointerId)) primaryResizer.releasePointerCapture(event.pointerId);
      activePrimaryResize = null;
      delete workarea.dataset.resizing;
      setPrimarySize(getPrimarySize(), true);
    };
    const resizePrimaryWithKeyboard = (event) => {
      if (!workarea.classList.contains('has-category')) return;
      const limits = getPrimaryLimits();
      const current = getPrimarySize();
      let next = current;
      if (event.key === 'Home') next = limits.min;
      else if (event.key === 'End') next = limits.max;
      else if (event.key === 'ArrowLeft') next = current + 10;
      else if (event.key === 'ArrowRight') next = current - 10;
      else return;
      event.preventDefault();
      setPrimarySize(next, true);
    };

    primaryResizer.addEventListener('pointerdown', beginPrimaryResize);
    primaryResizer.addEventListener('pointermove', movePrimaryResize);
    primaryResizer.addEventListener('pointerup', endPrimaryResize);
    primaryResizer.addEventListener('pointercancel', endPrimaryResize);
    primaryResizer.addEventListener('keydown', resizePrimaryWithKeyboard);

    const workflowModels = {
      ingest: [
        { profileRef: 'PARSER_PROFILE', model: '[默认] 本地解析', state: 'green', note: '文档处理默认路径；可切换 API、CLI 或本地模型' }
      ],
      embedding: [
        { profileRef: '', model: '尚未选择向量嵌入模型', state: 'yellow', note: '只显示已验证的 API 或本地向量嵌入模型', disabled: true }
      ],
      card: [
        { profileRef: 'DISTILLATION_PROFILE', model: '尚未映射已配置模型', state: 'yellow', note: '请从已保存的 API 或 CLI 模型中选择' }
      ],
      'card-review': [
        { profileRef: 'AUTO_HETEROGENEOUS', model: '按蒸馏模型自动选择异源校核者', state: 'yellow', note: '必须先配置至少两个异源模型', disabled: true }
      ],
      admission: [
        { profileRef: '', model: '确定性状态机 / 不调用模型', state: 'green', note: '只读状态门', disabled: true }
      ],
      context: [
        { profileRef: '', model: '—', state: 'yellow', note: '请先添加可用重排模型', disabled: true }
      ],
      analysis: [
        { profileRef: 'ANALYSIS_PROFILE', model: '尚未映射已配置模型', state: 'yellow', note: '请从已保存的 API 或 CLI 模型中选择' }
      ],
      'judgment-review': [
        { profileRef: 'AUTO_HETEROGENEOUS', model: '按 Analysis 生产者自动选择异源校核者', state: 'yellow', note: '必须先配置至少两个异源模型', disabled: true }
      ],
      human: [
        { profileRef: '', model: '人类判断 / 不调用模型', state: 'green', note: '人工终点', disabled: true }
      ]
    };
    const heterogeneousProducerStep = Object.freeze({
      'card-review': 'card',
      'judgment-review': 'analysis'
    });
    const requiredCapabilityByStep = Object.freeze({
      ingest: ['CHAT', 'VISION_OCR'],
      card: ['CHAT'],
      'card-review': ['CHAT'],
      embedding: ['EMBEDDING'],
      context: ['CHAT', 'RERANKER'],
      analysis: ['CHAT'],
      'judgment-review': ['CHAT']
    });
    const exclusiveBuiltInSteps = new Set(['ingest']);
    let configuredModelOptions = [];
    let refinementModelOptions = [];
    let refinementProfileRef = '';
    let workflowView = 'document';
    const workflowViews = ['document', 'refinement', 'daily', 'weekly', 'monthly'];
    const reportView = () => ['daily', 'weekly', 'monthly'].includes(workflowView);
    const flowLabels = {document:'文献处理流程',refinement:'会话精炼流程',daily:'日报流程',weekly:'周报流程',monthly:'月报流程'};
    const flowText = text => Memorive.app?.translate?.(text) || text;
    const workflowSteps = [...flowList.querySelectorAll('.desktop-settings-flow-step')];
    const captureDocumentWorkflow = () => workflowSteps.map((step) => {
      const node = step.querySelector('.desktop-settings-node');
      return {
        stepClass:step.className,
        stepHidden:step.hidden,
        stepDataset:{ ...step.dataset },
        nodeDataset:{ ...node.dataset },
        nodeHtml:node.innerHTML
      };
    });
    let documentWorkflowSnapshots = captureDocumentWorkflow();
    const refinementDefinitions = Object.freeze([
      {
        nodeId:'01_SESSION_SELECTION_FREEZE', number:'01', name:'准备会话',
        purpose:'确认所选会话并生成只读内容快照', model:'[默认] 本地快照'
      },
      {
        nodeId:'02_DIALOGUE_CLEANUP', number:'02', name:'对话清理',
        purpose:'清理空白与重复结构，保留原始语义', model:'[默认] 本地清理'
      },
      {
        nodeId:'03_REFINEMENT_MODEL_EXECUTION', number:'03', name:'精炼',
        purpose:'调用已保存的精炼模型生成结构化结果', model:'已配置精炼模型', modelChange:true
      },
      {
        nodeId:'04_RESULT_VERIFY_PERSIST', number:'04', name:'检查并保存',
        purpose:'核对精炼结果与执行回执，并保存为待复核草稿', model:'本地校核'
      }
    ]);

    const enhancedSelects = [];

    const clearToastTimers = () => {
      clearTimeout(toastTimer);
      clearTimeout(toastFadeTimer);
      toastTimer = 0;
      toastFadeTimer = 0;
    };
    const hideToast = ({ animate = true } = {}) => {
      const toast = q('#settings-toast');
      clearToastTimers();
      const version = ++toastVersion;
      if (toast.hidden) return;
      if (!animate || reducedMotion.matches) {
        toast.hidden = true;
        toast.classList.remove('is-fading');
        return;
      }
      toast.classList.add('is-fading');
      toastFadeTimer = setTimeout(() => {
        if (version !== toastVersion) return;
        toast.hidden = true;
        toast.classList.remove('is-fading');
        toastFadeTimer = 0;
      }, 280);
    };
    const showToast = (copy) => {
      const toast = q('#settings-toast');
      clearToastTimers();
      const version = ++toastVersion;
      toast.querySelector('span').textContent = copy;
      toast.classList.remove('is-fading');
      toast.hidden = false;
      toastTimer = setTimeout(() => {
        if (version === toastVersion) hideToast({ animate: true });
      }, 5000);
    };
    const refreshPageError = () => {
      pageError = nodeError || retryError || networkError;
      updatePageSave();
    };
    const updatePageSave = () => {
      pageSave.disabled = !pageDirty || pageError || activeRisk;
      const reason = pageError ? '存在红色配置错误' : activeRisk ? '风险变更仍待确认' : !pageDirty ? '无待保存修改' : '保存全部已确认修改';
      pageSave.dataset.desktopTip = reason;
    };
    const markDirty = () => {
      pageDirty = true;
      updatePageSave();
    };
    const setNodeDirty = (dirty = true) => {
      if (!dirty) return;
      if (selectedNode) {
        selectedNode.dataset.model = confirmedModel;
        const modelValue = selectedNode.querySelector('[data-node-model]');
        if (modelValue) modelValue.textContent = confirmedModel;
      }
      markDirty();
    };
    const updateRiskState = () => {
      activeRisk = Boolean(retryTimer || modelTimer);
      updatePageSave();
    };
    const stopRetryRisk = (restore = true) => {
      clearInterval(retryTimer);
      retryTimer = 0;
      q('#settings-retry-risk').hidden = true;
      if (restore) q('#settings-retries').value = String(confirmedRetries);
      updateRiskState();
    };
    const stopModelRisk = (restore = true) => {
      clearInterval(modelTimer);
      modelTimer = 0;
      q('#settings-model-risk').hidden = true;
      if (restore) {
        q('#settings-current-model').textContent = confirmedModel.startsWith('尚未') ? '—' : confirmedModel;
        q('#settings-model-dot').className = 'status-dot ' + confirmedModelState;
      }
      updateRiskState();
    };
    const cancelRisks = () => {
      stopRetryRisk(true);
      stopModelRisk(true);
    };
    const startCountdown = (kind, onExpire) => {
      let left = 10;
      const output = q(kind === 'retry' ? '#settings-retry-count' : '#settings-model-count');
      output.textContent = '10 秒';
      const timer = setInterval(() => {
        left -= 1;
        output.textContent = left + ' 秒';
        if (left <= 0) {
          clearInterval(timer);
          onExpire();
        }
      }, 1000);
      if (kind === 'retry') retryTimer = timer; else modelTimer = timer;
      updateRiskState();
    };

    const setModelMenu = (open) => {
      workflowModelPicker.setOpen(open);
    };

    const setShortcutPopover = (open) => {
      const next = Boolean(open);
      shortcutPopover.dataset.open = String(next);
      shortcutPopover.setAttribute('aria-hidden', String(!next));
      q('#settings-shortcuts').setAttribute('aria-expanded', String(next));
    };

    const setSelectOpen = (entry, open) => {
      const next = Boolean(open) && !entry.trigger.disabled;
      if (next) {
        enhancedSelects.forEach((other) => { if (other !== entry) setSelectOpen(other, false); });
        setModelMenu(false);
        setShortcutPopover(false);
        const boundary = entry.shell.closest('.desktop-settings-primary, .desktop-settings-node-settings') || root;
        const triggerRect = entry.trigger.getBoundingClientRect();
        const boundaryRect = boundary.getBoundingClientRect();
        const menuHeight = Math.min(entry.menu.scrollHeight || 220, 220);
        const below = boundaryRect.bottom - triggerRect.bottom;
        const above = triggerRect.top - boundaryRect.top;
        entry.shell.dataset.placement = below < menuHeight + 8 && above > below ? 'top' : 'bottom';
        if(entry.select.closest('[id="desktop-bottom-panel"]')){
          const height=Math.min(260,entry.menu.scrollHeight||220,innerHeight-24);
          const width=Math.min(innerWidth-24,entry.select.id==='desktop-accounting-model'?Math.max(triggerRect.width,560):Math.max(triggerRect.width,180));
          entry.menu.style.width=width+'px';
          entry.menu.style.maxHeight=height+'px';
          entry.menu.style.left=Math.max(12,Math.min(triggerRect.left,innerWidth-width-12))+'px';
          entry.menu.style.top=Math.max(12,triggerRect.top>height+12?triggerRect.top-height-6:Math.min(triggerRect.bottom+6,innerHeight-height-12))+'px';
        }
      }
      entry.shell.dataset.open = String(next);
      entry.trigger.setAttribute('aria-expanded', String(next));
      entry.menu.setAttribute('aria-hidden', String(!next));
    };

    const enhanceSelect = (select, index) => {
      const parent = select.parentNode;
      const fieldLabel = select.closest('.field-label');
      const explicitName = fieldLabel?.querySelector('.desktop-settings-field-name')?.textContent.trim();
      const ownText = fieldLabel ? [...fieldLabel.childNodes].filter((node) => node.nodeType === Node.TEXT_NODE).map((node) => node.textContent.trim()).filter(Boolean).join(' ') : '';
      const accessibleName = select.getAttribute('aria-label') || explicitName || ownText || '选择选项';
      const nativeId = select.id || `settings-native-select-${index + 1}`;
      select.id = nativeId;
      const shellElement = document.createElement('div');
      shellElement.className = 'desktop-settings-select-shell';
      shellElement.dataset.open = 'false';
      shellElement.dataset.placement = 'bottom';
      const preserveOptionText = select.hasAttribute('data-desktop-no-translate');
      parent.insertBefore(shellElement, select);
      shellElement.appendChild(select);
      select.classList.add('desktop-settings-native-select');
      select.tabIndex = -1;
      select.setAttribute('aria-hidden', 'true');

      const trigger = document.createElement('button');
      trigger.className = 'desktop-settings-select-trigger';
      trigger.type = 'button';
      trigger.setAttribute('aria-haspopup', 'listbox');
      trigger.setAttribute('aria-expanded', 'false');
      trigger.setAttribute('aria-label', accessibleName);
      const triggerValue = document.createElement('span');
      if(preserveOptionText)triggerValue.setAttribute('data-desktop-no-translate','');
      trigger.appendChild(triggerValue);

      const menu = document.createElement('div');
      menu.className = 'desktop-settings-select-menu';
      menu.id = `${nativeId}-${index}-menu`;
      menu.setAttribute('role', 'listbox');
      menu.setAttribute('aria-label', accessibleName);
      menu.setAttribute('aria-hidden', 'true');
      trigger.setAttribute('aria-controls', menu.id);
      shellElement.append(trigger, menu);

      const entry = { select, shell: shellElement, trigger, triggerValue, menu, rebuild: null };
      const rebuild = () => {
        trigger.disabled = select.disabled;
        triggerValue.textContent = select.selectedOptions[0]?.dataset.memoLabel || select.selectedOptions[0]?.textContent || '请选择';
        menu.replaceChildren();
        [...select.options].forEach((option, optionIndex) => {
          const button = document.createElement('button');
          button.type = 'button';
          button.setAttribute('role', 'option');
          button.setAttribute('aria-selected', String(optionIndex === select.selectedIndex));
          button.disabled = option.disabled || Boolean(option.parentElement?.disabled);
          const group=option.parentElement;
          if(select.closest('[id="desktop-bottom-panel"]')&&group?.tagName==='OPTGROUP'&&option===group.firstElementChild){
            const label=document.createElement('div');
            label.className='memo-select-group';
            label.textContent=group.label;
            menu.append(label);
          }
          button.dataset.index = String(optionIndex);
          button.textContent = option.dataset.memoLabel || option.textContent;
          if(preserveOptionText)button.setAttribute('data-desktop-no-translate','');
          button.addEventListener('click', () => {
            if (button.disabled) return;
            select.selectedIndex = optionIndex;
            rebuild();
            select.dispatchEvent(new Event('change', { bubbles: true }));
            setSelectOpen(entry, false);
            trigger.focus();
          });
          menu.appendChild(button);
        });
      };
      entry.rebuild = rebuild;
      enhancedSelects.push(entry);

      trigger.addEventListener('click', () => setSelectOpen(entry, shellElement.dataset.open !== 'true'));
      trigger.addEventListener('keydown', (event) => {
        if(event.key==='Escape'){
          setSelectOpen(entry,false);
          return;
        }
        if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        setSelectOpen(entry, true);
        const buttons = [...menu.querySelectorAll('button:not(:disabled)')];
        const selected = menu.querySelector('button[aria-selected="true"]:not(:disabled)');
        const target = event.key === 'ArrowUp' || event.key === 'End' ? buttons.at(-1) : selected || buttons[0];
        target?.focus();
      });
      menu.addEventListener('keydown', (event) => {
        const buttons = [...menu.querySelectorAll('button:not(:disabled)')];
        const current = buttons.indexOf(document.activeElement);
        if ((event.key === 'Enter' || event.key === ' ' || event.key === 'Spacebar') && current >= 0) {
          event.preventDefault();
          buttons[current].click();
          return;
        }
        if (event.key === 'Escape') {
          event.preventDefault();
          event.stopPropagation();
          setSelectOpen(entry, false);
          trigger.focus();
          return;
        }
        if (event.key === 'Tab') {
          setSelectOpen(entry, false);
          return;
        }
        if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        let next = current;
        if (event.key === 'Home') next = 0;
        else if (event.key === 'End') next = buttons.length - 1;
        else if (event.key === 'ArrowDown') next = Math.min(buttons.length - 1, Math.max(0, current + 1));
        else next = Math.max(0, current < 0 ? buttons.length - 1 : current - 1);
        buttons[next]?.focus();
      });
      select.addEventListener('change', rebuild);
      const observer = new MutationObserver(rebuild);
      observer.observe(select, { subtree: true, childList: true, attributes: true, attributeFilter: ['disabled', 'selected', 'label'] });
      rebuild();
    };

    qa('select.select').forEach(enhanceSelect);
    document.querySelectorAll('.desktop-accounting-filter select').forEach((select,index)=>enhanceSelect(select,1000+index));
    document.querySelectorAll('#desktop-sessions-preview .desktop-sessions-tools select.select').forEach((select,index)=>enhanceSelect(select,2000+index));
    window.addEventListener('resize',()=>enhancedSelects.filter(e=>e.select.closest('[id="desktop-bottom-panel"]')).forEach(e=>setSelectOpen(e,false)));

    const setApiSummaryState = (summary, open) => {
      summary.setAttribute('aria-expanded', String(open));
      const detail = summary.nextElementSibling;
      detail.setAttribute('aria-hidden', String(!open));
      summary.lastElementChild.textContent = open ? '详情⌃' : '详情⌄';
    };

    const toggleApiSummary = async (summary) => {
      const version = ++apiDisclosureVersion;
      const shouldOpen = summary.getAttribute('aria-expanded') !== 'true';
      const openSummaries = qa('.desktop-settings-api-summary[aria-expanded="true"]');
      openSummaries.forEach((other) => setApiSummaryState(other, false));
      if (!shouldOpen) return;
      if (openSummaries.length) await wait(motionMs(165));
      if (version !== apiDisclosureVersion) return;
      setApiSummaryState(summary, true);
    };

    const animateDraft = async (open) => {
      const draft = q('#settings-api-draft');
      draft.getAnimations().forEach((animation) => animation.cancel());
      if (open) draft.hidden = false;
      const duration = motionMs(open ? 220 : 160);
      if (!duration) {
        draft.hidden = !open;
        return;
      }
      const keyframes = open
        ? [{ opacity: 0, transform: 'translateY(-6px)', maxHeight: '0px' }, { opacity: 1, transform: 'translateY(0)', maxHeight: `${draft.scrollHeight}px` }]
        : [{ opacity: 1, transform: 'translateY(0)', maxHeight: `${draft.scrollHeight}px` }, { opacity: 0, transform: 'translateY(-6px)', maxHeight: '0px' }];
      const animation = draft.animate(keyframes, { duration, easing: open ? 'cubic-bezier(.2,.8,.2,1)' : 'cubic-bezier(.4,0,1,1)', fill: 'both' });
      try { await animation.finished; } catch {}
      if (!open) draft.hidden = true;
      animation.cancel();
    };

    const getFlowNode = (key) => flowList.querySelector(`.desktop-settings-node[data-step="${key}"]`);
    const isFlowNodeEnabled = (key) => getFlowNode(key)?.dataset.enabled !== 'false';
    const syncContextFromEmbedding = () => {
      const embedding = getFlowNode('embedding');
      const context = getFlowNode('context');
      if (!embedding || !context) return;
      // Match the installed pipeline: node 06 reuses node 02's embedding space.
      context.dataset.embeddingProfileRef = embedding.dataset.profileRef || '';
      context.dataset.profileRef = embedding.dataset.profileRef || '';
      context.dataset.model = embedding.dataset.model || '';
      context.dataset.modelLocked = 'true';
      context.dataset.scoreable = 'false';
      context.dataset.profileSourceStep = 'embedding';
    };

    const getBypassMetas = () => {
      const metas = [];
      if (!isFlowNodeEnabled('card-review')) metas.push({
        key: 'card-review',
        from: 'card',
        to: 'admission',
        side: 1,
        label: '绕过准入前校核',
        aria: '当前从 03 生成卡片绕过 04 核对卡片，进入 05 卡片准入'
      });
      if (!isFlowNodeEnabled('judgment-review')) metas.push({
        key: 'judgment-review',
        from: 'analysis',
        to: 'human',
        side: -1,
        label: '绕过判断类核对',
        aria: '当前从 07 Analysis 绕过 08 核对分析，进入 09 人工判断'
      });
      return metas;
    };

    const getBypassWarningCopy = (node = selectedNode) => {
      if (!node) return '';
      if (node.dataset.step === 'card-review') return '跳过核对可能遗漏卡片错误。';
      if (node.dataset.step === 'judgment-review') return '跳过核对可能遗漏分析错误。';
      return '';
    };

    const updateNodeTogglePresentation = () => {
      if (!selectedNode) return;
      const control = q('#settings-node-toggle');
      const enabled = selectedNode.dataset.enabled !== 'false';
      const locked = selectedNode.dataset.toggleLocked === 'true';
      control.setAttribute('aria-checked', String(enabled));
      control.disabled = locked;
      const lockedLabel = '必经步骤';
      const lockedCopy = '';
      q('#settings-node-toggle-label').textContent = locked ? lockedLabel : enabled ? '节点已启用' : '节点已禁用';
      q('#settings-node-toggle-copy').textContent = locked ? lockedCopy : enabled ? '关闭后跳过此步骤' : '已跳过';
      control.setAttribute('aria-label', locked ? `${selectedNode.dataset.node}为必经节点，不能禁用` : `${enabled ? '禁用' : '启用'}${selectedNode.dataset.node}`);
      bypassWarning.hidden = locked || enabled;
      bypassWarningCopy.textContent = getBypassWarningCopy(selectedNode);
    };

    const updateNodeRetryPresentation = (node = selectedNode) => {
      if (!node) return;
      const locked = node.dataset.retryLocked === 'true';
      q('#settings-retries').closest('.desktop-settings-control-row').hidden = locked;
      q('#settings-retry-minus').disabled = locked;
      q('#settings-retries').disabled = locked;
      q('#settings-retry-plus').disabled = locked;
      q('#settings-retry-copy').textContent = locked ? '确定性状态或人工终点不设置模型重试' : '';
      q('#settings-retries').setAttribute('aria-label', locked ? `${node.dataset.node}不设置重试次数` : `${node.dataset.node}重试次数`);
    };

    const drawBypass = ({ animate = false } = {}) => {
      if (workflowView !== 'document') {
        bypassRoutes.forEach((route) => {
          route.classList.remove('is-active', 'is-activating');
          route.setAttribute('aria-hidden', 'true');
        });
        bypassLayer.classList.remove('is-active');
        bypassLayer.setAttribute('aria-hidden', 'true');
        lastBypassKeys = new Set();
        return;
      }
      const metas = getBypassMetas();
      const activeKeys = new Set(metas.map((meta) => meta.key));
      bypassRoutes.forEach((route, key) => {
        route.classList.remove('is-activating');
        if (!activeKeys.has(key)) {
          route.classList.remove('is-active');
          route.setAttribute('aria-hidden', 'true');
        }
      });
      if (!metas.length) {
        bypassLayer.classList.remove('is-active');
        bypassLayer.setAttribute('aria-hidden', 'true');
        lastBypassKeys = new Set();
        return;
      }
      const stageRect = flowStage.getBoundingClientRect();
      const width = stageRect.width;
      const height = stageRect.height;
      if (width < 2 || height < 2) return;
      bypassLayer.setAttribute('viewBox', `0 0 ${width} ${height}`);
      bypassLayer.classList.add('is-active');
      bypassLayer.classList.toggle('is-compact', width < 600);
      bypassLayer.setAttribute('aria-hidden', 'false');
      const aria = metas.map((meta) => meta.aria).join('；');
      bypassLayer.setAttribute('aria-label', aria);
      bypassTitle.textContent = aria;
      metas.forEach((meta) => {
        const route = bypassRoutes.get(meta.key);
        const fromRect = getFlowNode(meta.from)?.getBoundingClientRect();
        const toRect = getFlowNode(meta.to)?.getBoundingClientRect();
        if (!route || !fromRect || !toRect) return;
        const startX = fromRect.left - stageRect.left + fromRect.width / 2;
        const endX = toRect.left - stageRect.left + toRect.width / 2;
        const startY = fromRect.bottom - stageRect.top + 1;
        const endY = toRect.top - stageRect.top - 2;
        const edgeX = meta.side > 0
          ? Math.max(fromRect.right, toRect.right) - stageRect.left
          : Math.min(fromRect.left, toRect.left) - stageRect.left;
        const freeLane = meta.side > 0 ? width - edgeX : edgeX;
        const laneOffset = Math.max(20, Math.min(38, freeLane * .62));
        const laneX = Math.max(10, Math.min(width - 10, edgeX + meta.side * laneOffset));
        const shoulder = Math.max(14, Math.min(22, (endY - startY) * .16));
        const upperY = startY + shoulder;
        const lowerY = endY - shoulder;
        const radius = Math.max(6, Math.min(10, shoulder * .55));
        const direction = meta.side;
        const path = [
          `M ${startX} ${startY}`,
          `L ${startX} ${upperY - radius}`,
          `Q ${startX} ${upperY} ${startX + direction * radius} ${upperY}`,
          `L ${laneX - direction * radius} ${upperY}`,
          `Q ${laneX} ${upperY} ${laneX} ${upperY + radius}`,
          `L ${laneX} ${lowerY - radius}`,
          `Q ${laneX} ${lowerY} ${laneX - direction * radius} ${lowerY}`,
          `L ${endX + direction * radius} ${lowerY}`,
          `Q ${endX} ${lowerY} ${endX} ${lowerY + radius}`,
          `L ${endX} ${endY}`
        ].join(' ');
        const base = route.querySelector('.desktop-settings-bypass-base');
        const sweep = route.querySelector('.desktop-settings-bypass-sweep');
        const label = route.querySelector('.desktop-settings-bypass-label');
        base.setAttribute('d', path);
        sweep.setAttribute('d', path);
        label.textContent = meta.label;
        label.setAttribute('x', String(laneX));
        label.setAttribute('y', String((upperY + lowerY) / 2));
        route.classList.add('is-active');
        route.setAttribute('aria-hidden', 'false');
        route.style.setProperty('--desktop-settings-path-length', String(Math.max(1, sweep.getTotalLength())));
        if (animate && !lastBypassKeys.has(meta.key) && !reducedMotion.matches) {
          void sweep.getBoundingClientRect();
          route.classList.add('is-activating');
        }
      });
      lastBypassKeys = activeKeys;
    };

    const hasHeterogeneousModelConflict = (node) => {
      const producerStep = heterogeneousProducerStep[node?.dataset?.step];
      const reviewerProfileRef = node?.dataset?.profileRef || '';
      if (!producerStep || !reviewerProfileRef || reviewerProfileRef === 'AUTO_HETEROGENEOUS') return false;
      const producerProfileRef = getFlowNode(producerStep)?.dataset?.profileRef || '';
      if (!producerProfileRef) return false;
      const reviewer = configuredModelOptions.find((option) => option.profileRef === reviewerProfileRef);
      const producer = configuredModelOptions.find((option) => option.profileRef === producerProfileRef);
      return reviewerProfileRef === producerProfileRef
        || Boolean(reviewer?.modelKey && producer?.modelKey && reviewer.modelKey === producer.modelKey);
    };

    const hasReadyHeterogeneousReviewer = (node) => {
      const producerStep = heterogeneousProducerStep[node?.dataset?.step];
      if (!producerStep) return true;
      const producerProfileRef = getFlowNode(producerStep)?.dataset?.profileRef || '';
      const producer = configuredModelOptions.find((option) => option.profileRef === producerProfileRef);
      if (!producer) return false;
      const reviewerProfileRef = node?.dataset?.profileRef || '';
      if (reviewerProfileRef === 'AUTO_HETEROGENEOUS') return false;
      const reviewer = configuredModelOptions.find((option) => option.profileRef === reviewerProfileRef);
      return Boolean(reviewer && reviewer.state === 'green');
    };

    const workflowText = (key, fallback) => Memorive.settingsText?.text?.(key, fallback) || fallback;
    const setWorkflowStatus = (node, markerClass, key, fallback) => {
      const target = node.querySelector('.desktop-settings-node-status');
      target.replaceChildren();
      const marker = document.createElement('span');
      marker.className = markerClass;
      marker.setAttribute('aria-hidden', 'true');
      const copy = document.createElement('span');
      copy.dataset.flowState = '';
      copy.textContent = workflowText(key, fallback);
      target.append(marker, copy);
      if (node.dataset.scoreable !== 'false') {
        const score = document.createElement('span');
        score.className = 'desktop-settings-node-test-score';
        score.dataset.nodeTestScore = '';
        score.textContent = /^\d+$/.test(node.dataset.testScore || '') ? node.dataset.testScore : '—';
        score.setAttribute('aria-label', `模型测试分数 ${score.textContent}`);
        target.append(score);
      }
    };

    const setWorkflowStepReady = (step, node, steps) => {
      step.classList.remove('state-yellow', 'state-red', 'state-disabled', 'link-yellow', 'link-red');
      step.classList.add('state-green');
      if (step !== steps.at(-1)) step.classList.add('link-green');
      setWorkflowStatus(node, 'status-dot green', 'workflow.status.ready', '就绪');
    };

    const restoreDocumentWorkflow = () => {
      documentWorkflowSnapshots.forEach((snapshot, index) => {
        const step = workflowSteps[index];
        const node = step?.querySelector('.desktop-settings-node');
        if (!step || !node || !snapshot) return;
        step.className = snapshot.stepClass;
        step.hidden = snapshot.stepHidden;
        Object.keys(step.dataset).forEach((key) => { delete step.dataset[key]; });
        Object.entries(snapshot.stepDataset).forEach(([key, value]) => { step.dataset[key] = value; });
        Object.keys(node.dataset).forEach((key) => { delete node.dataset[key]; });
        Object.entries(snapshot.nodeDataset).forEach(([key, value]) => { node.dataset[key] = value; });
        node.innerHTML = snapshot.nodeHtml;
      });
      flowList.classList.remove('is-session-refinement');
      workflowFlow?.setAttribute('aria-label', '文献处理九节点纵向流程；02 仅选择向量嵌入模型，06 同步 02，可选校核节点可受控绕过');
      if (workflowNote) workflowNote.textContent = '';
    };

    const refinementSelectedOption = () => refinementModelOptions
      .find((option) => option.profileRef === refinementProfileRef);

    const renderRefinementWorkflow = () => {
      const selectedOption = refinementSelectedOption();
      workflowSteps.forEach((step, index) => {
        const node = step.querySelector('.desktop-settings-node');
        const definition = refinementDefinitions[index];
        if (!definition) {
          step.hidden = true;
          step.classList.remove('desktop-settings-terminal-arrow');
          return;
        }
        const model = definition.modelChange
          ? (selectedOption?.model || '尚未配置精炼模型')
          : definition.model;
        const ready = !definition.modelChange || Boolean(selectedOption && !selectedOption.disabled);
        const tone = ready ? 'green' : 'yellow';
        const status = ready ? '就绪' : '待选择可用模型';
        step.hidden = false;
        step.className = `desktop-settings-flow-step state-${tone}${index < refinementDefinitions.length - 1 ? ` link-${tone}` : ' desktop-settings-no-terminal-arrow'}`;
        Object.keys(step.dataset).forEach((key) => { delete step.dataset[key]; });
        step.dataset.step = definition.nodeId;
        Object.keys(node.dataset).forEach((key) => { delete node.dataset[key]; });
        Object.assign(node.dataset, {
          step:definition.nodeId,
          node:definition.name,
          purpose:definition.purpose,
          retries:'0',
          model,
          profileRef:definition.modelChange ? refinementProfileRef : '',
          modelCopy:definition.modelChange
            ? '使用对话精炼设置中已保存且可执行的模型'
            : '本地只读处理，不单独选择模型',
          toggleLocked:'true',
          retryLocked:'true',
          modelLocked:String(!definition.modelChange),
          modelAction:definition.modelChange ? '调整' : '只读',
          modelTooltip:definition.modelChange ? '展开可用于会话精炼的模型' : '当前步骤采用固定的本地处理方式',
          scoreable:'false',
          independent:'false',
          enabled:'true'
        });
        node.innerHTML = `<span class="desktop-settings-node-index">${definition.number}</span><span class="desktop-settings-node-copy"><strong>${definition.name}</strong><small class="desktop-settings-node-purpose"><span class="desktop-settings-node-meta-label">作用</span><span data-node-purpose>${definition.purpose}</span></small><small class="desktop-settings-node-model"><span class="desktop-settings-node-meta-label">${definition.modelChange ? '当前模型' : '执行方式'}</span><span data-node-model>${model}</span></small></span><span class="desktop-settings-node-status"></span>`;
        setWorkflowStatus(node, `status-dot ${tone}`, '', status);
        node.setAttribute('aria-label', `${definition.name}，作用：${definition.purpose}，${status}，模型或执行方式：${model}`);
      });
      flowList.classList.add('is-session-refinement');
      flowList.classList.remove('is-card-review-disabled', 'is-judgment-review-disabled');
      routeCopy.textContent = '01 → 02 → 03 → 04 · 会话精炼';
      const completed = selectedOption && !selectedOption.disabled ? 4 : 3;
      const score = q('#settings-flow-score');
      score.textContent = `${completed} / 4`;
      score.className = `badge ${completed === 4 ? 'green' : 'yellow'}`;
      score.setAttribute('aria-label', `会话精炼四个步骤中 ${completed} 个已完成配置`);
      workflowFlow?.setAttribute('aria-label', '会话精炼四节点纵向流程');
      if (workflowNote) workflowNote.textContent = '';
      if (selectedNode) {
        updateNodeTogglePresentation();
        updateNodeRetryPresentation(selectedNode);
        renderModelMenu(selectedNode);
      }
      requestAnimationFrame(() => drawBypass());
    };

    const renderReportWorkflow = () => {
      const state=Memorive.reportWorkflow?.inspect();
      const ref=state?.draft?.[workflowView] || '';
      const options=Memorive.reportWorkflow?.options() || [];
      const selected=options.find(row=>row.profileRef===ref);
      const definitions=state?.saved?.steps || [
        {key:'WAITING',name:'确认报告周期'},{key:'COLLECTING',name:'汇总已完成成果'},
        {key:'COMPOSING',name:'生成报告'},{key:'PUBLISHING',name:'保存至资料库'},
        {key:'NOTIFYING',name:'发送报告提醒'},{key:'COMPLETE',name:'结果可用'}];
      workflowSteps.forEach((step,index)=>{
        const definition=definitions[index];
        step.hidden=!definition;
        if(!definition)return;
        const node=step.querySelector('.desktop-settings-node'),usesModel=definition.key==='COMPOSING';
        const ready=!usesModel||Boolean(selected&&!selected.disabled),tone=ready?'green':'yellow';
        step.className=`desktop-settings-flow-step state-${tone}${index<5?' link-'+tone:' desktop-settings-no-terminal-arrow'}`;
        Object.keys(step.dataset).forEach(key=>delete step.dataset[key]);
        Object.keys(node.dataset).forEach(key=>delete node.dataset[key]);
        const model=usesModel?(selected?.model||flowText('请选择报告模型')):flowText('本地生成 · 不调用模型');
        Object.assign(node.dataset,{step:definition.key,node:flowText(definition.name),purpose:flowText(usesModel?'根据已有成果整理摘要、变化与待办':'本地生成 · 不调用模型'),
          model,profileRef:usesModel?ref:'',retries:'0',retryLocked:'true',toggleLocked:'true',modelLocked:String(!usesModel),
          scoreable:'false',enabled:'true',reportPeriod:workflowView});
        node.innerHTML='<span class="desktop-settings-node-index"></span><span class="desktop-settings-node-copy"><strong></strong><small class="desktop-settings-node-model"><span data-node-model></span></small></span><span class="desktop-settings-node-status"></span>';
        node.querySelector('.desktop-settings-node-index').textContent=String(index+1).padStart(2,'0');
        node.querySelector('strong').textContent=flowText(definition.name);
        node.querySelector('[data-node-model]').textContent=model;
        setWorkflowStatus(node,`status-dot ${tone}`,'',flowText(ready?'就绪':'待设置'));
        node.setAttribute('aria-label',flowText(definition.name)+' · '+model);
      });
      flowList.classList.add('is-session-refinement');
      flowList.classList.remove('is-card-review-disabled','is-judgment-review-disabled');
      routeCopy.textContent='01 → 02 → 03 → 04 → 05 → 06';
      const ready=selected&&!selected.disabled,score=q('#settings-flow-score');
      score.textContent=ready?'6 / 6':'5 / 6';
      score.className='badge '+(ready?'green':'yellow');
      workflowFlow?.setAttribute('aria-label',flowText(flowLabels[workflowView]));
      if(workflowNote)workflowNote.textContent='';
      if(selectedNode){
        updateNodeTogglePresentation();
        updateNodeRetryPresentation(selectedNode);
        renderModelMenu(selectedNode);
      }
      requestAnimationFrame(()=>drawBypass());
    };

    const renderWorkflow = ({ animateBypass = false } = {}) => {
      if (reportView()) {
        renderReportWorkflow();
        return;
      }
      if (workflowView === 'refinement') {
        renderRefinementWorkflow();
        return;
      }
      syncContextFromEmbedding();
      const steps = [...flowList.querySelectorAll('.desktop-settings-flow-step')];
      steps.forEach((step) => {
        const node = step.querySelector('.desktop-settings-node');
        const disabled = node.dataset.optional === 'true' && node.dataset.enabled === 'false';
        const requiresConfiguredApi = ['embedding', 'context', 'card', 'analysis', 'logic-review'].includes(node.dataset.step);
        const mappedOption = configuredModelOptions.find((option) => option.profileRef === node.dataset.profileRef);
        const configuredApiMapped = node.dataset.step === 'embedding'
          ? mappedOption?.embeddingEligible === true
          : node.dataset.step === 'context' ? mappedOption?.embeddingEligible === true : Boolean(mappedOption);
        const embeddingReady = configuredModelOptions.some((option) => (
          option.profileRef === getFlowNode('embedding')?.dataset.profileRef
          && option.embeddingEligible === true
        ));
        const heterogeneousConflict = hasHeterogeneousModelConflict(node);
        const heterogeneousReady = hasReadyHeterogeneousReviewer(node);
        if (disabled) {
          step.classList.remove('state-green', 'state-yellow', 'state-red', 'link-green', 'link-yellow', 'link-red');
          step.classList.add('state-disabled');
          setWorkflowStatus(node, 'desktop-settings-disabled-mark', 'workflow.status.disabled', '已禁用');
        } else if ((mappedOption && mappedOption.state !== 'green')
          || (node.dataset.profileRef?.startsWith('cli:') && !mappedOption)) {
          step.classList.remove('state-green', 'state-red', 'state-disabled', 'link-green', 'link-red');
          step.classList.add('state-yellow');
          if (step !== steps.at(-1)) step.classList.add('link-yellow');
          setWorkflowStatus(node, 'status-dot yellow', 'workflow.status.model-unavailable', '待验证');
        } else if (heterogeneousConflict) {
          // The user chose the producing model to check itself: allowed, with a warning that stays.
          setWorkflowStepReady(step, node, steps);
          const warning = document.createElement('span');
          warning.className = 'desktop-settings-same-model-warning';
          warning.setAttribute('role', 'img');
          warning.setAttribute('aria-label', '与上游生产模型相同，可能遗漏相同错误');
          warning.dataset.desktopTip = '与上游生产模型相同，可能遗漏相同错误';
          node.querySelector('.desktop-settings-node-status').prepend(warning);
        } else if (['card-review','judgment-review'].includes(node.dataset.step) && !heterogeneousReady) {
          step.classList.remove('state-green', 'state-red', 'state-disabled', 'link-green', 'link-red');
          step.classList.add('state-yellow');
          if (step !== steps.at(-1)) step.classList.add('link-yellow');
          setWorkflowStatus(node, 'status-dot yellow', 'workflow.status.review-unconfigured', '待设置');
        } else if (requiresConfiguredApi && !configuredApiMapped) {
          step.classList.remove('state-green', 'state-red', 'state-disabled', 'link-green', 'link-red');
          step.classList.add('state-yellow');
          if (step !== steps.at(-1)) step.classList.add('link-yellow');
          setWorkflowStatus(
            node,
            'status-dot yellow',
            'workflow.status.api-unconfigured',
            '待设置'
          );
        } else if (node.dataset.step === 'context' && !embeddingReady) {
          step.classList.remove('state-green', 'state-red', 'state-disabled', 'link-green', 'link-red');
          step.classList.add('state-yellow');
          if (step !== steps.at(-1)) step.classList.add('link-yellow');
          setWorkflowStatus(node, 'status-dot yellow', '', '待设置');
        } else {
          setWorkflowStepReady(step, node, steps);
        }
        const displayModel = node.querySelector('[data-node-model]');
        if (displayModel) displayModel.textContent = mappedOption?.model || (node.dataset.model?.startsWith('[默认]') ? node.dataset.model : '—');
        const status = node.querySelector('[data-flow-state]').textContent;
        const bypassState = disabled ? '，当前路径绕过此节点' : '';
        const purpose = node.dataset.purpose || node.querySelector('[data-node-purpose]')?.textContent || '作用说明未提供';
        node.setAttribute('aria-label', `${node.dataset.node || node.querySelector('strong').textContent}，作用：${purpose}，${status}${bypassState}，模型或执行方式：${node.dataset.model || '不调用模型'}`);
      });
      const cardReviewEnabled = isFlowNodeEnabled('card-review');
      const judgmentReviewEnabled = isFlowNodeEnabled('judgment-review');
      flowList.classList.toggle('is-card-review-disabled', !cardReviewEnabled);
      flowList.classList.toggle('is-judgment-review-disabled', !judgmentReviewEnabled);
      const activeRoute = ['01', '02', '03', ...(cardReviewEnabled ? ['04'] : []), '05', '06', '07', ...(judgmentReviewEnabled ? ['08'] : []), '09'];
      const bypassed = [!cardReviewEnabled ? '04' : '', !judgmentReviewEnabled ? '08' : ''].filter(Boolean);
      routeCopy.textContent = `${activeRoute.join(' → ')}${bypassed.length ? ` · 绕过 ${bypassed.join('、')}` : ' · 无绕过'}`;
      const scoreableSteps = steps.filter((step) => step.querySelector('.desktop-settings-node')?.dataset.scoreable !== 'false');
      const completed = scoreableSteps.filter((step) => step.classList.contains('state-green') || step.classList.contains('state-disabled')).length;
      const severity = scoreableSteps.some((step) => step.classList.contains('state-red')) ? 'red' : scoreableSteps.some((step) => step.classList.contains('state-yellow')) ? 'yellow' : 'green';
      const score = q('#settings-flow-score');
      score.textContent = `${completed} / ${scoreableSteps.length}`;
      score.className = `badge ${severity}`;
      score.setAttribute('aria-label', `${scoreableSteps.length} 个可配置流程节点中 ${completed} 个已完成，${bypassed.length ? `已绕过 ${bypassed.join('、')}` : '无绕过节点'}；整理分析材料 单源继承 02，卡片准入与人工判断不计入配置分母`);
      if (selectedNode) {
        updateNodeTogglePresentation();
        nodeError = selectedNode.closest('.desktop-settings-flow-step')?.classList.contains('state-red') || false;
        q('#settings-node-error').hidden = !nodeError && !retryError;
        q('.desktop-settings-node-body').classList.toggle('has-error', nodeError || retryError);
        refreshPageError();
      }
      requestAnimationFrame(() => drawBypass({ animate: animateBypass }));
    };

    let workflowTemplateRevision = 1;
    const getDocumentWorkflowRows = () => {
      if (workflowView === 'document') documentWorkflowSnapshots = captureDocumentWorkflow();
      return documentWorkflowSnapshots.map((snapshot) => snapshot.nodeDataset);
    };
    const getWorkflowTemplateState = () => {
      const rows = getDocumentWorkflowRows();
      const byStep = new Map(rows.map((row) => [row.step, row]));
      return {
        revision:workflowTemplateRevision,
        enabled:{
          'card-review':byStep.get('card-review')?.enabled !== 'false',
          'judgment-review':byStep.get('judgment-review')?.enabled !== 'false'
        },
        models:Object.fromEntries(rows.map((row) => [row.step, row.model])),
        profileRefs:Object.fromEntries(rows.map((row) => [row.step, row.profileRef || ''])),
        fallbackProfileRefs:Object.fromEntries(rows.map((row) => [row.step, row.fallbackProfileRef || ''])),
        profileSources:Object.fromEntries(rows.map((row) => [row.step, row.profileSourceStep || ''])),
        scores:Object.fromEntries(rows.map((row) => [
          row.step,
          /^\d+$/.test(row.testScore || '') ? Number(row.testScore) : null
        ]))
      };
    };
    const applyWorkflowTemplateState = (template) => {
      if (!template || typeof template !== 'object') return;
      const refinementWasVisible = workflowView === 'refinement';
      if (refinementWasVisible) restoreDocumentWorkflow();
      const revision = Number(template.revision);
      if (Number.isInteger(revision) && revision > 0) workflowTemplateRevision = revision;
      ['card-review', 'judgment-review'].forEach((step) => {
        if (typeof template.enabled?.[step] !== 'boolean') return;
        const node = getFlowNode(step);
        if (node) node.dataset.enabled = String(template.enabled[step]);
      });
      qa('.desktop-settings-node[data-step]').forEach((node) => {
        const model = typeof template.models?.[node.dataset.step] === 'string' ? template.models[node.dataset.step].trim() : '';
        const profileRef = typeof template.profileRefs?.[node.dataset.step] === 'string' ? template.profileRefs[node.dataset.step].trim() : '';
        const profileSource = typeof template.profileSources?.[node.dataset.step] === 'string' ? template.profileSources[node.dataset.step].trim() : '';
        if (profileSource) node.dataset.profileSourceStep = profileSource;
        if (profileRef || node.dataset.modelLocked !== 'true') node.dataset.profileRef = profileRef;
        if (node.dataset.step === 'ingest') node.dataset.fallbackProfileRef = template.fallbackProfileRefs?.ingest || '';
        const score = template.scores?.[node.dataset.step];
        node.dataset.testScore = Number.isInteger(score) && score >= 0 && score <= 100 ? String(score) : '';
        if (!model) return;
        node.dataset.model = model;
        if (node.dataset.modelLocked !== 'true' || node.dataset.profileSourceStep) {
          const modelValue = node.querySelector('[data-node-model]');
          if (modelValue) modelValue.textContent = model;
        }
      });
      syncContextFromEmbedding();
      documentWorkflowSnapshots = captureDocumentWorkflow();
      if (refinementWasVisible) renderRefinementWorkflow();
      else renderWorkflow();
    };
    const publishWorkflowTemplate = () => {
      if (window.parent === window) return;
      window.parent.postMessage({
        type: 'desktop-workflow-template-saved',
        page: '设置',
        template: getWorkflowTemplateState()
      }, '*');
    };

    const modelOptionsFor = (node) => {
      if(node.dataset.step==='logic-review')return configuredModelOptions.filter(option=>!String(option.profileRef).startsWith('local:') && (option.capabilities||['CHAT']).includes('CHAT'));
      if(reportView() && node.dataset.step==='COMPOSING') return Memorive.reportWorkflow?.options() || [];
      const builtIn = workflowModels[node.dataset.step] || [{ profileRef: node.dataset.profileRef || '', model: node.dataset.model, state: 'green', note: '系统内置路径' }];
      if (node.dataset.modelLocked === 'true') return builtIn;
      if (workflowView === 'refinement' && node.dataset.step === '03_REFINEMENT_MODEL_EXECUTION') {
        return refinementModelOptions.length ? refinementModelOptions : [{
          profileRef:'',
          model:'尚无可用精炼模型',
          state:'yellow',
          note:'请先保存并验证一个可执行模型',
          disabled:true
        }];
      }
      if (node.dataset.step === 'embedding') {
        const eligible = configuredModelOptions.filter((option) => option.embeddingEligible === true);
        return eligible.length ? eligible : builtIn;
      }
      if (node.dataset.step === 'context') {
        const eligible = configuredModelOptions.filter((option) => option.rerankerEligible === true);
        return eligible.length ? eligible : builtIn;
      }
      if (exclusiveBuiltInSteps.has(node.dataset.step)) {
        const acceptedCapabilities = requiredCapabilityByStep[node.dataset.step];
        const compatibleOptions = configuredModelOptions.filter((option) => (
          (option.capabilities || []).some((capability) => acceptedCapabilities.includes(capability))
        ));
        return node.dataset.step === 'ingest' ? compatibleOptions : [...builtIn, ...compatibleOptions];
      }
      if (configuredModelOptions.length) {
        const acceptedCapabilities = requiredCapabilityByStep[node.dataset.step] || ['CHAT'];
        const compatibleOptions = configuredModelOptions.filter((option) => (
          (option.capabilities || ['CHAT']).some((capability) => acceptedCapabilities.includes(capability))
        ));
        if (!compatibleOptions.length) return [{
          profileRef: node.dataset.profileRef || '',
          model: '没有适用于该节点的已配置模型',
          state: 'yellow',
          note: '嵌入模型仅用于 整理分析材料；视觉 / OCR 模型仅用于文档摄取',
          disabled: true
        }];
        const producerStep = heterogeneousProducerStep[node.dataset.step];
        if (!producerStep) return compatibleOptions;
        const producerProfileRef = getFlowNode(producerStep)?.dataset?.profileRef || '';
        const producerOption = compatibleOptions.find((option) => option.profileRef === producerProfileRef);
        return compatibleOptions.map((option) => {
          const conflicts = Boolean(producerProfileRef) && (
            option.profileRef === producerProfileRef
            || Boolean(option.modelKey && producerOption?.modelKey && option.modelKey === producerOption.modelKey)
          );
          return conflicts ? {
            ...option,
            disabled: false,
            risk: true,
            note: '与上游生产模型相同，可能遗漏相同错误'
          } : { ...option, disabled: false };
        });
      }
      return [{
        profileRef: node.dataset.profileRef || '',
        model: '尚未配置 API 或 CLI 模型',
        state: 'yellow',
        note: '请先在“模型服务与 API”或“CLI 接入”中保存模型',
        disabled: true
      }];
    };

    const fallbackMenu = q('#settings-fallback-menu');
    const fallbackButton = q('#settings-adjust-fallback');
    const fallbackPicker = Memorive.modelPicker.bind({button:fallbackButton,menu:fallbackMenu,manageEvents:false});
    const closeFallbackMenu = () => {
      fallbackMenu.dataset.open = 'false';
      fallbackMenu.setAttribute('aria-hidden', 'true');
      fallbackButton.setAttribute('aria-expanded', 'false');
    };
    const renderFallback = (node) => {
      const ingest = workflowView === 'document' && node?.dataset.step === 'ingest';
      q('#settings-node-settings').dataset.ingestActive = String(ingest);
      q('#settings-fallback-setting').hidden = !ingest;
      q('#settings-model-heading').textContent = ingest ? '主模型 · 必选' : '模型';
      closeFallbackMenu();
      if (!ingest) return;
      const options = modelOptionsFor(node).filter(option => option.profileRef && option.profileRef !== node.dataset.profileRef);
      const ref = node.dataset.fallbackProfileRef || '';
      const selected = options.find(option => option.profileRef === ref);
      q('#settings-current-fallback').textContent = selected?.model || (ref ? '配置不可用' : '未设置');
      q('#settings-fallback-dot').className = 'status-dot ' + (selected?.state || 'yellow');
      fallbackPicker.render([{profileRef:'',model:'未设置',state:'yellow',note:'仅使用主模型'}, ...options], ref);
      q('#settings-retry-copy').textContent = '每个失败页面单独计算，切换备用模型计 1 次';
    };
    fallbackButton.addEventListener('click', () => {
      const open = fallbackMenu.dataset.open !== 'true';
      fallbackMenu.dataset.open = String(open);
      fallbackMenu.setAttribute('aria-hidden', String(!open));
      fallbackButton.setAttribute('aria-expanded', String(open));
    });
    fallbackMenu.addEventListener('click', event => {
      const button = event.target.closest('button[data-model]');
      if (!selectedNode || selectedNode.dataset.step !== 'ingest' || !button || button.disabled) return;
      selectedNode.dataset.fallbackProfileRef = button.dataset.profileRef || '';
      renderFallback(selectedNode);
      setNodeDirty();
    });
    const renderModelMenu = (node) => {
      const options = modelOptionsFor(node);
      workflowModelPicker.render(options,node.dataset.profileRef||'');
      const locked = node.dataset.modelLocked === 'true';
      modelButton.disabled = locked;
      modelButton.textContent = locked ? (node.dataset.modelAction || '自动路由') : '调整';
      modelButton.dataset.desktopTip = locked ? (node.dataset.modelTooltip || '校核者由上游模型自动决定，不能固定为单一模型') : '展开当前槽位可用模型';
    };

    const closeNode = async ({ restoreFocus = true } = {}) => {
      if (nodePanel.hidden && !workarea.classList.contains('has-node')) return;
      const version = ++nodeVersion;
      const focusTarget = selectedNode;
      closeFallbackMenu();
      cancelRisks();
      setModelMenu(false);
      workarea.classList.remove('has-node');
      if (workarea.classList.contains('has-category')) primaryResizer.hidden = false;
      nodePanel.setAttribute('aria-hidden', 'true');
      if (preview.app.dataset.right === 'true') preview.setInspector(false);
      window.requestAnimationFrame(updatePrimaryResizerBounds);
      await wait(motionMs(190));
      if (version !== nodeVersion || workarea.classList.contains('has-node')) return;
      nodePanel.hidden = true;
      qa('.desktop-settings-node').forEach((item) => item.removeAttribute('aria-current'));
      selectedNode = null;
      nodeError = false;
      retryError = false;
      q('#settings-node-error').hidden = true;
      q('.desktop-settings-node-body').classList.remove('has-error');
      refreshPageError();
      if (restoreFocus) focusTarget?.focus();
    };

    const updateWorkflowHeader = () => {
      const next=workflowViews[(workflowViews.indexOf(workflowView)+1)%workflowViews.length];
      const label=flowText('切换到'+flowLabels[next]);
      workflowKind.dataset.workflowView=workflowView;
      workflowKind.dataset.nextView=next;
      workflowKind.setAttribute('aria-label',label);
      workflowKind.dataset.desktopTip=label;
      workflowViewLabel.textContent='· '+flowText(flowLabels[workflowView]);
    };
    const setWorkflowView = async (view) => {
      if (!workflowViews.includes(view) || view === workflowView) return;
      if (workarea.classList.contains('has-node')) await closeNode({ restoreFocus:false });
      if (workflowView === 'document') documentWorkflowSnapshots = captureDocumentWorkflow();
      workflowView = view;
      updateWorkflowHeader();
      if (view === 'document') restoreDocumentWorkflow();
      if (reportView()) {
        try { await Memorive.reportWorkflow?.load(); }
        catch(error) { showToast(flowText('无法加载报告模型设置，请重试')); }
      }
      renderWorkflow();
      preview.announce(flowText(flowLabels[view]));
    };
    workflowKind?.addEventListener('click', () => {
      setWorkflowView(workflowViews[(workflowViews.indexOf(workflowView)+1)%workflowViews.length]);
    });

    const setSettingsMode = (mode) => {
      if (shell.dataset.modeScope === 'cli' && mode === 'normal') mode = 'advanced';
      qa('#settings-mode button[data-mode]').forEach((item) => {
        item.setAttribute('aria-pressed', String(item.dataset.mode === mode));
      });
      shell.dataset.settingsMode = mode;
      window.dispatchEvent(new CustomEvent('desktop:settings-mode-changed', {
        detail: { mode, scope: shell.dataset.modeScope || 'none' }
      }));
    };

    let beforeAboutMode=null;
    const setModeScope = (categoryKey = '') => {
      if(categoryKey!=='update'&&beforeAboutMode!==null){
        const previous=beforeAboutMode;
        beforeAboutMode=null;
        setSettingsMode(previous);
      }
      const scoped = categoryKey === 'api' || categoryKey === 'cli' || categoryKey === 'workflow' || categoryKey === 'research-weights' || categoryKey === 'refinement' || categoryKey === 'update';
      q('#settings-mode').hidden = !scoped;
      shell.dataset.modeScope = scoped ? categoryKey : 'none';
      const normalButton = q('#settings-mode button[data-mode="normal"]');
      const advancedButton = q('#settings-mode button[data-mode="advanced"]');
      normalButton.hidden = categoryKey === 'cli';
      advancedButton.hidden = categoryKey === 'workflow';
      q('#settings-mode button[data-mode="developer"]').hidden=categoryKey==='update';
      if(categoryKey==='update'){
        beforeAboutMode=shell.dataset.settingsMode;
        setSettingsMode('normal');
      }
      if (categoryKey === 'cli' && shell.dataset.settingsMode === 'normal') {
        setSettingsMode('advanced');
      }
      if (categoryKey === 'workflow' && shell.dataset.settingsMode === 'advanced') {
        setSettingsMode('normal');
      }
      window.dispatchEvent(new CustomEvent('desktop:settings-mode-scope-changed', {
        detail: { mode: shell.dataset.settingsMode, scope: shell.dataset.modeScope }
      }));
    };

    const setActiveCategory = (categoryKey = '') => {
      shell.dataset.activeCategory = categoryKey || 'none';
      window.dispatchEvent(new CustomEvent('desktop:settings-category-changed', {
        detail: { category: shell.dataset.activeCategory }
      }));
    };

    const openCategory = async (button) => {
      if (button.getAttribute('aria-selected') === 'true' && workarea.classList.contains('has-category')) {
        // SAME_CATEGORY_ALREADY_OPEN: return from tertiary detail without replaying the category transition.
        if (workarea.classList.contains('has-node')) await closeNode({ restoreFocus: false });
        return;
      }
      const version = ++categoryVersion;
      await closeNode({ restoreFocus: false });
      if (version !== categoryVersion) return;
      cancelRisks();
      categoryScrollPositions.set(shell.dataset.activeCategory, primary.scrollTop);
      categories.forEach((item) => item.setAttribute('aria-selected', String(item === button)));
      const key = button.dataset.category;
      setActiveCategory(key);
      setModeScope(key);
      qa('[data-panel]').forEach((panel) => panel.hidden = true);
      const target = q('[data-panel="' + key + '"]');
      target.hidden = false;
      primary.hidden = false;
      primary.setAttribute('aria-hidden', 'false');
      primaryResizer.hidden = false;
      workarea.classList.add('has-category');
      setPrimarySize(preferredPrimarySize(), false, false);
      primary.scrollTop = categoryScrollPositions.get(key) || 0;
      if (key === 'workflow') requestAnimationFrame(() => drawBypass());
    };
    const returnToCategories = async () => {
      if (primary.hidden && !workarea.classList.contains('has-category')) return;
      const version = ++categoryVersion;
      const focusTarget = categories.find((item) => item.getAttribute('aria-selected') === 'true');
      const closePromise = closeNode({ restoreFocus: false });
      cancelRisks();
      setModelMenu(false);
      workarea.classList.remove('has-category');
      updateCompact();
      setActiveCategory('');
      setModeScope('');
      primary.setAttribute('aria-hidden', 'true');
      primaryResizer.hidden = true;
      categories.forEach((item) => item.setAttribute('aria-selected', 'false'));
      await Promise.all([closePromise, wait(motionMs(220))]);
      if (version !== categoryVersion || workarea.classList.contains('has-category')) return;
      primary.hidden = true;
      qa('[data-panel]').forEach((panel) => panel.hidden = true);
      window.requestAnimationFrame(updatePrimaryResizerBounds);
      focusTarget?.focus();
    };
    categories.forEach((button) => button.addEventListener('click', () => openCategory(button)));
    categories.forEach((button) => {
      const label=button.querySelector('strong')?.textContent.trim();
      if(label){
        button.setAttribute('aria-label',label);
        button.setAttribute('title',label);
        button.dataset.desktopTip=label;
      }
    });
    q('#settings-assistant-settings-link')?.addEventListener('click',()=>openCategory(q('[data-category="behavior"]')));
    q('#settings-assistant-settings-link')?.setAttribute('title','Logo 左键显示 · 右键隐藏');
    qa('.desktop-settings-back').forEach((button) => button.addEventListener('click', returnToCategories));
    qa('#settings-api-list, #settings-cli-list').forEach((list) => list.addEventListener('click', async (event) => {
      const cliAddSummary = event.target.closest('.desktop-settings-cli-add-summary');
      if (cliAddSummary) {
        const open = cliAddSummary.getAttribute('aria-expanded') !== 'true';
        cliAddSummary.setAttribute('aria-expanded', String(open));
        cliAddSummary.nextElementSibling?.setAttribute('aria-hidden', String(!open));
        cliAddSummary.querySelector('.desktop-settings-cli-add-chevron').textContent = open ? '⌃' : '⌄';
        return;
      }
      const summary = event.target.closest('.desktop-settings-api-summary');
      if (summary) {
        await toggleApiSummary(summary);
        return;
      }
      if (event.target.closest('[data-replace-key]')) { showToast('凭据字段已进入替换态；预览不会保存或发送输入值。'); }
    }));
    q('#settings-add-api').addEventListener('click', async () => {
      ++apiDisclosureVersion;
      const openSummaries = qa('.desktop-settings-api-summary[aria-expanded="true"]');
      openSummaries.forEach((summary) => setApiSummaryState(summary, false));
      if (openSummaries.length) await wait(motionMs(165));
      await animateDraft(true);
      markDirty();
    });
    q('#settings-cancel-api').addEventListener('click', async () => {
      await animateDraft(false);
      showToast('未保存的 API 草稿已取消。');
    });
    const openNode = async (node) => {
      if (selectedNode === node && workarea.classList.contains('has-node') && !nodePanel.hidden) {
        // SAME_NODE_ALREADY_OPEN: keep the existing inspector and its unsaved draft intact.
        return;
      }
      const version = ++nodeVersion;
      const alreadyOpen = workarea.classList.contains('has-node') && !nodePanel.hidden;
      cancelRisks();
      setModelMenu(false);
      selectedNode = node;
      qa('.desktop-settings-node').forEach((item) => item.setAttribute('aria-current', String(item === node)));
      q('#settings-node-title').textContent = node.dataset.node;
      q('#settings-node-purpose').textContent = node.dataset.purpose || '';
      q('.desktop-settings-model-setting').hidden = node.dataset.modelLocked === 'true' && !node.dataset.profileSourceStep;
      q('#settings-adjust-model').hidden = node.dataset.modelLocked === 'true';
      confirmedRetries = Number(node.dataset.retries || 2);
      q('#settings-retries').value = String(confirmedRetries);
      updateNodeRetryPresentation(node);
      confirmedModel = node.dataset.model || '尚未映射已配置模型';
      const currentOption = [...(Memorive.reportWorkflow?.options()||[]), ...refinementModelOptions, ...configuredModelOptions, ...(workflowModels[node.dataset.step] || [])]
        .find((option) => option.profileRef === (node.dataset.profileRef || '') || option.model === confirmedModel);
      confirmedModelState = currentOption?.state || (confirmedModel.startsWith('[默认]') ? 'green' : 'yellow');
      q('#settings-current-model').textContent = confirmedModel.startsWith('尚未') ? '—' : (currentOption?.model || (confirmedModel.startsWith('[默认]') ? confirmedModel : '—'));
      q('#settings-current-model').title = confirmedModel;
      q('#settings-model-dot').className = 'status-dot ' + confirmedModelState;
      q('#settings-model-copy').textContent = '';
      q('#settings-adjust-model').title = '选择已保存的模型';
      const scoreable = node.dataset.scoreable !== 'false';
      q('#settings-node-test-row').hidden = !scoreable;
      q('#settings-node-test').disabled = !scoreable;
      q('#settings-node-test').dataset.step = node.dataset.step || '';
      q('#settings-node-test-score').textContent = /^\d+$/.test(node.dataset.testScore || '') ? node.dataset.testScore : '—';
      renderModelMenu(node);
      renderFallback(node);
      updateNodeTogglePresentation();
      nodeError = node.closest('.desktop-settings-flow-step')?.classList.contains('state-red') || false;
      retryError = false;
      q('#settings-node-error').hidden = !nodeError;
      q('.desktop-settings-node-body').classList.toggle('has-error', nodeError);
      if (!alreadyOpen) {
        nodePanel.hidden = false;
        nodePanel.setAttribute('aria-hidden', 'false');
        preview.setInspector(true);
        await nextFrame();
        if (version !== nodeVersion) return;
        workarea.classList.add('has-category', 'has-node');
        primaryResizer.hidden = true;
      }
      refreshPageError();
    };
    qa('button.desktop-settings-node').forEach((node) => node.addEventListener('click', () => openNode(node)));
    q('#settings-node-close').addEventListener('click', () => closeNode());
    q('#settings-node-toggle').addEventListener('click', () => {
      const control = q('#settings-node-toggle');
      if (control.disabled || !selectedNode) return;
      const enabled = control.getAttribute('aria-checked') === 'true';
      selectedNode.dataset.enabled = String(enabled);
      renderWorkflow({ animateBypass: true });
      if (enabled) showToast(`${selectedNode.dataset.node}已重新启用；主流程连线已恢复，其他绕过状态保持不变。`);
      else {
        const meta = getBypassMetas().find((item) => item.key === selectedNode.dataset.step);
        showToast(`${selectedNode.dataset.node}已禁用；${meta?.aria || '当前路径已更新'}。`);
      }
      setNodeDirty();
    });
    const evaluateRetry = () => {
      const input = q('#settings-retries');
      const value = Number(input.value);
      stopRetryRisk(false);
      const invalid = !Number.isInteger(value) || value < 0;
      retryError = invalid;
      q('.desktop-settings-node-body').classList.toggle('has-error', retryError || nodeError);
      q('#settings-node-error').hidden = !retryError && !nodeError;
      refreshPageError();
      if (invalid) {
        setNodeDirty(false);
        return;
      }
      if (value <= 1 || value > 5) {
        q('#settings-retry-risk-copy').textContent = value <= 1 ? '不重试或低重试可能使任务在首次失败后终止。' : '多次重试可能增加模型调用费用和时间。';
        q('#settings-retry-risk').hidden = false;
        startCountdown('retry', () => stopRetryRisk(true));
      } else {
        confirmedRetries = value;
        if (selectedNode) selectedNode.dataset.retries = String(value);
        setNodeDirty();
      }
      refreshPageError();
    };
    q('#settings-retry-minus').addEventListener('click', () => {
      q('#settings-retries').value = String(Math.max(0, Number(q('#settings-retries').value || 0) - 1));
      evaluateRetry();
    });
    q('#settings-retry-plus').addEventListener('click', () => {
      q('#settings-retries').value = String(Number(q('#settings-retries').value || 0) + 1);
      evaluateRetry();
    });
    q('#settings-retries').addEventListener('change', evaluateRetry);
    q('#settings-confirm-retry').addEventListener('click', () => {
      confirmedRetries = Number(q('#settings-retries').value);
      if (selectedNode) selectedNode.dataset.retries = String(confirmedRetries);
      stopRetryRisk(false);
      setNodeDirty();
    });
    modelButton.addEventListener('click', () => setModelMenu(modelMenu.dataset.open !== 'true'));
    modelMenu.addEventListener('click', (event) => {
      const button = event.target.closest('button[data-model]');
      if (!button || button.disabled) return;
      setModelMenu(false);
      q('#settings-current-model').textContent = button.dataset.model;
      q('#settings-current-model').title = button.dataset.model;
      q('#settings-model-dot').className = 'status-dot ' + button.dataset.state;
      if (selectedNode) selectedNode.dataset.profileRef = button.dataset.profileRef || '';
      if (selectedNode?.dataset.fallbackProfileRef === selectedNode?.dataset.profileRef) selectedNode.dataset.fallbackProfileRef = '';
      renderFallback(selectedNode);
      const changedNode = selectedNode;
      queueMicrotask(() => window.dispatchEvent(new CustomEvent('desktop:workflow-model-selected', {detail:{node:changedNode}})));
      if (selectedNode) selectedNode.dataset.testScore = button.dataset.testScore || '';
      q('#settings-node-test-score').textContent = button.dataset.testScore || '—';
      if (button.dataset.risk === 'true') {
        q('#settings-model-risk').hidden = false;
        startCountdown('model', () => stopModelRisk(true));
      }
      else {
        confirmedModel = button.dataset.model;
        confirmedModelState = button.dataset.state;
        if (selectedNode) selectedNode.dataset.model = confirmedModel;
        if (selectedNode?.dataset.step === 'embedding') syncContextFromEmbedding();
        if(reportView() && selectedNode?.dataset.step==='COMPOSING') Memorive.reportWorkflow?.select(workflowView,button.dataset.profileRef);
        if (workflowView === 'refinement' && selectedNode?.dataset.step === '03_REFINEMENT_MODEL_EXECUTION') {
          refinementProfileRef = button.dataset.profileRef || '';
          const select = q('#settings-refinement-model');
          if (select && [...select.options].some((option) => option.value === refinementProfileRef)) {
            select.value = refinementProfileRef;
            select.dispatchEvent(new Event('change', { bubbles:true }));
          }
        }
        renderWorkflow();
        setNodeDirty();
      }
    });
    q('#settings-confirm-model').addEventListener('click', () => {
      confirmedModel = q('#settings-current-model').textContent;
      q('#settings-current-model').title = confirmedModel;
      if (selectedNode) selectedNode.dataset.model = confirmedModel;
      if (selectedNode?.dataset.step === 'embedding') syncContextFromEmbedding();
      renderWorkflow();
      stopModelRisk(false);
      setNodeDirty();
    });

    q('#settings-mode').addEventListener('click', async (event) => {
      const button = event.target.closest('button[data-mode]');
      if (!button) return;
      if (button.hidden || button.disabled) return;
      if (shell.dataset.modeScope === 'cli' && button.dataset.mode === 'normal') return;
      if (button.dataset.mode === 'developer' && workarea.classList.contains('has-node')) {
        await closeNode({ restoreFocus: false });
      }
      setSettingsMode(button.dataset.mode);
    });
    q('#settings-model-plan').addEventListener('click', (event) => {
      const button = event.target.closest('button[data-model-plan]');
      if (!button) return;
      qa('#settings-model-plan button[data-model-plan]').forEach((item) => {
        item.setAttribute('aria-checked', String(item === button));
      });
      markDirty();
    });
    qa('[data-local-switch]').forEach((button) => button.addEventListener('click', () => markDirty()));
    primary.addEventListener('change', (event) => { if (!event.target.closest('#settings-network-error')) markDirty(); });
    q('#settings-library-provider').addEventListener('change', () => {
      const provider = q('#settings-library-provider').value;
      const configured = provider !== 'NONE';
      q('#settings-library-name').disabled = !configured;
      q('#settings-library-root').disabled = !configured;
      q('#settings-library-browse').disabled = !configured;
      q('#settings-library-test').disabled = !configured;
      if (configured && !q('#settings-library-name').value.trim()) {
        q('#settings-library-name').value = {
          OBSIDIAN: 'Obsidian Vault', LOGSEQ: 'Logseq Graph', ZOTERO: 'Zotero Library', CUSTOM: '外部资料库'
        }[provider] || '外部资料库';
      }
      q('#settings-library-root-help').textContent = {
        OBSIDIAN: '请选择包含 .obsidian 文件夹的 Vault 根目录。',
        LOGSEQ: '请选择包含 logseq 文件夹的 Graph 根目录。',
        ZOTERO: '请选择包含 zotero.sqlite 的 Zotero 数据目录。',
        CUSTOM: '请选择由外部程序实际管理的资料库根目录。'
      }[provider] || '选择资料库类型后，再选择对应根目录。';
      q('#settings-library-status').textContent = configured ? '待检测' : '未检测';
      q('#settings-library-copy').textContent = configured ? '请先完成真实本地检测，再保存设置' : '尚未配置';
      q('#settings-library-dot').className = 'status-dot yellow';
      markDirty();
    });
    q('#settings-proxy-mode').addEventListener('change', () => {
      q('#settings-custom-proxy-fields').hidden = q('#settings-proxy-mode').value !== 'custom';
    });
    q('#settings-proxy-address').addEventListener('input', () => {
      const value=q('#settings-proxy-address').value.trim();
      const invalid=value && !/^(https?|socks5h?):\/\//i.test(value);
      q('#settings-network-error').hidden=!invalid;
      networkError=Boolean(invalid);
      refreshPageError();
    });
    q('#settings-clear-cache').addEventListener('click', () => q('#settings-cache-confirm').hidden=false);
    q('#settings-clear-confirm').addEventListener('click', () => {
      q('#settings-cache-confirm').hidden=true;
      showToast('临时缓存清理请求已完成；正式资产未受影响。');
    });
    q('#settings-shortcuts').addEventListener('click', () => setShortcutPopover(shortcutPopover.dataset.open !== 'true'));
    qa('[data-font-step]').forEach((button) => button.addEventListener('click', () => {
      const output=q('#settings-font-size');
      const next=Math.min(140,Math.max(80,Number.parseInt(output.textContent,10)+Number(button.dataset.fontStep)));
      output.textContent=next+'%';
      markDirty();
    }));
    qa('[data-local-action]').forEach((button) => button.addEventListener('click', () => showToast(button.dataset.localAction)));
    pageSave.addEventListener('click', () => {
      if (window.pywebview?.api?.call) return;
      if (pageSave.disabled) return;
      pageDirty = false;
      workflowTemplateRevision += 1;
      updatePageSave();
      publishWorkflowTemplate();
      showToast(`流程模板 v${workflowTemplateRevision} 已保存；仅之后新建任务使用，运行中任务保持原快照。`);
    });
    q('#settings-toast-close').addEventListener('click', () => hideToast({ animate: true }));

    document.addEventListener('pointerdown', (event) => {
      if (!event.target.closest('.desktop-settings-select-shell')) enhancedSelects.forEach((entry) => setSelectOpen(entry, false));
      if (!event.target.closest('#settings-adjust-model, #settings-model-menu')) setModelMenu(false);
      if (!event.target.closest('#settings-shortcuts, #settings-shortcut-popover')) setShortcutPopover(false);
    });

    root.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        const openSelect = enhancedSelects.find((entry) => entry.shell.dataset.open === 'true');
        if (openSelect) {
          event.preventDefault();
          setSelectOpen(openSelect, false);
          openSelect.trigger.focus();
          return;
        }
        if (modelMenu.dataset.open === 'true') {
          event.preventDefault();
          setModelMenu(false);
          modelButton.focus();
          return;
        }
        if (shortcutPopover.dataset.open === 'true') {
          event.preventDefault();
          setShortcutPopover(false);
          q('#settings-shortcuts').focus();
          return;
        }
        if (retryTimer || modelTimer) {
          event.preventDefault();
          cancelRisks();
          return;
        }
        if (workarea.classList.contains('has-node')) {
          event.preventDefault();
          closeNode();
          return;
        }
      }
      if (event.key === 'Enter' && retryTimer) q('#settings-confirm-retry').click();
      if (event.key === 'Enter' && modelTimer) q('#settings-confirm-model').click();
    });

    window.addEventListener('message', (event) => {
      if (window.parent === window || event.source !== window.parent) return;
      const data = event.data;
      if (!data || data.type !== 'desktop-workflow-template-state') return;
      applyWorkflowTemplateState(data.template);
    });
    if (window.parent !== window) {
      window.parent.postMessage({ type: 'desktop-workflow-template-request', page: '设置' }, '*');
    }

    // The right panel button (and Ctrl+Alt+B) with no node selected says what to select.
    root.addEventListener('desktop:right-button', (event) => {
      if (!selectedNode && preview.app.dataset.right !== 'true') {
        event.preventDefault();
        preview.announce('请先选择流程节点');
      }
    });
    const inspectorObserver = new MutationObserver(() => {
      if (preview.app.dataset.right !== 'true' && workarea.classList.contains('has-node')) closeNode({ restoreFocus:false });
    });
    inspectorObserver.observe(preview.app, { attributes:true, attributeFilter:['data-right'] });

    if ('ResizeObserver' in window) {
      new ResizeObserver(() => drawBypass()).observe(flowStage);
      new ResizeObserver(() => updatePrimaryResizerBounds()).observe(workarea);
    }
    window.addEventListener('resize', () => {
      updatePrimaryResizerBounds();
      drawBypass();
    });
    window.addEventListener('desktop:settings-locale-change', () => {
      updateWorkflowHeader();
      renderWorkflow();
    });
    Memorive.settingsView = Object.freeze({
      setWorkflowView,
      getEditState: () => ({dirty:pageDirty}),
      resetPaneLayout() {
        rememberedCategoryPrimarySize=null;
        categoryScrollPositions.clear();
        primary.scrollTop=0;
        q('.desktop-settings-categories').scrollTop=0;
        updatePrimaryResizerBounds();
      },
      applySettingsMode(mode) {
        if (!['normal', 'advanced', 'developer'].includes(mode)) return;
        setSettingsMode(mode);
      },
      setApiDraftOpen(open) {
        return animateDraft(Boolean(open));
      },
      setConfiguredModelOptions(options) {
        configuredModelOptions = Array.isArray(options)
          ? options
            .filter((option) => option && typeof option.profileRef === 'string' && typeof option.model === 'string')
            .map((option) => ({ ...option, modelKey: typeof option.modelKey === 'string' ? option.modelKey : '' }))
          : [];
        if (selectedNode) {
          const current = modelOptionsFor(selectedNode).find((option) => option.profileRef === selectedNode.dataset.profileRef);
          if (current) {
            selectedNode.dataset.model = current.model;
            q('#settings-current-model').textContent = current.model;
            q('#settings-current-model').title = current.model;
            q('#settings-model-dot').className = `status-dot ${current.state || 'yellow'}`;
          }
          renderModelMenu(selectedNode);
        }
        renderWorkflow();
      },
      setRefinementModelOptions(projection) {
        refinementProfileRef = typeof projection?.settings?.profile_ref === 'string'
          ? projection.settings.profile_ref
          : '';
        refinementModelOptions = Array.isArray(projection?.model_options)
          ? projection.model_options.flatMap((option) => {
            if (!option || typeof option.profile_ref !== 'string') return [];
            const profileRef = option.profile_ref.trim();
            if (!profileRef) return [];
            let model = String(option.display_name || option.model_name || profileRef).trim();
            if (/^local:[0-9a-f]{24}$/i.test(profileRef) && !model.startsWith('[本地]')) model = `[本地] ${model}`;
            const eligible = option.execution_eligible === true;
            return [{
              profileRef,
              model,
              state:eligible ? 'green' : 'yellow',
              note:eligible ? '已保存且可用于会话精炼' : (option.disabled_reason || '尚未接通精炼执行通道'),
              disabled:!eligible
            }];
          })
          : [];
        if (workflowView === 'refinement') renderWorkflow();
      },
      applyWorkflow(template) {
        const activeView = workflowView;
        if (activeView !== 'document') {
          restoreDocumentWorkflow();
          workflowView = 'document';
        }
        applyWorkflowTemplateState(template);
        qa('.desktop-settings-node[data-step]').forEach((node) => {
          const retries = Number(template?.retries?.[node.dataset.step]);
          if (Number.isInteger(retries) && retries >= 0) node.dataset.retries = String(retries);
          const score = template?.scores?.[node.dataset.step];
          node.dataset.testScore = Number.isInteger(score) && score >= 0 && score <= 100 ? String(score) : '';
        });
        documentWorkflowSnapshots = captureDocumentWorkflow();
        workflowView = activeView;
        if (activeView === 'refinement') renderRefinementWorkflow();
        else renderWorkflow();
      },
      workflowState() {
        const rows = getDocumentWorkflowRows();
        return {
          ...getWorkflowTemplateState(),
          retries:Object.fromEntries(rows.map((row) => [row.step, Number(row.retries || 0)]))
        };
      },
      renderWorkflow,
      setClean() {
        pageDirty = false;
        updatePageSave();
      },
      setDirty() {
        markDirty();
      },
    });
    setModeScope('');
    updateWorkflowHeader();
    q('#settings-proxy-mode').dispatchEvent(new Event('change', { bubbles: true }));
    updatePrimaryResizerBounds();
    renderWorkflow();
    setModelMenu(false);
    setShortcutPopover(false);
    updatePageSave();
  })();

