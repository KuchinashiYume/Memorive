"use strict";

const SITE_CONFIG = globalThis.MEMORIVE_SITE_PROFILES;
const ACTIVE_SITES = SITE_CONFIG.sites.filter(row=>row.enabled);
const PROVIDER_ORDER = ACTIVE_SITES.map(row=>row.provider_id);
const APPROVED_URL_PATTERNS = ACTIVE_SITES.flatMap(row=>row.hosts.map(host=>`https://${host}/*`));
const APPROVED_HOSTS = new Set(ACTIVE_SITES.flatMap(row=>row.hosts));
const PROVIDER_ENTRY_URLS = Object.fromEntries(ACTIVE_SITES.map(row=>[row.provider_id,row.url]));
// Render one provider at a time in an extension-owned visible window.
// Within each provider, conversations remain sequential and reuse one owned
// tab. User windows and tabs are never queried, reused, navigated or closed.
const CAPTURE_ATTEMPTS_PER_CONVERSATION = 2;
const CAPTURE_NAVIGATION_COOLDOWN_MS = {
  deepseek: 3500,
  gemini: 18000,
  kimi: 3500
};
const CAPTURE_RETRY_COOLDOWN_MS = {
  deepseek: 5000,
  gemini: 30000,
  kimi: 5000
};
const CAPTURE_HEARTBEAT_INTERVAL_MS = 60 * 1000;
const MAX_CONSECUTIVE_FAILED_CONVERSATIONS = 3;
const AUTOMATED_NAVIGATION_ALLOWED = Object.fromEntries(ACTIVE_SITES.map(row=>[row.provider_id,true]));
// Retained only so visual-check can identify and migrate prior downloaded bundles.
const LEGACY_LIBRARY_SCHEMA = "MEMORIVE_BROWSER_SESSION_LIBRARY_V3";
const TAB_LOAD_TIMEOUT_MS = 12000;
const DOWNLOAD_TIMEOUT_MS = 60000;
// PREFLIGHT and incremental runs remain tightly bounded. FULL is a deliberate,
// one-time archive build and must accommodate long Gemini conversations without
// increasing per-provider concurrency or opening additional capture windows.
const SYNC_HARD_TIMEOUT_MS_BY_MODE = Object.freeze({
  PREFLIGHT: 20 * 60 * 1000,
  INCREMENTAL: 30 * 60 * 1000,
  FULL: 90 * 60 * 1000
});
const PROVIDER_LABELS = Object.fromEntries(ACTIVE_SITES.map(row=>[row.provider_id,row.name]));
const STAGE_LABELS = {
  DISCOVERING: "发现已打开站点",
  INDEXING: "读取会话列表",
  CAPTURING: "读取完整正文",
  DOWNLOADING: "写入本地会话包"
};
const statusNode = document.getElementById("runner-status");
const progressTrackNode = document.getElementById("runner-progress-track");
const progressFillNode = document.getElementById("runner-progress-fill");
const progressMetaNode = document.getElementById("runner-progress-meta");
const runnerUrl = new URL(location.href);
const trigger = runnerUrl.searchParams.get("trigger") || "MANUAL";
const rawRequestedLimit = runnerUrl.searchParams.get("limit");
const requestedLimit = rawRequestedLimit === null || rawRequestedLimit === ""
  ? Number.NaN
  : Number(rawRequestedLimit);
const INDEX_SAFETY_CEILING = Number.isFinite(requestedLimit)
  ? Math.max(1, Math.min(50, Math.trunc(requestedLimit)))
  : 50;
const requestedSyncMode = String(runnerUrl.searchParams.get("mode") || "").toUpperCase();
const SYNC_MODE = requestedSyncMode === "INCREMENTAL"
  ? "INCREMENTAL"
  : (INDEX_SAFETY_CEILING <= 2 ? "PREFLIGHT" : "FULL");
const SYNC_HARD_TIMEOUT_MS = SYNC_HARD_TIMEOUT_MS_BY_MODE[SYNC_MODE];
const INCREMENTAL_BODY_READ_LIMIT = 10;
// A small index-only reserve lets an unreadable/empty sidebar candidate be
// skipped without consuming the user's requested number of complete chats.
// Only INDEX_SAFETY_CEILING conversations are ever body-captured successfully.
const INDEX_DISCOVERY_CEILING = SYNC_MODE === "INCREMENTAL"
  ? 50
  : Math.min(50, INDEX_SAFETY_CEILING + 5);
const requestedProviders = (runnerUrl.searchParams.get("providers") || "")
  .split(",")
  .map((value) => value.trim().toLowerCase())
  .filter(Boolean);
