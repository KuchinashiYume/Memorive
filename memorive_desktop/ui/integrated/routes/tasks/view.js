  (() => {
    const root = document.getElementById('desktop-tasks-preview');
    const preview = window.DesktopPreview_tasks;
    if (!root || !preview) return;
    const q = (selector) => root.querySelector(selector);
    const qa = (selector) => [...root.querySelectorAll(selector)];
    const stage = q('#tasks-stage');
    const listPane = q('.desktop-tasks-listpane');
    const taskList = q('#tasks-list');
    const flowPane = q('#tasks-flow');
    const flowResizer = q('#tasks-resize-flow');
    let taskProjectionState = 'loading';
    let tasks = qa('.desktop-tasks-task');
    let nextTaskStableOrder = 0;
    const ensureTaskStableOrder = (task) => {
      if (!task.dataset.stableOrder) {
        nextTaskStableOrder += 1;
        task.dataset.stableOrder = String(nextTaskStableOrder);
      }
      return Number(task.dataset.stableOrder) || 0;
    };
    tasks.forEach(ensureTaskStableOrder);
    const nodes = qa('.desktop-tasks-node');
    const refinementBindings = new Map();
    const productionBindings = new Map();
    const refinementNodeIds = Object.freeze([
      '01_SESSION_SELECTION_FREEZE',
      '02_DIALOGUE_CLEANUP',
      '03_REFINEMENT_MODEL_EXECUTION',
      '04_RESULT_VERIFY_PERSIST'
    ]);
    const baselineFlowNodes = nodes.map((node) => ({
      step:node.closest('.desktop-tasks-flow-step')?.dataset.step || node.dataset.step,
      dataset:{ ...node.dataset },
      innerHTML:node.innerHTML
    }));
    let currentPre = false;
    let selectedNode = null;
    let lastInspectedNode = null;
    let nodeTransition = 0;
    let flowTransition = 0;
    let timer = 0;
    let left = 10;
    let currentTask = null;
    let taskMode = 'current';
    let lastBypassKeys = new Set();
    let activeFlowResize = null;
    const optionalSteps = ['card-review', 'judgment-review'];
    const stepKeys = ['ingest', 'data-review', 'embedding', 'card', 'card-review', 'admission', 'context', 'analysis', 'judgment-review', 'human'];
    const flowList = q('#tasks-flow-list');
    const flowStage = q('#tasks-flow-stage');
    const bypassLayer = q('#tasks-bypass-layer');
    const bypassTitle = q('#tasks-bypass-title');
    const bypassRoutes = new Map(qa('[data-bypass-route]').map((route) => [route.dataset.bypassRoute, route]));
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    const motionMs = (value) => reducedMotion.matches ? 0 : value;
    const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    const modelMenu = q('#tasks-model-menu');
    const modelButton = q('#tasks-adjust-model');
    const taskSnapshots = new Map();
    const defaultModels = Object.fromEntries(nodes.map((node) => [node.dataset.step, node.dataset.model]));
    const defaultProfileRefs = Object.fromEntries(stepKeys.map((step) => [step, '']));
    let templateState = {
      revision:1,
      enabled:{ 'card-review':true, 'judgment-review':false },
      models:{ ...defaultModels },
      profileRefs:{ ...defaultProfileRefs }
    };
    const clampPaneSize = (value, min, max) => Math.min(max, Math.max(min, value));
    const getFlowSize = () => {
      const inlineValue = Number.parseFloat(stage.style.getPropertyValue('--desktop-tasks-flow-size'));
      if (Number.isFinite(inlineValue)) return inlineValue;
      return Math.max(0, stage.getBoundingClientRect().width - 12) * .6;
    };
    const getFlowLimits = () => {
      const width = stage.getBoundingClientRect().width;
      const max = Math.max(96, width - 220 - 12);
      return { min: Math.min(420, max), max };
    };
    const updateFlowResizerAria = (limits, value) => {
      const rounded = Math.round(value);
      flowResizer.setAttribute('aria-valuemin', String(Math.round(limits.min)));
      flowResizer.setAttribute('aria-valuemax', String(Math.round(limits.max)));
      flowResizer.setAttribute('aria-valuenow', String(rounded));
      flowResizer.setAttribute('aria-valuetext', `${rounded} 像素`);
    };
    const setFlowSize = (value, announce = false) => {
      const limits = getFlowLimits();
      const next = clampPaneSize(value, limits.min, limits.max);
      stage.style.setProperty('--desktop-tasks-flow-size', `${Math.round(next)}px`);
      updateFlowResizerAria(limits, next);
      if (announce) q('#desktop-live-state').textContent = `任务流程边栏宽度 ${Math.round(next)} 像素`;
      return next;
    };
    const updateFlowResizerBounds = () => {
      if (stage.classList.contains('has-inspector')) return;
      const limits = getFlowLimits();
      const size = getFlowSize();
      const hasCustomSize = stage.style.getPropertyValue('--desktop-tasks-flow-size').trim() !== '';
      if (stage.classList.contains('has-flow') && hasCustomSize) setFlowSize(size);
      else updateFlowResizerAria(limits, clampPaneSize(size, limits.min, limits.max));
    };
    const beginFlowResize = (event) => {
      if (event.button !== 0 || !stage.classList.contains('has-flow') || stage.classList.contains('has-inspector')) return;
      event.preventDefault();
      activeFlowResize = event.pointerId;
      stage.dataset.resizing = 'flow';
      flowResizer.setPointerCapture(event.pointerId);
    };
    const moveFlowResize = (event) => {
      if (activeFlowResize !== event.pointerId) return;
      setFlowSize(stage.getBoundingClientRect().right - event.clientX);
    };
    const endFlowResize = (event) => {
      if (activeFlowResize !== event.pointerId) return;
      if (flowResizer.hasPointerCapture(event.pointerId)) flowResizer.releasePointerCapture(event.pointerId);
      activeFlowResize = null;
      delete stage.dataset.resizing;
      setFlowSize(getFlowSize(), true);
    };
    const resizeFlowWithKeyboard = (event) => {
      if (!stage.classList.contains('has-flow') || stage.classList.contains('has-inspector')) return;
      const limits = getFlowLimits();
      const current = getFlowSize();
      let next = current;
      if (event.key === 'Home') next = limits.min;
      else if (event.key === 'End') next = limits.max;
      else if (event.key === 'ArrowLeft') next = current + 10;
      else if (event.key === 'ArrowRight') next = current - 10;
      else return;
      event.preventDefault();
      setFlowSize(next, true);
    };

    flowResizer.addEventListener('pointerdown', beginFlowResize);
    flowResizer.addEventListener('pointermove', moveFlowResize);
    flowResizer.addEventListener('pointerup', endFlowResize);
    flowResizer.addEventListener('pointercancel', endFlowResize);
    flowResizer.addEventListener('keydown', resizeFlowWithKeyboard);
    // Compact width is a CSS constraint, never sampled from an in-flight transition.
    const workflowModels = {
      ingest: [
        { model:'[默认] 本地解析', profileRef:'PARSER_PROFILE', capabilities:['CHAT'], state:'green', note:'文档处理默认路径' }
      ],
      embedding: [
        { model:'尚未选择向量嵌入模型', profileRef:'', capabilities:['EMBEDDING'], embeddingEligible:false, state:'yellow', note:'请先在设置中验证 API 或本地向量嵌入模型', disabled:true }
      ],
      card: [
        { model:'DeepSeek V4 Pro', state:'green', note:'默认蒸馏' },
        { model:'DeepSeek Flash', state:'yellow', note:'受控降档' }
      ],
      'card-review': [
        { model:'按蒸馏模型自动选择异源校核者', state:'green', note:'默认异源路由' },
        { model:'GPT-5 · 异源校核', state:'green', note:'任务级指定' },
        { model:'Claude Sonnet 5 · 异源校核', state:'green', note:'任务级指定' }
      ],
      admission: [
        { model:'确定性状态机 / 不调用模型', state:'green', note:'不可替换', disabled:true }
      ],
      context: [
        { model:'同步 02 · 尚未选择向量嵌入模型', profileRef:'', capabilities:['EMBEDDING'], state:'yellow', note:'单源继承 02', disabled:true }
      ],
      analysis: [
        { model:'Claude Sonnet 5', state:'green', note:'默认模型' },
        { model:'Claude Opus 5', state:'yellow', note:'人工升档' },
        { model:'DeepSeek V4 Pro', state:'green', note:'受控后备' }
      ],
      'judgment-review': [
        { model:'按 Analysis 生产者自动选择异源校核者', state:'green', note:'默认异源路由' },
        { model:'GPT-5 · 独立核对', state:'green', note:'任务级指定' },
        { model:'DeepSeek V4 Pro · 独立核对', state:'green', note:'任务级指定' }
      ],
      human: [
        { model:'人类判断 / 不调用模型', state:'green', note:'不可替换', disabled:true }
      ]
    };
    const exclusiveBuiltInSteps = new Set(['ingest']);
    const exclusiveCapabilities = Object.freeze({ ingest:'CHAT' });
    const configuredWorkflowSteps = new Set(['card', 'card-review', 'analysis', 'judgment-review']);
    const heterogeneousProducerStep = Object.freeze({
      'card-review':'card',
      'judgment-review':'analysis'
    });
    let configuredModelOptions = [];
    let configuredModelOptionsFingerprint = '';
    const createStatuses = (overrides = {}) => Object.fromEntries(stepKeys.map((step) => [step, overrides[step] || { tone:'neutral', label:'未开始', human:false }]));
    const taskProfiles = {
      'run-042': {
        statuses:createStatuses({ ingest:{ tone:'yellow', label:'等待批准', human:true } })
      },
      'run-043': {
        revision:1,
        enabled:{ 'card-review':true, 'judgment-review':true },
        statuses:createStatuses({
          ingest:{ tone:'green', label:'已完成', human:false },
          embedding:{ tone:'green', label:'已完成', human:false },
          card:{ tone:'green', label:'已完成', human:false },
          'card-review':{ tone:'green', label:'已完成', human:false },
          admission:{ tone:'green', label:'已完成', human:false },
          context:{ tone:'green', label:'已完成', human:false },
          analysis:{ tone:'blue', label:'执行中', human:false }
        })
      },
      'run-041': {
        revision:1,
        enabled:{ 'card-review':true, 'judgment-review':false },
        statuses:createStatuses({
          ingest:{ tone:'green', label:'已完成', human:false },
          embedding:{ tone:'green', label:'已完成', human:false },
          card:{ tone:'green', label:'已完成', human:false },
          'card-review':{ tone:'green', label:'已完成', human:false },
          admission:{ tone:'green', label:'已完成', human:false },
          context:{ tone:'green', label:'已完成', human:false },
          analysis:{ tone:'red', label:'执行异常', human:true }
        })
      },
      'run-038': {
        revision:1,
        enabled:{ 'card-review':true, 'judgment-review':true },
        statuses:createStatuses(Object.fromEntries(stepKeys.map((step) => [step, { tone:'green', label:'已完成', human:false }])))
      }
    };
    const cloneEnabled = (enabled) => ({
      'card-review': enabled?.['card-review'] !== false,
      'judgment-review': enabled?.['judgment-review'] !== false
    });
    const cloneModels = (models, fallback = defaultModels) => Object.fromEntries(stepKeys.map((step) => {
      const value = typeof models?.[step] === 'string' ? models[step].trim() : '';
      return [step, value || fallback?.[step] || defaultModels[step]];
    }));
    const cloneProfileRefs = (profileRefs, fallback = defaultProfileRefs) => Object.fromEntries(stepKeys.map((step) => {
      const value = typeof profileRefs?.[step] === 'string' ? profileRefs[step].trim() : '';
      return [step, value || fallback?.[step] || ''];
    }));
    const syncContextFromEmbedding = (snapshot) => {
      if (!snapshot || typeof snapshot !== 'object') return snapshot;
      const apply = (models, profileRefs) => {
        if (!models || !profileRefs) return;
        const embeddingModel = String(models.embedding || '').trim();
        models.context = embeddingModel && embeddingModel !== '尚未选择向量嵌入模型'
          ? `同步 02 · ${embeddingModel}`
          : '同步 02 · 尚未选择向量嵌入模型';
        profileRefs.context = profileRefs.embedding || '';
      };
      apply(snapshot.models, snapshot.profileRefs);
      apply(snapshot.baseModels, snapshot.baseProfileRefs);
      return snapshot;
    };
    const serializeSnapshot = (snapshot) => ({
      revision:snapshot.revision,
      origin:snapshot.origin,
      baseEnabled:cloneEnabled(snapshot.baseEnabled),
      enabled:cloneEnabled(snapshot.enabled),
      overrides:[...snapshot.overrides],
      baseModels:cloneModels(snapshot.baseModels),
      models:cloneModels(snapshot.models, snapshot.baseModels),
      baseProfileRefs:cloneProfileRefs(snapshot.baseProfileRefs),
      profileRefs:cloneProfileRefs(snapshot.profileRefs, snapshot.baseProfileRefs),
      modelOverrides:[...snapshot.modelOverrides]
    });
    const publishTaskSnapshot = (snapshot) => {
      if (window.parent === window || !snapshot) return;
      window.parent.postMessage({
        type:'desktop-workflow-task-snapshot-updated',
        page:'当前任务',
        run:snapshot.run,
        snapshot:serializeSnapshot(snapshot)
      }, '*');
    };
    const getTaskSnapshot = (task) => {
      const run = task.dataset.run;
      if (taskSnapshots.has(run)) return taskSnapshots.get(run);
      const preExecution = task.dataset.pre === 'true';
      const profile = taskProfiles[run] || taskProfiles['run-042'];
      const source = preExecution ? templateState : {
        revision:profile.revision || 1,
        enabled:profile.enabled || templateState.enabled,
        models:profile.models || defaultModels,
        profileRefs:profile.profileRefs || defaultProfileRefs
      };
      const baseEnabled = cloneEnabled(source.enabled);
      const baseModels = cloneModels(source.models);
      const baseProfileRefs = cloneProfileRefs(source.profileRefs);
      const snapshot = {
        run,
        revision:Number.isInteger(Number(source.revision)) ? Number(source.revision) : 1,
        origin:preExecution ? 'settings-template' : 'run-frozen',
        baseEnabled,
        enabled:cloneEnabled(baseEnabled),
        overrides:new Set(),
        baseModels,
        models:cloneModels(baseModels),
        baseProfileRefs,
        profileRefs:cloneProfileRefs(baseProfileRefs),
        modelOverrides:new Set()
      };
      syncContextFromEmbedding(snapshot);
      taskSnapshots.set(run, snapshot);
      publishTaskSnapshot(snapshot);
      return snapshot;
    };
    const getCurrentSnapshot = () => currentTask ? getTaskSnapshot(currentTask) : null;
    const getFlowNode = (key) => flowList.querySelector('.desktop-tasks-node[data-step="' + key + '"]');
    const isTaskNodeEnabled = (snapshot, key) => snapshot?.enabled?.[key] !== false;
    const getNodeProgress = (step) => {
      const profile = currentTask ? taskProfiles[currentTask.dataset.run] || taskProfiles['run-042'] : null;
      return profile?.statuses?.[step] || { tone:'neutral', label:'未开始', human:false };
    };
    const isNodeNotStarted = (node) => Boolean(node) && getNodeProgress(node.dataset.step).tone === 'neutral';
    const canToggleNode = (node) => {
      if(currentTask?.dataset.workflowKind?.startsWith('RESEARCH_'))return false;
      if (currentTask?.dataset.workflowKind === 'CORE_DOCUMENT') {
        return node?.dataset.enableToggleEligible === 'true';
      }
      return isNodeNotStarted(node) && node.dataset.optional === 'true';
    };
    const modelOptionsForTask = (node, snapshot) => {
      const step = node?.dataset?.step || '';
      if (currentTask?.dataset.workflowKind === 'SESSION_REFINEMENT') {
        const binding = refinementBindings.get(currentTask.dataset.run);
        if (step !== '03_REFINEMENT_MODEL_EXECUTION') return [];
        return configuredModelOptions.filter((option) => option.capabilities.includes('CHAT') && option.embeddingEligible !== true);
      }
      const sampleOptions = workflowModels[step] || [];
      if (step === 'embedding') {
        const eligible = configuredModelOptions.filter((option) => option.embeddingEligible === true);
        return eligible.length ? eligible : sampleOptions;
      }
      if (step === 'context') return sampleOptions;
      if (exclusiveBuiltInSteps.has(step)) {
        const capability = exclusiveCapabilities[step];
        const compatible = configuredModelOptions.filter((option) => (
          Array.isArray(option.capabilities) && (step === 'ingest'
            ? option.capabilities.some(value => ['CHAT','VISION_OCR'].includes(value))
            : option.capabilities.includes(capability))
        ));
        return step === 'ingest' ? compatible : [...sampleOptions, ...compatible];
      }
      if (!configuredWorkflowSteps.has(step) || !configuredModelOptions.length) return sampleOptions;
      const compatibleConfigured = configuredModelOptions.filter((option) => option.embeddingEligible !== true);
      const producerStep = heterogeneousProducerStep[step];
      if (!producerStep) return compatibleConfigured;
      const producerProfileRef = snapshot?.profileRefs?.[producerStep] || '';
      const producerOption = configuredModelOptions.find((option) => option.profileRef === producerProfileRef);
      return compatibleConfigured.map((option) => {
        const conflicts = Boolean(producerProfileRef) && (
          option.profileRef === producerProfileRef
          || Boolean(option.modelKey && producerOption?.modelKey && option.modelKey === producerOption.modelKey)
        );
        return conflicts ? {
          ...option,
          disabled:true,
          note:'异源校核不能选择与上游生产节点相同的模型'
        } : { ...option, disabled:false };
      });
    };
    const canReplaceModel = (node) => {
      const snapshot = getCurrentSnapshot();
      const notStarted = ['SESSION_REFINEMENT', 'CORE_DOCUMENT'].includes(currentTask?.dataset.workflowKind)
        ? node?.dataset.tone === 'neutral'
        : isNodeNotStarted(node);
      return notStarted
        && node.dataset.modelLocked !== 'true'
        && modelOptionsForTask(node, snapshot).some((option) => !option.disabled);
    };
    const getModelOption = (node, snapshot, model) => modelOptionsForTask(node, snapshot)
      .find((option) => option.model === model || option.profileRef === snapshot?.profileRefs?.[node.dataset.step]);
    const setModelMenu = (open) => {
      const next = Boolean(open) && selectedNode && canReplaceModel(selectedNode);
      modelMenu.dataset.open = String(next);
      modelMenu.setAttribute('aria-hidden', String(!next));
      modelButton.setAttribute('aria-expanded', String(next));
    };
    const renderModelMenu = (node, snapshot) => {
      modelMenu.replaceChildren();
      modelOptionsForTask(node, snapshot).forEach((option) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.setAttribute('role', 'menuitem');
        button.setAttribute('aria-current', String(snapshot.models[node.dataset.step] === option.model));
        button.dataset.model = option.model;
        button.dataset.profileRef = option.profileRef || '';
        button.dataset.modelKey = option.modelKey || '';
        button.dataset.state = option.state;
        button.disabled = Boolean(option.disabled);
        const dot = document.createElement('span');
        dot.className = 'status-dot ' + option.state;
        const label = document.createElement('span');
        label.textContent = option.model;
        const note = document.createElement('small');
        note.textContent = option.note;
        button.append(dot, label, note);
        modelMenu.appendChild(button);
      });
    };
    const getBypassMetas = (snapshot) => {
      const metas = [];
      if (!isTaskNodeEnabled(snapshot, 'card-review')) metas.push({
        key:'card-review',
        from:'card',
        to:'admission',
        side:1,
        label:'绕过准入前校核',
        aria:'当前从 03 生成卡片绕过 04 核对卡片，进入 05 卡片准入'
      });
      if (!isTaskNodeEnabled(snapshot, 'judgment-review')) metas.push({
        key:'judgment-review',
        from:'analysis',
        to:'human',
        side:-1,
        label:'绕过判断类核对',
        aria:'当前从 07 Analysis 绕过 08 核对分析，进入 09 人工判断'
      });
      return metas;
    };
    const drawBypass = ({ animate = false } = {}) => {
      if (currentTask?.dataset.workflowKind === 'SESSION_REFINEMENT') {
        bypassRoutes.forEach((route) => {
          route.classList.remove('is-active', 'is-activating');
          route.setAttribute('aria-hidden', 'true');
        });
        bypassLayer.classList.remove('is-active');
        bypassLayer.setAttribute('aria-hidden', 'true');
        lastBypassKeys = new Set();
        return;
      }
      const snapshot = currentTask?.dataset.workflowKind === 'CORE_DOCUMENT'
        ? {
            enabled:Object.fromEntries(nodes.map((node) => [node.dataset.step, node.dataset.enabled !== 'false'])),
            models:Object.fromEntries(nodes.map((node) => [node.dataset.step, node.dataset.model || ''])),
            profileRefs:Object.fromEntries(nodes.map((node) => [node.dataset.step, node.dataset.profileRef || '']))
          }
        : getCurrentSnapshot();
      const metas = snapshot ? getBypassMetas(snapshot) : [];
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
      bypassLayer.setAttribute('viewBox', '0 0 ' + width + ' ' + height);
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
          'M ' + startX + ' ' + startY,
          'L ' + startX + ' ' + (upperY - radius),
          'Q ' + startX + ' ' + upperY + ' ' + (startX + direction * radius) + ' ' + upperY,
          'L ' + (laneX - direction * radius) + ' ' + upperY,
          'Q ' + laneX + ' ' + upperY + ' ' + laneX + ' ' + (upperY + radius),
          'L ' + laneX + ' ' + (lowerY - radius),
          'Q ' + laneX + ' ' + lowerY + ' ' + (laneX - direction * radius) + ' ' + lowerY,
          'L ' + (endX + direction * radius) + ' ' + lowerY,
          'Q ' + endX + ' ' + lowerY + ' ' + endX + ' ' + (lowerY + radius),
          'L ' + endX + ' ' + endY
        ].join(' ');
        const base = route.querySelector('.desktop-tasks-bypass-base');
        const sweep = route.querySelector('.desktop-tasks-bypass-sweep');
        const label = route.querySelector('.desktop-tasks-bypass-label');
        base.setAttribute('d', path);
        sweep.setAttribute('d', path);
        label.textContent = meta.label;
        label.setAttribute('x', String(laneX));
        label.setAttribute('y', String((upperY + lowerY) / 2));
        route.classList.add('is-active');
        route.setAttribute('aria-hidden', 'false');
        const pathLength = typeof sweep.getTotalLength === 'function' ? sweep.getTotalLength() : 1;
        route.style.setProperty('--desktop-tasks-path-length', String(Math.max(1, pathLength)));
        if (animate && !lastBypassKeys.has(meta.key) && !reducedMotion.matches) {
          void sweep.getBoundingClientRect();
          route.classList.add('is-activating');
        }
      });
      lastBypassKeys = activeKeys;
    };
    const statusIcon = (tone) => {
      if (tone === 'neutral') return '<span class="desktop-tasks-neutral-dot" aria-hidden="true"></span>';
      if (tone === 'disabled') return '<span class="desktop-tasks-disabled-mark" aria-hidden="true"></span>';
      return '<span class="status-dot ' + tone + (tone === 'blue' ? ' running' : '') + '" aria-hidden="true"></span>';
    };
    const refinementNodePresentation = Object.freeze({
      NOT_STARTED:{ tone:'neutral', label:'未开始' },
      RUNNING:{ tone:'blue', label:'执行中' },
      COMPLETED:{ tone:'green', label:'已完成' },
      SUCCEEDED:{ tone:'green', label:'已完成' },
      FAILED:{ tone:'red', label:'执行异常' },
      CANCELLED:{ tone:'yellow', label:'已取消' },
      WAITING_APPROVAL:{ tone:'yellow', label:'等待人工处理' },
      PAUSED:{ tone:'yellow', label:'已暂停' },
      PAUSE_SCHEDULED:{ tone:'yellow', label:'已设置暂停点' },
      DISABLED:{ tone:'disabled', label:'已禁用' }
    });
    const formatRefinementError = (code) => {
      const value = String(code || '').trim();
      const known = {
        LOCAL_STRUCTURED_CHAT_RESPONSE_SCHEMA_INVALID:'本地模型返回的结构不符合精炼结果格式。',
        LOCAL_STRUCTURED_CHAT_MODEL_IDENTITY_MISMATCH:'本地服务返回的模型与所选模型不一致。',
        LOCAL_MODEL_NOT_EXECUTION_ELIGIBLE:'所选本地模型当前不可执行，请在设置中重新验证。',
        REFINEMENT_EXECUTION_CHANNEL_NOT_AVAILABLE:'所选模型尚未接通精炼执行通道。',
        SESSION_REFINEMENT_FROZEN_SNAPSHOT_DRIFT:'会话内容已变化，请重新创建任务。',
        SESSION_REFINEMENT_SOURCE_MESSAGE_INVALID:'会话正文包含无法处理的消息结构。',
        SESSION_REFINEMENT_CANCELLED:'任务已取消。'
      };
      return known[value] || (value ? `执行失败：${value}` : '执行失败：未返回可识别的错误原因。');
    };
    const refinementNodeProductCopy = Object.freeze({
      '01_SESSION_SELECTION_FREEZE':{
        name:'准备会话',
        purpose:'确认所选会话并生成只读内容快照',
        model:'[默认] 本地快照'
      },
      '02_DIALOGUE_CLEANUP':{
        name:'对话清理',
        purpose:'清理空白与重复结构，保留原始语义',
        model:'[默认] 本地清理'
      },
      '03_REFINEMENT_MODEL_EXECUTION':{
        name:'精炼',
        purpose:'调用本任务选定模型生成结构化精炼结果',
        model:'已配置精炼模型'
      },
      '04_RESULT_VERIFY_PERSIST':{
        name:'检查并保存',
        purpose:'核对精炼结果与执行回执，并保存为待复核草稿',
        model:'本地校核'
      }
    });
    const refinementSelectedModel = (binding) => configuredModelOptions
      .find((option) => option.profileRef === (binding?.selected_profile_ref || ''))?.model
      || '已保存模型';
    const restoreCoreFlowNodes = () => {
      nodes.forEach((node, index) => {
        const baseline = baselineFlowNodes[index];
        const step = node.closest('.desktop-tasks-flow-step');
        if (!baseline || !step) return;
        step.hidden = false;
        step.classList.remove('desktop-flow-end');
        step.dataset.step = baseline.step;
        step.classList.remove('desktop-tasks-terminal-arrow');
        Object.keys(node.dataset).forEach((key) => { delete node.dataset[key]; });
        Object.entries(baseline.dataset).forEach(([key, value]) => { node.dataset[key] = value; });
        node.innerHTML = baseline.innerHTML;
      });
      flowList.classList.remove('is-session-refinement');
    };
    const renderRefinementTaskFlow = () => {
      window.__MEMORIVE_REVIEW_UI__?.renderTask(null,flowList);
      const binding = refinementBindings.get(currentTask?.dataset.run || '');
      if (!binding) return;
      const workflowNodes = Array.isArray(binding.workflow_nodes) ? binding.workflow_nodes : [];
      nodes.forEach((node, index) => {
        const step = node.closest('.desktop-tasks-flow-step');
        const definition = workflowNodes[index];
        if (!step) return;
        if (index >= refinementNodeIds.length || !definition) {
          step.hidden = true;
          step.classList.remove('desktop-tasks-terminal-arrow');
          return;
        }
        const copy = refinementNodeProductCopy[definition.node_id] || {
          name:'会话精炼节点',
          purpose:'处理会话精炼任务',
          model:'[默认] 本地处理'
        };
        const model = definition.node_id === '03_REFINEMENT_MODEL_EXECUTION'
          ? refinementSelectedModel(binding)
          : copy.model;
        step.hidden = false;
        step.dataset.step = definition.node_id;
        step.classList.toggle('desktop-tasks-terminal-arrow', definition.node_id === '04_RESULT_VERIFY_PERSIST');
        node.dataset.step = definition.node_id;
        node.dataset.node = copy.name;
        node.dataset.purpose = copy.purpose;
        node.dataset.model = model;
        node.dataset.profileRef = binding.selected_profile_ref || '';
        node.dataset.modelLocked = String(definition.model_change_eligible !== true);
        node.dataset.errorCode = definition.error_code || binding.error_code || '';
        node.dataset.summary = definition.state === 'FAILED'
          ? formatRefinementError(node.dataset.errorCode)
          : copy.purpose;
        node.dataset.enabled = 'true';
        node.dataset.human = 'false';
        delete node.dataset.optional;
        const state = refinementNodePresentation[definition.state] || refinementNodePresentation.NOT_STARTED;
        node.dataset.status = state.label;
        node.dataset.tone = state.tone;
        step.classList.remove('state-green', 'state-blue', 'state-yellow', 'state-red', 'state-neutral', 'state-disabled', 'link-green', 'link-blue', 'link-yellow', 'link-red');
        step.classList.add('state-' + state.tone);
        if (index < refinementNodeIds.length - 1 && state.tone !== 'neutral') step.classList.add('link-' + state.tone);
        node.querySelector('.desktop-tasks-node-index').textContent = String(index + 1).padStart(2, '0');
        node.querySelector('.desktop-tasks-node-copy strong').textContent = copy.name;
        node.querySelector('[data-node-purpose]').textContent = copy.purpose;
        node.querySelector('[data-node-model]').textContent = model;
        node.querySelector('.desktop-tasks-node-status').innerHTML = statusIcon(state.tone) + '<span data-flow-state>' + state.label + '</span>';
        node.setAttribute('aria-label', `${copy.name}，${state.label}`);
        window.__MEMO_NODE_PROGRESS__?.apply(node,definition,binding);
      });
      flowList.classList.add('is-session-refinement');
      flowList.classList.remove('is-card-review-disabled', 'is-judgment-review-disabled');
      q('#tasks-route-copy').textContent = '01 → 02 → 03 → 04 · 会话精炼';
      q('#tasks-flow-source').textContent = `会话精炼 · ${refinementSelectedModel(binding)}`;
      q('#tasks-flow-help').textContent = '';
      if (selectedNode) updateInspector(selectedNode);
      window.requestAnimationFrame(() => drawBypass());
    };
    const formatCoreError = (code) => {
      const value = String(code || '').toUpperCase();
      if (value.includes('FINISH_REASON_LENGTH') || value.includes('OUTPUT_TRUNCAT') || value.includes('8192_TO_16384')) {
        return '输出不完整；重新处理时将分段生成。';
      }
      if (value.includes('PHYSICAL_BATCH') || value.includes('TOO_LARGE_TO_PROCESS')) {
        return '向量处理容量不足；重新处理时将分段处理。';
      }
      if (value.includes('HTTP_400')) {
        return '向量服务拒绝请求，可调整模型后重试。';
      }
      return '此步骤未生成有效结果。可调整模型后重新处理。';
    };
    const renderProductionTaskFlow = () => {
      const binding = productionBindings.get(currentTask?.dataset.run || '');
      if (!binding) return;
      window.__MEMORIVE_REVIEW_UI__?.renderTask(binding,flowList,{openNode:openNodeInspector});
      restoreCoreFlowNodes();
      const workflowNodes = Array.isArray(binding.workflow_nodes) ? binding.workflow_nodes : [];
      const steps = Array.from(flowList.querySelectorAll('.desktop-tasks-flow-step')); 
      steps.forEach((step, index) => {
        const node = step.querySelector('.desktop-tasks-node');
        const definition = workflowNodes[index];
        if (!definition || !node) {
          step.hidden = true;
          return;
        }
        const uiStep = ({'E2_DATA_REVIEW':'data-review','01_DOCUMENT_INGEST':'ingest','02_CHUNK_EMBEDDING':'embedding','03_CARD_DISTILL':'card','04_CARD_CROSS_CHECK':'card-review','05_CARD_ADMISSION':'admission','06_CONTEXT_PACK':'context','07_ANALYSIS':'analysis','08_JUDGMENT_CROSS_CHECK':'judgment-review','09_HUMAN_FINAL':'human'})[definition.node_id];
        const state = refinementNodePresentation[definition.state] || refinementNodePresentation.NOT_STARTED;
        const model = String(definition.model || '未绑定模型');
        const purpose = String(definition.purpose || '执行文献处理节点');
        step.hidden = false;
        step.classList.toggle('desktop-flow-end',index===workflowNodes.length-1);
        step.dataset.step = uiStep;
        node.dataset.step = uiStep;
        node.dataset.runtimeNodeId = String(definition.node_id || '');
        node.dataset.node = String(definition.name || definition.node_id || '文献处理节点');
        node.dataset.purpose = purpose;
        const contextRuntime = definition.context_pack_runtime || null;
        node.dataset.summary = definition.state === 'FAILED'
          ? formatCoreError(definition.error_code || binding.error_code)
          : (definition.node_id === '06_CONTEXT_PACK' && contextRuntime?.truncated === true
              ? `整理分析材料 发生容量取舍：保留 ${Number(contextRuntime.retained_estimated_tokens || 0).toLocaleString()} / 原始估算 ${Number(contextRuntime.original_estimated_tokens || 0).toLocaleString()} tokens，丢弃 ${Number(contextRuntime.dropped_evidence_block_count || 0)} 个证据块。${contextRuntime.recovery_action || ''}`
              : purpose);
        node.dataset.model = model;
        node.dataset.profileRef = String(definition.profile_ref || '');
        node.dataset.fallbackProfileRef = String(definition.fallback_profile_ref || '');
        node.dataset.retryPageNumber = String(definition.retry_page_number || '');
        node.dataset.modelLocked = String(definition.model_change_eligible !== true);
        node.dataset.modelChangeEligible = String(definition.model_change_eligible === true);
        node.dataset.enableToggleEligible = String(definition.enable_toggle_eligible === true);
        node.dataset.retryChangeEligible = String(definition.retry_change_eligible === true);
        node.dataset.retryCount = String(Number(definition.retry_count || 0));
        node.dataset.retryConsumed = definition.retry_consumed == null ? '' : String(Number(definition.retry_consumed));
        node.dataset.retryRemaining = definition.retry_remaining == null ? '' : String(Number(definition.retry_remaining));
        node.dataset.rollbackEligible = String(definition.rollback_eligible === true);
        node.dataset.retryEligible = String(definition.retry_eligible === true);
        node.dataset.pauseEligible = String(definition.pause_eligible === true);
        node.dataset.resumeEligible = String(definition.resume_eligible === true);
        node.dataset.pauseScheduled = String(definition.pause_scheduled === true);
        node.dataset.retryRuntimeConfigured = definition.retry_runtime_configured == null ? '' : String(definition.retry_runtime_configured);
        node.dataset.recoveryRequestId = String(definition.recovery_request?.request_id || '');
        node.dataset.recoverySuccessor = String(definition.recovery_request?.successor_job_id || '');
        node.dataset.configurationSource = String(definition.configuration_source || 'TASK_CREATION_SNAPSHOT');
        node.dataset.enabled = String(definition.state !== 'DISABLED');
        node.dataset.optional = String(definition.optional === true);
        node.dataset.human = String(definition.node_id === '09_HUMAN_FINAL');
        node.dataset.errorCode = String(definition.error_code || binding.error_code || '');
        const capacity = definition.capacity || {};
        const effectiveCapacity = capacity.effective_capacity || {};
        node.dataset.capacityProvider = String(capacity.provider_input_capacity_tokens || '');
        node.dataset.capacityProviderOutput = String(capacity.provider_output_capacity_tokens || '');
        node.dataset.capacityNodeOutput = String(capacity.node_requested_output_tokens || '');
        node.dataset.capacityMachine = String(capacity.machine_safe_input_tokens || '');
        node.dataset.effectiveCapacity = String(effectiveCapacity.input_tokens || '');
        node.dataset.effectiveOutputCapacity = String(effectiveCapacity.output_tokens || '');
        node.dataset.capacitySource = String(capacity.capacity_source || capacity.effective_capacity_source || '');
        node.dataset.status = state.label;
        node.dataset.tone = state.tone;
        step.classList.remove('state-green', 'state-blue', 'state-yellow', 'state-red', 'state-neutral', 'state-disabled', 'link-green', 'link-blue', 'link-yellow', 'link-red');
        step.classList.add('state-' + state.tone);
        if (index < workflowNodes.length - 1 && !['neutral', 'disabled'].includes(state.tone)) {
          step.classList.add('link-' + state.tone);
        }
        node.querySelector('.desktop-tasks-node-index').textContent = String(index + 1).padStart(2, '0');
        node.querySelector('.desktop-tasks-node-copy strong').textContent = node.dataset.node;
        node.querySelector('[data-node-purpose]').textContent = purpose;
        const modelValue = node.querySelector('[data-node-model]');
        if (modelValue) {
          modelValue.textContent = definition.node_id === '06_CONTEXT_PACK'
            ? `同步 02 · ${model}`
            : model;
        }
        node.querySelector('.desktop-tasks-node-status').innerHTML = statusIcon(state.tone) + '<span data-flow-state>' + state.label + '</span>';
        if(definition.pause_scheduled && binding.control_state!=='PAUSED'){
          const hint=document.createElement('span');
          hint.className='paper-review-pause-hint';
          hint.textContent=window.__MEMORIVE_REVIEW_UI__?.pauseHint(node.dataset.node)||('将在'+node.dataset.node+'前暂停');
          node.querySelector('.desktop-tasks-node-status').prepend(hint);
        }
        node.setAttribute('aria-label', `${node.dataset.node}，${state.label}，${model}`);
        window.__MEMO_NODE_PROGRESS__?.apply(node,definition,binding);
      });
      const disabledNodes = workflowNodes.filter((node) => node?.state === 'DISABLED');
      flowList.classList.toggle('is-card-review-disabled', disabledNodes.some((node) => node.node_id === '04_CARD_CROSS_CHECK'));
      flowList.classList.toggle('is-judgment-review-disabled', disabledNodes.some((node) => node.node_id === '08_JUDGMENT_CROSS_CHECK'));
      const activeRoute = workflowNodes.filter((node) => node?.state !== 'DISABLED').map((node) => String(node.number || '').padStart(2, '0'));
      const bypassed = disabledNodes.map((node) => String(node.number || '').padStart(2, '0'));
      q('#tasks-route-copy').textContent = activeRoute.join(' → ') + (bypassed.length ? ` · 绕过 ${bypassed.join('、')}` : ' · 无绕过');
      const completed = Number(binding.progress?.completed_units || 0);
      const total = Number(binding.progress?.total_units || workflowNodes.length || 9);
      q('#tasks-flow-source').textContent = `实际文献处理作业 · ${completed}/${total} 节点`;
      q('#tasks-flow-help').textContent = '该任务使用收件箱派发时冻结的模型映射与输入哈希；未开始的节点可建立任务级覆盖，失败节点可在继续前调整。';
      q('#tasks-research-actions')?.remove();
      if(binding.workflow_kind.startsWith('RESEARCH_')){
        q('#tasks-flow-source').textContent='实际研究任务 · '+completed+'/'+total+' 节点';
        q('#tasks-flow-help').textContent='使用创建时冻结的研究方向与来源。只有用户的明确反馈影响下一批；不自动下载全文或生成 Analysis。';
        if(binding.workflow_kind==='RESEARCH_REPORT'){
          q('#tasks-flow-source').textContent='周期报告';
          q('#tasks-flow-help').textContent='';
        }
        const actions=document.createElement('div');
        actions.id='tasks-research-actions';
        actions.className='desktop-research-task-actions';
        q('#tasks-route-status').after(actions);
        window.__Desktop_RESEARCH__?.taskActions(binding.job_id,binding,actions);
      }
      if (selectedNode) updateInspector(selectedNode);
      window.requestAnimationFrame(() => drawBypass());
    };
    const renderTaskFlow = ({ animateBypass = false } = {}) => {
      if (!currentTask) return;
      if(!currentTask.dataset.workflowKind?.startsWith('RESEARCH_'))q('#tasks-research-actions')?.remove();
      if (currentTask.dataset.workflowKind === 'SESSION_REFINEMENT') {
        renderRefinementTaskFlow();
        return;
      }
      if (['CORE_DOCUMENT','CONSOLE_SIMULATION','RESEARCH_DISCOVERY','RESEARCH_REPORT'].includes(currentTask.dataset.workflowKind)) {
        renderProductionTaskFlow();
        return;
      }
      restoreCoreFlowNodes();
      const snapshot = getCurrentSnapshot();
      syncContextFromEmbedding(snapshot);
      const profile = taskProfiles[currentTask.dataset.run] || taskProfiles['run-042'];
      const steps = Array.from(flowList.querySelectorAll('.desktop-tasks-flow-step')); 
      steps.forEach((step, index) => {
        const node = step.querySelector('.desktop-tasks-node');
        const key = node.dataset.step;
        const optional = node.dataset.optional === 'true';
        const disabled = optional && !isTaskNodeEnabled(snapshot, key);
        const state = profile.statuses[key] || { tone:'neutral', label:'未开始', human:false };
        const tone = disabled ? 'disabled' : state.tone;
        const label = disabled ? (state.tone === 'neutral' ? '已禁用 · 可调整' : '已禁用') : state.label;
        const model = snapshot.models[key] || defaultModels[key];
        step.classList.remove('state-green', 'state-blue', 'state-yellow', 'state-red', 'state-neutral', 'state-disabled', 'link-green', 'link-blue', 'link-yellow', 'link-red');
        step.classList.add('state-' + tone);
        if (!disabled && index < steps.length - 1 && tone !== 'neutral') step.classList.add('link-' + tone);
        node.dataset.enabled = String(!disabled);
        node.dataset.status = label;
        node.dataset.tone = tone;
        node.dataset.human = String(!disabled && state.human === true);
        node.dataset.model = model;
        node.dataset.profileRef = snapshot.profileRefs[key] || '';
        if (node.dataset.modelLocked !== 'true' || node.dataset.profileSourceStep) {
          const modelValue = node.querySelector('[data-node-model]');
          if (modelValue) modelValue.textContent = model;
        }
        node.querySelector('.desktop-tasks-node-status').innerHTML = statusIcon(tone) + '<span data-flow-state>' + label + '</span>';
        node.setAttribute('aria-label', (node.dataset.node || '') + '，' + label + (disabled ? '，当前路径绕过此节点' : ''));
        window.__MEMO_NODE_PROGRESS__?.clear(node);
      });
      const cardReviewEnabled = isTaskNodeEnabled(snapshot, 'card-review');
      const judgmentReviewEnabled = isTaskNodeEnabled(snapshot, 'judgment-review');
      flowList.classList.toggle('is-card-review-disabled', !cardReviewEnabled);
      flowList.classList.toggle('is-judgment-review-disabled', !judgmentReviewEnabled);
      const activeRoute = ['01', '02', '03'].concat(cardReviewEnabled ? ['04'] : [], ['05', '06', '07'], judgmentReviewEnabled ? ['08'] : [], ['09']);
      const bypassed = [!cardReviewEnabled ? '04' : '', !judgmentReviewEnabled ? '08' : ''].filter(Boolean);
      q('#tasks-route-copy').textContent = activeRoute.join(' → ') + (bypassed.length ? ' · 绕过 ' + bypassed.join('、') : ' · 无绕过');
      q('#tasks-flow-source').textContent = currentPre
        ? '设置 v' + snapshot.revision + ' · 新任务快照'
        : '运行冻结快照 · v' + snapshot.revision;
      q('#tasks-flow-help').textContent = currentPre
        ? '创建时设置'
        : '创建时设置';
      if (selectedNode) updateInspector(selectedNode);
      window.requestAnimationFrame(() => drawBypass({ animate:animateBypass }));
    };
    const updateInspector = (node) => {
      if (window.__MEMORIVE_REVIEW_UI__?.renderTaskInspector(node,root)) {
        setModelMenu(false);
        return;
      }
      const deleteTask = q('#tasks-delete-task');
      const selectedProductionBinding = productionBindings.get(currentTask?.dataset.run || '');
      deleteTask.hidden = !(
        currentTask?.dataset.workflowKind === 'CORE_DOCUMENT'
        && currentTask?.dataset.collectionState !== 'HISTORY'
        && selectedProductionBinding?.control_state === 'FAILED'
        && node?.dataset.tone === 'red'
      );
      deleteTask.disabled = deleteTask.getAttribute('aria-busy') === 'true';
      if (currentTask?.dataset.workflowKind !== 'CORE_DOCUMENT') q('#tasks-node-control').hidden = true;
      q('#tasks-adjust-footer').hidden = false;

      q('#tasks-fallback-row').hidden = true;
      if (currentTask?.dataset.workflowKind === 'SESSION_REFINEMENT') {
        const binding = refinementBindings.get(currentTask.dataset.run);
        const tone = node?.dataset.tone || 'neutral';
        q('#tasks-node-title').textContent = node?.dataset.node || '会话精炼节点';
        q('#tasks-state-node').textContent = node?.dataset.node || '会话精炼节点';
        q('#tasks-state-text').textContent = node?.dataset.status || '未开始';
        q('#tasks-state-summary').textContent = node?.dataset.summary || node?.dataset.purpose || '';
        const modelAllowed = canReplaceModel(node);
        q('#tasks-state-config').textContent = node?.dataset.modelLocked === 'true'
          ? '创建时设置 · 只读'
          : '会话精炼创建时快照 · 本任务可调整';
        q('#tasks-node-status').className = 'badge' + (['green', 'blue', 'yellow', 'red'].includes(tone) ? ' ' + tone : '');
        q('#tasks-node-status').innerHTML = statusIcon(tone) + (node?.dataset.status || '未开始');
        q('#tasks-human').hidden = true;
        q('#tasks-capacity').hidden = true;
        q('#tasks-adjust').hidden = !modelAllowed;
        q('#tasks-enable-row').hidden = true;
        q('#tasks-model-row').hidden = !modelAllowed;
        q('#tasks-retry-row').hidden = true;
        q('#tasks-restore-template').hidden = true;
        q('#tasks-node-toggle').disabled = true;
        modelButton.disabled = !modelAllowed;
        q('#tasks-retry-minus').disabled = true;
        q('#tasks-retry-plus').disabled = true;
        q('#tasks-restore-template').disabled = true;
        if (modelAllowed) {
          const snapshot = {
            models:{ [node.dataset.step]:node.dataset.model },
            profileRefs:{ [node.dataset.step]:node.dataset.profileRef || '' }
          };
          const option = getModelOption(node, snapshot, node.dataset.model);
          q('#tasks-current-model').textContent = node.dataset.model;
          q('#tasks-model-dot').className = 'status-dot ' + (option?.state || 'green');
          q('#tasks-model-copy').textContent = '该模型节点未开始；替换只影响本任务，开始执行后自动锁定。';
          q('#tasks-adjust-copy').textContent = '仅此任务';
          renderModelMenu(node, snapshot);
        } else {
          setModelMenu(false);
        }
        return;
      }
      if (['CORE_DOCUMENT','RESEARCH_DISCOVERY','RESEARCH_REPORT'].includes(currentTask?.dataset.workflowKind)) {
        const tone = node?.dataset.tone || 'neutral';
        q('#tasks-node-title').textContent = node?.dataset.node || '文献处理节点';
        q('#tasks-state-node').textContent = node?.dataset.node || '文献处理节点';
        q('#tasks-state-text').textContent = node?.dataset.status || '未开始';
        q('#tasks-state-summary').textContent = node?.dataset.summary || node?.dataset.purpose || '';
        const toggleAllowed = canToggleNode(node);
        const modelAllowed = canReplaceModel(node);
        const retryAllowed = node?.dataset.retryChangeEligible === 'true';
        const overridden = node?.dataset.configurationSource === 'TASK_OVERRIDE';
        q('#tasks-state-config').textContent = overridden
          ? '收件箱派发时冻结的运行快照 · 本任务覆盖'
          : '收件箱派发时冻结的运行快照';
        if(currentTask.dataset.workflowKind.startsWith('RESEARCH_'))q('#tasks-state-config').textContent='任务创建时冻结的研究方向与来源';
        const hasCapacity = Boolean(node?.dataset.capacitySource);
        q('#tasks-capacity').hidden = !hasCapacity;
        if (hasCapacity) {
          const tokens = (value, fallback) => value ? `${Number(value).toLocaleString()} tokens` : fallback;
          q('#tasks-capacity-provider').textContent = `输入 ${tokens(node.dataset.capacityProvider, '未公开')} / 输出 ${tokens(node.dataset.capacityProviderOutput, '未公开')}`;
          q('#tasks-capacity-node').textContent = tokens(node.dataset.capacityNodeOutput, '节点不添加独立输出硬上限');
          q('#tasks-capacity-machine').textContent = tokens(node.dataset.capacityMachine, '未设置独立本机硬上限');
          q('#tasks-capacity-effective').textContent = `输入 ${tokens(node.dataset.effectiveCapacity, '运行时管理')} / 输出 ${tokens(node.dataset.effectiveOutputCapacity, '运行时管理')}`;
          q('#tasks-capacity-source').textContent = node.dataset.capacitySource;
        }
        q('#tasks-node-status').className = 'badge' + (['green', 'blue', 'yellow', 'red'].includes(tone) ? ' ' + tone : '');
        q('#tasks-node-status').innerHTML = statusIcon(tone) + (node?.dataset.status || '未开始');
        const rollbackAllowed = node?.dataset.rollbackEligible === 'true';
        const retryExecutionAllowed = node?.dataset.retryEligible === 'true';
        const recoveryPending = Boolean(node?.dataset.recoveryRequestId);
        const control = q('#tasks-node-control');
        const action = node?.dataset.resumeEligible === 'true' ? 'resume'
          : node?.dataset.pauseEligible === 'true' ? 'pause'
          : rollbackAllowed || retryExecutionAllowed ? 'retry' : '';
        const label = {pause:'暂停此节点',resume:'继续',retry:'从此节点重试'}[action] || '';
        control.hidden = currentTask?.dataset.workflowKind !== 'CORE_DOCUMENT' || !action || recoveryPending;
        control.dataset.action = action;
        if (control.dataset.restartArmed !== 'true') {
          control.setAttribute('aria-label',label);
          control.dataset.desktopTip=label;
          const paths = {pause:'<path d="M8 5v14M16 5v14"/>',
            resume:'<path d="m8 5 11 7-11 7z"/>',
            retry:'<path d="M3 10a9 9 0 1 1 2 8M3 4v6h6"/>'};
          control.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'+(paths[action]||'')+'</svg>';
        }
        control.disabled = control.getAttribute('aria-busy') === 'true';
        q('#tasks-human').hidden = !recoveryPending;
        q('#tasks-open-successor').hidden = !node?.dataset.recoverySuccessor;
        q('#tasks-open-successor').dataset.jobId = node?.dataset.recoverySuccessor || '';
        if (rollbackAllowed || retryExecutionAllowed || recoveryPending) {
          q('#tasks-return-title').textContent = retryExecutionAllowed ? '重试此节点' : '从此步骤重新处理';
          q('#tasks-return-copy').textContent = recoveryPending ? '同一文献已有继续处理的任务，请查看当前任务。'
            : `将从“${node.dataset.node}”继续处理，旧执行记录保留在历史中。`;
          if (q('#tasks-return').dataset.restartArmed !== 'true') {
            q('#tasks-return').textContent = retryExecutionAllowed ? '重试此节点' : '从此步骤重新执行';
          }
          q('#tasks-return').disabled = recoveryPending || q('#tasks-return').getAttribute('aria-busy') === 'true';
        }
        q('#tasks-adjust').hidden = !toggleAllowed && !modelAllowed && !retryAllowed;
        q('#tasks-enable-row').hidden = !toggleAllowed;
        q('#tasks-model-row').hidden = !modelAllowed;
        const ingest = node.dataset.step === 'ingest';
        q('#tasks-model-label').textContent = ingest ? '主模型 · 必选' : '当前模型';
        q('#tasks-fallback-row').hidden = !ingest || !modelAllowed;
        const fallbackSelect = q('#tasks-fallback-select');
        if (ingest && modelAllowed) {
          const choices = [{profileRef:'', model:'未设置'}, ...configuredModelOptions.filter(option =>
            option.profileRef && option.profileRef !== node.dataset.profileRef &&
            (option.capabilities || []).some(cap => ['CHAT','VISION_OCR'].includes(cap)))];
          fallbackSelect.replaceChildren(...choices.map(choice => {
            const option = document.createElement('option');
            option.value = choice.profileRef;
            option.textContent = choice.model;
            return option;
          }));
          fallbackSelect.value = node.dataset.fallbackProfileRef || '';
          fallbackSelect.disabled = false;
        }
        q('#tasks-retry-copy').textContent = ingest ? '' : '发生暂时性错误时重试';
        q('#tasks-retry-copy').hidden = ingest;
        q('#tasks-retry-row').hidden = !retryAllowed;
        q('#tasks-restore-template').hidden = !overridden;
        q('#tasks-node-toggle').disabled = !toggleAllowed;
        modelButton.disabled = !modelAllowed;
        q('#tasks-retry-minus').disabled = !retryAllowed;
        q('#tasks-retry-plus').disabled = !retryAllowed;
        q('#tasks-restore-template').disabled = !overridden;
        if (toggleAllowed) {
          const enabled = node.dataset.enabled === 'true';
          q('#tasks-node-toggle').setAttribute('aria-checked', String(enabled));
          q('#tasks-enable-label').textContent = enabled ? '节点已启用' : '节点已禁用';
        }
        if (modelAllowed) {
          const snapshot = {
            models:{ [node.dataset.step]:node.dataset.model },
            profileRefs:{ [node.dataset.step]:node.dataset.profileRef || '' }
          };
          const option = getModelOption(node, snapshot, node.dataset.model);
          q('#tasks-current-model').textContent = node.dataset.model;
          q('#tasks-model-dot').className = 'status-dot ' + (option?.state || 'green');
          q('#tasks-model-copy').textContent = node.dataset.tone === 'red'
            ? '该失败节点可在继续前替换模型；只影响本任务后继执行。'
            : '只修改当前任务；该节点开始后自动锁定。';
          renderModelMenu(node, snapshot);
        } else {
          setModelMenu(false);
        }
        if (retryAllowed) {
          const retryCount = Math.max(0, Math.min(10, Number(node.dataset.retryCount || 0)));
          const retryConsumed = node.dataset.retryConsumed === '' ? '—' : String(Number(node.dataset.retryConsumed));
          const retryRemaining = node.dataset.retryRemaining === '' ? '—' : String(Number(node.dataset.retryRemaining));
          q('#tasks-retry-count').textContent = String(retryCount);
          const recordedLimit = node.dataset.retryRuntimeConfigured;
          q('#tasks-retry-summary').textContent = recordedLimit
            ? `${node.dataset.retryPageNumber ? `第 ${node.dataset.retryPageNumber} 页` : '最近调用'}：已重试 ${retryConsumed} / ${recordedLimit} · 剩余 ${retryRemaining}`
            : `已配置 ${retryCount} · 已使用 ${retryConsumed} · 剩余 ${retryRemaining}`;
          q('#tasks-retry-minus').disabled = retryCount <= 0;
          q('#tasks-retry-plus').disabled = retryCount >= 10;
        }
        q('#tasks-adjust-copy').textContent = overridden ? '本任务设置' : '';
        q('#tasks-adjust-footer').hidden = !overridden;
        return;
      }
      const snapshot = getCurrentSnapshot();
      q('#tasks-capacity').hidden = true;
      if (!snapshot || !node) return;
      const step = node.dataset.step;
      const enableOverridden = snapshot.overrides.has(step);
      const modelOverridden = snapshot.modelOverrides.has(step);
      const overridden = enableOverridden || modelOverridden;
      const toggleAllowed = canToggleNode(node);
      const modelAllowed = canReplaceModel(node);
      q('#tasks-node-title').textContent = node.dataset.node;
      q('#tasks-state-node').textContent = node.dataset.node;
      q('#tasks-state-text').textContent = node.dataset.status;
      q('#tasks-state-summary').textContent = node.dataset.summary;
      const sourceCopy = currentPre
        ? '设置 v' + snapshot.revision + ' · 创建时快照'
        : '运行冻结快照 · 设置 v' + snapshot.revision;
      q('#tasks-state-config').textContent = sourceCopy + (overridden ? ' · 本任务覆盖' : '');
      const tone = node.dataset.tone;
      q('#tasks-node-status').className = 'badge' + (['green', 'blue', 'yellow', 'red'].includes(tone) ? ' ' + tone : '');
      q('#tasks-node-status').innerHTML = statusIcon(tone) + node.dataset.status;
      q('#tasks-human').hidden = node.dataset.human !== 'true';
      q('#tasks-adjust').hidden = !toggleAllowed && !modelAllowed;
      q('#tasks-enable-row').hidden = !toggleAllowed;
      q('#tasks-model-row').hidden = !modelAllowed;
      q('#tasks-retry-row').hidden = true;
      q('#tasks-node-toggle').disabled = !toggleAllowed;
      modelButton.disabled = !modelAllowed;
      q('#tasks-retry-minus').disabled = true;
      q('#tasks-retry-plus').disabled = true;
      if (toggleAllowed) {
        const enabled = isTaskNodeEnabled(snapshot, step);
        q('#tasks-node-toggle').setAttribute('aria-checked', String(enabled));
        q('#tasks-enable-label').textContent = enabled ? '节点已启用' : '节点已禁用';
        q('#tasks-enable-copy').textContent = '只修改本任务未开始的可选节点；不会改变全局模板。';
      }
      if (modelAllowed) {
        const model = snapshot.models[step] || defaultModels[step];
        const option = getModelOption(node, snapshot, model);
        q('#tasks-current-model').textContent = model;
        q('#tasks-model-dot').className = 'status-dot ' + (option?.state || 'green');
        q('#tasks-model-copy').textContent = modelOverridden
          ? '当前为本任务覆盖；尚未进入本节点。'
          : '当前取自创建时设置；尚未进入本节点。';
        renderModelMenu(node, snapshot);
      } else {
        setModelMenu(false);
      }
      if (overridden) {
        const parts = [enableOverridden ? '启用状态' : '', modelOverridden ? '模型' : ''].filter(Boolean);
        q('#tasks-adjust-copy').textContent = parts.join('与') + '已更新';
      } else {
        q('#tasks-adjust-copy').textContent = toggleAllowed && modelAllowed
          ? '仅此任务'
          : toggleAllowed
            ? '仅此任务'
            : '仅此任务';
      }
      q('#tasks-restore-template').hidden = !overridden;
      q('#tasks-restore-template').disabled = !overridden;
    };
    const openNodeInspector = (node) => {
      if (selectedNode === node && stage.classList.contains('has-inspector')) {
        // SAME_TASK_NODE_ALREADY_OPEN: do not replay the inspector transition.
        return;
      }
      resetReturn();
      selectedNode = node;
      lastInspectedNode = node;
      const version = ++nodeTransition;
      qa('.desktop-tasks-node').forEach((item) => {
        const selected = item === node;
        item.setAttribute('aria-selected', String(selected));
        item.setAttribute('aria-expanded', String(selected));
      });
      window.requestAnimationFrame(() => {
        if (version !== nodeTransition || selectedNode !== node) return;
        updateInspector(node);
        resetReturn();
        flowResizer.hidden = true;
        stage.classList.add('has-inspector');
        preview.setInspector(true);
        window.requestAnimationFrame(() => {
          updateFlowResizerBounds();
          drawBypass();
        });
      });
    };
    const hydrateTaskSnapshots = (snapshots) => {
      if (!snapshots || typeof snapshots !== 'object') return;
      Object.entries(snapshots).forEach(([run, raw]) => {
        if (!tasks.some((task) => task.dataset.run === run) || !raw || typeof raw !== 'object') return;
        const revision = Number(raw.revision);
        const baseEnabled = cloneEnabled(raw.baseEnabled);
        const enabled = cloneEnabled(raw.enabled);
        const baseModels = cloneModels(raw.baseModels);
        const models = cloneModels(raw.models, baseModels);
        const baseProfileRefs = cloneProfileRefs(raw.baseProfileRefs);
        const profileRefs = cloneProfileRefs(raw.profileRefs, baseProfileRefs);
        const hydrated = {
          run,
          revision:Number.isInteger(revision) && revision > 0 ? revision : 1,
          origin:raw.origin === 'settings-template' ? 'settings-template' : 'run-frozen',
          baseEnabled,
          enabled,
          overrides:new Set(Array.isArray(raw.overrides) ? raw.overrides.filter((step) => optionalSteps.includes(step)) : []),
          baseModels,
          models,
          baseProfileRefs,
          profileRefs,
          modelOverrides:new Set(Array.isArray(raw.modelOverrides) ? raw.modelOverrides.filter((step) => stepKeys.includes(step)) : [])
        };
        syncContextFromEmbedding(hydrated);
        taskSnapshots.set(run, hydrated);
      });
      if (currentTask) renderTaskFlow();
    };
    const applyTemplateState = (template) => {
      if (!template || typeof template !== 'object') return;
      const revision = Number(template.revision);
      templateState = {
        revision:Number.isInteger(revision) && revision > 0 ? revision : templateState.revision,
        enabled:{
          'card-review':typeof template.enabled?.['card-review'] === 'boolean' ? template.enabled['card-review'] : templateState.enabled['card-review'],
          'judgment-review':typeof template.enabled?.['judgment-review'] === 'boolean' ? template.enabled['judgment-review'] : templateState.enabled['judgment-review']
        },
        models:cloneModels(template.models, templateState.models),
        profileRefs:cloneProfileRefs(template.profileRefs, templateState.profileRefs)
      };
      syncContextFromEmbedding(templateState);
    };

    const normalizeWorkflowModelOptions = (source) => {
      const configured = Array.isArray(source)
        ? source
        : Array.isArray(source?.configuredModelOptions)
          ? source.configuredModelOptions
          : Array.isArray(source?.configured_model_options)
            ? source.configured_model_options
            : [];
      const localProfiles = Array.isArray(source?.localModelProfiles)
        ? source.localModelProfiles
        : Array.isArray(source?.local_model_profiles)
          ? source.local_model_profiles
          : Array.isArray(source?.model_profiles)
            ? source.model_profiles
            : [];
      const seen = new Set();
      return [...configured, ...localProfiles].flatMap((option) => {
        if (!option || typeof option !== 'object') return [];
        const profileRef = String(option.profileRef || option.profile_ref || '').trim();
        if (!profileRef || seen.has(profileRef)) return [];
        const local = /^local:[0-9a-f]{24}$/i.test(profileRef);
        let model = String(
          option.model || option.display_name || option.displayName || option.model_name || profileRef
        ).trim();
        if (!model) return [];
        if (local && !model.startsWith('[本地]')) model = `[本地] ${model}`;
        const capabilities = (Array.isArray(option.capabilities)
          ? option.capabilities
          : [option.capability || 'CHAT'])
          .map((value) => String(value || '').trim().toUpperCase())
          .filter(Boolean);
        const embeddingEligible = option.embeddingEligible === true || option.embedding_eligible === true || (
          local
          && capabilities.includes('EMBEDDING')
          && option.connection_status === 'AVAILABLE'
          && option.endpoint_kind === 'ollama'
          && option.exact_identity_available === true
          && option.execution_eligible === true
        );
        seen.add(profileRef);
        return [{
          ...option,
          profileRef,
          model,
          capabilities,
          embeddingEligible,
          modelKey:String(option.modelKey || option.model_key || option.model_name || '').trim(),
          state:option.state || (option.connection_status === 'AVAILABLE' ? 'green' : 'yellow'),
          note:option.note || (local ? '已识别本地模型' : '已配置模型 API')
        }];
      });
    };

    const setConfiguredModelOptions = (options) => {
      const normalized = normalizeWorkflowModelOptions(options);
      const nextOptions = Array.isArray(normalized)
        ? normalized
          .filter((option) => option && typeof option.profileRef === 'string' && typeof option.model === 'string')
          .map((option) => ({
            ...option,
            modelKey:typeof option.modelKey === 'string' ? option.modelKey : '',
            state:option.state || 'yellow',
            note:option.note || '已配置模型 API'
          }))
        : [];
      const fingerprint = JSON.stringify(nextOptions);
      if (fingerprint === configuredModelOptionsFingerprint) return false;
      configuredModelOptions = nextOptions;
      configuredModelOptionsFingerprint = fingerprint;
      if (currentTask) renderTaskFlow();
      return true;
    };
    const refreshTaskCount = () => {
      const counts = { '等待批准':0, '等待执行':0, '正在执行':0, '执行异常':0, '已经结束':0 };
      const visibleTasks = tasks.filter((task) => !task.hidden);
      visibleTasks.forEach((task) => { if (Object.hasOwn(counts, task.dataset.state)) counts[task.dataset.state] += 1; });
      const detail = `等待批准 ${counts['等待批准']} · 等待执行 ${counts['等待执行']} · 正在执行 ${counts['正在执行']} · 执行异常 ${counts['执行异常']} · 已经结束 ${counts['已经结束']}`;
      const status = q('#tasks-list-status');
      if (status) {
        status.hidden = visibleTasks.length > 0 || taskProjectionState === 'ready';
        status.textContent = taskProjectionState === 'loading' ? '正在加载任务…'
          : taskProjectionState === 'error' ? '任务加载失败，请刷新重试'
          : taskMode === 'history' ? '暂无历史任务' : '暂无执行中的任务';
      }
      const empty = q('#tasks-empty');
      if (empty) {
        empty.hidden = visibleTasks.length > 0 || taskProjectionState !== 'ready';
        q('#tasks-empty-copy').textContent = taskMode === 'history' ? '暂无历史任务' : '暂无执行中的任务';
      }
      q('#tasks-task-count').textContent = visibleTasks.length;
      q('#tasks-task-count').setAttribute('aria-label', `${taskMode === 'history' ? '历史任务' : '当前任务'} ${visibleTasks.length} 项`);
      q('#tasks-task-count').dataset.desktopTip = detail;
    };
    const keepSelectedTaskVisible = () => {
      if (!currentTask?.isConnected || !stage.classList.contains('has-flow')) return taskMode;
      taskMode = currentTask.dataset.collectionState === 'HISTORY' ? 'history' : 'current';
      return taskMode;
    };
    const setTaskMode = (mode) => {
      taskMode = mode === 'history' ? 'history' : 'current';
      const wanted = taskMode === 'history' ? 'HISTORY' : 'CURRENT';
      tasks.forEach((task) => {
        const collection_state = task.dataset.collectionState || 'CURRENT';
        task.hidden = collection_state !== wanted || (wanted === 'CURRENT' && task.dataset.executionWaiting === 'true');
      });
      q('#tasks-mode')?.querySelectorAll('button[data-mode]').forEach((button) => {
        button.setAttribute('aria-pressed', String(button.dataset.mode === taskMode));
      });
      q('#tasks-eyebrow').textContent = '';
      q('#tasks-heading-title').textContent = taskMode === 'history' ? '历史任务' : '当前任务';
      if (currentTask?.hidden) void returnToTaskList();
      refreshTaskCount();
      return taskMode;
    };
    const refinementTaskPresentation = Object.freeze({
      QUEUED:{ bucket:'等待执行', label:'等待执行', tone:'yellow' },
      RUNNING:{ bucket:'正在执行', label:'正在执行', tone:'blue' },
      PAUSED:{ bucket:'已暂停', label:'已暂停', tone:'yellow' },
      PAUSE_REQUESTED:{ bucket:'已暂停', label:'暂停中', tone:'yellow' },
      SUCCEEDED:{ bucket:'已经结束', label:'已完成', tone:'green' },
      FAILED:{ bucket:'执行异常', label:'执行异常', tone:'red' },
      CANCELLED:{ bucket:'已经结束', label:'已取消', tone:'yellow' }
    });
    const formatRetentionCountdown = (rawSeconds) => {
      const seconds = Math.max(0, Number.parseInt(rawSeconds, 10) || 0);
      return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
    };
    const refreshRetentionCountdowns = () => {
      const now = Date.now();
      taskList.querySelectorAll('.desktop-tasks-task [data-retention-countdown]').forEach((countdown) => {
        const task = countdown.closest('.desktop-tasks-task');
        const removalAt = Date.parse(task?.dataset.removalAt || '');
        if (!task || !Number.isFinite(removalAt)) return;
        const remaining = Math.max(0, Math.ceil((removalAt - now) / 1000));
        const label = formatRetentionCountdown(remaining);
        countdown.textContent = label;
        task.dataset.retentionSecondsRemaining = String(remaining);
        task.setAttribute('aria-label', `${task.dataset.task || '任务'}，已经结束，${label} 后移出当前任务`);
      });
    };
    const formatTaskTimestamp = (raw) => {
      const parsed = new Date(String(raw || ''));
      if (Number.isNaN(parsed.getTime())) {
        return { raw:String(raw || ''), day:'日期未知', clock:'—', full:'时间未知' };
      }
      const now = new Date();
      const sameLocalDay = parsed.getFullYear() === now.getFullYear()
        && parsed.getMonth() === now.getMonth()
        && parsed.getDate() === now.getDate();
      const day = sameLocalDay
        ? '今天'
        : new Intl.DateTimeFormat('zh-CN', { month:'2-digit', day:'2-digit' })
          .format(parsed).replace('/', '-');
      const clock = new Intl.DateTimeFormat('zh-CN', {
        hour:'2-digit', minute:'2-digit', hourCycle:'h23'
      }).format(parsed);
      const full = new Intl.DateTimeFormat('zh-CN', {
        year:'numeric', month:'2-digit', day:'2-digit',
        hour:'2-digit', minute:'2-digit', second:'2-digit', hourCycle:'h23'
      }).format(parsed);
      return { raw:String(raw), day, clock, full };
    };
    const appendTaskTimestamp = (task, row, historical) => {
      task.classList.toggle('has-task-time', historical);
      if (!historical) return null;
      const timestamp = formatTaskTimestamp(
        row.completed_at || row.updated_at || row.created_at || ''
      );
      task.dataset.displayTime = timestamp.full;
      const time = document.createElement('time');
      time.className = 'desktop-tasks-task-time';
      time.dateTime = timestamp.raw;
      time.setAttribute('aria-label', timestamp.full);
      const timeDay = document.createElement('span');
      timeDay.className = 'desktop-tasks-time-day';
      timeDay.textContent = timestamp.day;
      const timeClock = document.createElement('span');
      timeClock.className = 'desktop-tasks-time-clock';
      timeClock.textContent = timestamp.clock;
      time.append(timeDay, timeClock);
      task.append(time);
      return timestamp;
    };
    const taskFlowSignature = (row) => JSON.stringify({
      workflow_kind:row?.workflow_kind || null,
      control_state:row?.control_state || null,
      error_code:row?.error_code || null,
      selected_profile_ref:row?.selected_profile_ref || null,
      workflow_nodes:row?.workflow_nodes || [],
      literature_review:row?.literature_review || null,
      progress:row?.progress || null,
      artifact_count:Number(row?.artifact_count || 0),
      model_calls:Number(row?.model_calls || 0),
      external_model_calls:Number(row?.external_model_calls || 0)
    });
    let productionMode = false;
    const taskStateRank = Object.freeze({
      '正在执行':0, '等待批准':1, '等待执行':2, '执行异常':3, '已经结束':4
    });
    const sortTaskRows = () => {
      const scrollTop = taskList.scrollTop;
      const focusedRun = document.activeElement?.closest?.('.desktop-tasks-task')?.dataset?.run || null;
      const currentOrder = [...taskList.querySelectorAll('.desktop-tasks-task')];
      const nextOrder = [...currentOrder].sort((left, right) => {
          const stateDelta = (taskStateRank[left.dataset.state] ?? 5)
            - (taskStateRank[right.dataset.state] ?? 5);
          if (stateDelta) return stateDelta;
          const leftHistorical = left.dataset.collectionState === 'HISTORY';
          const rightHistorical = right.dataset.collectionState === 'HISTORY';
          const leftStableTime = Date.parse(leftHistorical
            ? left.dataset.completedAt || left.dataset.updatedAt || ''
            : left.dataset.createdAt || '') || 0;
          const rightStableTime = Date.parse(rightHistorical
            ? right.dataset.completedAt || right.dataset.updatedAt || ''
            : right.dataset.createdAt || '') || 0;
          return rightStableTime - leftStableTime
            || ensureTaskStableOrder(left) - ensureTaskStableOrder(right)
            || String(left.dataset.run || '').localeCompare(String(right.dataset.run || ''));
        });
      if (nextOrder.some((task, index) => task !== currentOrder[index])) {
        nextOrder.forEach((task) => taskList.appendChild(task));
      }
      taskList.scrollTop = scrollTop;
      if (focusedRun) {
        taskList.querySelector(`.desktop-tasks-task[data-run="${CSS.escape(focusedRun)}"]`)
          ?.focus?.({ preventScroll:true });
      }
    };
    const setProductionMode = (enabled = true) => {
      const nextProductionMode = Boolean(enabled);
      const demoRowsRemain = nextProductionMode
        && Boolean(taskList.querySelector('.desktop-tasks-task:not([data-workflow-kind])'));
      if (productionMode === nextProductionMode && !demoRowsRemain) return productionMode;
      productionMode = nextProductionMode;
      const badge = root.querySelector('.desktop-sample-badge');
      if (badge) badge.hidden = productionMode;
      if (productionMode) {
        taskList.querySelectorAll('.desktop-tasks-task:not([data-workflow-kind])').forEach((task) => task.remove());
        if (currentTask && !currentTask.isConnected) {
          currentTask = null;
          void returnToTaskList();
        }
      }
      tasks = qa('.desktop-tasks-task');
      sortTaskRows();
      refreshTaskCount();
      return productionMode;
    };
    const isWaitingTaskRow = (row) => row.control_state === 'QUEUED'
      || (row.control_state === 'RUNNING' && Number(row.progress?.completed_units || 0) === 0
        && !(row.workflow_nodes || []).some(node => ['COMPLETED','RUNNING','PAUSED'].includes(node.state))
        && (row.progress?.waiting
        || String(row.progress?.phase_code || '').startsWith('WAITING_')
        || row.progress?.phase_code === 'QUEUED'));
    const setProjectionLoadState = (state) => {
      taskProjectionState = ['loading','ready','error'].includes(state) ? state : 'error';
      refreshTaskCount();
    };
    const upsertRefinementTasks = (rows) => {
      const accepted = Array.isArray(rows)
        ? rows.filter((row) => row && row.workflow_kind === 'SESSION_REFINEMENT' && typeof row.job_id === 'string')
        : [];
      const activeRuns = new Set(accepted.map((row) => row.job_id));
      let selectedFlowChanged = false;
      taskList.querySelectorAll('.desktop-tasks-task[data-workflow-kind="SESSION_REFINEMENT"]').forEach((task) => {
        if (!activeRuns.has(task.dataset.run)) task.remove();
      });
      refinementBindings.clear();
      accepted.forEach((row) => {
        const run = row.job_id;
        const presentation = refinementTaskPresentation[row.progress?.waiting && row.control_state === 'RUNNING' ? 'QUEUED' : row.control_state] || refinementTaskPresentation.QUEUED;
        refinementBindings.set(run, row);
        let task = taskList.querySelector(`.desktop-tasks-task[data-run="${CSS.escape(run)}"]`);
        if (!task) {
          task = document.createElement('button');
          task.className = 'desktop-tasks-task';
          task.type = 'button';
          task.setAttribute('aria-controls', 'tasks-flow');
          task.setAttribute('aria-expanded', 'false');
          task.setAttribute('aria-selected', 'false');
          taskList.prepend(task);
          ensureTaskStableOrder(task);
        }
        const name = String(row.display_name || '会话精炼任务');
        const nextFlowSignature = taskFlowSignature(row);
        const flowSignatureChanged = task.dataset.flowSignature !== nextFlowSignature;
        task.dataset.flowSignature = nextFlowSignature;
        if (currentTask?.dataset.run === run && flowSignatureChanged) selectedFlowChanged = true;
        task.dataset.task = name;
        task.dataset.run = run;
        task.dataset.jobId = run;
        task.dataset.state = presentation.bucket;
        task.dataset.pre = 'false';
        task.dataset.workflowKind = 'SESSION_REFINEMENT';
        task.dataset.executionWaiting = String(isWaitingTaskRow(row));
        task.dataset.collectionState = row.collection_state === 'HISTORY' ? 'HISTORY' : 'CURRENT';
        task.dataset.syntheticOnly = 'false';
        task.dataset.createdAt = String(row.created_at || '');
        task.dataset.completedAt = String(row.completed_at || '');
        task.dataset.updatedAt = String(row.updated_at || row.created_at || '');
        const historical = task.dataset.collectionState === 'HISTORY';
        const retainedSuccess = !historical && row.control_state === 'SUCCEEDED'
          && Number.isFinite(Number(row.retention_seconds_remaining));
        const stateLabel = presentation.label;
        task.dataset.retentionSecondsRemaining = retainedSuccess
          ? String(row.retention_seconds_remaining)
          : '';
        task.dataset.removalAt = String(row.removal_at || '');
        const presentationSignature = JSON.stringify([
          name, presentation.label, presentation.tone, historical,
          historical ? row.completed_at || row.updated_at || row.created_at || '' : null
        ]);
        if (task.dataset.presentationSignature !== presentationSignature) {
          task.dataset.presentationSignature = presentationSignature;
          task.replaceChildren();
          const copy = document.createElement('span');
          copy.title = name;
          const title = document.createElement('strong');
          title.textContent = name;
          copy.appendChild(title);
          const state = document.createElement('span');
          state.className = 'desktop-tasks-task-state';
          const dot = document.createElement('span');
          dot.className = `status-dot ${presentation.tone}${presentation.tone === 'blue' ? ' running' : ''}`;
          state.append(dot, document.createTextNode(presentation.label));
          task.append(copy, state);
          const timestamp = appendTaskTimestamp(task, row, historical);
          task.setAttribute('aria-label', timestamp
            ? `${name}，${presentation.label}，${timestamp.full}`
            : `${name}，${presentation.label}`);
        }
      });
      tasks = qa('.desktop-tasks-task');
      refreshRetentionCountdowns();
      sortTaskRows();
      keepSelectedTaskVisible();
      setTaskMode(taskMode);
      if (currentTask?.dataset.workflowKind === 'SESSION_REFINEMENT') {
        const refreshed = tasks.find((task) => task.dataset.run === currentTask.dataset.run);
        if (refreshed) {
          currentTask = refreshed;
          currentTask.setAttribute('aria-selected', 'true');
          currentTask.setAttribute('aria-expanded', 'true');
          if (selectedFlowChanged) renderTaskFlow();
        } else {
          void returnToTaskList();
        }
      }
      return accepted;
    };
    const upsertProductionTasks = (rows) => {
      const accepted = Array.isArray(rows)
        ? rows.filter((row) => row && ['CORE_DOCUMENT','CONSOLE_SIMULATION','RESEARCH_DISCOVERY','RESEARCH_REPORT'].includes(row.workflow_kind) && typeof row.job_id === 'string')
        : [];
      const activeRuns = new Set(accepted.map((row) => row.job_id));
      let selectedFlowChanged = false;
      taskList.querySelectorAll('.desktop-tasks-task[data-workflow-kind="CORE_DOCUMENT"], .desktop-tasks-task[data-workflow-kind="CONSOLE_SIMULATION"],.desktop-tasks-task[data-workflow-kind^="RESEARCH_"]').forEach((task) => {
        if (!activeRuns.has(task.dataset.run)) task.remove();
      });
      productionBindings.clear();
      accepted.forEach((row) => {
        const run = row.job_id;
        const presentation = refinementTaskPresentation[row.progress?.waiting && row.control_state === 'RUNNING' ? 'QUEUED' : row.control_state] || refinementTaskPresentation.QUEUED;
        productionBindings.set(run, row);
        let task = taskList.querySelector(`.desktop-tasks-task[data-run="${CSS.escape(run)}"]`);
        if (!task) {
          task = document.createElement('button');
          task.className = 'desktop-tasks-task';
          task.type = 'button';
          task.setAttribute('aria-controls', 'tasks-flow');
          task.setAttribute('aria-expanded', 'false');
          task.setAttribute('aria-selected', 'false');
          taskList.prepend(task);
          ensureTaskStableOrder(task);
        }
        const resumeNode = row.progress?.resume_from_node_id;
        const name = String(row.display_name || '文献处理任务') + (resumeNode ? ` · 从 ${String(resumeNode).slice(0,2)} 重制` : '');
        const completed = Number(row.progress?.completed_units || 0);
        const total = Number(row.progress?.total_units || 9);
        const progressLabel = row.control_state === 'RUNNING' && !row.progress?.waiting ? ` · ${completed}/${total}` : '';
        const nextFlowSignature = taskFlowSignature(row);
        const flowSignatureChanged = task.dataset.flowSignature !== nextFlowSignature;
        task.dataset.flowSignature = nextFlowSignature;
        if (currentTask?.dataset.run === run && flowSignatureChanged) selectedFlowChanged = true;
        task.dataset.task = name;
        task.dataset.run = run;
        task.dataset.jobId = run;
        task.dataset.state = presentation.bucket;
        task.dataset.pre = 'false';
        task.dataset.workflowKind = row.workflow_kind;
        task.dataset.executionWaiting = String(isWaitingTaskRow(row));
        task.dataset.collectionState = row.collection_state === 'HISTORY' ? 'HISTORY' : 'CURRENT';
        task.dataset.syntheticOnly = 'false';
        task.dataset.createdAt = String(row.created_at || '');
        task.dataset.completedAt = String(row.completed_at || '');
        task.dataset.updatedAt = String(row.updated_at || row.created_at || '');
        const historical = task.dataset.collectionState === 'HISTORY';
        const metaCopy = `${row.workflow_kind.startsWith('RESEARCH_') ? '研究任务' : row.workflow_kind === 'CONSOLE_SIMULATION' ? '控制台模拟' : '文献处理'}${resumeNode ? ` · 从 ${String(resumeNode).slice(0,2)} 继续` : ''} · ${completed}/${total} 节点 · ${Number(row.artifact_count || 0)} 份产物`;
        const presentationSignature = JSON.stringify([
          name, presentation.label, presentation.tone, progressLabel, metaCopy, historical,
          historical ? row.completed_at || row.updated_at || row.created_at || '' : null
        ]);
        if (task.dataset.presentationSignature !== presentationSignature) {
          task.dataset.presentationSignature = presentationSignature;
          task.replaceChildren();
          const copy = document.createElement('span');
          copy.title = name;
          const title = document.createElement('strong');
          title.textContent = name;
          const meta = document.createElement('small');
          meta.textContent = metaCopy;
          copy.append(title, meta);
          const state = document.createElement('span');
          state.className = 'desktop-tasks-task-state';
          const dot = document.createElement('span');
          dot.className = `status-dot ${presentation.tone}${presentation.tone === 'blue' ? ' running' : ''}`;
          state.append(dot, document.createTextNode(presentation.label + progressLabel));
          task.append(copy, state);
          const timestamp = appendTaskTimestamp(task, row, historical);
          task.setAttribute('aria-label', timestamp
            ? `${name}，${presentation.label}，${timestamp.full}`
            : `${name}，${presentation.label}${progressLabel}`);
        }
      });
      tasks = qa('.desktop-tasks-task');
      sortTaskRows();
      keepSelectedTaskVisible();
      setTaskMode(taskMode);
      if (['CORE_DOCUMENT','RESEARCH_DISCOVERY','RESEARCH_REPORT'].includes(currentTask?.dataset.workflowKind)) {
        const refreshed = tasks.find((task) => task.dataset.run === currentTask.dataset.run);
        if (refreshed) {
          currentTask = refreshed;
          currentTask.setAttribute('aria-selected', 'true');
          currentTask.setAttribute('aria-expanded', 'true');
          if (selectedFlowChanged) renderTaskFlow();
        } else {
          void returnToTaskList();
        }
      }
      return accepted;
    };
    const resetReturn = () => {
      clearInterval(timer);
      timer = 0;
      left = 10;
      q('#tasks-return-start').hidden = false;
      q('#tasks-return-confirm').hidden = true;
      q('#tasks-return').disabled = false;
      q('#tasks-return-cancel').disabled = false;
      q('#tasks-return-commit').disabled = false;
      q('#tasks-return-commit').textContent = '确认退回 · 10';
      q('#tasks-return-help').textContent = '';
    };
    const collapseInspectorState = () => {
      nodeTransition += 1;
      resetReturn();
      setModelMenu(false);
      stage.classList.remove('has-inspector');
      flowResizer.hidden = !stage.classList.contains('has-flow');
      window.requestAnimationFrame(() => {
        updateFlowResizerBounds();
        drawBypass();
      });
      qa('.desktop-tasks-node').forEach((node) => {
        node.setAttribute('aria-selected', 'false');
        node.setAttribute('aria-expanded', 'false');
      });
      selectedNode = null;
    };
    const closeInspector = (returnFocus = false) => {
      const focusTarget = selectedNode;
      const wasOpen = preview.app.dataset.right === 'true';
      collapseInspectorState();
      if (wasOpen) preview.setInspector(false);
      if (returnFocus && focusTarget) window.setTimeout(() => focusTarget.focus(), 0);
    };
    const returnToTaskList = async () => {
      if (flowPane.hidden && !stage.classList.contains('has-flow')) return;
      const version = ++flowTransition;
      const focusTarget = currentTask;
      closeInspector();
      lastInspectedNode = null;
      stage.classList.remove('has-flow', 'has-inspector');
      flowPane.setAttribute('aria-hidden', 'true');
      flowResizer.hidden = true;
      tasks.forEach((item) => {
        item.setAttribute('aria-selected', 'false');
        item.setAttribute('aria-expanded', 'false');
      });
      currentTask = null;
      currentPre = false;
      lastBypassKeys = new Set();
      await wait(motionMs(220));
      if (version !== flowTransition || stage.classList.contains('has-flow')) return;
      flowPane.hidden = true;
      window.requestAnimationFrame(updateFlowResizerBounds);
      focusTarget?.focus();
    };
    const openTask = (task) => {
      if (currentTask === task && stage.classList.contains('has-flow')) {
        // SAME_TASK_ALREADY_OPEN: preserve the existing flow and inspector state.
        return;
      }
      flowTransition += 1;
      closeInspector();
      lastInspectedNode = null;
      tasks.forEach((item) => {
        const selected = item === task;
        item.setAttribute('aria-selected', String(selected));
        item.setAttribute('aria-expanded', String(selected));
      });
      currentTask = task;
      currentPre = task.dataset.pre === 'true';
      if (!['SESSION_REFINEMENT', 'CORE_DOCUMENT','RESEARCH_DISCOVERY','RESEARCH_REPORT'].includes(task.dataset.workflowKind)) getTaskSnapshot(task);
      q('#tasks-flow-title').textContent = task.dataset.task;
      q('#tasks-flow-run').textContent = task.dataset.run;
      const firstOpen = flowPane.hidden;
      flowPane.hidden = false;
      flowPane.setAttribute('aria-hidden', 'false');
      flowResizer.hidden = false;
      lastBypassKeys = new Set();
      renderTaskFlow({ animateBypass:true });
      if (firstOpen) window.requestAnimationFrame(() => {
        stage.classList.add('has-flow');
        updateFlowResizerBounds();
        window.requestAnimationFrame(() => drawBypass({ animate:true }));
      });
      else {
        stage.classList.add('has-flow');
        updateFlowResizerBounds();
      }
    };
    taskList.addEventListener('click', (event) => {
      const task = event.target.closest('.desktop-tasks-task');
      if (task && taskList.contains(task)) openTask(task);
    });
    q('#tasks-back-to-list').addEventListener('click', returnToTaskList);
    nodes.forEach((node) => node.addEventListener('click', () => {
      if (!currentTask) return;
      openNodeInspector(node);
    }));
    q('#tasks-node-toggle').addEventListener('click', () => {
      const snapshot = getCurrentSnapshot();
      if (!snapshot || !selectedNode || !canToggleNode(selectedNode)) return;
      const step = selectedNode.dataset.step;
      const enabled = q('#tasks-node-toggle').getAttribute('aria-checked') === 'true';
      snapshot.enabled[step] = enabled;
      if (enabled === snapshot.baseEnabled[step]) snapshot.overrides.delete(step);
      else snapshot.overrides.add(step);
      publishTaskSnapshot(snapshot);
      setModelMenu(false);
      renderTaskFlow({ animateBypass:true });
    });
    modelButton.addEventListener('click', () => {
      if (!selectedNode || !canReplaceModel(selectedNode)) return;
      setModelMenu(modelMenu.dataset.open !== 'true');
    });
    modelMenu.addEventListener('click', (event) => {
      const button = event.target.closest('button[data-model]');
      const snapshot = getCurrentSnapshot();
      if (!button || button.disabled || !snapshot || !selectedNode || !canReplaceModel(selectedNode)) return;
      const step = selectedNode.dataset.step;
      if (currentTask?.dataset.workflowKind === 'SESSION_REFINEMENT') {
        selectedNode.dataset.model = button.dataset.model;
        selectedNode.dataset.profileRef = button.dataset.profileRef || '';
        const modelValue = selectedNode.querySelector('[data-node-model]');
        if (modelValue) modelValue.textContent = button.dataset.model;
        q('#tasks-current-model').textContent = button.dataset.model;
        q('#tasks-model-dot').className = 'status-dot ' + (button.dataset.state || 'green');
        q('#tasks-model-copy').textContent = '已提交本任务模型覆盖；执行开始后自动锁定。';
        setModelMenu(false);
        modelButton.focus();
        return;
      }
      snapshot.models[step] = button.dataset.model;
      snapshot.profileRefs[step] = button.dataset.profileRef || '';
      if (snapshot.models[step] === snapshot.baseModels[step]
        && snapshot.profileRefs[step] === snapshot.baseProfileRefs[step]) snapshot.modelOverrides.delete(step);
      else snapshot.modelOverrides.add(step);
      syncContextFromEmbedding(snapshot);
      publishTaskSnapshot(snapshot);
      setModelMenu(false);
      renderTaskFlow();
      modelButton.focus();
    });
    q('#tasks-restore-template').addEventListener('click', () => {
      const snapshot = getCurrentSnapshot();
      if (!snapshot || !selectedNode) return;
      const step = selectedNode.dataset.step;
      if (!snapshot.overrides.has(step) && !snapshot.modelOverrides.has(step)) return;
      snapshot.enabled[step] = snapshot.baseEnabled[step];
      snapshot.models[step] = snapshot.baseModels[step];
      snapshot.profileRefs[step] = snapshot.baseProfileRefs[step];
      snapshot.overrides.delete(step);
      snapshot.modelOverrides.delete(step);
      syncContextFromEmbedding(snapshot);
      publishTaskSnapshot(snapshot);
      setModelMenu(false);
      renderTaskFlow({ animateBypass:true });
      updateInspector(selectedNode);
    });
    q('#tasks-close').addEventListener('click', () => closeInspector(true));
    q('#tasks-mode')?.addEventListener('click', (event) => {
      const button = event.target.closest('button[data-mode]');
      if (button) setTaskMode(button.dataset.mode);
    });
    const inspectorObserver = new MutationObserver(() => {
      if (preview.app.dataset.right !== 'true' && stage.classList.contains('has-inspector')) collapseInspectorState();
    });
    inspectorObserver.observe(preview.app, { attributes:true, attributeFilter:['data-right'] });
    window.setInterval(refreshRetentionCountdowns, 1000);
    refreshTaskCount();
    q('#tasks-return').addEventListener('click', () => {
      resetReturn();
      q('#tasks-return-start').hidden = true;
      q('#tasks-return-confirm').hidden = false;
      q('#tasks-return-help').textContent = '';
      timer = setInterval(() => {
        left -= 1;
        if (left <= 0) resetReturn();
        else q('#tasks-return-commit').textContent = '确认退回 · ' + left;
      }, 1000);
    });
    q('#tasks-return-cancel').addEventListener('click', resetReturn);
    q('#tasks-return-commit').addEventListener('click', () => {
      clearInterval(timer);
      timer = 0;
      q('#tasks-return-cancel').disabled = true;
      q('#tasks-return-commit').disabled = true;
      q('#tasks-return-commit').textContent = '测试请求已记录';
      q('#tasks-return-help').textContent = '当前为示例 / 测试数据通道；不会修改生产任务或节点状态。';
      window.setTimeout(() => {
        q('#tasks-return-cancel').disabled = false;
        resetReturn();
      }, 1800);
    });

    const toggleTaskInspector = (event) => {
      event.preventDefault();
      event.stopImmediatePropagation();
      if (preview.app.dataset.right === 'true') {
        closeInspector();
        return;
      }
      const visible = (node) => node?.isConnected && node.getClientRects().length && !node.closest('[hidden]');
      const target = visible(lastInspectedNode) ? lastInspectedNode : qa('.desktop-tasks-node').find(visible);
      if (currentTask && target) openNodeInspector(target);
      else q('#desktop-live-state').textContent = '请先选择任务';
    };
    const rightToggle = q('#desktop-toggle-right');
    rightToggle.addEventListener('click', toggleTaskInspector, true);
    root.addEventListener('keydown', (event) => {
      if (event.ctrlKey && event.altKey && event.code === 'KeyB') toggleTaskInspector(event);
    }, true);
    if (typeof ResizeObserver === 'function') {
      const flowResizeObserver = new ResizeObserver(() => {
        if (currentTask && !flowPane.hidden) window.requestAnimationFrame(() => drawBypass());
      });
      flowResizeObserver.observe(flowStage);
      const stageResizeObserver = new ResizeObserver(() => updateFlowResizerBounds());
      stageResizeObserver.observe(stage);
    }
    window.addEventListener('resize', () => {
      updateFlowResizerBounds();
      if (currentTask && !flowPane.hidden) window.requestAnimationFrame(() => drawBypass());
    });
    root.addEventListener('pointerdown', (event) => {
      if (!event.target.closest('#tasks-adjust-model, #tasks-model-menu')) setModelMenu(false);
    });
    root.addEventListener('keydown', (event) => {
      if (event.key !== 'Escape' || modelMenu.dataset.open !== 'true') return;
      event.preventDefault();
      event.stopPropagation();
      setModelMenu(false);
      modelButton.focus();
    });
    window.addEventListener('message', (event) => {
      if (window.parent === window || event.source !== window.parent) return;
      const data = event.data;
      if (!data || typeof data !== 'object') return;
      if (data.type === 'desktop-workflow-template-state') applyTemplateState(data.template);
      if (data.type === 'desktop-workflow-task-snapshots-state') hydrateTaskSnapshots(data.snapshots);
    });
    window.addEventListener('desktop:workflow-config-updated', (event) => {
      const detail = event.detail;
      if (!detail || typeof detail !== 'object') return;
      setConfiguredModelOptions(detail);
      applyTemplateState(detail.template);
      if (currentTask) renderTaskFlow();
    });
    window.addEventListener('desktop:workflow-sync', (event) => {
      const detail = event.detail;
      if (!detail || typeof detail !== 'object') return;
      setConfiguredModelOptions(detail);
      applyTemplateState(detail.template);
      if (currentTask) renderTaskFlow();
    });
    window.addEventListener('desktop:current-task-refinement-bindings', (event) => {
      upsertRefinementTasks(event.detail?.refinement_bindings);
    });
    window.addEventListener('desktop:current-task-production-mode', (event) => {
      setProductionMode(event.detail?.enabled !== false);
    });
    window.addEventListener('desktop:current-task-production-bindings', (event) => {
      upsertProductionTasks(event.detail?.production_bindings);
    });
    window.addEventListener('desktop:current-task-override-state', (event) => {
      const detail = event.detail || {};
      const status = q('#tasks-adjust-status');
      if (!status) return;
      status.hidden = false;
      status.textContent = detail.message || '';
      status.dataset.tone = detail.status === 'FAILED' ? 'error' : (detail.status === 'PASS' ? 'success' : 'neutral');
      const busy = detail.status === 'SAVING';
      if (busy) {
        [q('#tasks-node-toggle'), q('#tasks-retry-minus'), q('#tasks-retry-plus'), q('#tasks-adjust-model'), q('#tasks-fallback-select'), q('#tasks-restore-template')]
          .filter(Boolean)
          .forEach((control) => { control.disabled = true; });
      } else if (selectedNode) {
        if (detail.status === 'PASS'
          && detail.job_id === currentTask?.dataset.run
          && detail.node_id === selectedNode.dataset.step) {
          const snapshot = getCurrentSnapshot();
          const step = selectedNode.dataset.step;
          if (snapshot && detail.method === 'current_task.set_override' && detail.override) {
            if (typeof detail.override.enabled === 'boolean') {
              snapshot.enabled[step] = detail.override.enabled;
              if (snapshot.enabled[step] === snapshot.baseEnabled[step]) snapshot.overrides.delete(step);
              else snapshot.overrides.add(step);
            }
            if (typeof detail.override.model === 'string' && typeof detail.override.profile_ref === 'string') {
              snapshot.models[step] = detail.override.model;
              snapshot.profileRefs[step] = detail.override.profile_ref;
              if (snapshot.models[step] === snapshot.baseModels[step]
                && snapshot.profileRefs[step] === snapshot.baseProfileRefs[step]) snapshot.modelOverrides.delete(step);
              else snapshot.modelOverrides.add(step);
              syncContextFromEmbedding(snapshot);
            }
            if (Number.isInteger(detail.override.retry_count)) {
              selectedNode.dataset.retryCount = String(detail.override.retry_count);
            }
          } else if (snapshot && detail.method === 'current_task.restore_snapshot') {
            snapshot.enabled[step] = snapshot.baseEnabled[step];
            snapshot.models[step] = snapshot.baseModels[step];
            snapshot.profileRefs[step] = snapshot.baseProfileRefs[step];
            snapshot.overrides.delete(step);
            snapshot.modelOverrides.delete(step);
            syncContextFromEmbedding(snapshot);
          }
          renderTaskFlow({ animateBypass:true });
        }
        // Saving does not change the selected node. Refresh its controls
        // directly; openNodeInspector intentionally deduplicates an already
        // open node and therefore cannot restore disabled controls here.
        updateInspector(selectedNode);
      }
    });
    window.__Desktop_TASKS_UI__ = Object.freeze({
      refreshNodeProgress: () => renderTaskFlow(),
      setConfiguredModelOptions,
      setProjectionLoadState,
      normalizeWorkflowModelOptions,
      applyTemplateState,
      hydrateTaskSnapshots,
      upsertRefinementTasks,
      upsertProductionTasks,
      setTaskMode,
      setProductionMode,
      sortTaskRows,
      openTaskByRun(run) {
        const task = taskList.querySelector(
          `.desktop-tasks-task[data-run="${CSS.escape(String(run || ''))}"]`
        );
        if (!task || task.hidden) return false;
        openTask(task);
        return true;
      },
      openNodeByStep(step) {
        const node = getFlowNode(String(step || ''));
        if (!node || node.closest('.desktop-tasks-flow-step')?.hidden) return false;
        openNodeInspector(node);
        return true;
      },
      inspect() {
        return {
          configured_model_count:configuredModelOptions.length,
          template_revision:templateState.revision,
          snapshot_count:taskSnapshots.size,
          core_binding_count:productionBindings.size,
          current_run:currentTask?.dataset?.run || null,
          selected_step:selectedNode?.dataset?.step || null,
          inspector_open:stage.classList.contains('has-inspector'),
          task_mode:taskMode
        };
      }
    });
    if (window.__Desktop_WORKFLOW_SYNC__) {
      setConfiguredModelOptions(window.__Desktop_WORKFLOW_SYNC__);
      applyTemplateState(window.__Desktop_WORKFLOW_SYNC__.template);
    }
    if (window.parent !== window) {
      window.parent.postMessage({ type:'desktop-workflow-template-request', page:'当前任务' }, '*');
      window.parent.postMessage({ type:'desktop-workflow-task-snapshots-request', page:'当前任务' }, '*');
    }
    updateFlowResizerBounds();
    tasks.forEach((task) => { task.dataset.collectionState ||= 'CURRENT'; });
    setTaskMode('current');
    setModelMenu(false);
  })();

