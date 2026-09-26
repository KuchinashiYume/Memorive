"use strict";

(() => {
  const BRIDGE_VERSION = "3.10.1";
  const SITE_CONFIG = globalThis.MEMORIVE_SITE_PROFILES;
  if (!SITE_CONFIG) return;
  const bridgeIdentity = BRIDGE_VERSION + ':' + SITE_CONFIG.sha256 + ':history-ready-v2';
  if (globalThis.__MEMORIVE_SESSION_LIBRARY_BRIDGE_VERSION__ === bridgeIdentity) return;
  if (globalThis.__MEMORIVE_OLD_BRIDGE_LISTENER__) chrome.runtime.onMessage.removeListener(globalThis.__MEMORIVE_OLD_BRIDGE_LISTENER__);
  if (globalThis.__MEMORIVE_OLD_HASH_LISTENER__) window.removeEventListener("hashchange", globalThis.__MEMORIVE_OLD_HASH_LISTENER__);
  globalThis.__MEMORIVE_STOP_RECORDING__?.();
  globalThis.__MEMORIVE_SESSION_LIBRARY_BRIDGE_VERSION__ = null;
  globalThis.__MEMORIVE_OLD_BRIDGE_LISTENER__ = null;
  globalThis.__MEMORIVE_OLD_HASH_LISTENER__ = null;
  // Drop any old listener before refusing a newly disabled site.
  try { globalThis.MEMORIVE_OFFICIAL_WEB.requireCaptureUrl(location.href); }
  catch (_) { return; }
  globalThis.__MEMORIVE_SESSION_LIBRARY_BRIDGE_VERSION__ = bridgeIdentity;

  const WAIT_MS = 420;
  const MAX_HISTORY_SCROLL_STEPS = 180;
  const MAX_CONVERSATION_SCROLL_STEPS = 320;
  const DEFAULT_BOTTOM_STABLE_CYCLES = 4;
  const RECENT_BOTTOM_STABLE_CYCLES = 7;
  const RECENT_SETTLE_RANK_LIMIT = 3;
  const FOURTEEN_DAYS_MS = 14 * 24 * 60 * 60 * 1000;
  const MAX_RECENT_CONVERSATIONS = 50;
  const UNDATED_FALLBACK_CONVERSATIONS = 30;
  const SYNC_TRIGGER_MODES = Object.freeze({
    "#memorive-browser-session-preflight-2-v5": "PREFLIGHT",
    "#memorive-browser-session-full-v4": "FULL"
  });
  const SYNC_PROVIDER_IDS = Object.freeze(SITE_CONFIG.sites.filter(row=>row.enabled).map(row=>row.provider_id));
  const PREFLIGHT_PROVIDER_TRIGGER_SCOPES = Object.freeze({
    "#memorive-browser-session-preflight-2-v5-deepseek-only": ["deepseek"],
    "#memorive-browser-session-preflight-2-v5-gemini-only": ["gemini"],
    "#memorive-browser-session-preflight-2-v5-kimi-only": ["kimi"]
  });
  const EXTENSION_RELOAD_TRIGGER_HASH = "#memorive-browser-companion-reload-v1";
  const INCREMENTAL_TRIGGER_PREFIX = "#memorive-browser-session-incremental-v2:";
  const consumedSyncTriggerHashes = new Set();
  const providers = {
    deepseek: {
      hosts: ["chat.deepseek.com"],
      history: ['a[href*="/a/chat/s/"]', 'a[href*="/chat/s/"]'],
      path: /\/(?:a\/)?chat\/s\/([^/?#]+)/i,
      user: [
        '[data-role="user"]', '[data-message-role="user"]',
        '[data-testid*="user-message"]', '.ds-user-message', '[class*="user-message"]',
        '.fbb737a4'
      ],
      assistant: [
        '[data-role="assistant"]', '[data-message-role="assistant"]',
        '[data-testid*="assistant-message"]', '.ds-assistant-message-main-content', '.ds-markdown',
        '.f9bf7997'
      ]
    },
    gemini: {
      hosts: ["gemini.google.com"],
      history: ['a[href*="/app/"]'],
      path: /\/app\/(?:c\/)?([^/?#]+)/i,
      user: ['user-query', 'user-query-content', '[data-test-id*="user"]', '.user-query'],
      assistant: ['model-response', '[data-test-id*="model-response"]', '.model-response']
    },
    kimi: {
      hosts: ["kimi.com", "www.kimi.com", "kimi.moonshot.cn"],
      history: [
        'a[href*="/chat/"]', '[data-testid*="history"] a[href]',
        '[data-testid*="conversation"] a[href]', '[class*="history"] a[href]'
      ],
      path: /\/chat\/(?:history\/)?([^/?#]+)/i,
      user: [
        '[data-role="user"]', '[data-message-role="user"]',
        '[data-testid="user-message"]', '[data-testid^="user-message-"]'
      ],
      assistant: [
        '[data-role="assistant"]', '[data-message-role="assistant"]',
        '[data-testid="assistant-message"]', '[data-testid^="assistant-message-"]',
        '[data-testid="model-message"]', '[data-testid^="model-message-"]'
      ],
      generic: [
        '[class*="chat-message" i]', '[class*="MessageItem"]',
        '[class*="message-item" i]', '.segment',
        '[class*="ChatItem"]', '[class*="bubble" i]'
      ]
    }
  };
  const currentPageDefinition = {
    user: [
      '[data-message-author-role="user"]', '[data-author-role="user"]',
      '[data-role="user"]', '[data-message-role="user"]',
      '[data-testid*="user-message" i]', '[data-test-id*="user-message" i]',
      'user-query', 'user-query-content', '.user-query',
      '[class*="user-message" i]', '[class*="question-item" i]',
      '[class*="questionitem" i]'
    ],
    assistant: [
      '[data-message-author-role="assistant"]', '[data-author-role="assistant"]',
      '[data-message-author-role="model"]', '[data-author-role="model"]',
      '[data-role="assistant"]', '[data-message-role="assistant"]',
      '[data-testid*="assistant-message" i]', '[data-test-id*="assistant-message" i]',
      '[data-testid*="model-message" i]', '[data-test-id*="model-message" i]',
      'model-response', '.model-response',
      '[class*="assistant-message" i]', '[class*="answer-item" i]',
      '[class*="answeritem" i]'
    ],
    generic: [
      '[data-message-author-role]', '[data-author-role]',
      '[data-role]', '[data-message-role]',
      '[class*="chat-message" i]', '[class*="message-item" i]',
      '[class*="messageitem" i]', '[class*="chat-item" i]',
      '[class*="conversation-turn" i]', '[class*="bubble" i]',
      'main article', 'main [role="listitem"]', '[role="main"] article'
    ]
  };

  // Reuse the production scroll/role/boundary capture for declared custom DOMs.
  // Configuration is generated locally; no remote script, eval, cookie or storage read.
  const builtinDefinitions = {...providers};
  for (const key of Object.keys(providers)) delete providers[key];
  for (const site of SITE_CONFIG.sites.filter(row=>row.enabled)) {
    const escapedPrefix = site.selectors.path_prefix.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    providers[site.provider_id] = site.adapter === 'generic' ? {
      hosts:site.hosts, history:[site.selectors.history],
      path:new RegExp('^'+escapedPrefix+'([^/?#]+)/?$'),
      user:[site.selectors.user], assistant:[site.selectors.assistant]
    } : {...builtinDefinitions[site.adapter],hosts:site.hosts};
  }

  const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
  const humanVerificationDetected = () => {
    if (document.querySelector(
      'iframe[src*="recaptcha"], .g-recaptcha, [data-sitekey], [class*="captcha" i], [id*="captcha" i]'
    )) return true;
    const text = cleanText(document.body?.innerText || "").replace(/\s+/g, " ").toLowerCase();
    return [
      "进行人机身份验证",
      "异常流量",
      "verify you are human",
      "unusual traffic",
      "i'm not a robot",
      "recaptcha"
    ].some((marker) => text.includes(marker));
  };
  const cleanText = (value) => String(value || "")
    .replaceAll("\u0000", " ")
    .replace(/\r\n?/g, "\n")
    .trim();
  const safeErrorCode = (error) => String(error?.message || error || "UNKNOWN_ERROR")
    .toUpperCase()
    .replace(/[^A-Z0-9_]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 120) || "UNKNOWN_ERROR";
  const simpleHash = (text) => {
    let hash = 2166136261;
    for (let index = 0; index < text.length; index += 1) {
      hash ^= text.charCodeAt(index);
      hash = Math.imul(hash, 16777619);
    }
    return (hash >>> 0).toString(16).padStart(8, "0");
  };
  const visible = (node) => {
    if (!(node instanceof Element)) return false;
    const style = getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" && rect.width > 1 && rect.height > 1;
  };
  const uniqueNodes = (selectors) => {
    const seen = new Set();
    const rows = [];
    selectors.forEach((selector) => {
      document.querySelectorAll(selector).forEach((node) => {
        if (!seen.has(node)) {
          seen.add(node);
          rows.push(node);
        }
      });
    });
    return rows;
  };
  const providerForPage = () => {
    const hostname = location.hostname.toLowerCase();
    const entry = Object.entries(providers).find(([, definition]) => definition.hosts.includes(hostname));
    if (!entry) throw new Error("UNSUPPORTED_PROVIDER_PAGE");
    return { providerId: entry[0], definition: entry[1] };
  };
  const canonicalSessionUrl = (providerId, definition, rawUrl) => {
    const parsed = new URL(rawUrl, location.href);
    if (parsed.origin !== location.origin || parsed.protocol !== 'https:' || parsed.username || parsed.password || !definition.hosts.includes(parsed.hostname)) return null;
    // Separate page decoration from session identity. Unknown query keys may
    // contain a session ID or secret and must not be silently merged/dropped.
    if (parsed.search) {
      const entries = [...parsed.searchParams.entries()];
      const navigationOnly = entries.every(([key,value]) =>
        (providerId === 'kimi' && key === 'chat_enter_method' && value === 'history') ||
        (providerId === 'gemini' && ((key === 'hl' && /^[a-z]{2,3}(?:[-_][a-z0-9]{2,8})*$/i.test(value)) ||
          (key === 'pageId' && value === 'none'))));
      if (!navigationOnly) return null;
      parsed.search = '';
    }
    if (providerId === 'gemini' && parsed.pathname.startsWith('/app/')) {
      // A history link can omit the signed-in account prefix. Keep the account
      // context from the visible page rather than navigating to the default.
      const account = location.pathname.match(/^\/u\/([0-9]+)\/app(?:\/|$)/);
      if (account) parsed.pathname = '/u/' + account[1] + parsed.pathname;
    }
    parsed.hash = "";
    const match = parsed.pathname.match(definition.path);
    if (!match || !match[1]) return null;
    const rawId = decodeURIComponent(match[1]);
    if (providerId === "kimi" && rawId.toLowerCase() === "history") return null;
    const safeId = rawId.replace(/[^A-Za-z0-9._~-]+/g, "-").slice(0, 220);
    return {
      source_url: parsed.href,
      provider_session_id: safeId || `${providerId}-${simpleHash(parsed.pathname)}`
    };
  };

  const normalizeDate = (raw) => {
    const value = cleanText(raw);
    if (!value) return "UNKNOWN";
    const direct = Date.parse(value);
    if (Number.isFinite(direct) && /\d{4}/.test(value)) return new Date(direct).toISOString();
    const local = value.match(/(\d{4})[年/.\-](\d{1,2})[月/.\-](\d{1,2})日?(?:\s+(\d{1,2}):(\d{2}))?/);
    if (local) {
      const parsed = new Date(
        Number(local[1]), Number(local[2]) - 1, Number(local[3]),
        Number(local[4] || 0), Number(local[5] || 0)
      );
      return Number.isNaN(parsed.getTime()) ? "UNKNOWN" : parsed.toISOString();
    }
    const day = new Date();
    day.setHours(0, 0, 0, 0);
    const clock = value.match(/(?:^|\s)(\d{1,2}):(\d{2})(?:\s|$)/);
    if (clock) day.setHours(Number(clock[1]), Number(clock[2]), 0, 0);
    if (/今天|今日|today/i.test(value)) return day.toISOString();
    if (/昨天|昨日|yesterday/i.test(value)) {
      day.setDate(day.getDate() - 1);
      return day.toISOString();
    }
    const relative = value.match(/(\d+)\s*(?:日|天|days?)\s*(?:前|ago)/i);
    if (relative && !/以内|内|間|within|last|過去/i.test(value)) {
      day.setDate(day.getDate() - Number(relative[1]));
      return day.toISOString();
    }
    return "UNKNOWN";
  };
  const recencyFor = (raw) => {
    const value = cleanText(raw);
    const exact = normalizeDate(value);
    if (exact !== "UNKNOWN") {
      const age = Date.now() - Date.parse(exact);
      if (age < -24 * 60 * 60 * 1000 || age > FOURTEEN_DAYS_MS) {
        return { include: false, conversation_at: exact, recency_bucket: "UNKNOWN" };
      }
      return {
        include: true,
        conversation_at: exact,
        recency_bucket: age <= 24 * 60 * 60 * 1000 ? "TODAY" : age <= 7 * 24 * 60 * 60 * 1000 ? "LAST_7_DAYS" : "LAST_14_DAYS"
      };
    }
    if (/更早|较早|30\s*(?:日|天)前|older|more\s+than\s+30|以前/i.test(value)) {
      return { include: false, conversation_at: "UNKNOWN", recency_bucket: "UNKNOWN" };
    }
    if (/今天|今日|today/i.test(value)) {
      return { include: true, conversation_at: "UNKNOWN", recency_bucket: "TODAY" };
    }
    if (/昨天|昨日|7\s*(?:日|天)(?:間)?|7\s*days?|this\s+week|今週/i.test(value)) {
      return { include: true, conversation_at: "UNKNOWN", recency_bucket: "LAST_7_DAYS" };
    }
    if (/14\s*(?:日|天)(?:間)?|14\s*days?/i.test(value)) {
      return { include: true, conversation_at: "UNKNOWN", recency_bucket: "LAST_14_DAYS" };
    }
    if (/30\s*(?:日|天)(?:間)?|30\s*days?/i.test(value)) {
      return { include: false, conversation_at: "UNKNOWN", recency_bucket: "LAST_30_DAYS" };
    }
    return { include: false, conversation_at: "UNKNOWN", recency_bucket: "UNKNOWN" };
  };
  const dateContextFor = (node) => {
    const strictDateLabel = (raw) => {
      const text = cleanText(raw);
      if (!text || text.length > 48) return "";
      if (/^\d{4}[-/.年]\d{1,2}(?:[-/.月]\d{1,2}日?)?(?:\s+\d{1,2}:\d{2})?$/.test(text)) return text;
      if (/^(?:今天|今日|昨天|昨日|today|yesterday)(?:\s+\d{1,2}:\d{2})?$/i.test(text)) return text;
      if (/^(?:最近|过去|過去|近|last|within)?\s*(?:7|14|30)\s*(?:日|天|days?)(?:間|内|以内|ago)?$/i.test(text)) return text;
      if (/^(?:本周|这周|今週|this\s+week|更早|较早|以前|より前|older|more\s+than\s+30\s+days?)$/i.test(text)) return text;
      return "";
    };
    let current = node;
    for (let depth = 0; depth < 10 && current; depth += 1) {
      const time = depth < 2 ? current.querySelector?.("time[datetime]") : null;
      if (time) return time.getAttribute("datetime") || time.textContent || "";
      let sibling = current.previousElementSibling;
      for (let index = 0; index < 24 && sibling; index += 1) {
        const label = strictDateLabel(sibling.textContent);
        if (label) return label;
        sibling = sibling.previousElementSibling;
      }
      const precedingHeadings = [...(current.querySelectorAll?.(
        "h1,h2,h3,h4,h5,h6,[role='heading']"
      ) || [])].filter((heading) => (
        heading === node ||
        Boolean(heading.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING)
      ));
      const headingText = strictDateLabel(precedingHeadings.at(-1)?.textContent);
      if (headingText) return headingText;
      current = current.parentElement;
    }
    return "";
  };

  const visibleConversationRecency = () => {
    const exactTimes = [...document.querySelectorAll("time[datetime]")]
      .filter(visible)
      .map((time) => normalizeDate(time.getAttribute("datetime") || time.textContent || ""))
      .filter((value) => value !== "UNKNOWN")
      .sort((left, right) => Date.parse(right) - Date.parse(left));
    return exactTimes.length ? recencyFor(exactTimes[0]) : null;
  };
  const safeDomDiagnostics = (definition) => {
    const dateLabels = [...document.querySelectorAll(
      "time,h1,h2,h3,h4,h5,h6,[role='heading']"
    )]
      .filter(visible)
      .map((node) => cleanText(node.getAttribute?.("datetime") || node.textContent))
      .filter((text) => text.length <= 48 && (
        /^\d{4}[-/.年]\d{1,2}/.test(text) ||
        /^(?:今天|今日|昨天|昨日|today|yesterday|本周|这周|今週|this\s+week|更早|较早|以前|より前|older)$/i.test(text) ||
        /^(?:最近|过去|過去|近|last|within)?\s*(?:7|14|30)\s*(?:日|天|days?)(?:間|内|以内|ago)?$/i.test(text)
      ))
      .slice(0, 16);
    const safeDateValue = (value) => {
      const text = cleanText(value);
      if (!text || text.length > 64) return "";
      if (/^\d{10,13}$/.test(text)) return text;
      if (/^\d{4}[-/.年]\d{1,2}/.test(text)) return text;
      if (/^(?:今天|今日|昨天|昨日|today|yesterday|本周|这周|今週|this\s+week|更早|较早|以前|より前|older)$/i.test(text)) return text;
      if (/^(?:最近|过去|過去|近|last|within)?\s*(?:7|14|30)\s*(?:日|天|days?)(?:間|内|以内|ago)?$/i.test(text)) return text;
      return "";
    };
    const safeDateTokens = (value) => {
      const text = cleanText(value).slice(0, 500);
      const matches = text.match(
        /\d{4}[-/.年]\d{1,2}(?:[-/.月]\d{1,2}日?)?(?:[T\s]\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?Z?)?|\d{1,2}月\d{1,2}日|(?:今天|今日|昨天|昨日|today|yesterday|本周|这周|今週|this\s+week|更早|较早|以前|より前|older)|(?:最近|过去|過去|近|last|within)?\s*(?:7|14|30)\s*(?:日|天|days?)(?:間|内|以内|ago)?|\d+\s*(?:日|天|days?)\s*(?:前|ago)/gi
      ) || [];
      return [...new Set(matches.map((token) => cleanText(token)))].slice(0, 8);
    };
    const historyNodes = uniqueNodes(definition.history).slice(0, 20).map((node) => {
      const dateAttributes = {};
      const dateTokens = [];
      for (const attribute of node.attributes || []) {
        const value = safeDateValue(attribute.value);
        if (value) dateAttributes[attribute.name] = value;
        dateTokens.push(...safeDateTokens(attribute.value));
      }
      dateTokens.push(...safeDateTokens(node.textContent));
      let ancestor = node.parentElement;
      let ancestorDate = "";
      for (let depth = 0; depth < 5 && ancestor && !ancestorDate; depth += 1) {
        for (const attribute of ancestor.attributes || []) {
          ancestorDate = safeDateValue(attribute.value);
          dateTokens.push(...safeDateTokens(attribute.value));
          if (ancestorDate) break;
        }
        dateTokens.push(...safeDateTokens(ancestor.textContent));
        ancestor = ancestor.parentElement;
      }
      const parsed = new URL(node.href, location.href);
      const pathShape = parsed.pathname.split("/").map((segment) => (
        segment.length >= 8 || /\d{6,}/.test(segment) ? ":id" : segment
      )).join("/");
      return {
        tag: node.tagName.toLowerCase(),
        path_shape: pathShape,
        date_attributes: dateAttributes,
        date_tokens: [...new Set(dateTokens)].slice(0, 8),
        ancestor_date: ancestorDate
      };
    });
    const signatures = [];
    const seen = new Set();
    for (const node of document.querySelectorAll(
      "main *,[role='main'] *,article,[role='listitem'],[data-role],[data-message-role],[data-testid]"
    )) {
      if (!visible(node) || /^(?:BUTTON|INPUT|TEXTAREA|SVG|PATH)$/.test(node.tagName)) continue;
      const textLength = cleanText(node.innerText || node.textContent).length;
      if (textLength < 2) continue;
      const classes = String(node.className || "")
        .split(/\s+/)
        .filter((token) => /^[A-Za-z0-9_-]{2,80}$/.test(token))
        .slice(0, 8);
      const signature = {
        tag: node.tagName.toLowerCase(),
        classes,
        role: cleanText(node.getAttribute("role")).slice(0, 48),
        data_role: cleanText(node.getAttribute("data-role") || node.getAttribute("data-message-role")).slice(0, 48),
        test_id: cleanText(node.getAttribute("data-testid") || node.getAttribute("data-test-id")).slice(0, 80),
        text_length: textLength,
        x_zone: Math.max(0, Math.min(4, Math.floor(node.getBoundingClientRect().left / Math.max(1, innerWidth) * 5)))
      };
      const key = JSON.stringify(signature);
      if (!seen.has(key)) {
        seen.add(key);
        signatures.push(signature);
      }
      if (signatures.length >= 80) break;
    }
    return {
      page_state: {
        ready_state: document.readyState,
        visibility_state: document.visibilityState,
        body_child_count: document.body?.children.length || 0,
        main_descendant_count: document.querySelectorAll("main *,[role='main'] *").length,
        pathname_shape: location.pathname.split("/").map((segment) => (
          segment.length >= 8 || /\d{6,}/.test(segment) ? ":id" : segment
        )).join("/")
      },
      date_labels: [...new Set(dateLabels)],
      history_nodes: historyNodes,
      matched_user_nodes: uniqueNodes(definition.user).filter(visible).length,
      matched_assistant_nodes: uniqueNodes(definition.assistant).filter(visible).length,
      structural_signatures: signatures
    };
  };
  const titleForHistoryNode = (node) => {
    const excluded = /^(?:deepseek|gemini|kimi|新建对话|新对话|new chat|今天|昨天|今日|昨日|today|yesterday|7\s*(?:日|天)内|30\s*(?:日|天)内|7\s*days?|30\s*days?|更多|删除|重命名|more|delete|rename)$/i;
    const actionLabel = /^(?:打开|开启|显示|open|show|view)(?:此|该|this)?\s*(?:会话|对话|conversation|chat)?\s*(?:菜单|选项|menu|options|actions?)$/i;
    const pureTime = /^(?:\d{1,2}:\d{2}|\d{4}[-/.年]\d{1,2}(?:[-/.月]\d{1,2}日?)?)$/;
    const visibleTitleCandidates = [...node.querySelectorAll(
      '[data-testid*="title" i],[data-test-id*="title" i],[class*="title" i]'
    )].map((candidate) => candidate.textContent);
    const candidates = [
      ...visibleTitleCandidates,
      node.innerText,
      node.textContent,
      node.getAttribute("title"),
      node.getAttribute("aria-label")
    ];
    for (const raw of candidates) {
      const title = cleanText(raw).split("\n").map((line) => line.trim()).find((line) => (
        line.length >= 2 && line.length <= 180 && !excluded.test(line) &&
        !actionLabel.test(line) && !pureTime.test(line)
      ));
      if (title) return title;
    }
    return "";
  };
  const scrollingAncestor = (nodes) => {
    let best = null;
    let bestRange = 0;
    nodes.forEach((node) => {
      let current = node.parentElement;
      while (current && current !== document.body) {
        const range = current.scrollHeight - current.clientHeight;
        if (range > bestRange && /(auto|scroll)/.test(getComputedStyle(current).overflowY)) {
          best = current;
          bestRange = range;
        }
        current = current.parentElement;
      }
    });
    return best;
  };

  const indexRecentSessions = async (providerId, definition, safetyCeiling) => {
    const limit = Math.max(1, Math.min(MAX_RECENT_CONVERSATIONS, safetyCeiling));
    const recentByDate = new Map();
    const sidebarOrder = new Map();
    const diagnostics = {
      raw: 0,
      visible: 0,
      canonical: 0,
      titled: 0,
      dated: 0
    };
    const collectVisible = () => {
      const rawNodes = uniqueNodes(definition.history);
      diagnostics.raw = Math.max(diagnostics.raw, rawNodes.length);
      const visibleNodes = rawNodes.filter(visible).sort((left, right) => {
        if (left === right) return 0;
        return left.compareDocumentPosition(right) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1;
      });
      diagnostics.visible = Math.max(diagnostics.visible, visibleNodes.length);
      visibleNodes.forEach((node) => {
        const identity = canonicalSessionUrl(providerId, definition, node.href);
        if (!identity || sidebarOrder.has(identity.provider_session_id)) return;
        diagnostics.canonical += 1;
        const title = titleForHistoryNode(node);
        if (!title) return;
        diagnostics.titled += 1;
        const dateContext = dateContextFor(node);
        if (dateContext) diagnostics.dated += 1;
        const recency = recencyFor(dateContext);
        const descriptor = {
          provider_session_id: identity.provider_session_id,
          title,
          conversation_at: recency.conversation_at,
          recency_bucket: recency.recency_bucket,
          source_url: identity.source_url,
          selection_basis: "RECENT_30_CONVERSATIONS",
          source_recency_rank: sidebarOrder.size
        };
        sidebarOrder.set(identity.provider_session_id, descriptor);
        if (recency.include) {
          recentByDate.set(identity.provider_session_id, {
            ...descriptor,
            selection_basis: "RECENT_14_DAYS"
          });
        }
      });
    };
    // History can mount after the page load event, or live in Kimi's collapsed sidebar.
    // Only expand the known local navigation control; never click conversation actions.
    let nodes = uniqueNodes(definition.history).filter(visible);
    const readyDeadline = performance.now() + 15000;
    let expanded = false;
    while (!nodes.length && performance.now() < readyDeadline) {
      if (humanVerificationDetected()) throw new Error("PROVIDER_HUMAN_VERIFICATION_REQUIRED");
      if (providerId === 'kimi' && !expanded) {
        const trigger = document.querySelector('[data-testid="sidebar-expand-trigger"][role="button"],button[data-testid="sidebar-expand-trigger"]');
        if (trigger && visible(trigger) && trigger.getAttribute('aria-expanded') !== 'true') {
          trigger.click();
          expanded = true;
        }
      }
      await sleep(250);
      nodes = uniqueNodes(definition.history).filter(visible);
    }
    const container = scrollingAncestor(nodes);
    const originalTop = container?.scrollTop || 0;
    let stable = 0;
    let priorSignature = "";
    if (container) container.scrollTop = 0;
    for (let step = 0; step < MAX_HISTORY_SCROLL_STEPS; step += 1) {
      await sleep(step === 0 ? WAIT_MS * 2 : WAIT_MS);
      collectVisible();
      nodes = uniqueNodes(definition.history).filter(visible);
      if (sidebarOrder.size >= limit) break;
      if (!container) break;
      const signature = `${container.scrollTop}|${container.scrollHeight}|${sidebarOrder.size}`;
      stable = signature === priorSignature ? stable + 1 : 0;
      priorSignature = signature;
      const atBottom = container.scrollTop + container.clientHeight >= container.scrollHeight - 3;
      if (atBottom && stable >= 2) break;
      container.scrollTop = Math.min(
        container.scrollHeight,
        container.scrollTop + Math.max(160, Math.floor(container.clientHeight * 0.75))
      );
    }
    if (container) container.scrollTop = originalTop;
    const recent = [...recentByDate.values()].sort(
      (left, right) => left.source_recency_rank - right.source_recency_rank
    );
    const fallback = [...sidebarOrder.values()]
      .filter((row) => (
        row.source_recency_rank < UNDATED_FALLBACK_CONVERSATIONS &&
        !recentByDate.has(row.provider_session_id)
      ))
      .sort((left, right) => left.source_recency_rank - right.source_recency_rank);
    const sessions = [...recent, ...fallback].slice(0, limit);
    if (!sessions.length) {
      if (!diagnostics.raw) throw new Error("SESSION_INDEX_NODES_NOT_FOUND");
      if (!diagnostics.visible) throw new Error("SESSION_INDEX_NODES_NOT_VISIBLE");
      if (!diagnostics.canonical) throw new Error("SESSION_INDEX_URL_PATTERN_MISMATCH");
      if (!diagnostics.titled) throw new Error("SESSION_INDEX_TITLES_NOT_FOUND");
      throw new Error("SESSION_INDEX_TITLES_NOT_FOUND");
    }
    return sessions;
  };

  const MESSAGE_BLOCK_TAGS = new Set([
    "ADDRESS", "ARTICLE", "ASIDE", "BLOCKQUOTE", "DD", "DETAILS", "DIV", "DL", "DT",
    "FIGCAPTION", "FIGURE", "FOOTER", "H1", "H2", "H3", "H4", "H5", "H6", "HEADER",
    "LI", "MAIN", "NAV", "OL", "P", "PRE", "SECTION", "SUMMARY", "TABLE", "TBODY",
    "TFOOT", "THEAD", "TR", "UL"
  ]);
  const MESSAGE_ACTION_TEXT = /^(?:复制|编辑|分享|重试|朗读|点赞|点踩|收藏|展开|收起|继续生成|停止生成|copy|edit|share|retry|read aloud|like|dislike|stop generating)$/i;
  const NUMERIC_CITATION_TEXT = /^(?:[-‐–—]?\d{1,3})(?:\s*[-,，]\s*\d{1,3})*$/;
  const messageElementMarker = (element) => [
    element.id,
    element.className,
    element.getAttribute?.("role"),
    element.getAttribute?.("data-role"),
    element.getAttribute?.("data-message-role"),
    element.getAttribute?.("data-message-author-role"),
    element.getAttribute?.("data-author-role"),
    element.getAttribute?.("data-testid"),
    element.getAttribute?.("data-test-id"),
    element.getAttribute?.("aria-label")
  ].map((value) => String(value || "")).join(" ").toLowerCase();
  const messageNoiseElement = (element, isRoot = false) => {
    if (!(element instanceof Element)) return false;
    if (element.matches("script,style,noscript,template,button,input,textarea,select,option,svg,canvas,audio,video")) {
      return true;
    }
    if (element.hidden || element.getAttribute("aria-hidden") === "true") return true;
    const style = getComputedStyle(element);
    if (style.display === "none" || style.visibility === "hidden") return true;
    const marker = messageElementMarker(element);
    if (/(?:^|[-_\s])(?:toolbar|actions?|feedback|citation|reference|footnote|source-card|source-list|source-chip|copy-button|retry-button|share-button|thumbs?|avatar|model-selector|plugin-card|tool-call|thinking-toggle|fold-control|metadata|meta|sr-only|visually-hidden|screen-reader(?:-only)?|a11y)(?:$|[-_\s])/.test(marker)) {
      return true;
    }
    const text = cleanText(element.textContent);
    if (element.matches("a,sup") && NUMERIC_CITATION_TEXT.test(text)) return true;
    return !isRoot && text.length <= 32 && MESSAGE_ACTION_TEXT.test(text);
  };
  const structuredMessageText = (node) => {
    if (!(node instanceof Element) || messageNoiseElement(node, true)) return "";
    const chunks = [];
    const appendBreak = () => {
      if (chunks.length && chunks[chunks.length - 1] !== "\n") chunks.push("\n");
    };
    const visit = (current, isRoot = false) => {
      if (current.nodeType === Node.TEXT_NODE) {
        chunks.push(String(current.nodeValue || "").replaceAll("\u00a0", " "));
        return;
      }
      if (!(current instanceof Element) || messageNoiseElement(current, isRoot)) return;
      if (current.tagName === "BR") {
        appendBreak();
        return;
      }
      const block = MESSAGE_BLOCK_TAGS.has(current.tagName);
      if (block) appendBreak();
      if (current.tagName === "LI") chunks.push("• ");
      for (const child of current.childNodes) visit(child, false);
      if (current.matches("TH,TD")) chunks.push("\t");
      if (block) appendBreak();
    };
    visit(node, true);
    const lines = cleanText(chunks.join(""))
      .replace(/[ \f\v]+\n/g, "\n")
      .replace(/\n[ \f\v]+/g, "\n")
      .replace(/[ \f\v]{2,}/g, " ")
      .replace(/\t{2,}/g, "\t")
      .replace(/\n{3,}/g, "\n\n")
      .split("\n")
      .map((line) => line.trimEnd());
    const normalized = [];
    let pendingBullet = false;
    const lastTextIndex = () => {
      for (let index = normalized.length - 1; index >= 0; index -= 1) {
        if (normalized[index]) return index;
      }
      return -1;
    };
    for (const line of lines) {
      const trimmed = line.trim();
      if (MESSAGE_ACTION_TEXT.test(trimmed)) continue;
      if (/^[•·▪◦]$/.test(trimmed)) {
        pendingBullet = true;
        continue;
      }
      if (!trimmed) {
        if (!pendingBullet && normalized.length && normalized[normalized.length - 1] !== "") {
          normalized.push("");
        }
        continue;
      }
      if (/^[，。！？；：,.!?;:]$/.test(trimmed)) {
        const index = lastTextIndex();
        if (index >= 0) normalized[index] = `${normalized[index]}${trimmed}`;
        continue;
      }
      normalized.push(pendingBullet ? `• ${trimmed}` : line.trimStart());
      pendingBullet = false;
    }
    return normalized.join("\n").replace(/\n{3,}/g, "\n\n").trim();
  };
  const roleMarker = (node) => {
    let current = node;
    for (let depth = 0; depth < 4 && current; depth += 1) {
      const marker = [
        current.id,
        current.className,
        current.getAttribute?.("data-role"),
        current.getAttribute?.("data-message-role"),
        current.getAttribute?.("data-message-author-role"),
        current.getAttribute?.("data-author-role"),
        current.getAttribute?.("data-testid"),
        current.getAttribute?.("data-test-id"),
        current.getAttribute?.("aria-label")
      ].map((value) => String(value || "")).join(" ").toLowerCase();
      if (/(?:^|[-_\s])(user|human|question|query|prompt|mine|self)(?:$|[-_\s])/.test(marker)) return "user";
      if (/(?:^|[-_\s])(assistant|model|bot|answer|response|reply|kimi)(?:$|[-_\s])/.test(marker)) return "assistant";
      current = current.parentElement;
      if (current?.matches?.("aside,nav,header,footer,form")) break;
    }
    const rect = node.getBoundingClientRect();
    if (rect.left >= innerWidth * 0.42) return "user";
    if (rect.right <= innerWidth * 0.78) return "assistant";
    return "";
  };
  const genericMessageRows = (definition) => {
    if (!definition.generic?.length) return [];
    const all = uniqueNodes(definition.generic).filter((node) => (
      visible(node) && !node.closest("aside,nav,header,footer,form") && !messageNoiseElement(node, true)
    ));
    const variants = [
      all.filter((node) => !all.some((other) => other !== node && other.contains(node))),
      all.filter((node) => !all.some((other) => other !== node && node.contains(other)))
    ];
    const candidates = variants.map((nodes) => {
      const rows = nodes.map((node) => ({
        node,
        role: roleMarker(node),
        text: structuredMessageText(node)
      })).filter((row) => row.text && row.text.length >= 2);
      rows.sort((left, right) => (
        left.node === right.node ? 0 :
          (left.node.compareDocumentPosition(right.node) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1)
      ));
      const knownRoles = new Set(rows.map((row) => row.role).filter(Boolean));
      if (knownRoles.size < 2 && rows.length >= 2) {
        rows.forEach((row, index) => {
          if (!row.role) row.role = index % 2 === 0 ? "user" : "assistant";
        });
      }
      return rows.filter((row) => row.role);
    }).filter((rows) => (
      rows.some((row) => row.role === "user") && rows.some((row) => row.role === "assistant")
    ));
    // A provider turn often contains several nested ``segment`` nodes for
    // citations, model badges and tool metadata. Prefer the outer turn roots;
    // only fall back to leaves when outer roots cannot establish both roles.
    return candidates[0] || candidates[1] || [];
  };
  const messageRows = (providerId, definition, retainIdentity = false) => {
    const prepared = [];
    for (const [role, selectors] of [["user", definition.user], ["assistant", definition.assistant]]) {
      uniqueNodes(selectors).filter(visible).forEach((node) => {
        const text = structuredMessageText(node);
        if (text) prepared.push({ node, role, text });
      });
    }
    if (!prepared.some((row) => row.role === "user") || !prepared.some((row) => row.role === "assistant")) {
      prepared.push(...genericMessageRows(definition));
    }
    const specific = prepared.filter((row) => !prepared.some((other) => (
      other !== row && row.role === other.role && row.node.contains(other.node) &&
      other.text.length >= Math.floor(row.text.length * 0.72) && row.text.includes(other.text)
    )));
    specific.sort((left, right) => {
      if (left.node === right.node) return 0;
      return left.node.compareDocumentPosition(right.node) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1;
    });
    if (retainIdentity) {
      const seen = new Set();
      return specific.filter(row => {
        if (seen.has(row.node)) return false;
        seen.add(row.node); return true;
      }).map(({node,role,text}) => {
        let key = "", order = null, cursor = node;
        for (let i=0;cursor && i<8;i++,cursor=cursor.parentElement) {
          if (!key) key = cursor.getAttribute("data-message-id") || cursor.getAttribute("data-turn-id") || "";
          const testId = cursor.getAttribute("data-testid") || "";
          const match = /^conversation-turn-(\d+)$/.exec(testId);
          if (match) { order=Number(match[1]); if (!key) key=testId; }
        }
        return {role,text,source_key:key ? role+":"+key : "",source_order:order};
      });
    }
    const deduped = [];
    specific.forEach(({ role, text }) => {
      const prior = deduped[deduped.length - 1];
      if (prior && prior.role === role && (prior.text === text || prior.text.includes(text))) return;
      if (prior && prior.role === role && text.includes(prior.text)) {
        prior.text = text;
        return;
      }
      if (prior && prior.role === role) {
        prior.text = `${prior.text}\n\n${text}`;
        return;
      }
      deduped.push({ role, text });
    });
    return deduped;
  };
  const captureCurrentVisiblePage = (recording = false) => {
    if (location.protocol !== "https:") throw new Error("CURRENT_PAGE_HTTPS_REQUIRED");
    const known = Object.values(providers).find(row=>row.hosts.includes(location.hostname));
    const definition = Object.fromEntries(['user','assistant','generic'].map(key=>[
      key,[...new Set([...(currentPageDefinition[key]||[]),...(known?.[key]||[])])]
    ]));
    const rows = messageRows("universal", definition, recording).filter(row=>row.text.length > 0);
    if (
      !rows.some((row) => row.role === "user") ||
      !rows.some((row) => row.role === "assistant")
    ) {
      throw new Error("CURRENT_PAGE_CONVERSATION_NOT_FOUND");
    }
    const safeUrl = new URL(location.href);
    safeUrl.username = "";
    safeUrl.password = "";
    safeUrl.search = "";
    safeUrl.hash = "";
    const firstUser = rows.find((row) => row.role === "user")?.text || "";
    const documentTitle = cleanText(document.title)
      .replace(/\s+[|·]\s+[^|·]{1,80}$/u, "")
      .slice(0, 160);
    const title = documentTitle || firstUser.slice(0, 96) || "当前网页会话";
    const conversationPath = /\/(?:c|chat|app|conversation|session)\/[^/]+\/?$/.test(safeUrl.pathname);
    const stableSeed = conversationPath ? safeUrl.href.replace(/\/$/, "") :
      `${safeUrl.hostname}|${safeUrl.pathname}|${title}|${firstUser.slice(0, 160)}`;
    const observedAt = new Date().toISOString();
    return {
      provider_id: "universal",
      provider_session_id: `universal-${simpleHash(stableSeed)}`,
      title,
      created_at: "UNKNOWN",
      updated_at: "UNKNOWN",
      recency_bucket: "UNKNOWN",
      selection_basis: "CURRENT_PAGE_USER_GESTURE",
      source_recency_rank: 0,
      observed_at: observedAt,
      source_url: safeUrl.href,
      capture_method: "BROWSER_COMPANION_ACTIVE_TAB_V1",
      capture_status: "PARTIAL",
      source_truncated: true,
      top_boundary_reached: false,
      bottom_boundary_reached: false,
      error_code: "CURRENT_VISIBLE_INTERFACE_ONLY",
      messages: rows.map((row, index) => ({
        message_id: `universal-${simpleHash(`${stableSeed}|${index}|${row.role}|${row.text}`)}`,
        role: row.role,
        text: row.text,
        ...(recording ? {source_key:row.source_key,source_order:row.source_order} : {})
      }))
    };
  };
  const compatibleMessage = (left, right) => {
    if (left.role !== right.role) return false;
    if (left.text === right.text || left.text.includes(right.text) || right.text.includes(left.text)) return true;
    const prefixLength = Math.min(48, left.text.length, right.text.length);
    return prefixLength >= 24 && left.text.slice(0, prefixLength) === right.text.slice(0, prefixLength);
  };
  const mergeWindow = (target, page) => {
    let overlap = 0;
    for (let length = Math.min(target.length, page.length); length > 0; length -= 1) {
      if (target.slice(-length).every((message, index) => compatibleMessage(message, page[index]))) {
        overlap = length;
        break;
      }
    }
    for (let index = 0; index < overlap; index += 1) {
      const targetIndex = target.length - overlap + index;
      if (page[index].text.length > target[targetIndex].text.length) target[targetIndex] = page[index];
    }
    target.push(...page.slice(overlap));
  };
  const conversationScroller = (definition) => {
    const message = uniqueNodes([
      ...definition.user,
      ...definition.assistant,
      ...(definition.generic || [])
    ]).find(visible);
    if (!message) return null;
    let current = message.parentElement;
    let best = document.scrollingElement;
    let bestRange = Math.max(0, (best?.scrollHeight || 0) - (best?.clientHeight || 0));
    while (current && current !== document.documentElement) {
      const range = current.scrollHeight - current.clientHeight;
      if (range > bestRange && /(auto|scroll)/.test(getComputedStyle(current).overflowY)) {
        best = current;
        bestRange = range;
      }
      current = current.parentElement;
    }
    return best;
  };
  const waitForMessages = async (providerId, definition) => {
    for (let attempt = 0; attempt < 45; attempt += 1) {
      if (messageRows(providerId, definition).length) return;
      await sleep(WAIT_MS);
    }
    throw new Error("CONVERSATION_MESSAGES_NOT_FOUND");
  };
  const messageWindowSignature = (rows) => {
    const fingerprint = rows.map((row) => `${row.role}:${row.text.length}:${simpleHash(row.text)}`).join("|");
    return `${rows.length}|${fingerprint}`;
  };
  const conversationStillGenerating = () => uniqueNodes([
    'button[aria-label*="停止生成" i]', 'button[aria-label*="stop generating" i]',
    '[role="button"][aria-label*="停止生成" i]', '[role="button"][aria-label*="stop generating" i]',
    '[data-testid*="stop-generating" i]', '[data-test-id*="stop-generating" i]'
  ]).some(visible) || [...document.querySelectorAll("button,[role='button']")].some((node) => (
    visible(node) && /^(?:停止生成|stop generating)$/i.test(cleanText(node.textContent || node.getAttribute?.("aria-label")))
  ));
  const captureConversation = async (providerId, definition, descriptor) => {
    await waitForMessages(providerId, definition);
    const pageRecency = visibleConversationRecency();
    const selectionBasis = descriptor.selection_basis || "RECENT_14_DAYS";
    if (selectionBasis === "RECENT_14_DAYS" && pageRecency && !pageRecency.include) {
      throw new Error("SESSION_OUTSIDE_RECENT_14_DAYS");
    }
    if (!["RECENT_14_DAYS", "RECENT_30_CONVERSATIONS"].includes(selectionBasis)) {
      throw new Error("SESSION_SELECTION_BASIS_INVALID");
    }
    const target = conversationScroller(definition);
    if (!target) throw new Error("CONVERSATION_SCROLL_CONTAINER_NOT_FOUND");
    const originalTop = target.scrollTop;
    const collected = [];
    let priorTopSignature = "";
    let topStable = 0;
    for (let step = 0; step < 40; step += 1) {
      target.scrollTop = 0;
      await sleep(WAIT_MS);
      const rows = messageRows(providerId, definition);
      const signature = `${target.scrollTop}|${target.scrollHeight}|${messageWindowSignature(rows)}`;
      topStable = signature === priorTopSignature ? topStable + 1 : 0;
      priorTopSignature = signature;
      if (topStable >= 3) break;
    }
    const topBoundaryReached = target.scrollTop <= 3 && topStable >= 3;
    if (!topBoundaryReached) throw new Error("CONVERSATION_TOP_BOUNDARY_NOT_REACHED");
    let priorSignature = "";
    let stable = 0;
    let complete = false;
    const sourceRecencyRank = Number(descriptor.source_recency_rank);
    const requiredBottomStableCycles = (
      Number.isInteger(sourceRecencyRank) &&
      sourceRecencyRank >= 0 &&
      sourceRecencyRank < RECENT_SETTLE_RANK_LIMIT
    )
      ? RECENT_BOTTOM_STABLE_CYCLES
      : DEFAULT_BOTTOM_STABLE_CYCLES;
    for (let step = 0; step < MAX_CONVERSATION_SCROLL_STEPS; step += 1) {
      const rows = messageRows(providerId, definition);
      mergeWindow(collected, rows);
      const signature = `${target.scrollTop}|${target.scrollHeight}|${messageWindowSignature(rows)}|${messageWindowSignature(collected)}`;
      stable = signature === priorSignature ? stable + 1 : 0;
      priorSignature = signature;
      const atBottom = target.scrollTop + target.clientHeight >= target.scrollHeight - 3;
      if (
        atBottom &&
        stable >= requiredBottomStableCycles &&
        !conversationStillGenerating()
      ) {
        complete = true;
        break;
      }
      target.scrollTop = Math.min(
        target.scrollHeight,
        target.scrollTop + Math.max(180, Math.floor(target.clientHeight * 0.72))
      );
      await sleep(WAIT_MS);
    }
    target.scrollTop = originalTop;
    if (!complete) throw new Error("CONVERSATION_SCROLL_LIMIT_REACHED");
    if (!collected.length) throw new Error("CONVERSATION_MESSAGES_NOT_FOUND");
    if (
      !collected.some((message) => message.role === "user") ||
      !collected.some((message) => message.role === "assistant")
    ) {
      const userCount = collected.filter((message) => message.role === "user").length;
      const assistantCount = collected.filter((message) => message.role === "assistant").length;
      throw new Error(`CONVERSATION_ROLE_COVERAGE_INCOMPLETE_U${userCount}_A${assistantCount}`);
    }
    return {
      provider_id: providerId,
      provider_session_id: descriptor.provider_session_id,
      title: descriptor.title,
      conversation_at: pageRecency?.conversation_at || descriptor.conversation_at || "UNKNOWN",
      recency_bucket: pageRecency?.recency_bucket || descriptor.recency_bucket || "UNKNOWN",
      selection_basis: selectionBasis,
      source_recency_rank: descriptor.source_recency_rank,
      observed_at: new Date().toISOString(),
      source_url: descriptor.source_url,
      capture_method: "BROWSER_COMPANION_BACKGROUND_TAB_V4",
      capture_status: "COMPLETE",
      source_truncated: false,
      top_boundary_reached: topBoundaryReached,
      bottom_boundary_reached: complete,
      error_code: "",
      messages: collected.map((message, index) => ({
        message_id: `${providerId}-${simpleHash(`${descriptor.provider_session_id}|${index}|${message.role}|${message.text}`)}`,
        role: message.role,
        text: message.text
      }))
    };
  };

  const recorder = globalThis.MEMORIVE_SCROLL_RECORDING.createRecorder({
    capture: () => {
      const result=captureCurrentVisiblePage(true);
      return result;
    },
    validate: (sourceUrl) => {
      globalThis.MEMORIVE_OFFICIAL_WEB.requireCaptureUrl(location.href);
      const url = new URL(location.href); url.search="";url.hash="";
      if (sourceUrl && url.href!==sourceUrl) throw Error("CURRENT_PAGE_ORIGIN_OR_SESSION_CHANGED");
      if (humanVerificationDetected()) throw Error("PROVIDER_HUMAN_VERIFICATION_REQUIRED");
    },
    finish: (conversation,token) => chrome.runtime.sendMessage({type:"MEMORIVE_RECORDING_FINISH",token,conversation})
  });
  const onRecordingScroll = event => recorder.sample(Number(event.target?.scrollTop ?? window.scrollY));
  document.addEventListener("scroll",onRecordingScroll,true);
  const onRecordingExit = () => void recorder.stop("PAGE_CLOSED");
  window.addEventListener("pagehide",onRecordingExit);
  globalThis.__MEMORIVE_STOP_RECORDING__ = () => {
    void recorder.stop("EXTENSION_UPDATED");
    document.removeEventListener("scroll",onRecordingScroll,true);
    window.removeEventListener("pagehide",onRecordingExit);
  };

  const bridgeListener = (message, _sender, sendResponse) => {
    const execute = async () => {
      if (message?.type === "MEMORIVE_RECORDING_STATUS") return {ok:true,state:recorder.status()};
      if (message?.type === "MEMORIVE_RECORDING_START") {
        globalThis.MEMORIVE_OFFICIAL_WEB.requireCaptureUrl(location.href);
        return {ok:true,state:recorder.start(message)};
      }
      if (message?.type === "MEMORIVE_RECORDING_STOP") {
        if (message.token && message.token!==recorder.status().token) throw Error("RECORDING_TOKEN_MISMATCH");
        recorder.sample();
        return {ok:true,state:await recorder.stop(message.reason || "USER_STOP")};
      }
      if (message?.type === "MEMORIVE_CAPTURE_CURRENT_PAGE_GENERIC") {
        globalThis.MEMORIVE_OFFICIAL_WEB.requireCaptureUrl(location.href);
        if (humanVerificationDetected()) throw new Error("PROVIDER_HUMAN_VERIFICATION_REQUIRED");
        return { ok: true, conversation: captureCurrentVisiblePage() };
      }
      globalThis.MEMORIVE_OFFICIAL_WEB.requireAllowedUrl(location.href);
      if (humanVerificationDetected()) {
        throw new Error("PROVIDER_HUMAN_VERIFICATION_REQUIRED");
      }
      const { providerId, definition } = providerForPage();
      if (["MEMORIVE_INDEX_RECENT_14_DAYS_OR_30_CONVERSATIONS", "MEMORIVE_INDEX_RECENT_30_DAYS"].includes(message?.type)) {
        const safetyCeiling = Math.max(
          1,
          Math.min(MAX_RECENT_CONVERSATIONS, Number(message.safety_ceiling) || MAX_RECENT_CONVERSATIONS)
        );
        return {
          ok: true,
          provider_id: providerId,
          sessions: await indexRecentSessions(providerId, definition, safetyCeiling)
        };
      }
      if (message?.type === "MEMORIVE_CAPTURE_INDEXED_CONVERSATION") {
        const descriptor = message.descriptor;
        if (!descriptor || typeof descriptor !== "object") throw new Error("SESSION_DESCRIPTOR_REQUIRED");
        const current = canonicalSessionUrl(providerId, definition, location.href);
        const expected = canonicalSessionUrl(providerId, definition, descriptor.source_url);
        if (!current || !expected || expected.source_url !== current.source_url ||
            current.provider_session_id !== descriptor.provider_session_id) {
          throw new Error("INDEXED_SESSION_PAGE_MISMATCH");
        }
        const conversation = await captureConversation(providerId, definition, descriptor);
        globalThis.MEMORIVE_OFFICIAL_WEB.requireAllowedUrl(location.href);
        if (canonicalSessionUrl(providerId, definition, location.href)?.source_url !== expected.source_url)
          throw new Error("INDEXED_SESSION_PAGE_MISMATCH");
        return {
          ok: true,
          conversation
        };
      }
      return null;
    };
    if (!message?.type?.startsWith("MEMORIVE_")) return false;
    execute().then((response) => sendResponse(response)).catch((error) => {
      const errorCode = safeErrorCode(error);
      if (errorCode === "PROVIDER_HUMAN_VERIFICATION_REQUIRED") {
        sendResponse({ ok: false, error_code: errorCode, diagnostic: null });
        return;
      }
      let diagnostic = null;
      try {
        globalThis.MEMORIVE_OFFICIAL_WEB.requireAllowedUrl(location.href);
        diagnostic = safeDomDiagnostics(providerForPage().definition);
      } catch (_diagnosticError) {
        diagnostic = null;
      }
      sendResponse({ ok: false, error_code: errorCode, diagnostic });
    });
    return true;
  };
  chrome.runtime.onMessage.addListener(bridgeListener);
  globalThis.__MEMORIVE_OLD_BRIDGE_LISTENER__ = bridgeListener;

  const consumeApprovedSyncTrigger = () => {
    const triggerHash = location.hash;
    let cursorToken = "";
    let incrementalProviderScope = null;
    if (triggerHash.startsWith(INCREMENTAL_TRIGGER_PREFIX)) {
      cursorToken = triggerHash.slice(INCREMENTAL_TRIGGER_PREFIX.length);
      try {
        if (!cursorToken || cursorToken.length > 8192 || !/^[A-Za-z0-9_-]+$/.test(cursorToken)) return;
        const normalized = cursorToken.replace(/-/g, "+").replace(/_/g, "/");
        const padded = normalized + "=".repeat((4 - (normalized.length % 4)) % 4);
        const bytes = Uint8Array.from(atob(padded), (character) => character.charCodeAt(0));
        const parsed = JSON.parse(new TextDecoder().decode(bytes));
        if (parsed?.schema_version !== "MEMORIVE_BROWSER_SESSION_CURSOR_V1") return;
        incrementalProviderScope = Object.keys(parsed.providers || {});
        if (
          !incrementalProviderScope.length ||
          incrementalProviderScope.length > SYNC_PROVIDER_IDS.length ||
          !incrementalProviderScope.every((providerId, index, values) => (
            SYNC_PROVIDER_IDS.includes(providerId) && values.indexOf(providerId) === index
          ))
        ) return;
      } catch (_error) {
        return;
      }
    }
    const exactProviderScope = incrementalProviderScope || PREFLIGHT_PROVIDER_TRIGGER_SCOPES[triggerHash] || null;
    const suffixSeparator = triggerHash.indexOf(":");
    const baseHash = suffixSeparator >= 0 ? triggerHash.slice(0, suffixSeparator) : triggerHash;
    const providerSuffix = suffixSeparator >= 0 ? triggerHash.slice(suffixSeparator + 1) : "";
    const syncMode = incrementalProviderScope
      ? "INCREMENTAL"
      : (exactProviderScope ? "PREFLIGHT" : SYNC_TRIGGER_MODES[baseHash]);
    const requestedProviders = exactProviderScope || providerSuffix
      .split(",")
      .map((value) => value.trim().toLowerCase())
      .filter(Boolean);
    const providersValid = exactProviderScope || (
      suffixSeparator < 0 || (
        requestedProviders.length > 0 &&
        requestedProviders.join(",") === providerSuffix &&
        requestedProviders.every((providerId, index, values) => (
          SYNC_PROVIDER_IDS.includes(providerId) && values.indexOf(providerId) === index
        ))
      )
    );
    if (!syncMode || !providersValid || consumedSyncTriggerHashes.has(triggerHash)) return;
    consumedSyncTriggerHashes.add(triggerHash);
    history.replaceState(history.state, "", `${location.pathname}${location.search}`);
    chrome.runtime.sendMessage({
      type: "MEMORIVE_SYNC_ALL_LIBRARIES_FROM_APPROVED_FRAGMENT",
      bridge_version: BRIDGE_VERSION,
      site_profile_sha256: SITE_CONFIG.sha256,
      sync_mode: syncMode,
      providers: requestedProviders,
      cursor_token: cursorToken
    }).catch(() => {}).finally(() => consumedSyncTriggerHashes.delete(triggerHash));
  };

  const consumeExtensionReloadTrigger = () => {
    if (location.hash !== EXTENSION_RELOAD_TRIGGER_HASH) return false;
    history.replaceState(history.state, "", `${location.pathname}${location.search}`);
    const localReload = () => {
      if (typeof chrome.runtime.reload === "function") chrome.runtime.reload();
    };
    const requestBackgroundReload = (bridgeVersion) => chrome.runtime.sendMessage({
      type: "MEMORIVE_BRIDGE_IDENTITY_RELOAD_REQUEST",
      bridge_version: bridgeVersion
    });
    requestBackgroundReload(BRIDGE_VERSION).then(async (response) => {
      if (response?.reload_scheduled === true) return;
      const runningVersion = String(response?.extension_version || "").trim();
      if (
        response?.error_code === "IDENTITY_RELOAD_VERSION_MISMATCH" &&
        /^\d+\.\d+\.\d+$/.test(runningVersion) &&
        runningVersion !== BRIDGE_VERSION
      ) {
        const retry = await requestBackgroundReload(runningVersion).catch(() => null);
        if (retry?.reload_scheduled === true) return;
      }
      localReload();
    }).catch(localReload);
    return true;
  };

  const consumeHashChangeTrigger = () => {
    if (consumeExtensionReloadTrigger()) return;
    consumeApprovedSyncTrigger();
  };

  if (consumeExtensionReloadTrigger()) return;
  consumeApprovedSyncTrigger();
  globalThis.__MEMORIVE_OLD_HASH_LISTENER__ = consumeHashChangeTrigger;
  window.addEventListener("hashchange", consumeHashChangeTrigger, { passive: true });
})();
