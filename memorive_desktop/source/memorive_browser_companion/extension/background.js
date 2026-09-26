"use strict";

importScripts('site_profiles.js', 'browser_identity.js');
const SITE_CONFIG = globalThis.MEMORIVE_SITE_PROFILES;
const ACTIVE_SITES = SITE_CONFIG.sites.filter(row => row.enabled);
const ACTIVE_IDS = ACTIVE_SITES.map(row => row.provider_id);

let runnerTabId = null;
let runnerWindowId = null;
const RUNNER_WATCHDOG_ALARM = "memorive_sync_runner_watchdog";
const RUNNER_WATCHDOG_MINUTES = 5;
const IDENTITY_VERSION_MARKER_PREFIX = "memorive_identity_receipt_";
const IDENTITY_VERSION_MARKER_MINUTES = 525600;
const APPROVED_FRAGMENT_TRIGGER_HOSTS = new Set(ACTIVE_SITES.flatMap(row=>row.hosts));
let syncState = {
  running: false,
  trigger: "",
  provider_id: "",
  current: 0,
  total: 0,
  captured: 0,
  failed: 0,
  skipped: 0,
  unchanged: 0,
  status: "IDLE",
  error_code: "",
  providers: []
};

const safeErrorCode = (error) => {
  const raw = String(error?.message || error || "UNKNOWN_ERROR").toUpperCase();
  return raw.replace(/[^A-Z0-9_]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 120) || "UNKNOWN_ERROR";
};

const broadcast = (type) => {
  chrome.runtime.sendMessage({ type, state: { ...syncState } }).catch(() => {});
};

const armRunnerWatchdog = async () => {
  await chrome.alarms.clear(RUNNER_WATCHDOG_ALARM).catch(() => {});
  chrome.alarms.create(RUNNER_WATCHDOG_ALARM, { delayInMinutes: RUNNER_WATCHDOG_MINUTES });
};

const clearRunnerWatchdog = () => chrome.alarms.clear(RUNNER_WATCHDOG_ALARM).catch(() => {});

const writeIdentityReceipt = async (reason) => {
  const manifest = chrome.runtime.getManifest();
  const payload = {
    schema_version: "MEMORIVE_BROWSER_COMPANION_IDENTITY_V1",
    extension_id: chrome.runtime.id,
    extension_version: manifest.version,
    browser_family: await MEMORIVE_BROWSER_IDENTITY.detect(),
    observed_at: new Date().toISOString(),
    lifecycle_reason: String(reason || "UNKNOWN").toUpperCase(),
    trigger_protocol: "HTTPS_APPROVED_FRAGMENT_V3",
    preflight_limit: 2,
    full_limit: 50,
    incremental_limit: 10,
    providers: ACTIVE_IDS,
    site_profile_sha256: SITE_CONFIG.sha256,
    cookie_value_reads: 0,
    browser_storage_value_reads: 0,
    hidden_provider_api_calls: 0
  };
  const encoded = encodeURIComponent(JSON.stringify(payload, null, 2));
  await chrome.downloads.download({
    url: `data:application/json;charset=utf-8,${encoded}`,
    filename: `Memorive会话桥接/memorive-browser-companion-identity-${manifest.version}-${payload.browser_family}-${chrome.runtime.id}.json`,
    conflictAction: "overwrite"
  });
};

const ensureVersionIdentityReceipt = async () => {
  const manifestVersion = String(chrome.runtime.getManifest().version || "unknown");
  const markerName = `${IDENTITY_VERSION_MARKER_PREFIX}${manifestVersion.replace(/[^0-9A-Za-z]+/g, "_")}_${SITE_CONFIG.sha256}`;
  const existingMarker = await chrome.alarms.get(markerName).catch(() => null);
  if (existingMarker) return false;
  await writeIdentityReceipt("service_worker_version_start");
  chrome.alarms.create(markerName, { delayInMinutes: IDENTITY_VERSION_MARKER_MINUTES });
  return true;
};

