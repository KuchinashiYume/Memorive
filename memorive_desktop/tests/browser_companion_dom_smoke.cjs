"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

const attemptRoot = path.resolve(__dirname, "..");
const contentScript = fs.readFileSync(
  path.join(
    attemptRoot,
    "source",
    "memorive_browser_companion",
    "extension",
    "content.js"
  ),
  "utf8"
);

const providers = [
  {
    provider: "deepseek",
    indexUrl: "https://chat.deepseek.com/home",
    recentLabel: "7 天内",
    sessions: [
      {
        id: "ds-calories",
        href: "/a/chat/s/ds-calories",
        title: "440大卡食物对照",
        at: "2026-08-27T03:36:00Z",
      },
      {
        id: "ds-airport",
        href: "/a/chat/s/ds-airport",
        title: "川沙到浦东机场首班车",
        at: "2026-08-26T09:20:00Z",
      },
    ],
    oldHref: "/a/chat/s/ds-redacted-older-than-30-days",
    undatedHref: "/a/chat/s/ds-redacted-undated-history",
    messages: (id) => `
      <div data-role="user"><div>${id} user deep<span>s</span>eek full text</div><button>复制</button></div>
      <div data-role="assistant">
        <div class="ds-assistant-message-main-content">
          <div class="ds-markdown">
            <p>${id} assistant full text，正文中的 inline <span>span</span> 不应被拆行。引用不应混入正文<a href="https://example.com/source-4">-4</a><a href="https://example.com/source-7">-7</a>：</p>
            <ul><li><div>第一项完整内容</div></li><li><div>第二项完整内容</div></li></ul>
            <table><thead><tr><th>配置方案</th><th>核心硬件要求</th><th>性能与备注</th></tr></thead><tbody><tr><td>Mac</td><td>192GB</td><td>完整回答</td></tr></tbody></table>
            <div class="citation-reference"><span style="display:block">1</span><span style="display:block">-</span><span style="display:block">12</span></div>
            ${id === "ds-calories" ? `<p id="late-tail">等待完整尾段</p><button id="generating" aria-label="停止生成">停止生成</button>
              <script>setTimeout(() => { document.getElementById("late-tail").textContent = "完整尾段已经加载，不能提前截断。"; document.getElementById("generating")?.remove(); }, 3500);</script>` : ""}
          </div><button>重试</button>
        </div>
      </div>`,
  },
  {
    provider: "gemini",
    indexUrl: "https://gemini.google.com/app",
    recentLabel: "過去 7 日間",
    sessions: [
      {
        id: "gemini-one",
        href: "/app/c/gemini-one",
        title: "Gemini stable conversation one",
        at: "2026-08-27T05:10:00Z",
      },
      {
        id: "gemini-two",
        href: "/app/gemini-two",
        title: "Gemini stable conversation two",
        at: "2026-08-25T08:40:00Z",
      },
    ],
    oldHref: "/app/c/gemini-redacted-older-than-30-days",
    undatedHref: "/app/c/gemini-redacted-undated-history",
    messages: (id) => `
      <user-query><user-query-content><span class="cdk-visually-hidden">你说</span>${id} user full text<span class="sr-only">${id} user full text</span></user-query-content><button>编辑</button></user-query>
      <model-response>
        <div class="model-response"><span class="cdk-visually-hidden">Gemini 说</span>${id} assistant full text</div><button>分享</button>
      </model-response>`,
  },
  {
    provider: "kimi",
    indexUrl: "https://www.kimi.com/",
    recentLabel: "Last 7 days",
    sessions: [
      {
        id: "kimi-one",
        href: "/chat/kimi-one",
        title: "Kimi stable conversation one",
        at: "2026-08-27T06:25:00Z",
      },
      {
        id: "kimi-two",
        href: "/chat/kimi-two",
        title: "Kimi stable conversation two",
        at: "2026-08-24T11:00:00Z",
      },
    ],
    oldHref: "/chat/kimi-redacted-older-than-30-days",
    undatedHref: "/chat/kimi-redacted-undated-history",
    messages: (id) => `
      <div class="ChatItem user-bubble"><div class="segment message-content"><span>${id} user full text</span></div><button>复制</button></div>
      <div class="ChatItem assistant-bubble">
        <div class="segment assistant-message-meta">https://api.moonshot.ai/v1</div>
        <div class="segment model-message-meta">kimi-k3</div>
        <div class="segment citation-reference">引用</div>
        <div class="markdown-body"><p>${id} assistant full text</p><p>引用</p><p>这是需要保留的完整回答正文。</p></div><button>重试</button>
      </div>`,
  },
];