if (runnerUrl.searchParams.has("providers") && (!requestedProviders.length ||
    !requestedProviders.every((value,index,values)=>PROVIDER_ORDER.includes(value) && values.indexOf(value)===index))) {
  throw new Error("SYNC_PROVIDER_SCOPE_INVALID");
}
const ACTIVE_PROVIDER_ORDER = requestedProviders.length ? requestedProviders : PROVIDER_ORDER;
if (!ACTIVE_PROVIDER_ORDER.length) throw new Error('BROWSER_NO_ENABLED_SITES');
const ACTIVE_SYNC_LABEL = `${ACTIVE_PROVIDER_ORDER.length} 来源同步`;
let runnerWindowId = null;
const establishRunnerWindowOwnership = async () => {
  const current = await chrome.tabs.getCurrent();
  if (typeof current?.windowId !== "number") throw new Error("DEDICATED_RUNNER_WINDOW_UNAVAILABLE");
  runnerWindowId = current.windowId;
};
const decodeBase64UrlUtf8 = (value) => {
  const normalized = String(value || "").replace(/-/g, "+").replace(/_/g, "/");
  const padded = normalized + "=".repeat((4 - (normalized.length % 4)) % 4);
  const bytes = Uint8Array.from(atob(padded), (character) => character.charCodeAt(0));
  return new TextDecoder().decode(bytes);
};
const parseIncrementalCursor = (rawValue) => {
  if (SYNC_MODE !== "INCREMENTAL") return null;
  const token = String(rawValue || "");
  if (!token || token.length > 8192 || !/^[A-Za-z0-9_-]+$/.test(token)) {
    throw new Error("INCREMENTAL_CURSOR_TOKEN_INVALID");
  }
  let parsed;
  try {
    parsed = JSON.parse(decodeBase64UrlUtf8(token));
  } catch (_error) {
    throw new Error("INCREMENTAL_CURSOR_PAYLOAD_INVALID");
  }
  if (parsed?.schema_version !== "MEMORIVE_BROWSER_SESSION_CURSOR_V1") {
    throw new Error("INCREMENTAL_CURSOR_SCHEMA_INVALID");
  }
  const normalizedProviders = {};
  const providerEntries = Object.entries(parsed.providers || {});
  if (!providerEntries.length || providerEntries.length > PROVIDER_ORDER.length) {
    throw new Error("INCREMENTAL_CURSOR_PROVIDER_SCOPE_INVALID");
  }
  for (const [providerId, row] of providerEntries) {
    if (!PROVIDER_ORDER.includes(providerId) || !row || typeof row !== "object") {
      throw new Error("INCREMENTAL_CURSOR_PROVIDER_SCOPE_INVALID");
    }
    const known = Array.isArray(row.known_session_hashes) ? row.known_session_hashes : [];
    if (known.length > 50 || !known.every((value, index, values) => (
      /^[a-f0-9]{24}$/.test(value) && values.indexOf(value) === index
    ))) {
      throw new Error("INCREMENTAL_CURSOR_KNOWN_SET_INVALID");
    }
    const overlap = row.overlap_fingerprints || {};
    const overlapEntries = Object.entries(overlap);
    if (overlapEntries.length > 2 || !overlapEntries.every(([key, value]) => (
      known.includes(key) && /^[a-f0-9]{64}$/.test(value)
    ))) {
      throw new Error("INCREMENTAL_CURSOR_FINGERPRINT_SET_INVALID");
    }
    normalizedProviders[providerId] = {
      known: new Set(known),
      overlap: new Map(overlapEntries)
    };
  }
  return { providers: normalizedProviders };
};
const INCREMENTAL_CURSOR_TOKEN = runnerUrl.searchParams.get("cursor") || "";
const INCREMENTAL_CURSOR = parseIncrementalCursor(INCREMENTAL_CURSOR_TOKEN);
const browserFamily = MEMORIVE_BROWSER_IDENTITY.detect();

const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
const renderRunnerProgress = (percent, copy, state = "running") => {
  const normalized = Math.max(0, Math.min(100, Math.round(Number(percent) || 0)));
  if (progressTrackNode) {
    progressTrackNode.setAttribute("aria-valuenow", String(normalized));
    progressTrackNode.dataset.state = state;
  }
  if (progressFillNode) progressFillNode.style.width = `${normalized}%`;
  if (progressMetaNode) progressMetaNode.textContent = copy || `${normalized}%`;
};
const sha256Hex = async (value) => {
  const bytes = new TextEncoder().encode(String(value));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
};
const sessionIdentityHash = async (providerId, sessionId) => (
  (await sha256Hex(`${providerId}\u0000${sessionId}`)).slice(0, 24)
);
const archivedContentHash = async (conversation) => sha256Hex(
  conversation.messages.map((message) => (
    `${message.role}\u001f${message.content.length}\u001f${message.content}`
  )).join("\u001e")
);
const safeErrorCode = (error) => {
  const raw = String(error?.message || error || "UNKNOWN_ERROR").toUpperCase();
  return raw.replace(/[^A-Z0-9_]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 120) || "UNKNOWN_ERROR";
};
const responseError = (response, fallback) => {
  const error = new Error(response?.error_code || fallback);
  if (response?.diagnostic && typeof response.diagnostic === "object") {
    error.safe_diagnostic = response.diagnostic;
  }
  return error;
};
const providerForUrl = (rawUrl) => {
  const parsed = new URL(rawUrl);
  if (parsed.protocol !== 'https:' || parsed.username || parsed.password || parsed.port || !APPROVED_HOSTS.has(parsed.hostname)) throw new Error("UNSUPPORTED_PROVIDER_PAGE");
  return ACTIVE_SITES.find(row=>row.hosts.includes(parsed.hostname)).provider_id;
};
const canonicalUrl = (rawUrl, expectedProvider) => {
  const parsed = new URL(rawUrl);
  parsed.hash = "";
  if (!APPROVED_HOSTS.has(parsed.hostname) || providerForUrl(parsed.href) !== expectedProvider) {
    throw new Error("SESSION_URL_PROVIDER_MISMATCH");
  }
  return parsed.href;
};

let aggregateState = {
  running: true,
  trigger,
  sync_mode: SYNC_MODE,
  per_provider_limit: INDEX_SAFETY_CEILING,
  provider_id: "",
  current: 0,
  total: 0,
  captured: 0,
  failed: 0,
  skipped: 0,
  unchanged: 0,
  status: "DISCOVERING",
  error_code: "",
  provider_index: 0,
  provider_total: ACTIVE_PROVIDER_ORDER.length,
  providers: [],
  window_mode: "DEDICATED_VISIBLE",
  user_tab_reused: false
};
let syncAborted = false;
let completedProviderResults = [];
const liveProviderProgress = new Map();
const activeCaptureWorkers = new Map();

const recordCompletedProviderResult = (row) => {
  completedProviderResults = [
    ...completedProviderResults.filter((existing) => existing.provider_id !== row.provider_id),
    row
  ];
  const prior = liveProviderProgress.get(row.provider_id) || {};
  liveProviderProgress.set(row.provider_id, {
    ...prior,
    ...row,
    current: Number(row.captured_count) || 0,
    total: Number(row.capture_target) || 0
  });
};

