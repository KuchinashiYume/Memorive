(() => {
  'use strict';

  const methods = Object.freeze([
    'current_task.get_contract',
    'current_task.list',
    'current_task.select_task',
    'current_task.select_node',
    'current_task.view',
    'current_task.refresh',
    'current_task.pause',
    'current_task.pause_node',
    'current_task.resume_node',
    'current_task.resume',
    'current_task.begin_cancel',
    'current_task.confirm_cancel',
    'current_task.delete_failed',
    'current_task.retry',
    'current_task.restart_status',
    'current_task.rollback_node',
    'current_task.respond',
    'current_task.set_override',
    'current_task.restore_snapshot',
    'current_task.close_all',
    'current_task.effect_metrics'
  ]);
  const methodSet = new Set(methods);
  const bindings = new Map();
  const refinementRuns = new Set();
  const productionRuns = new Set();
  const receipts = [];
  let selectedRun = null;
  let selectedNode = null;
  let pendingCancel = null;
  let bootstrapPromise = null;
  let refreshPromise = null;
  let progressConnectionLost = false;
  let overrideCommitPromise = null;
  let restartRequestPromise = null;
  let nodeControlPromise = null;
  let deleteTaskPromise = null;
  let lastProjectionFingerprint = null;
  let selectedRunMissCount = 0;
  const CURRENT_TASK_SELECTION_MISS_GRACE = 3;
  const CURRENT_TASK_FALLBACK_REFRESH_MS = 15000;
  const VOLATILE_PROJECTION_KEYS = new Set(['retention_seconds_remaining']);

  const canonicalProjectionValue = (value) => {
    if (Array.isArray(value)) return value.map(canonicalProjectionValue);
    if (!value || typeof value !== 'object') return value;
    return Object.fromEntries(
      Object.keys(value)
        .filter((key) => !VOLATILE_PROJECTION_KEYS.has(key))
        .sort()
        .map((key) => [key, canonicalProjectionValue(value[key])])
    );
  };
  const projectionFingerprint = (projection) => JSON.stringify(canonicalProjectionValue({
    status:projection?.status || null,
    visual_bindings:projection?.visual_bindings || [],
    refinement_bindings:projection?.refinement_bindings || [],
    production_bindings:projection?.production_bindings || [],
    console_bindings:projection?.console_bindings || [],
    research_bindings:projection?.research_bindings || [],
    history_bindings:projection?.history_bindings || [],
    workflow_execution_snapshot:projection?.workflow_execution_snapshot || null
  }));
  const projectionHasRun = (projection, run) => {
    if (!run) return false;
    return ['visual_bindings', 'refinement_bindings', 'production_bindings', 'console_bindings', 'history_bindings','research_bindings']
      .some((key) => (Array.isArray(projection?.[key]) ? projection[key] : [])
        .some((row) => (row?.run || row?.job_id) === run));
  };

  const nativeCall = async (method, params = {}) => {
    if (!methodSet.has(method)) throw new Error(`CURRENT_TASK_METHOD_NOT_ALLOWLISTED:${method}`);
    if (!window.pywebview?.api?.call) {
      return { schema_version: 'CurrentTaskBrowserOnlyReceipt-v1', method, params, status: 'BROWSER_TEST_NO_NATIVE_TRANSPORT' };
    }
    const result = await (window.DesktopDecorations?.trackCall(method, () => window.pywebview.api.call(method, params)) || window.pywebview.api.call(method, params));
    receipts.push({ method, result });
    window.dispatchEvent(new CustomEvent('desktop:current-task-receipt', { detail: { method, result } }));
    return result;
  };

  const publishOverrideState = (status, message, context = {}) => {
    window.dispatchEvent(new CustomEvent('desktop:current-task-override-state', {
      detail: {
        status,
        message,
        job_id: context.job_id || selectedRun,
        node_id: context.node_id || selectedNode,
        method: context.method || null,
        override: context.override ? { ...context.override } : null
      }
    }));
  };

  const commitOverride = (method, params, successMessage) => {
    if (!selectedRun || !selectedNode || overrideCommitPromise) return overrideCommitPromise;
    const target = {
      job_id:selectedRun,
      node_id:selectedNode,
      method,
      override:{ ...params }
    };
    publishOverrideState('SAVING', '正在保存当前任务设置…', target);
    overrideCommitPromise = nativeCall(method, {
      job_id: target.job_id,
      node_id: target.node_id,
      ...params
    }).then(() => {
      bootstrapPromise = null;
      return bootstrap();
    }).then((receipt) => {
      publishOverrideState('PASS', successMessage, target);
      return receipt;
    }).catch(() => {
      publishOverrideState('FAILED', '保存失败：当前任务或节点状态已变化，请刷新后重试。', target);
      return null;
    }).finally(() => {
      overrideCommitPromise = null;
    });
    return overrideCommitPromise;
  };

  const bindRows = (projection) => {
    let rows = Array.isArray(projection?.visual_bindings) ? [...projection.visual_bindings] : [];
    const listedRefinementRows = Array.isArray(projection?.refinement_bindings) ? projection.refinement_bindings : [];
    const listedProductionRows = [...(projection?.production_bindings || []),...(projection?.console_bindings || []),...(projection?.research_bindings || [])];
    const history_bindings = Array.isArray(projection?.history_bindings)
      ? projection.history_bindings : [];
    let refinementRows = [
      ...listedRefinementRows,
      ...history_bindings.filter((row) => row?.workflow_kind === 'SESSION_REFINEMENT')
    ];
    let productionRows = [
      ...listedProductionRows,
      ...history_bindings.filter((row) => row?.workflow_kind === 'CORE_DOCUMENT')
    ];
    const previousSelectedBinding = selectedRun ? bindings.get(selectedRun) : null;
    const selectedMissing = Boolean(selectedRun && !projectionHasRun(projection, selectedRun));
    if (selectedMissing && previousSelectedBinding) {
      selectedRunMissCount += 1;
      if (selectedRunMissCount < CURRENT_TASK_SELECTION_MISS_GRACE) {
        if (previousSelectedBinding.workflow_kind === 'SESSION_REFINEMENT') {
          refinementRows = [...refinementRows, previousSelectedBinding];
        } else if (previousSelectedBinding.workflow_kind === 'CORE_DOCUMENT') {
          productionRows = [...productionRows, previousSelectedBinding];
        } else {
          rows = [...rows, previousSelectedBinding];
        }
      }
    } else {
      selectedRunMissCount = 0;
    }
    window.dispatchEvent(new CustomEvent('desktop:current-task-production-mode', {
      detail: { enabled: projection?.status === 'PASS_PRODUCTION_APPLICATION_SERVICE_PROJECTION' }
    }));
    refinementRuns.forEach((run) => bindings.delete(run));
    productionRuns.forEach((run) => bindings.delete(run));
    refinementRuns.clear();
    productionRuns.clear();
    rows.forEach((row) => {
      bindings.set(row.run, row);
      const task = document.querySelector(`.desktop-tasks-task[data-run="${CSS.escape(row.run)}"]`);
      if (task) {
        task.dataset.jobId = row.job_id;
        task.dataset.attemptId = row.attempt_id;
        task.dataset.syntheticOnly = 'true';
      }
    });
    refinementRows.forEach((row) => {
      const run = row.run || row.job_id;
      if (!run) return;
      bindings.set(run, row);
      refinementRuns.add(run);
      const task = document.querySelector(`.desktop-tasks-task[data-run="${CSS.escape(run)}"]`);
      if (task) {
        task.dataset.jobId = row.job_id;
        task.dataset.attemptId = row.attempt_id;
        task.dataset.syntheticOnly = 'false';
        task.dataset.workflowKind = 'SESSION_REFINEMENT';
      }
    });
    productionRows.forEach((row) => {
      const run = row.run || row.job_id;
      if (!run) return;
      bindings.set(run, row);
      productionRuns.add(run);
      const task = document.querySelector(`.desktop-tasks-task[data-run="${CSS.escape(run)}"]`);
      if (task) {
        task.dataset.jobId = row.job_id;
        task.dataset.attemptId = row.attempt_id;
        task.dataset.syntheticOnly = row.synthetic_only ? 'true' : 'false';
        task.dataset.workflowKind = row.workflow_kind;
      }
    });
    window.dispatchEvent(new CustomEvent('desktop:current-task-refinement-bindings', {
      detail: { refinement_bindings: refinementRows }
    }));
    window.dispatchEvent(new CustomEvent('desktop:current-task-production-bindings', {
      detail: { production_bindings: productionRows }
    }));
    if (selectedRun && !bindings.has(selectedRun)) {
      selectedRun = null;
      selectedNode = null;
      pendingCancel = null;
      selectedRunMissCount = 0;
      void nativeCall('current_task.close_all', {});
    }
    return { rows, refinementRows, productionRows, historyRows:history_bindings };
  };

  const bootstrapProjection = async () => {
    let projection;
    try {
      projection = await nativeCall('current_task.list', {});
    } catch (error) {
      window.__Desktop_TASKS_UI__?.setProjectionLoadState?.('error');
      window.__MEMO_NODE_PROGRESS__?.transport(false);
      lastProjectionFingerprint=null;
      progressConnectionLost=true;
      throw error;
    }
    window.__Desktop_TASKS_UI__?.setProjectionLoadState?.('ready');
    window.__MEMO_NODE_PROGRESS__?.transport(true);
    const fingerprint = projectionFingerprint(projection);
    const selectedMissing = Boolean(selectedRun && !projectionHasRun(projection, selectedRun));
    if (fingerprint === lastProjectionFingerprint && !selectedMissing) {
      return {
        schema_version:'CurrentTaskCurrentTaskBridgeBootstrap-v2',
        binding_count:bindings.size,
        refinement_binding_count:refinementRuns.size,
        core_binding_count:productionRuns.size,
        history_binding_count:0,
        production_job_count:refinementRuns.size + productionRuns.size,
        synthetic_only:refinementRuns.size === 0 && productionRuns.size === 0,
        status:'UNCHANGED_PROJECTION'
      };
    }
    lastProjectionFingerprint = fingerprint;
    if (projection && Object.hasOwn(projection, 'workflow_execution_snapshot')) {
      window.__Desktop_TASKS_UI__?.setConfiguredModelOptions?.(
        projection.workflow_execution_snapshot?.configured_models || []
      );
    }
    const bound = bindRows(projection);
    if(progressConnectionLost){
      window.__Desktop_TASKS_UI__?.refreshNodeProgress?.();
      progressConnectionLost=false;
    }
    return {
      schema_version: 'CurrentTaskCurrentTaskBridgeBootstrap-v2',
      binding_count: bound.rows.length + bound.refinementRows.length + bound.productionRows.length,
      refinement_binding_count: bound.refinementRows.length,
      core_binding_count: bound.productionRows.length,
      history_binding_count: bound.historyRows.length,
      production_job_count: projection.production_job_count ?? (bound.refinementRows.length + bound.productionRows.length),
      synthetic_only: bound.refinementRows.length === 0 && bound.productionRows.length === 0,
      status: 'PASS'
    };
  };

  const bootstrap = async () => {
    if (!bootstrapPromise) {
      bootstrapPromise = bootstrapProjection().catch((error) => {
        bootstrapPromise = null;
        throw error;
      });
    }
    return bootstrapPromise;
  };

  const refresh = () => {
    if (refreshPromise) return refreshPromise;
    bootstrapPromise = null;
    refreshPromise = bootstrapProjection().finally(() => {
      refreshPromise = null;
    });
    return refreshPromise;
  };

  const ensureBound = async (run) => {
    if (!bindings.has(run)) await refresh();
    if (!bindings.has(run)) throw new Error(`CURRENT_TASK_VISUAL_RUN_UNBOUND:${run}`);
    return bindings.get(run);
  };

  const selectRun = async (run) => {
    if (selectedRun === run) return { status: 'UNCHANGED', run };
    await ensureBound(run);
    selectedRun = run;
    selectedNode = null;
    pendingCancel = null;
    selectedRunMissCount = 0;
    return nativeCall('current_task.select_task', { run });
  };

  const openHistoricalRun = async (run) => {
    const acceptedRun = String(run || '').trim();
    if (!acceptedRun) throw new Error('CURRENT_TASK_STABLE_RUN_REQUIRED');
    const view = await nativeCall('current_task.select_task', { run:acceptedRun });
    const projection = view?.projection;
    if (projection?.workflow_kind !== 'SESSION_REFINEMENT') {
      throw new Error('CURRENT_TASK_REFINEMENT_PROJECTION_REQUIRED');
    }
    const row = {
      run:acceptedRun,
      job_id:acceptedRun,
      attempt_id:projection.attempt_id,
      display_name:projection.display_name,
      control_state:projection.state_axes?.job_control_state,
      workflow_kind:'SESSION_REFINEMENT',
      local_projection_id:projection.local_projection_id,
      selected_profile_ref:projection.selected_profile_ref,
      error_code:projection.error_code,
      workflow_nodes:projection.graph?.workflow_nodes || [],
      model_calls:projection.model_calls || 0,
      external_model_calls:projection.external_model_calls || 0,
      collection_state:'HISTORY',
      historical_read_only:true,
      synthetic_only:false
    };
    bindings.set(acceptedRun, row);
    refinementRuns.add(acceptedRun);
    selectedRun = acceptedRun;
    selectedNode = null;
    pendingCancel = null;
    selectedRunMissCount = 0;
    window.dispatchEvent(new CustomEvent('desktop:current-task-refinement-bindings', {
      detail:{ refinement_bindings:[...refinementRuns]
        .map((jobId) => bindings.get(jobId))
        .filter(Boolean) }
    }));
    window.__Desktop_TASKS_UI__?.setTaskMode?.('history');
    window.__Desktop_TASKS_UI__?.openTaskByRun?.(acceptedRun);
    return view;
  };

  document.querySelector('#tasks-fallback-select')?.addEventListener('change', event => {
    if (selectedNode !== 'ingest' || !selectedRun) return;
    void commitOverride('current_task.set_override', {
      fallback_profile_ref:event.target.value || null
    }, '备用模型已保存。');
  });
  const nodeUiKey = Object.freeze({
    'E2_DATA_REVIEW':'data-review',
    '01_DOCUMENT_INGEST':'ingest',
    '02_CHUNK_EMBEDDING':'embedding',
    '03_CARD_DISTILL':'card',
    '04_CARD_CROSS_CHECK':'card-review',
    '05_CARD_ADMISSION':'admission',
    '06_CONTEXT_PACK':'context',
    '07_ANALYSIS':'analysis',
    '08_JUDGMENT_CROSS_CHECK':'judgment-review',
    '09_HUMAN_FINAL':'human'
  });

  const openProductionRun = async (run, requestedNodeId = null) => {
    const acceptedRun = String(run || '').trim();
    if (!acceptedRun) throw new Error('CURRENT_TASK_Core_JOB_REQUIRED');
    await refresh();
    const view = await nativeCall('current_task.select_task', { run:acceptedRun });
    const projection = view?.projection;
    const workflowNodes = Array.isArray(projection?.graph?.workflow_nodes)
      ? projection.graph.workflow_nodes : [];
    if (!projection || !workflowNodes.some((node) => node?.node_id === '02_CHUNK_EMBEDDING')) {
      throw new Error('CURRENT_TASK_Core_PROJECTION_REQUIRED');
    }
    const row = {
      ...(bindings.get(acceptedRun) || {}),
      run:acceptedRun,
      job_id:acceptedRun,
      attempt_id:projection.attempt_id,
      display_name:bindings.get(acceptedRun)?.display_name || acceptedRun,
      control_state:projection.state_axes?.job_control_state,
      workflow_kind:'CORE_DOCUMENT',
      workflow_nodes:workflowNodes,
      literature_review:projection.literature_review,
      progress:projection.progress,
      artifact_count:projection.artifacts?.rows?.length || 0,
      synthetic_only:false
    };
    bindings.set(acceptedRun, row);
    productionRuns.add(acceptedRun);
    selectedRun = acceptedRun;
    selectedNode = null;
    pendingCancel = null;
    selectedRunMissCount = 0;
    window.dispatchEvent(new CustomEvent('desktop:current-task-production-bindings', {
      detail:{ production_bindings:[...productionRuns]
        .map((jobId) => bindings.get(jobId))
        .filter(Boolean) }
    }));
    window.__Desktop_TASKS_UI__?.setTaskMode?.(
      row.collection_state === 'HISTORY' ? 'history' : 'current'
    );
    const taskOpened = window.__Desktop_TASKS_UI__?.openTaskByRun?.(acceptedRun) === true;
    const inferredNode = requestedNodeId || workflowNodes.find((node) => node?.state === 'FAILED')?.node_id;
    const uiKey = nodeUiKey[inferredNode];
    if (uiKey) {
      selectedNode = uiKey;
      await nativeCall('current_task.select_node', { node_id:uiKey });
      window.__Desktop_TASKS_UI__?.openNodeByStep?.(uiKey);
    }
    await new Promise((resolve) => window.requestAnimationFrame(resolve));
    await new Promise((resolve) => window.requestAnimationFrame(resolve));
    const uiState = window.__Desktop_TASKS_UI__?.inspect?.() || {};
    const inspectorOpen = Boolean(
      inferredNode
      && uiKey
      && uiState.current_run === acceptedRun
      && uiState.selected_step === uiKey
      && uiState.inspector_open === true
    );
    const status = taskOpened && (!inferredNode || inspectorOpen)
      ? 'PASS' : 'CURRENT_TASK_INSPECTOR_NOT_OPEN';
    return {
      schema_version:'DesktopCurrentTaskExactInspectorReceipt-v1',
      job_id:acceptedRun,
      target_node_id:inferredNode || null,
      target_view:inferredNode ? 'CURRENT_TASK_NODE_INSPECTOR' : 'CURRENT_TASK_FLOW',
      task_opened:taskOpened,
      inspector_open:inspectorOpen,
      status,
      view
    };
  };

  const selectNode = async (nodeId) => {
    if (!selectedRun) throw new Error('CURRENT_TASK_TASK_SELECTION_REQUIRED');
    if (selectedNode === nodeId) return { status: 'UNCHANGED', node_id: nodeId };
    selectedNode = nodeId;
    pendingCancel = null;
    return nativeCall('current_task.select_node', { node_id: nodeId });
  };

  const beginCancel = async () => {
    if (!selectedRun) throw new Error('CURRENT_TASK_TASK_SELECTION_REQUIRED');
    const binding = bindings.get(selectedRun);
    if (binding?.workflow_kind === 'CORE_DOCUMENT') {
      const runtimeNodeId = Object.entries(nodeUiKey)
        .find(([, uiKey]) => uiKey === selectedNode)?.[0];
      const node = (binding.workflow_nodes || [])
        .find((row) => row?.node_id === runtimeNodeId);
      if (node?.retry_eligible === true) {
        pendingCancel = {workflow_kind:'Core_RETRY',job_id:selectedRun,node_id:selectedNode};
        return pendingCancel;
      }
      if (!node || node.rollback_eligible !== true) {
        throw new Error('只能退回终态任务中已经完成的步骤');
      }
      pendingCancel = {
        workflow_kind:'Core_ROLLBACK',
        job_id:selectedRun,
        node_id:selectedNode
      };
      return pendingCancel;
    }
    pendingCancel = await nativeCall('current_task.begin_cancel', { run: selectedRun });
    return pendingCancel;
  };

  const confirmCancel = async () => {
    if (!pendingCancel) throw new Error('CURRENT_TASK_CANCEL_CONFIRMATION_REQUIRED');
    if (pendingCancel.workflow_kind === 'Core_RETRY') {
      const receipt=await nativeCall('current_task.retry',{job_id:pendingCancel.job_id});
      pendingCancel=null;
      return receipt;
    }
    if (pendingCancel.workflow_kind === 'Core_ROLLBACK') {
      const receipt = await nativeCall('current_task.rollback_node', {
        job_id:pendingCancel.job_id,
        node_id:pendingCancel.node_id
      });
      pendingCancel = null;
      return receipt;
    }
    const receipt = await nativeCall('current_task.confirm_cancel', {
      token: pendingCancel.token,
      idempotency_key: `current_task-ui-cancel-${selectedRun}`
    });
    pendingCancel = null;
    return receipt;
  };

  const submitRecovery = (button, invoke) => {
    if (restartRequestPromise) return restartRequestPromise;
    button.disabled = true;
    button.setAttribute('aria-busy','true');
    restartRequestPromise = Promise.resolve().then(invoke).then(async(receipt) => {
        const request=receipt.retry_receipt || receipt.rollback_receipt;
        if (!request?.request_id) {
          if (receipt.action_receipt) {
            await refresh();
            return;
          }
          throw new Error('未收到重新执行回执');
        }
        let state=request;
        for (;;) {
          const help=document.querySelector('#tasks-return-help');
          if (state.status==='FAILED' || state.status==='CANCELLED') {
            const reason=state.error_code==='Core_ROLLBACK_CURRENT_TASK_NOT_TERMINAL'
              ? '同一文献已有任务正在处理，请查看当前任务。'
              : `重新执行未启动：${state.error_code || state.status}`;
            if(help)help.textContent=reason;
            throw new Error(reason);
          }
          if (state.successor_job_id) {
            if(help)help.textContent='已创建继续处理的任务，正在打开…';
            await refresh();
            await openProductionRun(state.successor_job_id, state.resume_from_node_id);
            return;
          }
          if(help)help.textContent='重新执行已排队，正在等待后台确认…';
          await new Promise(resolve=>setTimeout(resolve,1000));
          state=await nativeCall('current_task.restart_status',{request_id:request.request_id});
        }
    }).catch((error) => {
      const help=document.querySelector('#tasks-return-help');
      if(help)help.textContent=error.message || '未能重新执行，请刷新任务状态';
    }).finally(() => {
      button.removeAttribute('aria-busy');
      button.disabled=false;
      restartRequestPromise=null;
    });
    return restartRequestPromise;
  };

  document.addEventListener('click', (event) => {
    const task = event.target.closest('.desktop-tasks-task[data-run]');
    if (task) {
      void selectRun(task.dataset.run);
      return;
    }
    const node = event.target.closest('.desktop-tasks-node[data-step]');
    if (node && !node.classList.contains('paper-review-logic-node')) {
      void selectNode(node.dataset.step);
      return;
    }
    if (event.target.closest('#tasks-back-to-list')) {
      selectedRun = null;
      selectedNode = null;
      pendingCancel = null;
      selectedRunMissCount = 0;
      void nativeCall('current_task.close_all', {});
      return;
    }
    if (event.target.closest('#tasks-close')) {
      selectedNode = null;
      pendingCancel = null;
      return;
    }
    const nodeControl = event.target.closest('#tasks-node-control');
    const deleteTask = event.target.closest('#tasks-delete-task');
    if (deleteTask && selectedRun) {
      event.preventDefault();
      event.stopImmediatePropagation();
      if (deleteTask.disabled || deleteTaskPromise) return;
      const jobId = selectedRun;
      const feedback = document.querySelector('#tasks-node-control-feedback');
      feedback.hidden = true;
      deleteTask.disabled = true;
      deleteTask.setAttribute('aria-busy','true');
      deleteTaskPromise = nativeCall('current_task.delete_failed',{job_id:jobId})
        .then(() => {
          selectedRun = null;
          selectedNode = null;
          pendingCancel = null;
          selectedRunMissCount = 0;
          return refresh();
        })
        .then(() => window.__Desktop_TASKS_UI__?.setTaskMode?.('current'))
        .catch(() => {
          feedback.textContent = '删除失败，任务状态可能已变化。请刷新后重试。';
          feedback.hidden = false;
        }).finally(() => {
          deleteTask.removeAttribute('aria-busy');
          deleteTask.disabled = false;
          deleteTaskPromise = null;
        });
      return;
    }
    if (nodeControl && selectedRun && selectedNode) {
      event.preventDefault();
      event.stopImmediatePropagation();
      if (nodeControl.disabled || nodeControlPromise || restartRequestPromise) return;
      const action = nodeControl.dataset.action;
      if (action === 'retry') {
        if (!window.__Desktop_UI_CONTROLS__?.confirmRestart(nodeControl,selectedRun+':'+selectedNode)) return;
        void submitRecovery(nodeControl,()=>beginCancel().then(confirmCancel));
        return;
      }
      if (!['pause','resume'].includes(action)) return;
      const params={job_id:selectedRun,node_id:selectedNode};
      const feedback=document.querySelector('#tasks-node-control-feedback');
      feedback.hidden=true;
      nodeControl.disabled=true;
      nodeControl.setAttribute('aria-busy','true');
      nodeControlPromise=nativeCall('current_task.'+action+'_node',params)
        .then(()=>Promise.all([refresh(),window.__Desktop_INBOX_BRIDGE__?.refresh?.()]))
        .catch(()=>{
          feedback.textContent='操作未完成，任务状态可能已变化。请刷新后重试。';
          feedback.hidden=false;
        }).finally(()=>{
          nodeControl.removeAttribute('aria-busy');
          nodeControl.disabled=false;
          nodeControlPromise=null;
        });
      return;
    }
    const toggle = event.target.closest('#tasks-node-toggle');
    if (toggle && selectedNode) {
      event.preventDefault();
      event.stopImmediatePropagation();
      const enabled = toggle.getAttribute('aria-checked') !== 'true';
      void commitOverride('current_task.set_override', { enabled }, '节点设置已保存。');
      return;
    }
    const retryMinus = event.target.closest('#tasks-retry-minus');
    const retryPlus = event.target.closest('#tasks-retry-plus');
    if ((retryMinus || retryPlus) && selectedNode) {
      event.preventDefault();
      event.stopImmediatePropagation();
      const current = Number(document.querySelector('#tasks-retry-count')?.textContent || 0);
      const retryCount = Math.max(0, Math.min(10, current + (retryPlus ? 1 : -1)));
      void commitOverride('current_task.set_override', { retry_count:retryCount }, '重试次数已保存。');
      return;
    }
    const model = event.target.closest('#tasks-model-menu button[data-model]');
    if (model && selectedNode && !model.disabled) {
      const refinement = bindings.get(selectedRun)?.workflow_kind === 'SESSION_REFINEMENT';
      if (!refinement) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
      void commitOverride('current_task.set_override', {
        model: model.dataset.model,
        profile_ref: model.dataset.profileRef || ''
      }, '模型已切换并保存到当前任务。');
      return;
    }
    if (event.target.closest('#tasks-restore-template') && selectedNode) {
      event.preventDefault();
      event.stopImmediatePropagation();
      void commitOverride('current_task.restore_snapshot', {}, '已恢复任务创建时快照。');
      return;
    }
    if (event.target.closest('#tasks-open-successor')) {
      event.preventDefault();
      event.stopImmediatePropagation();
      const job=event.target.closest('#tasks-open-successor').dataset.jobId;
      if(job)void openProductionRun(job);
      return;
    }
    if (event.target.closest('#tasks-return')) {
      event.preventDefault();
      event.stopImmediatePropagation();
      const button=event.target.closest('#tasks-return'),key=selectedRun+':'+selectedNode;
      if(!window.__Desktop_UI_CONTROLS__?.confirmRestart(button,key))return;
      if (restartRequestPromise) return;
      void submitRecovery(button, () => beginCancel().then(confirmCancel));
      return;
    }
    if (event.target.closest('#tasks-return-cancel')) {
      pendingCancel = null;
      return;
    }
    const commit = event.target.closest('#tasks-return-commit');
    if (commit && pendingCancel) {
      event.preventDefault();
      event.stopImmediatePropagation();
      void submitRecovery(commit, confirmCancel);
    }
  }, true);

  const syncRefinementBindings = (event) => {
    const jobId = event.detail?.job_id;
    void refresh().then(() => {
      if (jobId && bindings.has(jobId)) {
        window.dispatchEvent(new CustomEvent('desktop:current-task-refinement-task-ready', {
          detail: { job_id: jobId }
        }));
      }
    });
  };
  window.addEventListener('desktop:session-refinement-task-created', syncRefinementBindings);
  window.addEventListener('desktop:session-refinement-task-updated', syncRefinementBindings);
  window.addEventListener('memorive:open-current-task', (event) => {
    if(String(event.detail?.job_id||'').startsWith('research-')){
      void window.__Desktop_RESEARCH__?.openTask(event.detail.job_id);
      return;
    }
    const jobId = event.detail?.job_id || event.detail?.run;
    if (jobId) void openProductionRun(jobId, event.detail?.node_id || null);
  });
  window.addEventListener('desktop-integrated-route', (event) => {
    const route = event.detail?.route;
    if (route === 'current-task') void refresh();
  });
  window.setInterval(() => {
    if (document.body.dataset.desktopActiveRoute === 'current-task') {
      void refresh().catch(() => {});
    }
  }, CURRENT_TASK_FALLBACK_REFRESH_MS);

  window.__Desktop_CURRENT_TASK_BRIDGE__ = Object.freeze({
    bootstrap,
    refresh,
    call: nativeCall,
    selectRun,
    openHistoricalRun,
    openProductionRun,
    selectNode,
    beginCancel,
    confirmCancel,
    inspect: () => ({
      method_count: methods.length,
      methods: [...methods],
      binding_count: bindings.size,
      receipt_count: receipts.length,
      last_projection_fingerprint:lastProjectionFingerprint,
      selected_run_miss_count:selectedRunMissCount,
      selected_run: selectedRun,
      selected_node: selectedNode,
      pending_cancel: Boolean(pendingCancel),
      native_transport: Boolean(window.pywebview?.api?.call),
      production_job_access: refinementRuns.size + productionRuns.size,
      core_job_access: productionRuns.size,
      synthetic_only: refinementRuns.size === 0 && productionRuns.size === 0
    })
  });
})();