const shell = (body) => `<!doctype html>
  <html>
    <head>
      <meta charset="utf-8">
      <style>
        html, body { margin: 0; min-height: 900px; }
        nav, main { display: block; min-height: 500px; padding: 20px; }
        nav a, main > * { display: block; min-width: 240px; min-height: 60px; margin: 12px; }
      </style>
    </head>
    <body>${body}</body>
  </html>`;

const historyBody = (profile) => shell(`
  <nav>
    <div><a href="${profile.undatedHref}"><span class="conversation-title">redacted undated historical session</span></a></div>
    <h2>今天</h2>
    <div>
      <a href="${profile.sessions[0].href}" aria-label="打开会话菜单">
        <span class="conversation-title">${profile.sessions[0].title}</span><button>更多</button>
      </a>
      <time datetime="${profile.sessions[0].at}">${profile.sessions[0].at}</time>
    </div>
    <h2>${profile.recentLabel}</h2>
    ${Array.from({ length: 8 }, (_, index) => `<span aria-hidden="true">separator-${index}</span>`).join("")}
    <div>
      <a href="${profile.sessions[1].href}" aria-label="Open conversation menu">
        <span data-testid="conversation-title">${profile.sessions[1].title}</span><button>More</button>
      </a>
    </div>
    <h2>更早</h2>
    <div><a href="${profile.oldHref}"><span class="conversation-title">redacted old session</span></a></div>
  </nav>`);

const conversationBody = (profile, session) => shell(`
  <main data-session-id="${session.id}">
    ${session.at ? `<time datetime="${session.at}">${session.at}</time>` : ""}
    ${profile.messages(session.id)}
  </main>`);

const attachContentBridge = async (page) => {
  await page.addInitScript(() => {
    globalThis.__captureListener = null;
    globalThis.chrome = {
      runtime: {
        onMessage: {
          addListener(listener) {
            globalThis.__captureListener = listener;
          },
        },
      },
    };
  });
};

const send = async (page, message) => page.evaluate((payload) => new Promise((resolve) => {
  const keepPort = globalThis.__captureListener(payload, {}, resolve);
  if (keepPort !== true) resolve({ ok: false, error_code: "MESSAGE_PORT_NOT_RETAINED" });
}), message);