// Manifest V3 workers can wake repeatedly. Keep the identity needed after an
// unpacked-package upgrade, but emit it once per version and exact site profile.
void ensureVersionIdentityReceipt().catch(() => {});

const writeWatchdogSyncReport = async () => {
  const manifest = chrome.runtime.getManifest();
  const providers = (Array.isArray(syncState.providers) && syncState.providers.length
    ? syncState.providers
    : ACTIVE_IDS.map((providerId) => ({ provider_id: providerId })))
    .map((row) => {
      const completed = String(row.status || "").startsWith("COMPLETE");
      return {
        provider_id: row.provider_id,
        status: completed ? row.status : "ERROR",
        indexed_count: Number(row.indexed_count) || 0,
        candidate_count: Number(row.candidate_count) || 0,
        capture_target: Number(row.capture_target ?? row.total) || 0,
        body_read_count: Number(row.body_read_count ?? row.attempted ?? row.current) || 0,
        captured_count: Number(row.captured_count ?? row.current) || 0,
        unchanged_count: Number(row.unchanged_count ?? row.unchanged) || 0,
        failed_count: Number(row.failed_count ?? row.failed) || 0,
        skipped_candidate_count: Number(row.skipped_candidate_count ?? row.skipped) || 0,
        error_code: completed ? String(row.error_code || "") : "SYNC_RUNNER_WATCHDOG_TIMEOUT",
        skipped_error_codes: Array.isArray(row.skipped_error_codes) ? row.skipped_error_codes : [],
        safe_diagnostic: null
      };
    });
  const payload = {
    schema_version: "MEMORIVE_BROWSER_SESSION_SYNC_REPORT_V2",
    site_profile_sha256: SITE_CONFIG.sha256,
    source: "MEMORIVE_BROWSER_COMPANION_BACKGROUND_WATCHDOG_V1",
    extension_id: chrome.runtime.id,
    extension_version: manifest.version,
    per_provider_limit: Number(syncState.per_provider_limit) || 0,
    sync_mode: String(syncState.sync_mode || "UNKNOWN"),
    browser_family: await MEMORIVE_BROWSER_IDENTITY.detect(),
    generated_at: new Date().toISOString(),
    trigger: String(syncState.trigger || "UNKNOWN"),
    status: "ERROR",
    error_code: "SYNC_RUNNER_WATCHDOG_TIMEOUT",
    active_provider_count: providers.length,
    provider_parallelism: false,
    maximum_capture_windows: 1,
    per_provider_capture_mode: "DEDICATED_VISIBLE_WINDOW_OWNED_TABS",
    window_mode: "DEDICATED_VISIBLE",
    user_tab_reused: false,
    providers,
    cookie_value_reads: 0,
    browser_storage_value_reads: 0,
    hidden_provider_api_calls: 0,
    remote_session_mutations: 0
  };
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const encoded = encodeURIComponent(JSON.stringify(payload, null, 2));
  await chrome.downloads.download({
    url: `data:application/json;charset=utf-8,${encoded}`,
    filename: `Memorive会话桥接/memorive-session-library-sync-report-${stamp}.json`,
    conflictAction: "uniquify"
  });
};

const existingRunnerTab = async () => {
  if (!syncState.running || typeof runnerTabId !== "number") return null;
  const tab = await chrome.tabs.get(runnerTabId).catch(() => null);
  return tab?.url?.startsWith(chrome.runtime.getURL("runner.html")) ? tab : null;
};

const runnerUrlFor = (trigger, options = {}) => {
  const url = new URL(chrome.runtime.getURL("runner.html"));
  url.searchParams.set("trigger", trigger || "MANUAL");
  if (Number.isFinite(options.limit)) {
    url.searchParams.set("limit", String(Math.max(1, Math.min(50, options.limit))));
  }
  if (["PREFLIGHT", "FULL", "INCREMENTAL"].includes(options.syncMode)) {
    url.searchParams.set("mode", options.syncMode);
  }
  if (typeof options.cursorToken === "string" && options.cursorToken) {
    url.searchParams.set("cursor", options.cursorToken);
  }
  const providers = Array.isArray(options.providers)
    ? options.providers.filter((providerId, index, values) => (
      ACTIVE_IDS.includes(providerId) && values.indexOf(providerId) === index
    ))
    : [];
  if (providers.length) url.searchParams.set("providers", providers.join(","));
  return url;
};

