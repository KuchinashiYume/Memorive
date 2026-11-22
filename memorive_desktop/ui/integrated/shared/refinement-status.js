/* What a session refinement's failure means for the user, in one sentence with the next step. The session page
   (routes/sessions/view.js), the inbox (routes/inbox/bridge.js) and the task page (routes/tasks/view.js) show the same
   sentence for the same failure; the error code itself stays in the technical details (a tooltip, the inspector, the
   node's data). Chinese source text; app.js translates it like the rest of the page. */
(() => {
  'use strict';
  const SOURCE_CHANGED = '排队期间会话有了新内容，请重新精炼。';
  const FAILURES = Object.freeze({
    SESSION_REFINEMENT_FROZEN_SNAPSHOT_DRIFT: SOURCE_CHANGED,
    SESSION_REFINEMENT_SOURCE_SNAPSHOT_CHANGED: SOURCE_CHANGED,
    SESSION_REFINEMENT_INTERRUPTED: '精炼被应用关闭打断，请重新精炼。',
    SESSION_REFINEMENT_CANCELLED: '任务已取消，没有继续调用模型。',
    SESSION_REFINEMENT_MODEL_NOT_CONFIGURED: '还没有选择精炼模型，请先到设置中选择。',
    SESSION_REFINEMENT_PROFILE_NOT_CONFIGURED: '原来的精炼模型已移除，请到设置中重新选择。',
    SESSION_REFINEMENT_PROFILE_EXECUTION_UNAVAILABLE: '所选模型暂不可用，请检查模型服务后重试。',
    SESSION_REFINEMENT_LOCAL_IDENTITY_REFRESH_FAILED: '本机模型没有响应，请确认本机模型服务已启动后重试。',
    SESSION_REFINEMENT_REQUIRES_COMPLETE_CONTENT: '会话没有完整读取，请重新同步后再精炼。',
    SESSION_REFINEMENT_SOURCE_MESSAGE_INVALID: '会话正文包含无法处理的消息结构。',
    REFINEMENT_MESSAGE_FIELDS_INVALID: '会话格式不兼容，请重新精炼。',
    REFINEMENT_EXECUTION_CHANNEL_NOT_AVAILABLE: '所选模型尚未接通精炼执行通道。',
    LOCAL_MODEL_NOT_EXECUTION_ELIGIBLE: '所选本地模型当前不可执行，请在设置中重新验证。',
    LOCAL_STRUCTURED_CHAT_MODEL_IDENTITY_MISMATCH: '本地服务返回的模型与所选模型不一致。',
    LOCAL_STRUCTURED_CHAT_RESPONSE_SCHEMA_INVALID: '本地模型返回的结构不符合精炼结果格式。'
  });
  const failureText = (code) => {
    const value = String(code || '');
    if (FAILURES[value]) return FAILURES[value];
    // The model answered, but not in the shape asked for.
    if (value.startsWith('REFINEMENT_PROPOSAL_')) return '模型返回的结果格式不对，请重试或换一个精炼模型。';
    return '精炼没有完成，请重试。';
  };
  Memorive.refinementStatus = Object.freeze({ failureText });
})();