const partialProviderRowsForTimeout = () => ACTIVE_PROVIDER_ORDER.map((providerId) => {
  const completed = completedProviderResults.find((row) => row.provider_id === providerId);
  if (completed) return completed;
  const progress = liveProviderProgress.get(providerId) || {};
  return {
    provider_id: providerId,
    status: "ERROR",
    indexed_count: Number(progress.indexed_count) || 0,
    candidate_count: Number(progress.candidate_count) || 0,
    capture_target: Number(progress.total) || 0,
    body_read_count: Number(progress.attempted) || Number(progress.current) || 0,
    captured_count: Number(progress.current) || 0,
    unchanged_count: Number(progress.unchanged) || 0,
    failed_count: Number(progress.failed) || 0,
    skipped_candidate_count: Number(progress.skipped) || 0,
    error_code: "SYNC_HARD_TIMEOUT",
    skipped_error_codes: [],
    safe_diagnostic: null
  };
});

const assertSyncActive = () => {
  if (syncAborted) throw new Error("SYNC_HARD_TIMEOUT");
};

const reportProgress = async (patch = {}) => {
  assertSyncActive();
  aggregateState = { ...aggregateState, ...patch, running: true };
  const provider = PROVIDER_LABELS[aggregateState.provider_id] || "三站";
  const stage = STAGE_LABELS[aggregateState.status] || aggregateState.status;
  const providerProgress = aggregateState.provider_index
    ? `站点 ${aggregateState.provider_index}/${aggregateState.provider_total}`
    : `站点 0/${aggregateState.provider_total}`;
  const itemProgress = aggregateState.total
    ? `，会话 ${aggregateState.current}/${aggregateState.total}`
    : "";
  statusNode.textContent = `同步中：${provider} · ${stage}（${providerProgress}${itemProgress}）；完整 ${aggregateState.captured}，失败 ${aggregateState.failed}。`;
  const progressPercent = aggregateState.total
    ? Math.min(96, (aggregateState.current / aggregateState.total) * 100)
    : Math.min(24, (aggregateState.provider_index / Math.max(1, aggregateState.provider_total)) * 24);
  renderRunnerProgress(progressPercent, `${providerProgress}${itemProgress || " · 正在准备"}`);
  document.title = `Memorive 同步 ${providerProgress} · ${provider} · ${stage}${itemProgress}`;
  await chrome.runtime.sendMessage({
    type: "MEMORIVE_SYNC_ALL_PROGRESS",
    state: { ...aggregateState }
  }).catch(() => {});
};

const reportProviderProgress = async (providerId, providerIndex, patch = {}) => {
  const prior = liveProviderProgress.get(providerId) || {
    provider_id: providerId,
    status: "WAITING",
    current: 0,
    total: 0
  };
  liveProviderProgress.set(providerId, { ...prior, ...patch, provider_id: providerId });
  const rows = ACTIVE_PROVIDER_ORDER.map((id) => liveProviderProgress.get(id) || {
    provider_id: id,
    status: "WAITING",
    current: 0,
    total: 0
  });
  const current = rows.reduce((total, row) => total + (Number(row.current) || 0), 0);
  const total = rows.reduce((sum, row) => sum + (Number(row.total) || 0), 0);
  await reportProgress({
    provider_id: providerId,
    provider_index: providerIndex,
    status: patch.status || prior.status,
    current,
    total,
    providers: rows
  });
  const providerSummary = rows.map((row) => (
    `${PROVIDER_LABELS[row.provider_id]} ${row.current}/${row.total || "?"}`
  )).join(" · ");
  statusNode.textContent = `${ACTIVE_SYNC_LABEL}：${providerSummary}；完整 ${aggregateState.captured}，失败 ${aggregateState.failed}。`;
  document.title = `Memorive ${ACTIVE_SYNC_LABEL} · ${current}/${total || "?"}`;
};

const startCaptureHeartbeat = (providerId, providerIndex, progress) => setInterval(() => {
  if (syncAborted) return;
  void reportProviderProgress(providerId, providerIndex, {
    ...progress(),
    heartbeat: true
  }).catch(() => {});
}, CAPTURE_HEARTBEAT_INTERVAL_MS);

const waitForTabComplete = (tabId) => new Promise((resolve, reject) => {
  let finished = false;
  const cleanup = () => {
    chrome.tabs.onUpdated.removeListener(listener);
    clearTimeout(timer);
  };
  const settle = (callback, value) => {
    if (finished) return;
    finished = true;
    cleanup();
    callback(value);
  };
  const listener = (updatedId, changeInfo) => {
    if (updatedId === tabId && changeInfo.status === "complete") settle(resolve);
  };
  const timer = setTimeout(
    () => settle(reject, new Error("BACKGROUND_TAB_LOAD_TIMEOUT")),
    TAB_LOAD_TIMEOUT_MS
  );
  chrome.tabs.onUpdated.addListener(listener);
  chrome.tabs.get(tabId).then((tab) => {
    if (tab.status === "complete") settle(resolve);
  }).catch((error) => settle(reject, error));
});

const prepareTabForVisibleUiRead = async (tabId) => {
  const owned = await chrome.tabs.get(tabId);
  if (owned.windowId !== runnerWindowId) throw new Error("CAPTURE_TAB_NOT_OWNED");
  await chrome.windows.update(runnerWindowId, {state:"normal", focused:true});
  await chrome.tabs.update(tabId, {active:true});
  try {
    await waitForTabComplete(tabId);
  } catch (error) {
    if (safeErrorCode(error) !== "BACKGROUND_TAB_LOAD_TIMEOUT") throw error;
  }
  for (let attempt=0; attempt<12; attempt+=1) {
    const state = await chrome.scripting.executeScript({target:{tabId},func:()=>document.visibilityState});
    if (state?.[0]?.result === 'visible') break;
    if (attempt === 11) throw new Error('CAPTURE_PAGE_NOT_VISIBLE');
    await delay(250);
  }
  await delay(900);
  await ensureContentScript(tabId).catch(() => {});
};

