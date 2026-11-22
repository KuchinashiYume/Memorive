"use strict";

importScripts('site_profiles.js', 'browser_identity.js');
const SITE_CONFIG = globalThis.MEMORIVE_SITE_PROFILES;
const ACTIVE_SITES = SITE_CONFIG.sites.filter(row => row.enabled);
const ACTIVE_IDS = ACTIVE_SITES.map(row => row.provider_id);

const IDENTITY_VERSION_MARKER_PREFIX = "memorive_identity_receipt_";
const IDENTITY_VERSION_MARKER_MINUTES = 525600;

const safeErrorCode = (error) => {
  const raw = String(error?.message || error || "UNKNOWN_ERROR").toUpperCase();
  return raw.replace(/[^A-Z0-9_]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 120) || "UNKNOWN_ERROR";
};

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
  if (readOnly) {
    const users = conversation.messages.filter((row) => row.role === "user").length;
    return {ok:true, message_count:conversation.messages.length, user_count:users,
            assistant_count:conversation.messages.length - users, read_method:conversation.read_method || "",
            capture_status:conversation.capture_status};
  }
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
      content_format: "MARKDOWN_V1",
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

// Extension-owned alarm grants survive MV3 suspension without storage permissions.
const RECORD_PREFIX="memorive-record:";
const completedRecordings=new Map();
const recordGrants=async()=> (await chrome.alarms.getAll()).filter(a=>a.name.startsWith(RECORD_PREFIX))
  .map(a=>{try{return {...JSON.parse(decodeURIComponent(a.name.slice(RECORD_PREFIX.length))),name:a.name};}catch{return null;}}).filter(Boolean);
// The popup asks for the recording status every second. The manifest already
// loads the page scripts on the enabled sites, so inject them only when the
// page has no listener yet (other official sites, or a tab opened before the
// extension was loaded).
const recordingStatus=async(tabId,sourceUrl)=>{
  const ask=()=>chrome.tabs.sendMessage(tabId,{type:"MEMORIVE_RECORDING_STATUS"});
  const reply=await ask().catch(()=>null);
  if (reply?.ok) return reply;
  await chrome.scripting.executeScript({target:{tabId},files:["site_profiles.js","recording.js","content.js"]});
  if (sanitizedHttpsPageUrl((await chrome.tabs.get(tabId)).url)!==sourceUrl) throw Error("CURRENT_PAGE_ORIGIN_OR_SESSION_CHANGED");
  return ask();
};
const recordingCommand=async(message,sender)=>{
  if (!popupSenderApproved(sender)) throw Error("RECORDING_SENDER_NOT_APPROVED");
  const [tab]=await chrome.tabs.query({active:true,currentWindow:true});
  const sourceUrl=sanitizedHttpsPageUrl(tab?.url);
  const state=await recordingStatus(tab.id,sourceUrl);
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
    // The grant alarm repeats so that finishRecording can still find it after
    // the deadline. The tab saves through MEMORIVE_RECORDING_FINISH before it
    // answers this stop request, so the grant is no longer needed afterwards.
    // Clearing it here also ends recordings that stopped with an error and
    // never reached finishRecording; before, those alarms fired every minute.
    let grant;try {grant=JSON.parse(decodeURIComponent(alarm.name.slice(RECORD_PREFIX.length)));} catch{grant=null;}
    if (grant) await chrome.tabs.sendMessage(grant.tabId,{type:"MEMORIVE_RECORDING_STOP",token:grant.token,reason:"TIME_LIMIT"}).catch(()=>{});
    await chrome.alarms.clear(alarm.name);
  })();
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (["MEMORIVE_RECORD_ACTIVE_START","MEMORIVE_RECORD_ACTIVE_STOP","MEMORIVE_RECORD_ACTIVE_STATUS","MEMORIVE_RECORDING_FINISH"].includes(message?.type)) {
    const action=message.type==="MEMORIVE_RECORDING_FINISH" ? finishRecording(message,sender) : recordingCommand(message,sender);
    action.then(sendResponse).catch(error=>sendResponse({ok:false,error_code:safeErrorCode(error)}));
    return true;
  }
  if (["MEMORIVE_CAPTURE_ACTIVE_TAB", "MEMORIVE_TEST_ACTIVE_TAB"].includes(message?.type)) {
    captureActiveTab(sender, message.type === "MEMORIVE_TEST_ACTIVE_TAB").then(sendResponse).catch((error) => {
      sendResponse({ ok: false, error_code: safeErrorCode(error) });
    });
    return true;
  }
  return false;
});