const createSyncRunner = async (trigger, options = {}) => {
  if (!ACTIVE_IDS.length) throw new Error('BROWSER_NO_ENABLED_SITES');
  if (syncState.running) throw new Error('BROWSER_SYNC_ALREADY_RUNNING');
  options.providers = Array.isArray(options.providers) && options.providers.length ? options.providers : ACTIVE_IDS;
  if (!options.providers.every(id=>ACTIVE_IDS.includes(id))) throw new Error('BROWSER_SITE_DISABLED');
  const existing = await existingRunnerTab();
  if (existing?.id) {
    runnerTabId = existing.id;
    runnerWindowId = existing.windowId;
    syncState = { ...syncState, running: true, trigger, status: "RUNNER_STARTING", error_code: "" };
    await armRunnerWatchdog();
    try {
      await chrome.tabs.update(existing.id, { url: runnerUrlFor(trigger, options).href });
    } catch (error) {
      clearRunnerWatchdog();
      syncState = {
        ...syncState,
        running: false,
        status: "ERROR",
        error_code: safeErrorCode(error)
      };
      throw error;
    }
    broadcast("MEMORIVE_SYNC_ALL_PROGRESS");
    return {
      ok: true,
      reused: true,
      window_mode: "DEDICATED_VISIBLE",
      dedicated_window_id: runnerWindowId,
      user_tab_reused: false,
      state: { ...syncState }
    };
  }
  const url = runnerUrlFor(trigger, options);
  const created = await chrome.windows.create({
    url: url.href,
    type: "normal",
    focused: true,
    state: "normal",
    width: 1100,
    height: 800
  });
  const tab = created?.tabs?.find((row) => typeof row?.id === "number")
    || (await chrome.tabs.query({ windowId: created?.id })).find((row) => typeof row?.id === "number");
  if (typeof created?.id !== "number" || typeof tab?.id !== "number") {
    if (typeof created?.id === "number") await chrome.windows.remove(created.id).catch(() => {});
    throw new Error("SYNC_RUNNER_WINDOW_CREATE_FAILED");
  }
  runnerTabId = tab.id;
  runnerWindowId = created.id;
  syncState = {
    running: true,
    trigger,
    provider_id: "",
    current: 0,
    total: 0,
    captured: 0,
    failed: 0,
    skipped: 0,
    unchanged: 0,
    status: "RUNNER_STARTING",
    error_code: "",
    providers: options.providers.map(provider_id=>({provider_id})),
    window_mode: "DEDICATED_VISIBLE",
    dedicated_window_id: runnerWindowId,
    user_tab_reused: false
  };
  await armRunnerWatchdog();
  broadcast("MEMORIVE_SYNC_ALL_PROGRESS");
  return {
    ok: true,
    reused: false,
    window_mode: "DEDICATED_VISIBLE",
    dedicated_window_id: runnerWindowId,
    user_tab_reused: false,
    state: { ...syncState }
  };
};

let runnerStarting = false;
const startSyncRunner = async (trigger, options = {}) => {
  // Claim the slot before the first await; rapid UI/popup clicks cannot fork
  // capture windows or retarget an in-progress bounded test.
  if (runnerStarting || syncState.running) throw new Error('BROWSER_SYNC_ALREADY_RUNNING');
  runnerStarting = true;
  try { return await createSyncRunner(trigger, options); }
  finally { runnerStarting = false; }
};

const popupSenderApproved = (sender) => (
  !sender?.tab && sender?.url === chrome.runtime.getURL("popup.html")
);