const ensureContentScript = async (tabId) => {
  const tab = await chrome.tabs.get(tabId);
  globalThis.MEMORIVE_OFFICIAL_WEB.requireAllowedUrl(tab.url);
  await chrome.scripting.executeScript({
    target: { tabId },
    files: ["site_profiles.js", "recording.js", "content.js"]
  });
};

const sendToTabWithRetry = async (tabId, message) => {
  let lastError = null;
  let injected = false;
  for (let attempt = 0; attempt < 16; attempt += 1) {
    try {
      const tab = await chrome.tabs.get(tabId);
      globalThis.MEMORIVE_OFFICIAL_WEB.requireAllowedUrl(tab.url);
      return await chrome.tabs.sendMessage(tabId, message);
    } catch (error) {
      if (safeErrorCode(error) === "BROWSER_SITE_NOT_VERIFIED_OFFICIAL") throw error;
      lastError = error;
      if (!injected) {
        injected = true;
        await ensureContentScript(tabId).catch(() => {});
      }
      await delay(450);
    }
  }
  throw lastError || new Error("CONTENT_SCRIPT_UNAVAILABLE");
};

const newCaptureWorker = (providerId) => {
  const worker = {
    providerId,
    mode: "dedicated-visible",
    tabId: null,
    windowId: runnerWindowId
  };
  activeCaptureWorkers.set(providerId, worker);
  return worker;
};

const closeCaptureWorker = async (worker) => {
  if (typeof worker.tabId === "number") {
    await chrome.tabs.remove(worker.tabId).catch(() => {});
  }
  worker.tabId = null;
  worker.windowId = runnerWindowId;
  if (activeCaptureWorkers.get(worker.providerId) === worker) {
    activeCaptureWorkers.delete(worker.providerId);
  }
};

const closeAllCaptureWorkers = async () => {
  await Promise.all([...activeCaptureWorkers.values()].map((worker) => closeCaptureWorker(worker)));
};

const navigateCaptureWorker = async (worker, rawUrl) => {
  const url = canonicalUrl(rawUrl, worker.providerId);
  if (typeof worker.tabId === "number") {
    await chrome.tabs.update(worker.tabId, {
      url,
      active: false
    });
    await prepareTabForVisibleUiRead(worker.tabId);
    return worker.tabId;
  }
  if (typeof runnerWindowId !== "number") throw new Error("DEDICATED_RUNNER_WINDOW_UNAVAILABLE");
  const tab = await chrome.tabs.create({
    windowId: runnerWindowId,
    url,
    active: false
  });
  if (typeof tab?.id !== "number") throw new Error("DEDICATED_CAPTURE_TAB_CREATE_FAILED");
  worker.tabId = tab.id;
  await prepareTabForVisibleUiRead(worker.tabId);
  return worker.tabId;
};

const openProviderEntryTab = async (providerId) => {
  if (typeof runnerWindowId !== "number") throw new Error("DEDICATED_RUNNER_WINDOW_UNAVAILABLE");
  const tab = await chrome.tabs.create({
    windowId: runnerWindowId,
    url: PROVIDER_ENTRY_URLS[providerId],
    active: false
  });
  if (typeof tab.id !== "number") throw new Error("PROVIDER_ENTRY_TAB_CREATE_FAILED");
  await prepareTabForVisibleUiRead(tab.id);
  const loadedTab = await chrome.tabs.get(tab.id);
  if (!loadedTab?.url || providerForUrl(loadedTab.url) !== providerId) {
    throw new Error("PROVIDER_ENTRY_TAB_LOAD_FAILED");
  }
  return { ...loadedTab, memorive_owned: true };
};

const discoverProviderTabs = async () => {
  const selected = new Map();
  for (const providerId of ACTIVE_PROVIDER_ORDER) {
    try {
      selected.set(providerId, [await openProviderEntryTab(providerId)]);
    } catch (_error) {
      // The provider will remain NOT_OPEN/ERROR in the zero-privacy report.
      // Login and account changes are always left to the user.
      selected.set(providerId, []);
    }
  }
  return selected;
};

const indexRecentConversations = async (providerId, sourceTabs) => {
  let lastError = new Error("PROVIDER_TAB_UNAVAILABLE");
  const indexFromTab = async (sourceTab) => {
    if (typeof sourceTab?.id !== "number" || !sourceTab.url) {
      throw new Error("PROVIDER_TAB_UNAVAILABLE");
    }
    canonicalUrl(sourceTab.url, providerId);
    await prepareTabForVisibleUiRead(sourceTab.id);
    await ensureContentScript(sourceTab.id).catch(() => {});
    const response = await sendToTabWithRetry(sourceTab.id, {
      type: "MEMORIVE_INDEX_RECENT_14_DAYS_OR_30_CONVERSATIONS",
      safety_ceiling: INDEX_DISCOVERY_CEILING
    });
    if (!response?.ok) throw responseError(response, "SESSION_INDEX_FAILED");
    if (response.provider_id !== providerId || !Array.isArray(response.sessions) || !response.sessions.length) {
      throw new Error("SESSION_INDEX_LINKS_NOT_FOUND");
    }
    return response.sessions;
  };
  // Only extension-owned tabs in the dedicated window can reach this path.
  let freshTab = (sourceTabs || []).find((tab) => tab?.memorive_owned) || null;
  try {
    if (!freshTab) freshTab = await openProviderEntryTab(providerId);
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        return await indexFromTab(freshTab);
      } catch (error) {
        lastError = error;
        if (safeErrorCode(error) === "PROVIDER_HUMAN_VERIFICATION_REQUIRED") break;
        if (attempt >= 2) break;
        await chrome.tabs.reload(freshTab.id);
        await prepareTabForVisibleUiRead(freshTab.id);
      }
    }
  } catch (error) {
    lastError = error;
  } finally {
    if (freshTab?.memorive_owned && typeof freshTab.id === "number") {
      await chrome.tabs.remove(freshTab.id).catch(() => {});
    }
  }
  throw lastError;
};

