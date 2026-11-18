/* Decorations of the main window: the home and empty-state illustrations, loading placeholders while a page waits
   for its first data (trackCall wraps service calls for this), and signals to the desk assistant
   (assistant_ui_signal). Publishes Memorive.decorations. */
(() => {
  'use strict';
  if (Memorive.decorations) return;

  const assets = Object.freeze({
    interaction: 'assets/decorations/home-interaction.svg',
    question: 'assets/decorations/home-question.svg',
    empty: 'assets/decorations/empty-state.svg'
  });
  const prefersReducedMotion = () => Boolean(
    window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  );
  let signalSequence = 0;
  const recentSignals = new Map();
  const seenErrorMessages = new Set();
  const homeExpressions = [];
  let homeResetTimer = 0;
  let homeQuestionUntil = 0;
  let startupPlayed = false;

  // Reuse the native request lifecycle; decorative loading never starts work.
  const loadingFrames = Array.from({length:6}, (_, i) =>
    `assets/decorations/加载中合成_${String(i + 1).padStart(2, '0')}.svg`);
  const loadingDurations = [450,180,450,180,450,180];
  const loadingStates = new Map();
  const routeRoots = {settings:'desktop-settings-preview',inbox:'desktop-inbox-preview',
    'current-task':'desktop-tasks-preview',messages:'desktop-messages-preview',sessions:'desktop-sessions-preview',
    library:'desktop-library-preview','work-log':'desktop-worklog-preview',leaderboard:'desktop-leaderboard-preview'};
  const readRoutes = new Map(Object.entries({
    settings:['settings.get_state','settings.capability_state'],
    inbox:['inbox.projection','inbox.refresh'],
    'current-task':['current_task.list','current_task.refresh','current_task.view'],
    messages:['messages.bootstrap','messages.list','messages.switch_mode'],
    sessions:['sessions.bootstrap','sessions.list','sessions.get','sessions.refresh'],
    library:['library.bootstrap','library.list_artifacts','library.refresh','library.get_artifact'],
    'work-log':['work_log.bootstrap','work_log.list_entries','work_log.get_entry','work_log.refresh'],
    leaderboard:['settings.leaderboard_get','settings.leaderboard_refresh'],
    chat:['memo.state','memo.conversation_index','memo.thread_get']
  }).flatMap(([route, methods]) => methods.map(method => [method,route])));
  const loadingCopy = text => Memorive.app?.translate?.(text) || text;
  const loadingTarget = route => {
    if (route === 'chat') return document.querySelector('#memo-layout');
    const surface = document.querySelector(`[data-desktop-route="${route}"]`)
      || document.getElementById(routeRoots[route]);
    return surface?.querySelector(route === 'settings' ? '#settings-workarea' : route === 'current-task' ? '.desktop-tasks-listpane'
      : route === 'sessions' ? '#sessions-listpane' : route === 'leaderboard' ? '#lb-ranking-panel' : '.page-body') || null;
  };
  const stopLoadingMotion = state => {
    clearTimeout(state.frameTimer);
    state.frameTimer=0;
  };
  const syncLoadingMotion = state => {
    stopLoadingMotion(state);
    if (!state.overlay || !isVisible(state.overlay) || document.hidden) return;
    const hidden = document.documentElement.classList.contains('desktop-hide-expressions')
      || document.documentElement.classList.contains('desktop-user-loading');
    state.frames.forEach((image,index) => { image.hidden=index!==0; });
    if (hidden || prefersReducedMotion()) return;
    let frame=0;
    const next=()=>{
      if (!state.count || !state.overlay?.isConnected || !isVisible(state.overlay) || document.hidden) return;
      state.frames[frame].hidden=true;
      frame=(frame+1)%loadingFrames.length;
      state.frames[frame].hidden=false;
      state.frameTimer=setTimeout(next,loadingDurations[frame]);
    };
    state.frameTimer=setTimeout(next,loadingDurations[0]);
  };
  const showLoading = (target,state) => {
    if (!state.count || !target.isConnected || state.overlay) return;
    const overlay=document.createElement('div');
    overlay.className='desktop-loading-state';
    overlay.setAttribute('role','status');
    overlay.setAttribute('aria-label',loadingCopy('正在加载…'));
    const art=document.createElement('div');
    art.className='desktop-loading-art';
    art.setAttribute('aria-hidden','true');
    state.frames=loadingFrames.map((source,index)=>{
      const image=document.createElement('img');
      image.src=source;
      image.alt='';
      image.draggable=false;
      image.hidden=index!==0;
      image.addEventListener('error',()=>overlay.classList.add('desktop-loading-art-unavailable'),{once:true});
      art.append(image);
      return image;
    });
    const copy=document.createElement('span');
    copy.className='desktop-loading-copy';
    copy.textContent=loadingCopy('正在加载…');
    overlay.append(art,copy);
    target.append(overlay);
    state.overlay=overlay;
    state.shownAt=performance.now();
    syncLoadingMotion(state);
  };
  const beginLoading = target => {
    if (!(target instanceof HTMLElement)) return ()=>{};
    let state=loadingStates.get(target);
    if (!state) {
      state={count:0,loaded:false,overlay:null,frames:[],frameTimer:0,showTimer:0,settleTimer:0,error:false};
      loadingStates.set(target,state);
    }
    if (!state.count) {
      clearTimeout(state.settleTimer);
      state.generation=(state.generation||0)+1;
      if (!target.classList.contains('desktop-loading-pending')) state.priorBusy=target.getAttribute('aria-busy');
      state.error=false;
      target.querySelector(':scope > .desktop-load-error')?.remove();
      target.dataset.desktopLoadState='loading';
      target.setAttribute('aria-busy','true');
      if (getComputedStyle(target).position==='static') target.classList.add('desktop-loading-positioned');
      // Keep a successful projection visible while refreshing. Only the first
      // unresolved read owns the placeholder; fast reads never flash artwork.
      if (!state.loaded) {
        target.classList.add('desktop-loading-pending');
        state.showTimer=setTimeout(()=>showLoading(target,state),250);
      }
    }
    state.count++;
    const generation=state.generation;
    if(state.overlay)syncLoadingMotion(state);
    let released=false;
    return failed => {
      if (released) return;
      released=true;
      state.error ||= Boolean(failed);
      state.count=Math.max(0,state.count-1);
      if (state.count) return;
      clearTimeout(state.showTimer);
      stopLoadingMotion(state);
      // Projection rendering follows the awaited call; clear before the next paint.
      const settle=()=>requestAnimationFrame(()=>{
        if (state.count || state.generation!==generation) return;
        state.overlay?.remove();
        state.overlay=null;
        state.frames=[];
        target.classList.remove('desktop-loading-pending');
        if (state.priorBusy===null) target.removeAttribute('aria-busy');
        else target.setAttribute('aria-busy',state.priorBusy);
        target.dataset.desktopLoadState=state.error&&!state.loaded?'error':'ready';
        if (state.error && !state.loaded && !target.querySelector('#tasks-list-status')) {
          const error=document.createElement('p');
          error.className='desktop-load-error';
          error.setAttribute('role','alert');
          error.textContent=loadingCopy('加载失败，请刷新重试');
          target.append(error);
        }
        if (!state.error) state.loaded=true;
      });
      // Once artwork is visible, avoid a single-frame blink at the threshold.
      const remaining=state.overlay?Math.max(0,220-(performance.now()-state.shownAt)):0;
      if(remaining)state.settleTimer=setTimeout(settle,remaining);else settle();
    };
  };
  const trackCall = async (method, invoke) => {
    const route=readRoutes.get(method),target=route&&loadingTarget(route),state=target&&loadingStates.get(target);
    if (!target) return invoke();
    // A placeholder represents the first unresolved projection, not every click
    // or route entry. Background and explicit refreshes retain successful content.
    const wanted=target && (!state?.loaded || state.count);
    const finish=wanted?beginLoading(target):null;
    try {
      const value=await invoke();
      finish?.(['ERROR','FAILED','FAIL','BLOCKED'].includes(value?.status));
      return value;
    } catch(error) {
      finish?.(true);
      throw error;
    }
  };
  const syncAllLoading = () => {
    loadingStates.forEach((state,target)=>{
      if (!target.isConnected) {
        stopLoadingMotion(state);
        loadingStates.delete(target);
        return;
      }
      syncLoadingMotion(state);
    });
  };

  const isVisible = (element) => element instanceof HTMLElement
    && !element.closest('[hidden], [aria-hidden="true"]')
    && element.getClientRects().length > 0
    && getComputedStyle(element).visibility !== 'hidden';

  const resetHomeExpression = () => {
    window.clearTimeout(homeResetTimer);
    homeResetTimer = 0;
    homeQuestionUntil = 0;
    homeExpressions.forEach(({ button, image }) => {
      button.dataset.expression = 'interaction';
      button.removeAttribute('aria-busy');
      image.src = assets.interaction;
    });
  };
  const playHomeQuestion = () => {
    if(document.documentElement.classList.contains('desktop-hide-expressions'))return;
    if (homeQuestionUntil > Date.now()) return;
    homeQuestionUntil = Date.now() + 2000;
    homeExpressions.forEach(({ button, image }) => {
      button.dataset.expression = 'question';
      button.setAttribute('aria-busy', 'true');
      image.src = assets.question;
    });
    homeResetTimer = window.setTimeout(resetHomeExpression, 2000);
  };
  const playStartupQuestion = async () => {
    if (startupPlayed) return;
    const entry = homeExpressions.find(({ button }) => isVisible(button));
    if (!entry) return;
    startupPlayed = true;
    const question = new Image();
    question.src = assets.question;
    try { await Promise.all([entry.image.decode(), question.decode()]); }
    catch (_) { return; }
    if (isVisible(entry.button)) playHomeQuestion();
  };

  const signalAssistant = async (kind, detail = {}) => {
    if(document.documentElement.classList.contains('desktop-hide-expressions'))return null;
    const now = Date.now();
    const dedupeKey = `${kind}:${String(detail.key || detail.copy || '')}`;
    if (now - Number(recentSignals.get(dedupeKey) || 0) < 1200) return null;
    recentSignals.set(dedupeKey, now);
    signalSequence += 1;
    const request = {
      kind,
      event_id: `ui-${now.toString(36)}-${String(signalSequence).padStart(4, '0')}`,
      reduced_motion: prefersReducedMotion()
    };
    window.dispatchEvent(new CustomEvent('desktop:assistant-decoration-signal', {
      detail: { ...request, source: String(detail.source || 'product-ui') }
    }));
    if (typeof window.pywebview?.api?.assistant_ui_signal !== 'function') return {
      schema_version: 'DesktopAssistantUiSignalReceipt-v1',
      status: 'BROWSER_TEST_NO_NATIVE_TRANSPORT',
      ...request
    };
    try {
      return await window.pywebview.api.assistant_ui_signal(request);
    } catch (error) {
      window.dispatchEvent(new CustomEvent('desktop:assistant-decoration-signal-error', {
        detail: { kind, error: String(error?.message || error || 'UNKNOWN') }
      }));
      return null;
    }
  };

  const enhanceHomePrompt = (blank) => {
    /* Desktop_HOME_EXPRESSION_V1 */
    if (!(blank instanceof HTMLElement) || blank.dataset.expressionReady === 'true') return;
    // Final geometry is present in HTML; binding never replaces a painted copy.
    const group=blank.querySelector(':scope > .desktop-home-prompt');
    const button=group?.querySelector('.desktop-home-expression-button');
    const image=button?.querySelector('.desktop-home-expression-image');
    if(!button||!image)return;
    button.setAttribute('data-desktop-no-tooltip','');
    button.removeAttribute('title');
    homeExpressions.push({ button, image });
    button.addEventListener('click', (event) => {
      if (event.button === 0) playHomeQuestion();
    });
    blank.dataset.expressionReady = 'true';
  };

  const enhanceEmptyState = (empty) => {
    /* Desktop_EMPTY_STATE_ILLUSTRATION_V1 */
    if (!(empty instanceof HTMLElement) || empty.dataset.illustrationReady === 'true') return;
    if (!['inbox-empty', 'tasks-empty', 'messages-empty', 'library-empty', 'worklog-empty'].includes(empty.id)) return;
    const container = empty.firstElementChild;
    if (!(container instanceof HTMLElement)) return;
    container.querySelectorAll('.empty-mark').forEach((mark) => mark.remove());
    const image = document.createElement('img');
    image.className = 'desktop-empty-illustration';
    image.src = assets.empty;
    image.alt = '';
    image.draggable = false;
    container.prepend(image);
    empty.classList.add('desktop-empty-decorated');
    empty.dataset.illustrationReady = 'true';
  };

  const visibleSignalSurface = (element) => {
    if (!isVisible(element)) return false;
    return Boolean(element.matches('.toast, [role="alert"], [role="status"]'));
  };

  const isEmptyMessageNotice = (copy) => /^(?:(?:当前|目前)(?:筛选范围内|范围内|范围)?[：:]?\s*)?(?:暂无|没有|无)(?:新的?|未读|待处理|普通|错误|报错|异常|可处理的|可查看的|\s)*消息[。.!！]?$/.test(copy)
    || /^(?:0|零)\s*(?:条|个)\s*(?:错误|报错|异常)?消息[。.!！]?$/.test(copy);

  const inspectSignalSurface = (element) => {
    if (!(element instanceof HTMLElement)) return;
    const redCard = element.matches('.desktop-messages-card[data-tone="red"]')
      ? element
      : element.closest?.('.desktop-messages-card[data-tone="red"]');
    if (redCard) {
      if (!isVisible(redCard) || isEmptyMessageNotice(redCard.textContent.trim())) return;
      const messageId = redCard.dataset.messageId;
      if (messageId && seenErrorMessages.has(messageId)) return;
      if (messageId) {
        seenErrorMessages.add(messageId);
        if (seenErrorMessages.size > 256) seenErrorMessages.delete(seenErrorMessages.values().next().value);
      }
      void signalAssistant('ERROR_MESSAGE', {
        key: messageId || redCard.textContent.trim(),
        source: 'messages-red-card'
      });
      return;
    }
    const surface = visibleSignalSurface(element)
      ? element
      : element.closest?.('.toast, [role="alert"], [role="status"]');
    if (!visibleSignalSurface(surface)) return;
    const copy = surface.textContent.replace(/\s+/g, ' ').trim();
    if (!copy || isEmptyMessageNotice(copy.replace(/关闭$/, '').trim())) return;
    if (/(报错|失败|异常|错误)/.test(copy)) {
      void signalAssistant('ERROR_MESSAGE', { copy, source: 'visible-error-surface' });
    } else if (/(保存成功|已保存)/.test(copy) && !/(未保存|已恢复)/.test(copy)) {
      void signalAssistant('SAVE_SUCCESS', { copy, source: 'visible-save-surface' });
    }
  };

  const initialize = () => {
    // No-data artwork requires a successful first projection, not just an empty DOM.
    [...Object.keys(routeRoots),'chat'].forEach(route=>{
      const target=loadingTarget(route);
      if(target&&!target.dataset.desktopLoadState)target.dataset.desktopLoadState='uninitialized';
    });
    document.querySelectorAll('.desktop-blank-state').forEach(enhanceHomePrompt);
    document.querySelectorAll('.empty-state').forEach(enhanceEmptyState);
    if (document.readyState === 'complete') requestAnimationFrame(playStartupQuestion);
    else window.addEventListener('load', () => requestAnimationFrame(playStartupQuestion), { once: true });
    document.addEventListener('visibilitychange', () => {
      if (homeQuestionUntil && Date.now() >= homeQuestionUntil) resetHomeExpression();
      syncAllLoading();
    });
    window.addEventListener('desktop-integrated-route',syncAllLoading,true);
    window.matchMedia?.('(prefers-reduced-motion: reduce)').addEventListener?.('change',syncAllLoading);
    const observer = new MutationObserver((mutations) => {
      mutations.forEach((mutation) => {
        if (mutation.type === 'attributes') inspectSignalSurface(mutation.target);
        if (mutation.type === 'characterData') inspectSignalSurface(mutation.target.parentElement);
        // textContent replaces text nodes, producing childList rather than
        // characterData. Saved/error status lines use this normal DOM path.
        if (mutation.type === 'childList') inspectSignalSurface(mutation.target);
        mutation.addedNodes.forEach((node) => {
          if (!(node instanceof HTMLElement)) return;
          inspectSignalSurface(node);
          node.querySelectorAll?.('.desktop-messages-card[data-tone="red"], .toast, [role="alert"], [role="status"]')
            .forEach(inspectSignalSurface);
        });
      });
    });
    observer.observe(document.body, {
      attributes: true,
      attributeFilter: ['hidden', 'aria-hidden', 'data-tone'],
      childList: true,
      characterData: true,
      subtree: true
    });
  };

  Memorive.decorations = Object.freeze({
    applyHidden:hidden=>{
      if(hidden)resetHomeExpression();else requestAnimationFrame(playStartupQuestion);
      syncAllLoading();
    },
    beginLoading,
    trackCall,
    inspectLoading:()=>[...loadingStates].map(([target,state])=>({target:target.id||target.className,pending:state.count,
      visible:Boolean(state.overlay&&isVisible(state.overlay)),animating:Boolean(state.frameTimer),loaded:state.loaded})),
    notifySaveSuccess: (detail) => signalAssistant('SAVE_SUCCESS', detail),
    notifyErrorMessage: (detail) => signalAssistant('ERROR_MESSAGE', detail)
  });
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initialize, { once: true });
  } else {
    initialize();
  }
})();