(async () => {
  const browser = await chromium.launch({ channel: "msedge", headless: true });
  const receipts = [];
  try {
    for (const profile of providers) {
      const context = await browser.newContext({ javaScriptEnabled: true });
      const indexPage = await context.newPage();
      await indexPage.route("**/*", (route) => route.fulfill({
        status: 200,
        contentType: "text/html; charset=utf-8",
        body: historyBody(profile),
      }));
      await attachContentBridge(indexPage);
      await indexPage.goto(profile.indexUrl, { waitUntil: "domcontentloaded" });
      await indexPage.addScriptTag({ content: contentScript });

      const indexed = await send(indexPage, {
        type: "MEMORIVE_INDEX_RECENT_14_DAYS_OR_30_CONVERSATIONS",
        safety_ceiling: 50,
      });
      assert.equal(indexed.ok, true, JSON.stringify(indexed));
      assert.equal(indexed.provider_id, profile.provider);
      assert.equal(indexed.sessions.length, 4);
      assert.deepEqual(
        indexed.sessions.map((row) => row.provider_session_id),
        [
          ...profile.sessions.map((row) => row.id),
          profile.undatedHref.split("/").filter(Boolean).at(-1),
          profile.oldHref.split("/").filter(Boolean).at(-1),
        ]
      );
      assert.deepEqual(
        indexed.sessions.map((row) => row.selection_basis),
        [
          "RECENT_14_DAYS",
          "RECENT_14_DAYS",
          "RECENT_30_CONVERSATIONS",
          "RECENT_30_CONVERSATIONS",
        ]
      );
      assert.equal(
        Date.parse(indexed.sessions[0].conversation_at),
        Date.parse(profile.sessions[0].at)
      );
      assert.equal(indexed.sessions[1].conversation_at, "UNKNOWN", JSON.stringify(indexed.sessions));
      assert.equal(indexed.sessions[1].recency_bucket, "LAST_7_DAYS", JSON.stringify(indexed.sessions));
      assert.deepEqual(
        indexed.sessions.slice(2).map((row) => row.source_recency_rank),
        [0, 3]
      );

      const conversations = [];
      for (const descriptor of indexed.sessions) {
        const expected = profile.sessions.find((row) => row.id === descriptor.provider_session_id) || {
          id: descriptor.provider_session_id,
          at: descriptor.provider_session_id.includes("older-than-30-days")
            ? "2025-01-15T08:00:00Z"
            : null,
        };
        const capturePage = await context.newPage();
        await capturePage.route("**/*", (route) => route.fulfill({
          status: 200,
          contentType: "text/html; charset=utf-8",
          body: conversationBody(profile, expected),
        }));
        await attachContentBridge(capturePage);
        await capturePage.goto(descriptor.source_url, { waitUntil: "domcontentloaded" });
        await capturePage.addScriptTag({ content: contentScript });
        const captured = await send(capturePage, {
          type: "MEMORIVE_CAPTURE_INDEXED_CONVERSATION",
          descriptor,
        });
        assert.equal(captured.ok, true, JSON.stringify(captured));
        assert.equal(captured.conversation.provider_session_id, descriptor.provider_session_id);
        assert.equal(captured.conversation.capture_method, "BROWSER_COMPANION_BACKGROUND_TAB_V4");
        assert.equal(captured.conversation.capture_status, "COMPLETE");
        assert.equal(captured.conversation.source_truncated, false);
        assert.equal(captured.conversation.top_boundary_reached, true);
        assert.equal(captured.conversation.bottom_boundary_reached, true);
        assert.deepEqual(
          captured.conversation.messages.map((row) => row.role),
          ["user", "assistant"]
        );
        assert.equal(
          captured.conversation.messages.every((row) => row.text.includes(descriptor.provider_session_id)),
          true
        );
        if (profile.provider === "deepseek") {
          assert.equal(captured.conversation.messages.length, 2, JSON.stringify(captured));
          assert.equal(captured.conversation.messages[0].text.includes("deepseek"), true, JSON.stringify(captured));
          assert.equal(captured.conversation.messages[1].text.includes("\n1\n-\n12"), false, JSON.stringify(captured));
          assert.equal(captured.conversation.messages[1].text.includes("citation-reference"), false, JSON.stringify(captured));
          assert.equal(captured.conversation.messages[1].text.includes("-4-7"), false, JSON.stringify(captured));
          assert.equal(captured.conversation.messages[1].text.includes("\n•\n"), false, JSON.stringify(captured));
          assert.equal(captured.conversation.messages[1].text.includes("• 第一项完整内容"), true, JSON.stringify(captured));
          assert.equal(
            captured.conversation.messages[1].text.includes("配置方案\t核心硬件要求\t性能与备注"),
            true,
            JSON.stringify(captured)
          );
          if (descriptor.provider_session_id === "ds-calories") {
            assert.equal(
              captured.conversation.messages[1].text.includes("完整尾段已经加载，不能提前截断。"),
              true,
              JSON.stringify(captured)
            );
          }
        }
        if (profile.provider === "gemini") {
          assert.equal(
            captured.conversation.messages[0].text,
            `${descriptor.provider_session_id} user full text`,
            JSON.stringify(captured)
          );
          assert.equal(
            captured.conversation.messages[1].text,
            `${descriptor.provider_session_id} assistant full text`,
            JSON.stringify(captured)
          );
        }
        if (profile.provider === "kimi") {
          assert.deepEqual(
            captured.conversation.messages.map((row) => row.role),
            ["user", "assistant"],
            JSON.stringify(captured)
          );
          assert.equal(
            captured.conversation.messages[1].text.includes("这是需要保留的完整回答正文。"),
            true,
            JSON.stringify(captured)
          );
          assert.equal(
            captured.conversation.messages.some((row) => /api\.moonshot|^kimi-k3$/m.test(row.text)),
            false,
            JSON.stringify(captured)
          );
          assert.equal(
            (captured.conversation.messages[1].text.match(/^引用$/gm) || []).length,
            1,
            JSON.stringify(captured)
          );
        }
        conversations.push(captured.conversation);
        await capturePage.close();
      }

      const oldId = profile.oldHref.split("/").filter(Boolean).at(-1);
      const oldPage = await context.newPage();
      await oldPage.route("**/*", (route) => route.fulfill({
        status: 200,
        contentType: "text/html; charset=utf-8",
        body: conversationBody(profile, { id: oldId, at: "2025-01-15T08:00:00Z" }),
      }));
      await attachContentBridge(oldPage);
      await oldPage.goto(new URL(profile.oldHref, profile.indexUrl).href, { waitUntil: "domcontentloaded" });
      await oldPage.addScriptTag({ content: contentScript });
      const rejectedOld = await send(oldPage, {
        type: "MEMORIVE_CAPTURE_INDEXED_CONVERSATION",
        descriptor: {
          provider_session_id: oldId,
          title: "redacted old session",
          conversation_at: "UNKNOWN",
          recency_bucket: "LAST_30_DAYS",
          source_recency_rank: 99,
          source_url: new URL(profile.oldHref, profile.indexUrl).href,
        },
      });
      assert.equal(rejectedOld.ok, false, JSON.stringify(rejectedOld));
      assert.equal(rejectedOld.error_code, "SESSION_OUTSIDE_RECENT_14_DAYS", JSON.stringify(rejectedOld));
      await oldPage.close();

      assert.equal(indexPage.url(), profile.indexUrl);
      receipts.push({
        provider: profile.provider,
        indexed_count: indexed.sessions.length,
        captured_count: conversations.length,
        message_count: conversations.reduce((total, row) => total + row.messages.length, 0),
        current_page_fallback: false,
        status: "PASS",
      });
      await context.close();
    }

    const verificationContext = await browser.newContext();
    const verificationPage = await verificationContext.newPage();
    await verificationPage.route("**/*", (route) => route.fulfill({
      status: 200,
      contentType: "text/html; charset=utf-8",
      body: shell('<div class="g-recaptcha" data-sitekey="redacted"></div><p>进行人机身份验证</p>'),
    }));
    await attachContentBridge(verificationPage);
    await verificationPage.goto("https://gemini.google.com/app/redacted-verification", { waitUntil: "domcontentloaded" });
    await verificationPage.addScriptTag({ content: contentScript });
    const verificationBlocked = await send(verificationPage, {
      type: "MEMORIVE_INDEX_RECENT_14_DAYS_OR_30_CONVERSATIONS",
      safety_ceiling: 50,
    });
    assert.equal(verificationBlocked.ok, false, JSON.stringify(verificationBlocked));
    assert.equal(
      verificationBlocked.error_code,
      "PROVIDER_HUMAN_VERIFICATION_REQUIRED",
      JSON.stringify(verificationBlocked)
    );
    assert.equal(verificationBlocked.diagnostic, null);
    await verificationContext.close();

    const triggerContext = await browser.newContext();
    const triggerPage = await triggerContext.newPage();
    await triggerPage.route("**/*", (route) => route.fulfill({
      status: 200,
      contentType: "text/html; charset=utf-8",
      body: shell("<main>approved local trigger fixture</main>"),
    }));
    await triggerPage.addInitScript(() => {
      globalThis.__captureListener = null;
      globalThis.__runtimeMessages = [];
      globalThis.chrome = {
        runtime: {
          sendMessage(message) {
            globalThis.__runtimeMessages.push(message);
            return Promise.resolve({ ok: true, extension_version: "3.5.0" });
          },
          onMessage: {
            addListener(listener) {
              globalThis.__captureListener = listener;
            },
          },
        },
      };
    });
    await triggerPage.goto(
      "https://gemini.google.com/app#memorive-browser-session-preflight-2-v5-deepseek-only",
      { waitUntil: "domcontentloaded" }
    );
    await triggerPage.addScriptTag({ content: contentScript });
    const triggerEvidence = await triggerPage.evaluate(() => ({
      hash: location.hash,
      messages: globalThis.__runtimeMessages,
    }));
    assert.equal(triggerEvidence.hash, "");
    assert.deepEqual(triggerEvidence.messages, [{
      type: "MEMORIVE_SYNC_ALL_LIBRARIES_FROM_APPROVED_FRAGMENT",
      bridge_version: "3.5.0",
      sync_mode: "PREFLIGHT",
      providers: ["deepseek"],
      cursor_token: "",
    }]);
    const incrementalCursor = Buffer.from(JSON.stringify({
      schema_version: "MEMORIVE_BROWSER_SESSION_CURSOR_V1",
      providers: {
        deepseek: { known_session_hashes: [], overlap_fingerprints: {} },
      },
    }), "utf8").toString("base64url");
    await triggerPage.evaluate((cursor) => {
      location.hash = `#memorive-browser-session-incremental-v2:${cursor}`;
    }, incrementalCursor);
    await triggerPage.waitForTimeout(100);
    const incrementalTriggerEvidence = await triggerPage.evaluate(() => ({
      hash: location.hash,
      messages: globalThis.__runtimeMessages,
    }));
    assert.equal(incrementalTriggerEvidence.hash, "");
    assert.deepEqual(incrementalTriggerEvidence.messages[1], {
      type: "MEMORIVE_SYNC_ALL_LIBRARIES_FROM_APPROVED_FRAGMENT",
      bridge_version: "3.5.0",
      sync_mode: "INCREMENTAL",
      providers: ["deepseek"],
      cursor_token: incrementalCursor,
    });
    await triggerContext.close();

    const reloadContext = await browser.newContext();
    const reloadPage = await reloadContext.newPage();
    await reloadPage.route("**/*", (route) => route.fulfill({
      status: 200,
      contentType: "text/html; charset=utf-8",
      body: shell("<main>extension reload fixture</main>"),
    }));
    await reloadPage.addInitScript(() => {
      globalThis.__reloadCalls = 0;
      globalThis.__runtimeMessages = [];
      globalThis.chrome = {
        runtime: {
          reload() { globalThis.__reloadCalls += 1; },
          sendMessage(message) {
            globalThis.__runtimeMessages.push(message);
            if (globalThis.__runtimeMessages.length === 1) {
              return Promise.resolve({
                ok: false,
                error_code: "IDENTITY_RELOAD_VERSION_MISMATCH",
                extension_version: "3.4.3",
              });
            }
            return Promise.resolve({ ok: true, extension_version: "3.4.3", reload_scheduled: true });
          },
          onMessage: { addListener() {} },
        },
      };
    });
    // Edge normally reuses the already-open DeepSeek tab.  The reload marker
    // therefore arrives as a hashchange instead of a fresh document load.
    await reloadPage.goto("https://chat.deepseek.com/", { waitUntil: "domcontentloaded" });
    await reloadPage.addScriptTag({ content: contentScript });
    await reloadPage.evaluate(() => {
      location.hash = "#memorive-browser-companion-reload-v1";
    });
    await reloadPage.waitForTimeout(100);
    const reloadEvidence = await reloadPage.evaluate(() => ({
      hash: location.hash,
      reloadCalls: globalThis.__reloadCalls,
      messages: globalThis.__runtimeMessages,
    }));
    assert.equal(reloadEvidence.hash, "");
    assert.equal(reloadEvidence.reloadCalls, 0);
    assert.deepEqual(reloadEvidence.messages, [
      {
        type: "MEMORIVE_BRIDGE_IDENTITY_RELOAD_REQUEST",
        bridge_version: "3.5.0",
      },
      {
        type: "MEMORIVE_BRIDGE_IDENTITY_RELOAD_REQUEST",
        bridge_version: "3.4.3",
      },
    ]);
    await reloadContext.close();

    const output = {
      schema_version: "DesktopBrowserCompanionDomSmoke-v4",
      receipts,
      approved_https_fragment_trigger: true,
      incremental_fragment_trigger: true,
      extension_self_reload_trigger: true,
      status: "PASS",
    };
    if (process.env.Desktop_DOM_SMOKE_RECEIPT) {
      fs.writeFileSync(
        process.env.Desktop_DOM_SMOKE_RECEIPT,
        `${JSON.stringify(output, null, 2)}\n`,
        { encoding: "utf8", flag: "wx" }
      );
    }
    process.stdout.write(JSON.stringify(output));
  } finally {
    await browser.close();
  }
})().catch((error) => {
  process.stderr.write(String(error?.stack || error));
  process.exitCode = 1;
});