const sanitizedHttpsPageUrl = (rawUrl) => {
  const parsed = globalThis.MEMORIVE_OFFICIAL_WEB.requireCaptureUrl(rawUrl);
  if (parsed.protocol !== "https:" || !parsed.hostname) {
    throw new Error("CURRENT_PAGE_HTTPS_REQUIRED");
  }
  parsed.username = "";
  parsed.password = "";
  parsed.search = "";
  parsed.hash = "";
  return parsed.href;
};

const captureActiveTab = async (sender, readOnly = false) => {
  if (!popupSenderApproved(sender)) throw new Error("CURRENT_PAGE_CAPTURE_SENDER_NOT_APPROVED");
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (typeof tab?.id !== "number" || !tab.url) throw new Error("CURRENT_PAGE_ACTIVE_TAB_UNAVAILABLE");
  const sourceUrl = sanitizedHttpsPageUrl(tab.url);
  await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["site_profiles.js", "recording.js", "content.js"] });
  if (sanitizedHttpsPageUrl((await chrome.tabs.get(tab.id))?.url) !== sourceUrl) {
    throw new Error("CURRENT_PAGE_ORIGIN_OR_SESSION_CHANGED");
  }
  const response = await chrome.tabs.sendMessage(tab.id, {
    type: "MEMORIVE_CAPTURE_CURRENT_PAGE_GENERIC"
  });
  const conversation = response?.conversation;
  if (
    !response?.ok ||
    conversation?.provider_id !== "universal" ||
    conversation?.capture_method !== "BROWSER_COMPANION_ACTIVE_TAB_V1" ||
    conversation?.capture_status !== "PARTIAL" ||
    !Array.isArray(conversation?.messages) ||
    !conversation.messages.some((row) => row?.role === "user") ||
    !conversation.messages.some((row) => row?.role === "assistant")
  ) {
    throw new Error(response?.error_code || "CURRENT_PAGE_CAPTURE_INVALID");
  }
  const currentTab = await chrome.tabs.get(tab.id);
  if (sanitizedHttpsPageUrl(currentTab?.url) !== sourceUrl ||
      sanitizedHttpsPageUrl(conversation?.source_url) !== sourceUrl) {
    throw new Error("CURRENT_PAGE_ORIGIN_OR_SESSION_CHANGED");
  }
  if (readOnly) return {ok:true, message_count:conversation.messages.length, capture_status:conversation.capture_status};
  return exportVisibleConversation(conversation, sourceUrl);
};

const exportVisibleConversation = async (conversation, sourceUrl) => {
  const observedAt = new Date().toISOString();
  const archivedConversation = {
    provider_id: "universal",
    provider_session_id: String(conversation.provider_session_id || "").slice(0, 240),
    title: String(conversation.title || "当前网页会话").trim().slice(0, 240),
    created_at: "UNKNOWN",
    updated_at: "UNKNOWN",
    recency_bucket: "UNKNOWN",
    selection_basis: "CURRENT_PAGE_USER_GESTURE",
    source_recency_rank: 0,
    observed_at: observedAt,
    source_url: sourceUrl,
    capture_method: "BROWSER_COMPANION_ACTIVE_TAB_V1",
    capture_status: "PARTIAL",
    source_truncated: true,
    top_boundary_reached: false,
    bottom_boundary_reached: false,
    error_code: conversation.error_code === "SCROLL_RECORDING_VISIBLE_WINDOWS_ONLY" ? conversation.error_code : "CURRENT_VISIBLE_INTERFACE_ONLY",
    messages: conversation.messages.map((row, index) => ({
      message_id: String(row.message_id || `universal-${index + 1}`).slice(0, 240),
      role: row.role,
      content_kind: "text",
      content: String(row.text || "").trim(),
      occurred_at: "UNKNOWN"
    })).filter((row) => row.content)
  };
  const payload = {
    schema_version: "MEMORIVE_BROWSER_SESSION_LIBRARY_V4_DRAFT",
    source: "MEMORIVE_BROWSER_COMPANION_VISIBLE_UI_V4_DRAFT",
    generated_at: observedAt,
    sync_mode: "CURRENT_PAGE",
    sync_scope: "CURRENT_PAGE",
    site_profile_sha256: SITE_CONFIG.sha256,
    official_web_revision: SITE_CONFIG.official_web_revision,
    provider_id: "universal",
    enumerated_count: 1,
    candidate_count: 1,
    capture_target: 1,
    body_read_count: 1,
    captured_count: 1,
    unchanged_count: 0,
    failed_count: 0,
    skipped_candidate_count: 0,
    conversations: [archivedConversation],
    failures: [],
    skipped_candidates: [],
    cookie_value_reads: 0,
    browser_storage_value_reads: 0,
    hidden_provider_api_calls: 0,
    remote_session_mutations: 0
  };
  const stamp = observedAt.replace(/[:.]/g, "-");
  const encoded = encodeURIComponent(JSON.stringify(payload, null, 2));
  const downloadId = await chrome.downloads.download({
    url: `data:application/json;charset=utf-8,${encoded}`,
    filename: `Memorive会话桥接/memorive-session-library-universal-${stamp}.json`,
    conflictAction: "uniquify"
  });
  return {
    ok: true,
    provider_id: "universal",
    title: archivedConversation.title,
    download_id: downloadId,
    current_page_only: true,
    source_truncated: true
  };
};

