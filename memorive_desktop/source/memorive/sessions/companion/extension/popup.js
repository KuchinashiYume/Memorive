"use strict";

// The popup: record the conversation on the current tab, test-read it, or sync the configured sites.
// Its words come from _locales through i18n.js; the Aa button picks the language.
const { t, ready, choose, languages, current } = globalThis.MEMORIVE_I18N;
const statusNode = document.getElementById("status");
const syncButton = document.getElementById("sync-library");
const testButton = document.getElementById("test-current-page");
const recordButton = document.getElementById("record-page");
const recordStatus = document.getElementById("record-status");
const supportHint = document.getElementById("support-hint");
const languageButton = document.getElementById("language-button");
const languageMenu = document.getElementById("language-menu");
let testBusy = false, syncRunning = false, recordBusy = false, recordingState = { status: "IDLE" };
let lastSyncState = null;

// A line shown by key, so that a language change can show it again in the new language.
const say = (node, key, ...values) => {
  node.memoriveWords = key ? [key, values] : null;
  node.textContent = key ? t(key, ...values) : "";
};
const sayAgain = (node) => { if (node.memoriveWords) say(node, node.memoriveWords[0], ...node.memoriveWords[1]); };

const refreshCaptureAvailability = async () => {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    globalThis.MEMORIVE_OFFICIAL_WEB.requireCaptureUrl(tab?.url);
    supportHint.hidden = true;
    testButton.disabled = testBusy || syncRunning || ["RECORDING", "SAVING"].includes(recordingState.status);
  } catch (_error) {
    supportHint.hidden = false;
    testButton.disabled = true;
  }
};
const enabledSites = globalThis.MEMORIVE_SITE_PROFILES.sites.filter((row) => row.enabled);
document.getElementById("enabled-sites").textContent = enabledSites.map((row) => row.name).join(" / ") || "—";

const renderState = (state) => {
  if (!state) return;
  lastSyncState = state;
  syncRunning = Boolean(state.running);
  syncButton.disabled = !enabledSites.length || Boolean(state.running);
  const failed = state.failed ? t("syncFailedCount", state.failed) : "";
  if (["RUNNER_STARTING", "DISCOVERING", "INDEXING", "CAPTURING", "DOWNLOADING"].includes(state.status)) {
    const where = [state.provider_id, state.total ? `${state.current} / ${state.total}` : ""].filter(Boolean).join(" ");
    say(statusNode, "syncProgress", where, state.captured || 0, failed);
  } else if (state.status === "COMPLETE") {
    say(statusNode, "syncDone", state.captured || 0, failed);
  } else if (state.status === "ERROR") {
    say(statusNode, "syncError", state.error_code || "UNKNOWN_ERROR");
  }
};

const refreshStatus = async () => {
  try {
    const response = await chrome.runtime.sendMessage({ type: "MEMORIVE_SYNC_STATUS" });
    if (response?.ok) renderState(response.state);
  } catch (_error) {
    say(statusNode, "syncStatusUnreadable");
  }
};

chrome.runtime.onMessage.addListener((message) => {
  if (["MEMORIVE_SYNC_ALL_PROGRESS", "MEMORIVE_SYNC_ALL_COMPLETE", "MEMORIVE_SYNC_ALL_ERROR"].includes(message?.type)) {
    renderState(message.state);
  }
});

syncButton.addEventListener("click", async () => {
  syncButton.disabled = true;
  say(statusNode, "syncStarting");
  try {
    const response = await chrome.runtime.sendMessage({ type: "MEMORIVE_SYNC_ALL_LIBRARIES" });
    if (!response?.ok) throw new Error(response?.error_code || "SESSION_LIBRARY_SYNC_START_FAILED");
    renderState(response.state);
  } catch (error) {
    syncButton.disabled = !enabledSites.length;
    say(statusNode, "syncError", String(error?.message || error));
  }
});

// String() so a missing code shows the generic message instead of throwing.
const errorKey = (raw) => {
  const code = String(raw || "");
  return code.includes("NOT_VERIFIED_OFFICIAL") ? "errorNotOfficial"
    : code.includes("HUMAN_VERIFICATION") ? "errorVerification"
    : code.includes("CONVERSATION_NOT_FOUND") ? "errorNoConversation"
    : code.includes("ORIGIN_OR_SESSION_CHANGED") ? "errorPageChanged"
    : "errorGeneric";
};
testButton.addEventListener("click", async () => {
  testBusy = true; testButton.disabled = true;
  say(statusNode, "testReading");
  try {
    const response = await chrome.runtime.sendMessage({ type: "MEMORIVE_TEST_ACTIVE_TAB" });
    if (!response?.ok) throw Error(response?.error_code || "CURRENT_PAGE_CAPTURE_FAILED");
    say(statusNode, response.read_method === "GENERIC_LAYOUT" ? "testResultGeneric" : "testResultMarkers",
        response.message_count, response.user_count, response.assistant_count);
  } catch (error) {
    const code = String(error?.message || error), roles = /ROLES_UNCLEAR_U(\d+)_A(\d+)/.exec(code);
    if (roles) say(statusNode, "errorRolesUnclear", Number(roles[1]) + Number(roles[2]), roles[1], roles[2]);
    else say(statusNode, errorKey(code));
  } finally {
    testBusy = false;
    await refreshCaptureAvailability();
  }
});

