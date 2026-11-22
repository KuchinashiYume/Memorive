"use strict";

(() => {
  const BRIDGE_VERSION = "4.1.0";
  const SITE_CONFIG = globalThis.MEMORIVE_SITE_PROFILES;
  if (!SITE_CONFIG) return;
  const bridgeIdentity = BRIDGE_VERSION + ':' + SITE_CONFIG.sha256 + ':record-v1';
  if (globalThis.__MEMORIVE_SESSION_LIBRARY_BRIDGE_VERSION__ === bridgeIdentity) return;
  if (globalThis.__MEMORIVE_OLD_BRIDGE_LISTENER__) chrome.runtime.onMessage.removeListener(globalThis.__MEMORIVE_OLD_BRIDGE_LISTENER__);
  // Versions before 4.1.0 listened for sync triggers in the address; an upgraded page drops that listener.
  if (globalThis.__MEMORIVE_OLD_HASH_LISTENER__) window.removeEventListener("hashchange", globalThis.__MEMORIVE_OLD_HASH_LISTENER__);
  globalThis.__MEMORIVE_STOP_RECORDING__?.();
  globalThis.__MEMORIVE_SESSION_LIBRARY_BRIDGE_VERSION__ = null;
  globalThis.__MEMORIVE_OLD_BRIDGE_LISTENER__ = null;
  globalThis.__MEMORIVE_OLD_HASH_LISTENER__ = null;
  // Drop any old listener before refusing a newly disabled site.
  try { globalThis.MEMORIVE_OFFICIAL_WEB.requireCaptureUrl(location.href); }
  catch (_) { return; }
  globalThis.__MEMORIVE_SESSION_LIBRARY_BRIDGE_VERSION__ = bridgeIdentity;

  // ChatGPT since 2026-11: the question bubble and the answer body carry these two markers; the whole message is
  // found from its role heading (roleHeadingRows) when the answer body has no marker.
  const currentPageDefinition = {
    user: [
      '[data-user-message-bubble="true"]',
      '[data-message-author-role="user"]', '[data-author-role="user"]',
      '[data-role="user"]', '[data-message-role="user"]',
      '[data-testid*="user-message" i]', '[data-test-id*="user-message" i]',
      'user-query', 'user-query-content', '.user-query',
      '[class*="user-message" i]', '[class*="question-item" i]',
      '[class*="questionitem" i]'
    ],
    assistant: [
      '[data-markdown-text-style="assistant-message"]',
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
  // Message markers of single sites that the shared lists above do not cover. DeepSeek, Gemini and Kimi: the
  // markers the extension has read them by since its first versions; the others as other open-source exporters
  // read them in 2026-11 (Covai-Labs/ACE, AiMsgExport). Check on the site when one of them stops reading.
  const CAPTURE_SITE_RULES = [
    { hosts: ["chat.deepseek.com"],
      user: ['[data-role="user"]', '[data-message-role="user"]', '[data-testid*="user-message"]',
             '.ds-user-message', '.ds-chat-user-message', '[class*="user-message"]', '.fbb737a4'],
      assistant: ['[data-role="assistant"]', '[data-message-role="assistant"]', '[data-testid*="assistant-message"]',
                  '.ds-assistant-message-main-content', '.ds-markdown', '.f9bf7997'] },
    { hosts: ["gemini.google.com"],
      user: ['user-query', 'user-query-content', '[data-test-id*="user"]', '.user-query', '.user-query-container',
             '.query-text'],
      assistant: ['model-response', '[data-test-id*="model-response"]', '.model-response', 'message-content',
                  '.markdown-main-panel'] },
    { hosts: ["kimi.com", "www.kimi.com", "kimi.moonshot.cn"],
      user: ['[data-role="user"]', '[data-message-role="user"]', '[data-testid="user-message"]',
             '[data-testid^="user-message-"]'],
      assistant: ['[data-role="assistant"]', '[data-message-role="assistant"]', '[data-testid="assistant-message"]',
                  '[data-testid^="assistant-message-"]', '[data-testid="model-message"]', '[data-testid^="model-message-"]'],
      generic: ['[class*="chat-message" i]', '[class*="MessageItem"]', '[class*="message-item" i]', '.segment',
                '[class*="ChatItem"]', '[class*="bubble" i]'] },
    { hosts: ["claude.ai"],
      user: ['[data-testid="user-message"]', '[data-author="user"]'],
      assistant: ['[data-testid="assistant-message"]', '[data-cds="AssistantMessage"]', '.font-claude-response',
                  '.font-claude-message', '[data-author="assistant"]'] },
    { hosts: ["copilot.microsoft.com"],
      user: ['[data-content="user-message"]', '.fai-UserMessage', '[data-testid="chatQuestion"]',
             '[class*="group/user-message"]'],
      assistant: ['[data-content="ai-message"]', '.fai-CopilotMessage', '[data-testid="copilot-message-div"]',
                  '[data-testid="ai-message"]', '[class*="group/ai-message"]'] },
    { hosts: ["www.perplexity.ai", "perplexity.ai"],
      user: ['[class~="group/query"]', '[class~="group/user-bubble"]'],
      assistant: ['[id^="markdown-content-"]'] },
    { hosts: ["chat.qwen.ai"],
      user: ['.qwen-chat-message-user', '.chat-user-message-container-wrapper'],
      assistant: ['.qwen-chat-message-assistant', '.chat-assistant-message-container-wrapper', '.qwen-markdown'] },
    { hosts: ["www.doubao.com", "doubao.com"],
      user: ['[data-testid="send_message"]'],
      assistant: ['[data-testid="receive_message"]'] }
  ];

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
    // An element without a box of its own (display: contents) is visible when its content is.
    if (style.display === "contents") return [...node.children].some(visible);
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
  const MESSAGE_BLOCK_TAGS = new Set([
    "ADDRESS", "ARTICLE", "ASIDE", "BLOCKQUOTE", "DD", "DETAILS", "DIV", "DL", "DT",
    "FIGCAPTION", "FIGURE", "FOOTER", "H1", "H2", "H3", "H4", "H5", "H6", "HEADER",
    "LI", "MAIN", "NAV", "OL", "P", "PRE", "SECTION", "SUMMARY", "TABLE", "TBODY",
    "TFOOT", "THEAD", "TR", "UL"
  ]);
  const MESSAGE_ACTION_TEXT = /^(?:复制|编辑|分享|重试|朗读|点赞|点踩|收藏|展开|收起|继续生成|停止生成|copy|edit|share|retry|read aloud|like|dislike|stop generating)$/i;
  const NUMERIC_CITATION_TEXT = /^(?:[-‐–—]?\d{1,3})(?:\s*[-,，]\s*\d{1,3})*$/;
  // Controls inside a message on one site, left out of its text: Claude's status line above an answer (what it
  // is thinking or which tool it used, with the button that opens it; class group/status, checked on claude.ai
  // 2026-11-22).
  const SITE_NOISE_CLASSES = { "claude.ai": ["group/status"] }[globalThis.location?.hostname] || [];
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
    if (SITE_NOISE_CLASSES.length &&
        String(element.className || "").split(/\s+/).some((name) => SITE_NOISE_CLASSES.includes(name))) return true;
    // ChatGPT's role heading ("You said:") inside a message is a label, not part of the message.
    if (!isRoot && element.getAttribute?.("data-conversation-role") != null) return true;
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
  // The text of one message as light Markdown (content_format MARKDOWN_V1 in the export): a link keeps its
  // address as [text](url), a code block keeps its lines and indentation in a fenced block with its language,
  // inline code sits in backticks. Code blocks are set aside while the spacing of the prose is tidied.
  const structuredMessageText = (node) => {
    if (!(node instanceof Element) || messageNoiseElement(node, true)) return "";
    const chunks = [], codeBlocks = [];
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
      if (current.tagName === "PRE") {
        const code = current.querySelector("code") || current;
        const language = /(?:^|\s)(?:language|lang)-([\w+#.-]+)/i.exec(`${code.className || ""} ${current.className || ""}`)?.[1] || "";
        appendBreak();
        chunks.push(`\ue000${codeBlocks.length}\ue001`);
        codeBlocks.push("```" + language + "\n" + String(code.textContent || "").replace(/\n+$/, "") + "\n```");
        appendBreak();
        return;
      }
      if (current.tagName === "CODE") {
        chunks.push("`" + String(current.textContent || "") + "`");
        return;
      }
      if (current.tagName === "A") {
        const href = current.getAttribute("href") || "", label = cleanText(current.textContent);
        if (/^https?:\/\//i.test(href) && label) {
          chunks.push(label === href ? href : `[${label}](${href})`);
          return;
        }
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
    return normalized.join("\n").replace(/\n{3,}/g, "\n\n").trim()
      .replace(/\ue000(\d+)\ue001/g, (_, index) => codeBlocks[Number(index)]);
  };
  const roleMarker = (node) => {
    let current = node;
    for (let depth = 0; depth < 4 && current; depth += 1) {
      const marker = [
        current.id,
        current.className,
        current.getAttribute?.("data-role"),
        current.getAttribute?.("data-message-role"),
        current.getAttribute?.("data-conversation-role"),
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
  // The element that holds one message (or one turn) on the supported sites. Text blocks of one message
  // (an answer split into several rendered parts) join only inside the same container; two containers are two
  // messages even when they are next to each other and have the same role. A row outside every container is
  // its own message. (.ds-message and data-virtual-list-item-key: DeepSeek's message rows; check on the site.)
  const MESSAGE_CONTAINER_SELECTOR = [
    "[data-message-id]", "[data-turn-id]", '[data-testid^="conversation-turn-"]',
    ".ds-message", "[data-virtual-list-item-key]"
  ].join(",");
  // ChatGPT since 2026-11 starts each message with a small heading for screen readers ("You said:" / "ChatGPT
  // said:") that carries data-conversation-role. The message is the largest element around that heading that
  // holds no other such heading, never above one turn (data-turn-key) or the page's main area. On
  // chatgpt.com (2026-11-22) the heading is an h4 and the message a div around it.
  const ROLE_HEADING = "[data-conversation-role]";
  // Shown on screen: the element itself, or, when it has no height of its own (ChatGPT's message wrappers measure
  // 0 px high, checked 2026-11-22), an element inside it. Content under a hidden ancestor has no size and does
  // not count.
  const showsContent = (node) => visible(node) || [...node.children].some(showsContent);
  const TURN = '[data-turn-key], article, [data-testid^="conversation-turn-"]';
  const roleHeadingRows = () => [...document.querySelectorAll(ROLE_HEADING)].flatMap((heading) => {
    const role = heading.getAttribute("data-conversation-role");
    if (!["user", "assistant"].includes(role)) return [];
    let section = heading;
    while (!section.matches(TURN) && section.parentElement &&
           !section.parentElement.matches("main, [role='main'], body") &&
           section.parentElement.querySelectorAll(ROLE_HEADING).length === 1) {
      section = section.parentElement;
    }
    if (section === heading) return [];
    const text = showsContent(section) ? structuredMessageText(section) : "";
    if (text) return [{ node: section, role, text }];
    // The heading is drawn but the text is not yet (ChatGPT draws an answer after its question): a hole that a
    // recording does not join across (recording.js windowParts). Only inside a turn that is on screen, so the
    // hidden earlier conversations ChatGPT keeps in the page leave none.
    const turn = section.closest(TURN) || section.parentElement;
    return turn && showsContent(turn) ? [{ node: heading, role, text: "", unread: true }] : [];
  });
  const messageContainer = (node) => node.closest(MESSAGE_CONTAINER_SELECTOR) || node;
  // Rows in page order, each with its container: joins the blocks of one message.
  const groupMessageBlocks = (rows) => {
    const grouped = [];
    for (const row of rows) {
      const prior = grouped[grouped.length - 1];
      if (prior && prior.role === row.role && prior.container === row.container) {
        if (!prior.text.includes(row.text)) {
          prior.text = row.text.includes(prior.text) ? row.text : `${prior.text}\n\n${row.text}`;
        }
        continue;
      }
      grouped.push({ ...row });
    }
    return grouped;
  };
  // How the last read found the messages: by the site's question and answer markers, or by the general layout
  // rules when the markers did not show both (the test button says which).
  let lastReadMethod = "PAGE_MARKERS";
  const messageRows = (definition, retainIdentity = false) => {
    const prepared = [];
    for (const [role, selectors] of [["user", definition.user], ["assistant", definition.assistant]]) {
      uniqueNodes(selectors).filter(visible).forEach((node) => {
        const text = structuredMessageText(node);
        if (text) prepared.push({ node, role, text });
      });
    }
    prepared.push(...roleHeadingRows());
    lastReadMethod = "PAGE_MARKERS";
    if (!prepared.some((row) => row.role === "user") || !prepared.some((row) => row.role === "assistant")) {
      prepared.push(...genericMessageRows(definition));
      lastReadMethod = "GENERIC_LAYOUT";
    }
    const nested = prepared.filter((row) => !prepared.some((other) => (
      other !== row && row.role === other.role && row.node.contains(other.node) &&
      other.text.length >= Math.floor(row.text.length * 0.72) && row.text.includes(other.text)
    )));
    // What is left inside another marker of the same role (an answer body inside a message element that also
    // holds more) is part of that message: keep the outer one, not a second copy of the text.
    const specific = nested.filter((row) => !nested.some((other) => (
      other !== row && other.role === row.role && other.node !== row.node && other.node.contains(row.node)
    )));
    specific.sort((left, right) => {
      if (left.node === right.node) return 0;
      return left.node.compareDocumentPosition(right.node) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1;
    });
    const seen = new Set();
    const grouped = groupMessageBlocks(specific.filter((row) => {
      if (seen.has(row.node)) return false;
      seen.add(row.node);
      return true;
    }).map((row) => ({ ...row, container: messageContainer(row.node) })));
    if (retainIdentity) {
      return grouped.map(({node,role,text,unread}) => {
        let key = "", order = null, cursor = node;
        for (let i=0;cursor && i<8;i++,cursor=cursor.parentElement) {
          if (!key) key = cursor.getAttribute("data-message-id") || cursor.getAttribute("data-turn-id") || "";
          const testId = cursor.getAttribute("data-testid") || "";
          const match = /^conversation-turn-(\d+)$/.exec(testId);
          if (match) { order=Number(match[1]); if (!key) key=testId; }
        }
        return {role,text,source_key:key ? role+":"+key : "",source_order:order,...(unread ? {unread} : {})};
      });
    }
    return grouped.map(({ role, text }) => ({ role, text }));
  };
  const captureCurrentVisiblePage = (recording = false) => {
    if (location.protocol !== "https:") throw new Error("CURRENT_PAGE_HTTPS_REQUIRED");
    // The site's own markers (CAPTURE_SITE_RULES) join the shared lists.
    const site = CAPTURE_SITE_RULES.find((row) => row.hosts.includes(location.hostname)) || {};
    const definition = Object.fromEntries(['user','assistant','generic'].map(key=>[
      key,[...new Set([...(currentPageDefinition[key]||[]),...(site[key]||[])])]
    ]));
    // A recording keeps the holes where the page has not drawn a message yet; a single read leaves them out.
    const rows = messageRows(definition, recording).filter(row=>row.text.length > 0 || (recording && row.unread));
    // A recording samples any window, also one long reply alone or none while the page loads; the saved record
    // must still hold both roles (recording.js). A single read must show both now.
    if (
      !recording && (
        !rows.some((row) => row.role === "user") ||
        !rows.some((row) => row.role === "assistant")
      )
    ) {
      if (!rows.length) throw new Error("CURRENT_PAGE_CONVERSATION_NOT_FOUND");
      const users = rows.filter((row) => row.role === "user").length;
      throw new Error(`CURRENT_PAGE_ROLES_UNCLEAR_U${users}_A${rows.length - users}`);
    }
    const safeUrl = new URL(location.href);
    safeUrl.username = "";
    safeUrl.password = "";
    safeUrl.search = "";
    safeUrl.hash = "";
    const firstUser = rows.find((row) => row.role === "user" && row.text)?.text || "";
    const documentTitle = cleanText(document.title)
      .replace(/\s+[|·]\s+[^|·]{1,80}$/u, "")
      .slice(0, 160);
    const title = documentTitle || firstUser.slice(0, 96) || "当前网页会话";
    // A conversation address (…/c/<id>, …/chat/<id>, DeepSeek's …/s/<id>) is the conversation's identity, so a
    // page read and a recording of one conversation are one conversation in Memorive; the desktop merges the same
    // addresses (archive/store.py _capture_identity). Other pages: the title and the first question.
    const conversationPath = /\/(?:c|chat|app|conversation|session|s)\/[^/]+\/?$/.test(safeUrl.pathname);
    const stableSeed = conversationPath ? safeUrl.href.replace(/\/$/, "") :
      `${safeUrl.hostname}|${safeUrl.pathname}|${title}|${firstUser.slice(0, 160)}`;
    const observedAt = new Date().toISOString();
    return {
      provider_id: "universal",
      provider_session_id: `universal-${simpleHash(stableSeed)}`,
      title,
      read_method: lastReadMethod,
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
        ...(recording ? {source_key:row.source_key,source_order:row.source_order,...(row.unread ? {unread:true} : {})} : {})
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
  const onRecordingScroll = () => recorder.sample();
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
        recorder.sample(true);
        return {ok:true,state:await recorder.stop(message.reason || "USER_STOP")};
      }
      if (message?.type === "MEMORIVE_CAPTURE_CURRENT_PAGE_GENERIC") {
        globalThis.MEMORIVE_OFFICIAL_WEB.requireCaptureUrl(location.href);
        if (humanVerificationDetected()) throw new Error("PROVIDER_HUMAN_VERIFICATION_REQUIRED");
        return { ok: true, conversation: captureCurrentVisiblePage() };
      }
      return null;
    };
    if (!message?.type?.startsWith("MEMORIVE_")) return false;
    execute().then((response) => sendResponse(response)).catch((error) => {
      sendResponse({ ok: false, error_code: safeErrorCode(error) });
    });
    return true;
  };
  chrome.runtime.onMessage.addListener(bridgeListener);
  globalThis.__MEMORIVE_OLD_BRIDGE_LISTENER__ = bridgeListener;
})();