const approvedFragmentTriggerSender = (sender) => {
  try {
    const url = globalThis.MEMORIVE_OFFICIAL_WEB.requireAllowedUrl(sender?.tab?.url || sender?.url || "");
    return APPROVED_FRAGMENT_TRIGGER_HOSTS.has(url.hostname);
  } catch (_error) {
    return false;
  }
};

// Extension-owned alarm grants survive MV3 suspension without storage permissions.
const RECORD_PREFIX="memorive-record:";
const completedRecordings=new Map();
const recordGrants=async()=> (await chrome.alarms.getAll()).filter(a=>a.name.startsWith(RECORD_PREFIX))
  .map(a=>{try{return {...JSON.parse(decodeURIComponent(a.name.slice(RECORD_PREFIX.length))),name:a.name};}catch{return null;}}).filter(Boolean);
const recordingCommand=async(message,sender)=>{
  if (!popupSenderApproved(sender)) throw Error("RECORDING_SENDER_NOT_APPROVED");
  const [tab]=await chrome.tabs.query({active:true,currentWindow:true});
  const sourceUrl=sanitizedHttpsPageUrl(tab?.url);
  await chrome.scripting.executeScript({target:{tabId:tab.id},files:["site_profiles.js","recording.js","content.js"]});
  if (sanitizedHttpsPageUrl((await chrome.tabs.get(tab.id)).url)!==sourceUrl) throw Error("CURRENT_PAGE_ORIGIN_OR_SESSION_CHANGED");
  const state=await chrome.tabs.sendMessage(tab.id,{type:"MEMORIVE_RECORDING_STATUS"});
  if (message.type==="MEMORIVE_RECORD_ACTIVE_STATUS") return state;
  if (message.type==="MEMORIVE_RECORD_ACTIVE_STOP")
    return chrome.tabs.sendMessage(tab.id,{type:"MEMORIVE_RECORDING_STOP",token:state?.state?.token});
  if (["RECORDING","SAVING"].includes(state?.state?.status)) return state;
  const grant={tabId:tab.id,sourceUrl,token:crypto.randomUUID(),deadline:Date.now()+600000};
  const name=RECORD_PREFIX+encodeURIComponent(JSON.stringify(grant));
  await chrome.alarms.create(name,{when:grant.deadline,periodInMinutes:1});
  try {
    return await chrome.tabs.sendMessage(tab.id,{type:"MEMORIVE_RECORDING_START",token:grant.token,deadline:grant.deadline});
  } catch(error) {await chrome.alarms.clear(name);throw error;}
};
const finishRecording=async(message,sender)=>{
  if (sender.id!==chrome.runtime.id || !Number.isInteger(sender.tab?.id)) throw Error("RECORDING_SENDER_NOT_APPROVED");
  const key=sender.tab.id+":"+message.token;
  if (completedRecordings.has(key)) return completedRecordings.get(key);
  const grant=(await recordGrants()).find(g=>g.tabId===sender.tab.id && g.token===message.token);
  if (completedRecordings.has(key)) return completedRecordings.get(key);
  if (!grant) throw Error("RECORDING_GRANT_NOT_FOUND");
  const conversation=message.conversation;
  if (sanitizedHttpsPageUrl(conversation?.source_url)!==grant.sourceUrl ||
      conversation?.capture_status!=="PARTIAL" ||
      conversation?.capture_method!=="BROWSER_COMPANION_ACTIVE_TAB_V1" ||
      !Array.isArray(conversation.messages) || conversation.messages.length>10000 ||
      !conversation.messages.some(r=>r.role==="user") || !conversation.messages.some(r=>r.role==="assistant"))
    throw Error("RECORDING_CAPTURE_INVALID");
  const work=(async()=>{
    await chrome.alarms.clear(grant.name);
    return exportVisibleConversation(conversation,grant.sourceUrl);
  })();
  completedRecordings.set(key,work);
  if (completedRecordings.size>100) completedRecordings.delete(completedRecordings.keys().next().value);
  return work;
};
chrome.alarms.onAlarm.addListener(alarm=>{
  if (!alarm.name.startsWith(RECORD_PREFIX)) return;
  void (async()=>{
    // One-shot alarms may already be removed when the callback fires.
    let grant;try {grant=JSON.parse(decodeURIComponent(alarm.name.slice(RECORD_PREFIX.length)));} catch{return;}
    try { await chrome.tabs.sendMessage(grant.tabId,{type:"MEMORIVE_RECORDING_STOP",token:grant.token,reason:"TIME_LIMIT"}); }
    catch { await chrome.alarms.clear(alarm.name); }
  })();
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (["MEMORIVE_RECORD_ACTIVE_START","MEMORIVE_RECORD_ACTIVE_STOP","MEMORIVE_RECORD_ACTIVE_STATUS","MEMORIVE_RECORDING_FINISH"].includes(message?.type)) {
    const action=message.type==="MEMORIVE_RECORDING_FINISH" ? finishRecording(message,sender) : recordingCommand(message,sender);
    action.then(sendResponse).catch(error=>sendResponse({ok:false,error_code:safeErrorCode(error)}));
    return true;
  }
  if (message?.type === "MEMORIVE_SYNC_STATUS") {
    sendResponse({ ok: true, state: { ...syncState } });
    return false;
  }
  if (["MEMORIVE_CAPTURE_ACTIVE_TAB", "MEMORIVE_TEST_ACTIVE_TAB"].includes(message?.type)) {
    captureActiveTab(sender, message.type === "MEMORIVE_TEST_ACTIVE_TAB").then(sendResponse).catch((error) => {
      sendResponse({ ok: false, error_code: safeErrorCode(error) });
    });
    return true;
  }
  if (message?.type === "MEMORIVE_BRIDGE_IDENTITY_RELOAD_REQUEST") {
    if (!approvedFragmentTriggerSender(sender)) {
      sendResponse({ ok: false, error_code: "IDENTITY_RELOAD_SENDER_NOT_APPROVED" });
      return false;
    }
    const manifestVersion = chrome.runtime.getManifest().version;
    if (message.bridge_version !== manifestVersion) {
      sendResponse({
        ok: false,
        error_code: "IDENTITY_RELOAD_VERSION_MISMATCH",
        extension_version: manifestVersion
      });
      return false;
    }
    // Chromium does not guarantee that chrome.runtime.reload() emits
    // onInstalled. Persist a fresh identity before the content script restarts
    // the service worker so the desktop app can distinguish slow from offline.
    writeIdentityReceipt("explicit_reload_request").then(() => {
      sendResponse({ ok: true, extension_version: manifestVersion, reload_scheduled: true });
      let productReloadTransport = false;
      try {
        const senderUrl = new URL(sender.tab?.url || "");
        productReloadTransport = senderUrl.searchParams.get("memorive_bridge_reload") === "v1";
      } catch (_error) {
        productReloadTransport = false;
      }
      const closeTrigger = productReloadTransport && typeof sender.tab?.id === "number"
        ? chrome.tabs.remove(sender.tab.id).catch(() => {})
        : Promise.resolve();
      // Close the one-shot provider tab first, then reload from the background
      // itself. This avoids depending on a content-script promise that may be
      // destroyed when Chromium closes the trigger tab.
      void closeTrigger.finally(() => chrome.runtime.reload());
    }).catch((error) => {
      sendResponse({ ok: false, error_code: safeErrorCode(error) });
    });
    return true;
  }
  if (message?.type === "MEMORIVE_SYNC_ALL_LIBRARIES") {
    if (!popupSenderApproved(sender)) { sendResponse({ok:false,error_code:'SYNC_TRIGGER_SENDER_NOT_APPROVED'}); return false; }
    // Popup uses the same bounded test path; full capture remains desktop gated.
    startSyncRunner("MANUAL_POPUP",{limit:2,syncMode:'PREFLIGHT'}).then(sendResponse).catch((error) => {
      sendResponse({ ok: false, error_code: safeErrorCode(error), state: { ...syncState } });
    });
    return true;
  }
  if (message?.type === "MEMORIVE_SYNC_ALL_LIBRARIES_FROM_APPROVED_FRAGMENT") {
    if (!approvedFragmentTriggerSender(sender)) {
      sendResponse({ ok: false, error_code: "SYNC_TRIGGER_SENDER_NOT_APPROVED", state: { ...syncState } });
      return false;
    }
    if (message.site_profile_sha256 !== SITE_CONFIG.sha256) {
      sendResponse({ok:false,error_code:'BROWSER_SITE_PROFILE_MISMATCH'}); return false;
    }
    const syncMode = ["PREFLIGHT", "FULL", "INCREMENTAL"].includes(message.sync_mode)
      ? message.sync_mode
      : "PREFLIGHT";
    const fullSync = syncMode === "FULL";
    const incrementalSync = syncMode === "INCREMENTAL";
    const requestedProviders = Array.isArray(message.providers) ? message.providers : [];
    if (!requestedProviders.every((providerId, index, values) => (
      ACTIVE_IDS.includes(providerId) && values.indexOf(providerId) === index
    ))) {
      sendResponse({ ok: false, error_code: "SYNC_TRIGGER_PROVIDER_SCOPE_INVALID", state: { ...syncState } });
      return false;
    }
    const cursorToken = typeof message.cursor_token === "string" ? message.cursor_token : "";
    if (incrementalSync && (
      !cursorToken || cursorToken.length > 8192 || !/^[A-Za-z0-9_-]+$/.test(cursorToken)
    )) {
      sendResponse({ ok: false, error_code: "INCREMENTAL_CURSOR_TOKEN_INVALID", state: { ...syncState } });
      return false;
    }
    startSyncRunner(
      incrementalSync
        ? "APPROVED_PROVIDER_FRAGMENT_INCREMENTAL_V1"
        : (fullSync ? "APPROVED_PROVIDER_FRAGMENT_FULL_V1" : "APPROVED_PROVIDER_FRAGMENT_PREFLIGHT_V1"),
      {
        limit: incrementalSync ? 10 : (fullSync ? 50 : 2),
        providers: requestedProviders,
        syncMode,
        cursorToken
      }
    ).then((result) => {
      sendResponse(result);
      // The approved HTTPS fragment is only a one-shot transport into the
      // extension.  Close that product-opened trigger tab after the runner has
      // accepted the request so repeated incremental syncs do not accumulate
      // provider tabs.  The provider workers have their own bounded lifecycle.
      let productTransport = false;
      try {
        const senderUrl = new URL(sender.tab?.url || "");
        productTransport = senderUrl.searchParams.get("memorive_bridge_sync") === "v1";
      } catch (_error) {
        productTransport = false;
      }
      if (productTransport && typeof sender.tab?.id === "number" && sender.tab.id !== runnerTabId) {
        void chrome.tabs.remove(sender.tab.id).catch(() => {});
      }
    }).catch((error) => {
      sendResponse({ ok: false, error_code: safeErrorCode(error), state: { ...syncState } });
    });
    return true;
  }
  if (message?.type === "MEMORIVE_SYNC_ALL_PROGRESS") {
    if (sender.tab?.id) {
      runnerTabId = sender.tab.id;
      if (typeof sender.tab.windowId === "number") runnerWindowId = sender.tab.windowId;
    }
    syncState = { ...syncState, ...message.state, running: true };
    void armRunnerWatchdog();
    broadcast("MEMORIVE_SYNC_ALL_PROGRESS");
    sendResponse({ ok: true });
    return false;
  }
  if (message?.type === "MEMORIVE_SYNC_ALL_COMPLETE") {
    syncState = { ...syncState, ...message.state, running: false, status: "COMPLETE", error_code: "" };
    runnerTabId = null;
    runnerWindowId = null;
    clearRunnerWatchdog();
    broadcast("MEMORIVE_SYNC_ALL_COMPLETE");
    sendResponse({ ok: true });
    return false;
  }
  if (message?.type === "MEMORIVE_SYNC_ALL_ERROR") {
    syncState = {
      ...syncState,
      ...message.state,
      running: false,
      status: "ERROR",
      error_code: message.state?.error_code || "SYNC_RUNNER_FAILED"
    };
    runnerTabId = null;
    runnerWindowId = null;
    clearRunnerWatchdog();
    broadcast("MEMORIVE_SYNC_ALL_ERROR");
    sendResponse({ ok: true });
    return false;
  }
  return false;
});