const closeOwnedSourceTabs = async (sourceTabs) => {
  const tabIds = (sourceTabs || [])
    .filter((tab) => tab?.memorive_owned && typeof tab.id === "number")
    .map((tab) => tab.id);
  if (tabIds.length) await chrome.tabs.remove(tabIds).catch(() => {});
};

const contentBridgeVersion = async (tabId) => {
  const rows = await chrome.scripting.executeScript({
    target: { tabId },
    func: () => globalThis.__MEMORIVE_SESSION_LIBRARY_BRIDGE_VERSION__ || ""
  }).catch(() => []);
  return String(rows?.[0]?.result || "");
};

const currentBridgeSessionTab = async (providerId, rawUrl) => {
  const expected = canonicalUrl(rawUrl, providerId);
  if (typeof runnerWindowId !== "number") return null;
  const tabs = await chrome.tabs.query({
    windowId: runnerWindowId,
    url: APPROVED_URL_PATTERNS
  });
  for (const tab of tabs) {
    if (typeof tab.id !== "number" || !tab.url) continue;
    try {
      if (
        canonicalUrl(tab.url, providerId) === expected &&
        await contentBridgeVersion(tab.id) === "3.7.0:" + SITE_CONFIG.sha256
      ) return tab;
    } catch (_error) {
      // Keep looking; stale or mismatched tabs are never reused.
    }
  }
  return null;
};

const failedConversation = (descriptor, error) => ({
  provider_session_id: descriptor.provider_session_id,
  title: descriptor.title,
  error_code: safeErrorCode(error)
});

const archiveConversation = (conversation) => {
  const messages = Array.isArray(conversation.messages) ? conversation.messages : [];
  if (
    conversation.capture_status !== "COMPLETE" ||
    conversation.source_truncated !== false ||
    conversation.top_boundary_reached !== true ||
    conversation.bottom_boundary_reached !== true ||
    !["BROWSER_COMPANION_BACKGROUND_TAB_V3", "BROWSER_COMPANION_BACKGROUND_TAB_V4"].includes(
      conversation.capture_method
    ) ||
    !messages.some((message) => message.role === "user") ||
    !messages.some((message) => message.role === "assistant")
  ) {
    throw new Error("CONVERSATION_COMPLETENESS_PROOF_MISSING");
  }
  const occurredAt = conversation.conversation_at || "UNKNOWN";
  return {
    provider_id: conversation.provider_id,
    provider_session_id: conversation.provider_session_id,
    title: conversation.title,
    created_at: occurredAt,
    updated_at: occurredAt,
    recency_bucket: conversation.recency_bucket || "UNKNOWN",
    selection_basis: conversation.selection_basis,
    source_recency_rank: conversation.source_recency_rank,
    source_url: conversation.source_url,
    capture_method: conversation.capture_method,
    capture_status: conversation.capture_status,
    source_truncated: conversation.source_truncated,
    top_boundary_reached: conversation.top_boundary_reached,
    bottom_boundary_reached: conversation.bottom_boundary_reached,
    messages: messages.map((message) => ({
      message_id: message.message_id,
      role: message.role,
      content_kind: "text",
      content: message.text,
      occurred_at: occurredAt
    }))
  };
};

const finalizeArchivedConversation = async (conversation) => {
  const archived = archiveConversation(conversation);
  archived.session_id_sha256 = await sessionIdentityHash(
    archived.provider_id,
    archived.provider_session_id
  );
  archived.content_sha256 = await archivedContentHash(archived);
  return archived;
};

const capturePlanFor = async (providerId, indexedSessions) => {
  if (SYNC_MODE !== "INCREMENTAL") {
    return {
      sessions: indexedSessions,
      captureTarget: Math.min(INDEX_SAFETY_CEILING, indexedSessions.length)
    };
  }
  const cursor = INCREMENTAL_CURSOR?.providers?.[providerId];
  if (!cursor) throw new Error("INCREMENTAL_CURSOR_PROVIDER_MISSING");
  const decorated = await Promise.all(indexedSessions.map(async (descriptor) => {
    const identityHash = await sessionIdentityHash(providerId, descriptor.provider_session_id);
    return {
      ...descriptor,
      incremental_session_hash: identityHash,
      incremental_known_content_hash: cursor.overlap.get(identityHash) || ""
    };
  }));
  const unknown = decorated.filter((descriptor) => !cursor.known.has(descriptor.incremental_session_hash));
  const overlap = decorated.filter((descriptor) => (
    cursor.known.has(descriptor.incremental_session_hash) &&
    descriptor.incremental_known_content_hash
  )).slice(0, 2);
  const selected = [];
  const selectedHashes = new Set();
  for (const descriptor of [...unknown, ...overlap]) {
    if (selected.length >= INCREMENTAL_BODY_READ_LIMIT) break;
    if (selectedHashes.has(descriptor.incremental_session_hash)) continue;
    selectedHashes.add(descriptor.incremental_session_hash);
    selected.push(descriptor);
  }
  return { sessions: selected, captureTarget: selected.length };
};

const captureFromTab = async (providerId, descriptor, tabId) => {
  await ensureContentScript(tabId).catch(() => {});
    const response = await sendToTabWithRetry(tabId, {
      type: "MEMORIVE_CAPTURE_INDEXED_CONVERSATION",
      descriptor: { ...descriptor, source_url: canonicalUrl(descriptor.source_url, providerId) }
    });
    if (!response?.ok || !response.conversation) {
      throw responseError(response, "CONVERSATION_CAPTURE_FAILED");
    }
    if (
      response.conversation.provider_id !== providerId ||
      response.conversation.provider_session_id !== descriptor.provider_session_id
    ) {
      throw new Error("CAPTURED_SESSION_IDENTITY_MISMATCH");
    }
    return response.conversation;
};

