"use strict";

const statusNode = document.getElementById("status");
const syncButton = document.getElementById("sync-library");
const testButton = document.getElementById("test-current-page");
let testBusy=false,syncRunning=false;
const refreshCaptureAvailability = async () => {
  try {
    const [tab] = await chrome.tabs.query({ active:true, currentWindow:true });
    globalThis.PR_OS_OFFICIAL_WEB.requireCaptureUrl(tab?.url);
    testButton.disabled = testBusy || syncRunning || ['RECORDING','SAVING'].includes(recordingState.status);
  } catch (_error) {
    testButton.disabled = true;
  }
};
const enabledSites = globalThis.PR_OS_SITE_PROFILES.sites.filter(row=>row.enabled);
document.getElementById("enabled-sites").textContent = enabledSites.map(row=>row.name).join(" / ") || "—";

const renderState = (state) => {
  if (!state) return;
  syncRunning=Boolean(state.running);
  syncButton.disabled = !enabledSites.length || Boolean(state.running);
  if (["RUNNER_STARTING", "DISCOVERING", "INDEXING", "CAPTURING", "DOWNLOADING"].includes(state.status)) {
    const provider = state.provider_id ? ` · ${state.provider_id}` : "";
    const progress = state.total ? ` ${state.current} / ${state.total}` : "";
    statusNode.textContent = `同步中${provider}${progress} · ${state.captured || 0} 条` + (state.failed ? ` · 失败 ${state.failed} 条` : "");
  } else if (state.status === "COMPLETE") {
    statusNode.textContent = `已同步 ${state.captured || 0} 条` + (state.failed ? ` · 失败 ${state.failed} 条` : "");
  } else if (state.status === "ERROR") {
    statusNode.textContent = `同步失败：${state.error_code || "UNKNOWN_ERROR"}`;
  }
};

const refreshStatus = async () => {
  try {
    const response = await chrome.runtime.sendMessage({ type: "PR_OS_SYNC_STATUS" });
    if (response?.ok) renderState(response.state);
  } catch (_error) {
    statusNode.textContent = "无法读取同步状态；请在扩展管理页重新加载此扩展。";
  }
};

chrome.runtime.onMessage.addListener((message) => {
  if (["PR_OS_SYNC_ALL_PROGRESS", "PR_OS_SYNC_ALL_COMPLETE", "PR_OS_SYNC_ALL_ERROR"].includes(message?.type)) {
    renderState(message.state);
  }
});

syncButton.addEventListener("click", async () => {
  syncButton.disabled = true;
  statusNode.textContent = "后台同步中";
  try {
    const response = await chrome.runtime.sendMessage({ type: "PR_OS_SYNC_ALL_LIBRARIES" });
    if (!response?.ok) throw new Error(response?.error_code || "SESSION_LIBRARY_SYNC_START_FAILED");
    renderState(response.state);
  } catch (error) {
    syncButton.disabled = !enabledSites.length;
    statusNode.textContent = `同步失败：${String(error?.message || error)}`;
  }
});

const readError = code => code?.includes('RECORDING_ORDER_') ? '消息顺序无法确认，请刷新页面后重新录制'
  : code.includes('NOT_VERIFIED_OFFICIAL') ? '仅支持 AI 官网会话页面'
  : code.includes('HUMAN_VERIFICATION') ? '请先在网页完成验证，再重试'
  : code.includes('CONVERSATION_NOT_FOUND') ? '未找到可见的对话，请先打开一段会话'
  : code.includes('ORIGIN_OR_SESSION_CHANGED') ? '页面已切换，请重试'
  : '读取失败，请刷新当前网页后重试';
testButton.addEventListener('click', async()=>{
  testBusy=true;testButton.disabled=true;statusNode.textContent='正在读取当前页面…';
  try {
    const response=await chrome.runtime.sendMessage({type:'PR_OS_TEST_ACTIVE_TAB'});
    if(!response?.ok)throw Error(response?.error_code||'CURRENT_PAGE_CAPTURE_FAILED');
    statusNode.textContent=`已读取 ${response.message_count} 条（当前页面），未录入`;
  } catch(error) {statusNode.textContent=readError(String(error?.message||error));}
  finally {testBusy=false;await refreshCaptureAvailability();}
});

refreshStatus();
void refreshCaptureAvailability();

const recordButton=document.getElementById("record-page");
const recordStatus=document.getElementById("record-status");
let recordingState={status:"IDLE"},recordBusy=false;
const showRecording=state=>{
  recordingState=state || {status:"IDLE"};
  const active=recordingState.status==="RECORDING";
  recordButton.textContent=active ? "停止并录入" : "开始录制";
  recordButton.disabled=recordBusy || testBusy || syncRunning || recordingState.status==="SAVING";
  testButton.disabled=active || testBusy || syncRunning || recordBusy || recordingState.status==="SAVING";
  syncButton.disabled=active || testBusy || syncRunning || recordBusy || recordingState.status==="SAVING" || !enabledSites.length;
  recordButton.title=active ? "停止并录入已收集的消息" : "仅支持 AI 官网；开始后滚动加载对话";
  if (active) recordStatus.textContent="已累计 "+state.message_count+" 条 · 剩余 "+state.remaining_seconds+" 秒";
  else if (state?.status==="SAVING") recordStatus.textContent="录制已停止，正在保存…";
  else if (state?.status==="SAVED") recordStatus.textContent="已录入 "+state.message_count+" 条消息（部分内容）";
  else if (state?.status==="ERROR") recordStatus.textContent=readError(state.error_code);
};
const refreshRecording=async()=>{
  if (recordBusy) return;
  try {
    const [tab]=await chrome.tabs.query({active:true,currentWindow:true});
    globalThis.PR_OS_OFFICIAL_WEB.requireCaptureUrl(tab?.url);
    const reply=await chrome.runtime.sendMessage({type:"PR_OS_RECORD_ACTIVE_STATUS"});
    if (reply?.ok) showRecording(reply.state);
    else recordButton.disabled=false;
  } catch {recordButton.disabled=true;testButton.disabled=true;}
};
recordButton.addEventListener("click",async()=>{
  recordBusy=true;recordButton.disabled=true;
  statusNode.textContent="";
  try {
    const type=recordingState.status==="RECORDING"?"PR_OS_RECORD_ACTIVE_STOP":"PR_OS_RECORD_ACTIVE_START";
    const reply=await chrome.runtime.sendMessage({type});
    if (!reply?.ok) throw Error(reply?.error_code || "RECORDING_FAILED");
    recordBusy=false;showRecording(reply.state);
  } catch(error) {recordStatus.textContent=readError(String(error?.message || error));}
  finally {recordBusy=false;await refreshRecording();}
});
void refreshRecording();
setInterval(refreshRecording,1000);