chrome.tabs.onRemoved.addListener((tabId) => {
  if (tabId !== runnerTabId || !syncState.running) return;
  runnerTabId = null;
  runnerWindowId = null;
  clearRunnerWatchdog();
  syncState = {
    ...syncState,
    running: false,
    status: "ERROR",
    error_code: "SYNC_RUNNER_CLOSED_BEFORE_COMPLETION"
  };
  broadcast("MEMORIVE_SYNC_ALL_ERROR");
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name !== RUNNER_WATCHDOG_ALARM || !syncState.running) return;
  const staleRunnerTabId = runnerTabId;
  const staleRunnerWindowId = runnerWindowId;
  runnerTabId = null;
  runnerWindowId = null;
  syncState = {
    ...syncState,
    running: false,
    status: "ERROR",
    error_code: "SYNC_RUNNER_WATCHDOG_TIMEOUT"
  };
  void (async () => {
    await writeWatchdogSyncReport();
    if (typeof staleRunnerWindowId === "number") {
      await chrome.windows.remove(staleRunnerWindowId).catch(() => {});
    } else if (typeof staleRunnerTabId === "number") {
      await chrome.tabs.remove(staleRunnerTabId).catch(() => {});
    }
    broadcast("MEMORIVE_SYNC_ALL_ERROR");
  })().catch(() => {
    if (typeof staleRunnerWindowId === "number") {
      chrome.windows.remove(staleRunnerWindowId).catch(() => {});
    } else if (typeof staleRunnerTabId === "number") {
      chrome.tabs.remove(staleRunnerTabId).catch(() => {});
    }
    broadcast("MEMORIVE_SYNC_ALL_ERROR");
  });
});

// Installation, update and browser startup are deliberately sync-idle.
// The version-deduplicated identity receipt above is metadata only; crawling
// starts only from the popup or an explicit Memorive runner request.
chrome.runtime.onInstalled.addListener(() => {
  syncState = { ...syncState, running: false, trigger: "", status: "IDLE", error_code: "" };
});

chrome.runtime.onStartup.addListener(() => {
  syncState = { ...syncState, running: false, trigger: "", status: "IDLE", error_code: "" };
});