const captureIndexedConversation = async (providerId, descriptor, captureWorker) => {
  const currentTab = await currentBridgeSessionTab(providerId, descriptor.source_url);
  if (typeof currentTab?.id === "number") {
    return captureFromTab(providerId, descriptor, currentTab.id);
  }
  if (!AUTOMATED_NAVIGATION_ALLOWED[providerId]) {
    throw new Error("FULL_BODY_REQUIRES_VISIBLE_OPEN_TAB_OR_OFFICIAL_EXPORT");
  }
  let captureUrl = descriptor.source_url;
  if (providerId === "kimi") {
    const parsed = new URL(captureUrl);
    parsed.searchParams.set("chat_enter_method", "history");
    captureUrl = parsed.href;
  }
  let lastError = null;
  for (let attempt = 0; attempt < CAPTURE_ATTEMPTS_PER_CONVERSATION; attempt += 1) {
    try {
      const tabId = await navigateCaptureWorker(captureWorker, captureUrl);
      return await captureFromTab(providerId, descriptor, tabId);
    } catch (error) {
      lastError = error;
      if (safeErrorCode(error) === "PROVIDER_HUMAN_VERIFICATION_REQUIRED") throw error;
      // A failed navigation may leave a broken renderer behind. Close it
      // before the bounded retry; there is still at most one worker window
      // for this provider.
      await closeCaptureWorker(captureWorker);
      if (attempt + 1 < CAPTURE_ATTEMPTS_PER_CONVERSATION) {
        await delay(CAPTURE_RETRY_COOLDOWN_MS[providerId] ?? 5000);
      }
    }
  }
  throw lastError || new Error("CONVERSATION_CAPTURE_FAILED");
};

const waitForDownload = (downloadId) => new Promise((resolve, reject) => {
  let finished = false;
  const cleanup = () => {
    chrome.downloads.onChanged.removeListener(listener);
    clearTimeout(timer);
  };
  const settle = (callback, value) => {
    if (finished) return;
    finished = true;
    cleanup();
    callback(value);
  };
  const listener = (delta) => {
    if (delta.id !== downloadId) return;
    if (delta.state?.current === "complete") settle(resolve);
    if (delta.state?.current === "interrupted") {
      settle(reject, new Error(`DOWNLOAD_INTERRUPTED_${delta.error?.current || "UNKNOWN"}`));
    }
  };
  const timer = setTimeout(
    () => settle(reject, new Error("DOWNLOAD_COMPLETION_TIMEOUT")),
    DOWNLOAD_TIMEOUT_MS
  );
  chrome.downloads.onChanged.addListener(listener);
});

const downloadJson = async (filename, payload, { allowAfterAbort = false } = {}) => {
  if (!allowAfterAbort) assertSyncActive();
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  try {
    const downloadId = await chrome.downloads.download({
      url,
      filename: `Memorive会话桥接/${filename}`,
      conflictAction: "uniquify"
    });
    await waitForDownload(downloadId);
    return downloadId;
  } finally {
    URL.revokeObjectURL(url);
  }
};

