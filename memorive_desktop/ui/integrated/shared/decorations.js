/* Decorations of the main window: the home and empty-state faces, loading placeholders while a page waits
   for its first data (trackCall wraps service calls for this), and signals to the desk assistant
   (assistant_ui_signal). The faces are looks of the expression pack in use (shared/expression-pack.js); without a
   pack, or with 隐藏所有表情 on, the page shows none (.desktop-hide-expressions). Publishes Memorive.decorations. */
(() => {
  'use strict';
  if (Memorive.decorations) return;

  // A face is a look of the pack's position: {text}, {image} or {frames}; a step of an encoding animation is text.
  const packs = Memorive.expressionPack;
  const asLook = value => typeof value === 'string' ? { text: value } : value;
  // Empty pages: no files (inbox, library), nothing waiting (tasks, messages), nothing written yet (work log).
  const emptyPosition = Object.freeze({
    'inbox-empty': 'empty', 'library-empty': 'empty', 'tasks-empty': 'saved',
    'messages-empty': 'saved', 'worklog-empty': 'interaction'
  });
  const prefersReducedMotion = () => Boolean(
    window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  );
  // A face changes into the next one through an encoding animation: for a few frames the new face shows with its
  // characters not yet decoded replaced by the glyphs of an encoding, decoded in that encoding's order. There are
  // three fixed encodings, taken in encodingOrder, a fixed order that looks random. The desk assistant plays the same
  // three (product/desktop/assistant_native_motion.py, ENCODINGS); change both together.
  // EASTER EGG (彩蛋), intentional: the glyphs look like garbled text (锟斤拷, 烫, 屯 are the classic mojibake) on
  // purpose. They show for a few frames while a face changes; they are not an encoding bug, do not "fix" them.
  const encodings = Object.freeze([
    { narrow: '01', wide: '０１', order: 'left' },
    { narrow: '?#%&', wide: '锟斤拷烫屯', order: 'right' },
    { narrow: ".:;'", wide: '░▒▓█', order: 'middle' }
  ]);
  const encodingOrder = [1, 0, 2, 2, 1, 0, 2];
  let encodingCount = 0;
  const nextEncoding = () => encodingOrder[encodingCount++ % encodingOrder.length];
  // The count frames of one encoding animation that decodes into text (text itself comes after them).
  const encodingFrames = (text, encoding, count) => {
    const { narrow, wide, order } = encodings[encoding];
    const characters = Array.from(text), size = characters.length;
    const positions = characters.map((_, index) => index);
    if (order === 'right') positions.reverse();
    else if (order === 'middle') positions.sort((a, b) => Math.abs(2 * a - (size - 1)) - Math.abs(2 * b - (size - 1)) || a - b);
    const place = new Map(positions.map((position, rank) => [position, rank]));
    return Array.from({ length: count }, (_, frame) => {
      const decoded = Math.floor(size * frame / count);
      return characters.map((character, index) => {
        if (place.get(index) < decoded || character === ' ') return character;
        const glyphs = Array.from(character.codePointAt(0) < 0x370 && character !== '×' ? narrow : wide);
        return glyphs[(index * 3 + frame * 5 + encoding) % glyphs.length];
      }).join('');
    });
  };
  // EASTER EGG (彩蛋), intentional: a face as a program that reads UTF-8 as GBK shows it, e.g. (・ω・) as (銉幌夈兓).
  // Shown only after five clicks on the home face within three seconds (countHomeClick); not an encoding bug.
  const garbled = text => new TextDecoder('gbk').decode(new TextEncoder().encode(text)).replace(/\uFFFD/g, '?');
  let signalSequence = 0;
  const recentSignals = new Map();
  const seenErrorMessages = new Set();

  // Reuse the native request lifecycle; decorative loading never starts work.
  // The pack's loading look, or its open and closed eyes in turn.
  const loadingDurations = [450,180,450,180,450,180];
  const loadingLooks = () => {
    const loading = packs.look('loading');
    return loading ? [loading] : loadingDurations.map((_, i) => packs.look(i % 2 ? 'closed' : 'open'));
  };
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
    if (hidden || prefersReducedMotion() || state.frames.length < 2) return;
    let frame=0;
    const next=()=>{
      if (!state.count || !state.overlay?.isConnected || !isVisible(state.overlay) || document.hidden) return;
      state.frames[frame].hidden=true;
      frame=(frame+1)%state.frames.length;
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
    state.frames=loadingLooks().map((look,index)=>{
      const frame=document.createElement('span');
      // Decoration, like the other faces: app.js gives aria-hidden elements no text size.
      frame.setAttribute('aria-hidden','true');
      packs.paint(frame,look);
      frame.hidden=index!==0;
      art.append(frame);
      return frame;
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

  // The home faces (one in each page's blank state, changed together; the visible one is seen). Each rests on its
  // page's position (data-expression). Memorive opens on the pack's greeting, kept until another page is opened; a
  // click shows the question (text faces type a question mark) and goes back to rest two seconds later; five clicks
  // within three seconds show a text face garbled for two seconds (EASTER EGG (彩蛋), intentional: playHomeGarbled,
  // not a bug).
  const homeExpressions = [];
  const homeClicks = [];
  let homeMode = 'rest';
  let homeTimer = 0;
  let greetingRoute = '';
  let startupPlayed = false;
  const expressionsHidden = () => document.documentElement.classList.contains('desktop-hide-expressions');
  // Steps are [look, ms] (a string is a text face): each shows for ms, the last one stays and is the entry's face.
  const playSteps = (entry, steps) => {
    window.clearTimeout(entry.timer);
    entry.face = asLook(steps[steps.length - 1][0]);
    const show = index => {
      packs.paint(entry.image, asLook(steps[index][0]));
      if (index + 1 < steps.length) entry.timer = window.setTimeout(() => show(index + 1), steps[index][1]);
    };
    show(0);
  };
  // A text face decodes in through an encoding animation; a picture just shows.
  const decodeSteps = (look, encoding, hold) => !packs.isText(look) || prefersReducedMotion() ? [[look, hold]]
    : [...encodingFrames(look.text, encoding, 6).map(frame => [frame, 50]), [look, hold]];
  const sameLook = (a, b) => a === b || (packs.isText(a) && packs.isText(b) && a.text === b.text);
  const setHome = (mode, stepsFor) => {
    window.clearTimeout(homeTimer);
    homeMode = mode;
    homeExpressions.forEach(entry => {
      entry.button.dataset.expression = mode === 'rest' ? entry.rest.expression : mode;
      if (mode === 'rest') entry.button.removeAttribute('aria-busy');
      else entry.button.setAttribute('aria-busy', 'true');
      playSteps(entry, stepsFor(entry));
    });
  };
  const restHome = animate => {
    const encoding = nextEncoding();
    setHome('rest', entry => animate && !sameLook(entry.face, entry.rest.look)
      ? decodeSteps(entry.rest.look, encoding, 0) : [[entry.rest.look, 0]]);
  };
  const playHomeQuestion = () => {
    if (expressionsHidden() || homeMode === 'question' || homeMode === 'garbled') return;
    const encoding = nextEncoding(), open = packs.look('open'), question = packs.look('question');
    // Text faces type a question mark after the open eyes; otherwise the question look just shows.
    const typing = packs.isText(open) && packs.isText(question) && !prefersReducedMotion();
    const caret = typing && open.text + '_';
    setHome('question', () => typing
      ? [...decodeSteps(open, encoding, 160), [caret, 160], [open, 160], [caret, 160], [question, 0]]
      : [[question, 0]]);
    homeTimer = window.setTimeout(() => restHome(true), 2000);
  };
  const playHomeGarbled = () => {
    if (expressionsHidden() || !homeExpressions.some(entry => packs.isText(entry.face))) return;
    setHome('garbled', entry => packs.isText(entry.face)
      ? [[garbled(entry.face.text), 2000], ...decodeSteps(entry.rest.look, 1, 0)]
      : [[entry.face, 2000], [entry.rest.look, 0]]);
    homeTimer = window.setTimeout(() => restHome(false), 2400);
  };
  // EASTER EGG (彩蛋), intentional: the fifth click within three seconds shows the garbled face.
  const countHomeClick = () => {
    const now = Date.now();
    homeClicks.push(now);
    while (now - homeClicks[0] > 3000) homeClicks.shift();
    if (homeClicks.length < 5) return;
    homeClicks.length = 0;
    playHomeGarbled();
  };
  const playStartupGreeting = () => {
    if (startupPlayed || expressionsHidden() || !packs.active()) return;
    if (!homeExpressions.some(({ button }) => isVisible(button))) return;
    startupPlayed = true;
    greetingRoute = document.body.dataset.desktopActiveRoute || 'home';
    const encoding = nextEncoding(), greeting = packs.look('greeting');
    setHome('greeting', () => decodeSteps(greeting, encoding, 0));
  };

  // Paints the faces from the pack in use: each home face at rest, and every face marked data-expression-face (the
  // empty pages' and the research chat's home face, which workspace.js copies from a template and paints with this).
  const paintFaces = (root = document) => {
    if (root === document) {
      homeExpressions.forEach(entry => { entry.rest.look = packs.look(entry.rest.expression); });
      restHome(false);
    }
    root.querySelectorAll('[data-expression-face]').forEach(face => packs.paint(face, packs.look(face.dataset.expressionFace)));
  };
  // The faces are hidden while 隐藏所有表情 is on or no pack is in use. The page keeps its home and logo out of sight
  // (.desktop-user-loading) until both the person's settings (local-user.js) and the pack are read.
  let userHidden = null;
  const syncHidden = () => {
    if (userHidden === null || !packs.loaded()) return;
    const hidden = userHidden || !packs.active();
    document.documentElement.classList.toggle('desktop-hide-expressions', hidden);
    document.documentElement.classList.remove('desktop-user-loading');
    if (hidden) restHome(false); else requestAnimationFrame(playStartupGreeting);
    syncAllLoading();
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
    if (typeof window.pywebview?.api?.assistant_ui_signal !== 'function') return {
      schema_version: 'DesktopAssistantUiSignalReceipt-v1',
      status: 'BROWSER_TEST_NO_NATIVE_TRANSPORT',
      ...request
    };
    try {
      return await window.pywebview.api.assistant_ui_signal(request);
    } catch (error) {
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
    // The look it rests on is picked from the pack when the pack is read (paintFaces).
    homeExpressions.push({ button, image, timer: 0, face: null,
      rest: { expression: button.dataset.expression || 'interaction', look: null } });
    // The easter egg counts every click, also those that the research home's click interval holds back
    // (routes/inbox/research-home.js stops them on the way down); the question plays on the clicks that arrive.
    blank.addEventListener('click', (event) => {
      if (event.button === 0 && button.contains(event.target)) countHomeClick();
    }, true);
    button.addEventListener('click', (event) => {
      if (event.button === 0) playHomeQuestion();
    });
    blank.dataset.expressionReady = 'true';
  };

  const enhanceEmptyState = (empty) => {
    /* Desktop_EMPTY_STATE_ILLUSTRATION_V1 */
    if (!(empty instanceof HTMLElement) || empty.dataset.illustrationReady === 'true') return;
    if (!emptyPosition[empty.id]) return;
    const container = empty.firstElementChild;
    if (!(container instanceof HTMLElement)) return;
    container.querySelectorAll('.empty-mark').forEach((mark) => mark.remove());
    const face = document.createElement('span');
    face.className = 'desktop-empty-illustration';
    face.setAttribute('aria-hidden', 'true');
    face.dataset.expressionFace = emptyPosition[empty.id];
    packs.paint(face, packs.look(face.dataset.expressionFace));
    container.prepend(face);
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
    packs.onChange(() => { paintFaces(); syncHidden(); });
    if (packs.loaded()) paintFaces();
    if (document.readyState === 'complete') requestAnimationFrame(playStartupGreeting);
    else window.addEventListener('load', () => requestAnimationFrame(playStartupGreeting), { once: true });
    document.addEventListener('visibilitychange', syncAllLoading);
    window.addEventListener('desktop-integrated-route',syncAllLoading,true);
    window.addEventListener('desktop-integrated-route', event => {
      if (homeMode === 'greeting' && event.detail?.route !== greetingRoute) restHome(false);
    });
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
      userHidden=Boolean(hidden);
      syncHidden();
    },
    paintFaces,
    beginLoading,
    trackCall,
    encodingFrames,
    garbled,
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

