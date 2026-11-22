"use strict";
globalThis.MEMORIVE_SITE_PROFILES = Object.freeze({"hosts":["chat.deepseek.com","chat.mistral.ai","chat.qwen.ai","chatglm.cn","chatgpt.com","claude.ai","copilot.microsoft.com","doubao.com","gemini.google.com","grok.com","kimi.com","kimi.moonshot.cn","perplexity.ai","poe.com","qianwen.com","tongyi.aliyun.com","www.doubao.com","www.kimi.com","www.perplexity.ai","www.qianwen.com","yiyan.baidu.com","yuanbao.tencent.com"],"sha256":"1C9877A5F1AAC67670D910FD5DC86C35CA95B7ED74E300EF0A3065FE08F3DF0D","official_web_revision":"Desktop_OFFICIAL_CHAT_ORIGINS_20261122_V3"});
(() => {
  const officialHosts = globalThis.MEMORIVE_SITE_PROFILES.hosts;
  const requireCaptureUrl = raw => {
    if (typeof raw !== "string" || !/^https:\/\/[^/]/i.test(raw) || /[\u0000-\u0020\u007f\\]/.test(raw)) throw new Error("BROWSER_SITE_NOT_VERIFIED_OFFICIAL");
    let url; try { url = new URL(raw); } catch (_) { throw new Error("BROWSER_SITE_NOT_VERIFIED_OFFICIAL"); }
    const authority = raw.split("/")[2] || "";
    if (url.protocol !== "https:" || url.username || url.password || url.port ||
        /[%\u0080-\uffff]/.test(authority) || url.hostname.endsWith(".") ||
        !officialHosts.includes(url.hostname)) {
      throw new Error("BROWSER_SITE_NOT_VERIFIED_OFFICIAL");
    }
    return url;
  };
  globalThis.MEMORIVE_OFFICIAL_WEB = Object.freeze({requireCaptureUrl});
})();