const syncProvider = async (providerId, sourceTabs, providerIndex) => {
  await reportProviderProgress(providerId, providerIndex, {
    status: "INDEXING",
    current: 0,
    total: 0
  });
  const indexedSessions = await indexRecentConversations(providerId, sourceTabs);
  const capturePlan = await capturePlanFor(providerId, indexedSessions);
  const sessions = capturePlan.sessions;
  const captureTarget = capturePlan.captureTarget;
  await closeOwnedSourceTabs(sourceTabs);
  await reportProviderProgress(providerId, providerIndex, {
    status: "CAPTURING",
    current: 0,
    total: captureTarget
  });
  const conversations = [];
  const skippedCandidates = [];
  const failures = [];
  let captured = 0;
  let failed = 0;
  let attempted = 0;
  let unchanged = 0;
  const captureErrorCodes = new Set();
  let safeDiagnostic = null;
  let verificationRequired = false;
  let consecutiveFailures = 0;
  let failureBurstDetected = false;
  const captureWorker = newCaptureWorker(providerId);
  try {
    for (
      let index = 0;
      index < sessions.length && (SYNC_MODE === "INCREMENTAL" || captured < captureTarget);
      index += 1
    ) {
      assertSyncActive();
      if (index > 0 && AUTOMATED_NAVIGATION_ALLOWED[providerId]) {
        await delay(CAPTURE_NAVIGATION_COOLDOWN_MS[providerId] ?? 3500);
      }
      const descriptor = sessions[index];
      const captureHeartbeatHandle = startCaptureHeartbeat(providerId, providerIndex, () => ({
        status: "CAPTURING",
        current: SYNC_MODE === "INCREMENTAL" ? attempted : Math.min(captured, captureTarget),
        total: captureTarget,
        attempted,
        skipped: skippedCandidates.length,
        unchanged
      }));
      try {
        const archived = await finalizeArchivedConversation(
          await captureIndexedConversation(providerId, descriptor, captureWorker)
        );
        if (
          SYNC_MODE === "INCREMENTAL" &&
          descriptor.incremental_known_content_hash &&
          archived.content_sha256 === descriptor.incremental_known_content_hash
        ) {
          unchanged += 1;
          aggregateState.unchanged += 1;
        } else {
          conversations.push(archived);
          captured += 1;
          aggregateState.captured += 1;
        }
        consecutiveFailures = 0;
      } catch (error) {
        const skippedCandidate = failedConversation(descriptor, error);
        skippedCandidates.push(skippedCandidate);
        const errorCode = safeErrorCode(error);
        captureErrorCodes.add(errorCode);
        if (!safeDiagnostic && error?.safe_diagnostic) safeDiagnostic = error.safe_diagnostic;
        aggregateState.skipped += 1;
        consecutiveFailures += 1;
        if (errorCode === "PROVIDER_HUMAN_VERIFICATION_REQUIRED") {
          verificationRequired = true;
        } else if (consecutiveFailures >= MAX_CONSECUTIVE_FAILED_CONVERSATIONS) {
          failureBurstDetected = true;
          captureErrorCodes.add("PROVIDER_CAPTURE_ABORTED_AFTER_FAILURE_BURST");
        }
      } finally {
        clearInterval(captureHeartbeatHandle);
      }
      attempted += 1;
      await reportProviderProgress(providerId, providerIndex, {
        status: "CAPTURING",
        current: SYNC_MODE === "INCREMENTAL" ? attempted : Math.min(captured, captureTarget),
        total: captureTarget,
        attempted,
        skipped: skippedCandidates.length,
        unchanged
      });
      if (verificationRequired || failureBurstDetected) {
        break;
      }
    }
  } finally {
    await closeCaptureWorker(captureWorker);
  }
  if (SYNC_MODE === "INCREMENTAL" && skippedCandidates.length) {
    failures.push(...skippedCandidates);
    failed = failures.length;
    aggregateState.failed += failed;
  } else if (SYNC_MODE !== "INCREMENTAL" && captured < captureTarget) {
    failures.push(...skippedCandidates);
    failed = failures.length || (captureTarget - captured);
    aggregateState.failed += failed;
  }
  const bundle = {
    schema_version: "MEMORIVE_BROWSER_SESSION_LIBRARY_V4_DRAFT",
    site_profile_sha256: SITE_CONFIG.sha256,
    source: "MEMORIVE_BROWSER_COMPANION_VISIBLE_UI_V4_DRAFT",
    generated_at: new Date().toISOString(),
    sync_mode: SYNC_MODE,
    sync_scope: "RECENT_14_DAYS_OR_30_CONVERSATIONS",
    provider_id: providerId,
    enumerated_count: conversations.length + failures.length,
    candidate_count: indexedSessions.length,
    capture_target: captureTarget,
    body_read_count: attempted,
    captured_count: captured,
    unchanged_count: unchanged,
    failed_count: failed,
    skipped_candidate_count: skippedCandidates.length,
    conversations,
    failures,
    skipped_candidates: SYNC_MODE !== "INCREMENTAL" && captured >= captureTarget
      ? skippedCandidates
      : []
  };
  await reportProviderProgress(providerId, providerIndex, {
    status: "DOWNLOADING",
    current: captured,
    total: captureTarget
  });
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  await downloadJson(`memorive-session-library-${providerId}-${stamp}.json`, bundle);
  return {
    provider_id: providerId,
    status: SYNC_MODE === "INCREMENTAL"
      ? (failed ? (captured || unchanged ? "COMPLETE_WITH_ERRORS" : "ERROR") : "COMPLETE")
      : (captured >= captureTarget ? "COMPLETE" : (captured ? "COMPLETE_WITH_ERRORS" : "ERROR")),
    indexed_count: SYNC_MODE === "INCREMENTAL" ? indexedSessions.length : captureTarget,
    candidate_count: indexedSessions.length,
    capture_target: captureTarget,
    body_read_count: attempted,
    captured_count: captured,
    unchanged_count: unchanged,
    failed_count: failed,
    skipped_candidate_count: skippedCandidates.length,
    error_code: failed ? [...captureErrorCodes].join("+").slice(0, 120) : "",
    skipped_error_codes: captured >= captureTarget ? [...captureErrorCodes] : [],
    safe_diagnostic: failed ? safeDiagnostic : null
  };
};

const writeSyncReport = async (
  providers,
  status,
  { allowAfterAbort = false, errorCode = "" } = {}
) => {
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  await downloadJson(
    `memorive-session-library-sync-report-${stamp}.json`,
    {
      schema_version: "MEMORIVE_BROWSER_SESSION_SYNC_REPORT_V2",
      site_profile_sha256: SITE_CONFIG.sha256,
      source: "MEMORIVE_BROWSER_COMPANION_RUNNER_V4_DRAFT",
      extension_id: chrome.runtime.id,
      extension_version: chrome.runtime.getManifest().version,
      per_provider_limit: INDEX_SAFETY_CEILING,
      sync_mode: SYNC_MODE,
      browser_family: await browserFamily,
      generated_at: new Date().toISOString(),
      trigger,
      status,
      error_code: errorCode,
      hard_timeout_ms: SYNC_HARD_TIMEOUT_MS,
      active_provider_count: ACTIVE_PROVIDER_ORDER.length,
      provider_parallelism: false,
      maximum_capture_windows: 1,
      per_provider_capture_mode: "DEDICATED_VISIBLE_WINDOW_OWNED_TABS",
      window_mode: "DEDICATED_VISIBLE",
      user_tab_reused: false,
      providers: providers.map((row) => ({
        provider_id: row.provider_id,
        status: row.status,
        indexed_count: Number(row.indexed_count) || 0,
        candidate_count: Number(row.candidate_count) || 0,
        capture_target: Number(row.capture_target) || 0,
        body_read_count: Number(row.body_read_count) || 0,
        captured_count: Number(row.captured_count) || 0,
        unchanged_count: Number(row.unchanged_count) || 0,
        failed_count: Number(row.failed_count) || 0,
        skipped_candidate_count: Number(row.skipped_candidate_count) || 0,
        error_code: row.error_code || "",
        skipped_error_codes: Array.isArray(row.skipped_error_codes) ? row.skipped_error_codes : [],
        safe_diagnostic: row.safe_diagnostic || null
      })),
      cookie_value_reads: 0,
      browser_storage_value_reads: 0,
      hidden_provider_api_calls: 0,
      remote_session_mutations: 0
    },
    { allowAfterAbort }
  );
};