const showRecording = (state) => {
  recordingState = state || { status: "IDLE" };
  const active = recordingState.status === "RECORDING";
  recordButton.textContent = t(active ? "recordStop" : "recordStart");
  recordButton.title = t(active ? "recordStopTitle" : "recordStartTitle");
  recordButton.disabled = recordBusy || testBusy || syncRunning || recordingState.status === "SAVING";
  testButton.disabled = active || testBusy || syncRunning || recordBusy || recordingState.status === "SAVING";
  syncButton.disabled = active || testBusy || syncRunning || recordBusy || recordingState.status === "SAVING" || !enabledSites.length;
  const count = recordingState.message_count || 0, unplaced = recordingState.unplaced_count || 0;
  if (active) {
    if (unplaced) say(recordStatus, "recordingProgressGap", count, unplaced, recordingState.remaining_seconds);
    else say(recordStatus, "recordingProgress", count, recordingState.remaining_seconds);
  } else if (recordingState.status === "SAVING") say(recordStatus, "recordingSaving");
  else if (recordingState.status === "SAVED") {
    if (unplaced) say(recordStatus, "recordingSavedGap", count, unplaced);
    else say(recordStatus, "recordingSaved", count);
  } else if (recordingState.status === "ERROR") {
    if (String(recordingState.error_code).includes("ROLES_UNCLEAR")) {
      say(recordStatus, "errorRolesUnclear", count, recordingState.user_count || 0, recordingState.assistant_count || 0);
    } else say(recordStatus, errorKey(recordingState.error_code));
  }
};
const refreshRecording = async () => {
  if (recordBusy) return;
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    globalThis.MEMORIVE_OFFICIAL_WEB.requireCaptureUrl(tab?.url);
    const reply = await chrome.runtime.sendMessage({ type: "MEMORIVE_RECORD_ACTIVE_STATUS" });
    if (reply?.ok) showRecording(reply.state);
    else recordButton.disabled = false;
  } catch {
    recordButton.disabled = true;
    testButton.disabled = true;
  }
};
recordButton.addEventListener("click", async () => {
  recordBusy = true; recordButton.disabled = true;
  say(statusNode, null);
  try {
    const type = recordingState.status === "RECORDING" ? "MEMORIVE_RECORD_ACTIVE_STOP" : "MEMORIVE_RECORD_ACTIVE_START";
    const reply = await chrome.runtime.sendMessage({ type });
    if (!reply?.ok) throw Error(reply?.error_code || "RECORDING_FAILED");
    recordBusy = false;
    showRecording(reply.state);
  } catch (error) {
    say(recordStatus, errorKey(String(error?.message || error)));
  } finally {
    recordBusy = false;
    await refreshRecording();
  }
});

// The Aa button: a short menu of the three languages, each written in itself.
const closeLanguageMenu = () => {
  languageMenu.hidden = true;
  languageButton.setAttribute("aria-expanded", "false");
};
const fillLanguageMenu = () => {
  languageMenu.replaceChildren(...languages.map(([code, name]) => {
    const item = document.createElement("button");
    item.type = "button";
    item.setAttribute("role", "menuitemradio");
    item.setAttribute("aria-checked", String(code === current()));
    item.lang = code.replace("_", "-");
    item.textContent = name;
    item.addEventListener("click", async () => {
      closeLanguageMenu();
      await choose(code);
    });
    return item;
  }));
};
languageButton.addEventListener("click", () => {
  if (!languageMenu.hidden) return closeLanguageMenu();
  fillLanguageMenu();
  languageMenu.hidden = false;
  languageButton.setAttribute("aria-expanded", "true");
  languageMenu.querySelector('[aria-checked="true"]')?.focus();
});
document.addEventListener("click", (event) => {
  if (!event.target.closest?.(".language")) closeLanguageMenu();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !languageMenu.hidden) {
    closeLanguageMenu();
    languageButton.focus();
  }
});
// i18n.js has already put the fixed words in the new language; show the changing lines again too.
document.addEventListener("memorive-language", () => {
  const shown = statusNode.memoriveWords?.[0] || "";
  if (lastSyncState && ["syncProgress", "syncDone", "syncError"].includes(shown)) renderState(lastSyncState);
  else sayAgain(statusNode);
  sayAgain(recordStatus);
  showRecording(recordingState);
});

ready.then(() => {
  refreshStatus();
  void refreshCaptureAvailability();
  void refreshRecording();
  setInterval(refreshRecording, 1000);
});
