"use strict";
globalThis.MEMORIVE_SITE_PROFILES = Object.freeze({"sites":[{"slot":1,"name":"DeepSeek","url":"https://chat.deepseek.com/","adapter":"deepseek","enabled":true,"selectors":{"path_prefix":"/chat/","history":"nav a[href], aside a[href]","user":"[data-message-author-role=\"user\"], [data-role=\"user\"]","assistant":"[data-message-author-role=\"assistant\"], [data-role=\"assistant\"]"},"provider_id":"deepseek","hosts":["chat.deepseek.com"]},{"slot":2,"name":"Gemini","url":"https://gemini.google.com/app","adapter":"gemini","enabled":true,"selectors":{"path_prefix":"/chat/","history":"nav a[href], aside a[href]","user":"[data-message-author-role=\"user\"], [data-role=\"user\"]","assistant":"[data-message-author-role=\"assistant\"], [data-role=\"assistant\"]"},"provider_id":"gemini","hosts":["gemini.google.com"]},{"slot":3,"name":"Kimi","url":"https://www.kimi.com/","adapter":"kimi","enabled":true,"selectors":{"path_prefix":"/chat/","history":"nav a[href], aside a[href]","user":"[data-message-author-role=\"user\"], [data-role=\"user\"]","assistant":"[data-message-author-role=\"assistant\"], [data-role=\"assistant\"]"},"provider_id":"kimi","hosts":["kimi.com","www.kimi.com","kimi.moonshot.cn"]}],"sha256":"27E54E5723831CAACC743C8AFA664F183A8D9EC12D35EDF3F0391206814F950D","official_web_revision":"Desktop_OFFICIAL_CHAT_ORIGINS_20260914_V2"});
(() => { const officialHosts = Object.freeze(["chat.deepseek.com", "chat.qwen.ai", "chatgpt.com", "claude.ai", "gemini.google.com", "grok.com", "kimi.com", "kimi.moonshot.cn", "www.kimi.com"]);

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
  const requireAllowedUrl = raw => {
    const url = requireCaptureUrl(raw);
    if (!globalThis.MEMORIVE_SITE_PROFILES.sites.some(site => site.enabled &&
        officialHosts.includes(new URL(site.url).hostname) && site.hosts.includes(url.hostname)))
      throw new Error("BROWSER_SITE_NOT_VERIFIED_OFFICIAL");
    return url;
  };
  globalThis.MEMORIVE_OFFICIAL_WEB = Object.freeze({requireAllowedUrl,requireCaptureUrl});
})();