const closeRunnerSoon = () => setTimeout(async () => {
  const current = await chrome.tabs.getCurrent().catch(() => null);
  if (current?.id) await chrome.tabs.remove(current.id).catch(() => {});
}, 1500);

const executeSync = async () => {
  await establishRunnerWindowOwnership();
  aggregateState = { ...aggregateState, dedicated_window_id: runnerWindowId };
  await reportProgress({ status: "DISCOVERING", provider_index: 0 });
  const providerTabs = await discoverProviderTabs();
  const runProvider = async (providerId, providerOffset) => {
    assertSyncActive();
    const providerIndex = providerOffset + 1;
    const sourceTabs = providerTabs.get(providerId);
    if (!sourceTabs?.length) {
      await reportProviderProgress(providerId, providerIndex, {
        status: "INDEXING",
        current: 0,
        total: 0
      });
      const result = {
        provider_id: providerId,
        status: "NOT_OPEN",
        indexed_count: 0,
        captured_count: 0,
        failed_count: 0,
        error_code: "PROVIDER_TAB_NOT_OPEN"
      };
      recordCompletedProviderResult(result);
      return result;
    }
    try {
      const result = await syncProvider(providerId, sourceTabs, providerIndex);
      recordCompletedProviderResult(result);
      return result;
    } catch (error) {
      const result = {
        provider_id: providerId,
        status: "ERROR",
        indexed_count: 0,
        captured_count: 0,
        failed_count: 0,
        error_code: safeErrorCode(error),
        safe_diagnostic: error?.safe_diagnostic || null
      };
      recordCompletedProviderResult(result);
      return result;
    } finally {
      await closeOwnedSourceTabs(sourceTabs);
    }
  };
  const results = [];
  for (const [offset, providerId] of ACTIVE_PROVIDER_ORDER.entries()) {
    results.push(await runProvider(providerId, offset));
  }
  completedProviderResults = results;
  aggregateState = { ...aggregateState, providers: [...results] };
  const completed = results.filter((row) => row.status.startsWith("COMPLETE")).length;
  const finalStatus = completed ? "COMPLETE" : "ERROR";
  await writeSyncReport(results, finalStatus);
  return { results, completed, finalStatus };
};

let hardTimeoutHandle = null;
const hardTimeout = new Promise((_, reject) => {
  hardTimeoutHandle = setTimeout(() => {
    syncAborted = true;
    reject(new Error("SYNC_HARD_TIMEOUT"));
  }, SYNC_HARD_TIMEOUT_MS);
});

Promise.race([executeSync(), hardTimeout]).then(async ({ results, completed, finalStatus }) => {
  if (hardTimeoutHandle) clearTimeout(hardTimeoutHandle);
  aggregateState = {
    ...aggregateState,
    running: false,
    provider_id: "",
    status: finalStatus,
    error_code: completed ? "" : "NO_PROVIDER_SYNC_COMPLETED",
    providers: results
  };
  statusNode.textContent = completed
    ? `同步完成：${completed} 个站点，新增或更新 ${aggregateState.captured}，未变化 ${aggregateState.unchanged}，跳过候选 ${aggregateState.skipped}，失败 ${aggregateState.failed}。`
    : "同步未完成；诊断报告已在本机生成，Memorive 刷新时会自动接管。";
  renderRunnerProgress(
    completed ? 100 : Math.min(99, aggregateState.total ? aggregateState.current / aggregateState.total * 100 : 0),
    completed ? "同步完成 · 会话包已写入本机" : "同步未完成 · 已生成诊断报告",
    completed ? "complete" : "error"
  );
  await chrome.runtime.sendMessage({
    type: completed ? "MEMORIVE_SYNC_ALL_COMPLETE" : "MEMORIVE_SYNC_ALL_ERROR",
    state: { ...aggregateState }
  }).catch(() => {});
  closeRunnerSoon();
}).catch(async (error) => {
  if (hardTimeoutHandle) clearTimeout(hardTimeoutHandle);
  syncAborted = true;
  aggregateState = {
    ...aggregateState,
    running: false,
    status: "ERROR",
    error_code: safeErrorCode(error)
  };
  await closeAllCaptureWorkers();
  const partialRows = partialProviderRowsForTimeout();
  aggregateState.providers = partialRows;
  const timeoutMinutes = Math.round(SYNC_HARD_TIMEOUT_MS / 60000);
  statusNode.textContent = aggregateState.error_code === "SYNC_HARD_TIMEOUT"
    ? `同步已在 ${timeoutMinutes} 分钟安全上限后停止；正在写入终止报告。`
    : `同步失败：${aggregateState.error_code}`;
  renderRunnerProgress(
    Math.min(99, aggregateState.total ? aggregateState.current / aggregateState.total * 100 : 0),
    aggregateState.error_code === "SYNC_HARD_TIMEOUT" ? "同步超时 · 已保留成功结果" : "同步失败 · 请返回 Memorive 查看诊断",
    "error"
  );
  await chrome.runtime.sendMessage({
    type: "MEMORIVE_SYNC_ALL_ERROR",
    state: { ...aggregateState }
  }).catch(() => {});
  let terminalReportWritten = false;
  try {
    await writeSyncReport(partialRows, "ERROR", {
      allowAfterAbort: true,
      errorCode: "SYNC_HARD_TIMEOUT"
    });
    terminalReportWritten = true;
  } catch (reportError) {
    const reportErrorCode = safeErrorCode(reportError);
    statusNode.textContent = `同步已停止，但终止报告写入失败：${reportErrorCode}。此页面将保留。`;
    renderRunnerProgress(
      Math.min(99, aggregateState.total ? aggregateState.current / aggregateState.total * 100 : 0),
      "终止报告写入失败 · 页面已保留",
      "error"
    );
  }
  if (terminalReportWritten) closeRunnerSoon();
});
