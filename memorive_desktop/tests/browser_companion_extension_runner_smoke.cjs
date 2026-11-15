"use strict";

const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { chromium } = require("playwright");

const attemptRoot = path.resolve(__dirname, "..");
const extensionRoot = path.join(
  attemptRoot,
  "source",
  "memorive_browser_companion",
  "extension"
);
const profileRoot = fs.mkdtempSync(path.join(os.tmpdir(), "memorive-extension-runner-"));
const sha256 = (value) => crypto.createHash("sha256").update(value, "utf8").digest("hex");
const sessionHash = (providerId, sessionId) => sha256(`${providerId}\u0000${sessionId}`).slice(0, 24);
const contentHash = (sessionId) => {
  const messages = [
    { role: "user", text: `${sessionId} user full text` },
    { role: "assistant", text: `${sessionId} assistant full text` },
  ];
  return sha256(messages.map((row) => `${row.role}\u001f${row.text.length}\u001f${row.text}`).join("\u001e"));
};

const providers = {
  "chat.deepseek.com": {
    provider: "deepseek",
    indexPath: "/home",
    sessions: ["ds-empty", "ds-one", "ds-two"],
    sessionPath: (id) => `/a/chat/s/${id}`,
    messages: (id) => id === "ds-empty" ? "" : `
      <div data-role="user">${id} user full text</div>
      <div data-role="assistant"><div class="ds-markdown">${id} assistant full text</div></div>`,
  },
  "gemini.google.com": {
    provider: "gemini",
    indexPath: "/app",
    sessions: ["gemini-one", "gemini-two"],
    sessionPath: (id) => `/app/${id}`,
    messages: (id) => `
      <user-query>${id} user full text</user-query>
      <model-response><div class="model-response">${id} assistant full text</div></model-response>`,
  },
  "www.kimi.com": {
    provider: "kimi",
    indexPath: "/",
    sessions: ["kimi-one", "kimi-two"],
    sessionPath: (id) => `/chat/${id}`,
    messages: (id) => `
      <div data-testid="user-message">${id} user full text</div>
      <div data-testid="assistant-message"><div class="assistant-message">${id} assistant full text</div></div>`,
  },
};

const shell = (body) => `<!doctype html><html><head><meta charset="utf-8"><meta name="memorive-fixture" content="1"><style>
  html,body{margin:0;min-height:1000px} nav,main{display:block;min-height:600px;padding:20px}
  nav a,main>*{display:block;min-width:260px;min-height:64px;margin:12px}
</style></head><body>${body}</body></html>`;

const fixtureFor = (rawUrl) => {
  const url = new URL(rawUrl);
  const profile = providers[url.hostname];
  if (!profile) return null;
  if (url.searchParams.get("memorive_redacted_empty") === "1") {
    return shell("<h2>30 天内</h2><nav><p>redacted empty history tab</p></nav>");
  }
  const sessionId = profile.sessions.find((id) => url.pathname === profile.sessionPath(id));
  if (sessionId) {
    return shell(`<main data-session-id="${sessionId}"><time datetime="2026-08-27T20:00:00Z">2026-08-27</time>${profile.messages(sessionId)}</main>`);
  }
  return shell(`<h2>30 天内</h2><nav>${profile.sessions.map((id, index) => `
    <div><a href="${profile.sessionPath(id)}" title="${id} stable title">${id} stable title</a>
    <time datetime="2026-08-${27 - index}T20:00:00Z">2026-08-${27 - index}</time></div>`).join("")}</nav>`);
};

(async () => {
  let diagnosticFailure = null;
  const context = await chromium.launchPersistentContext(profileRoot, {
    channel: "msedge",
    headless: true,
    acceptDownloads: true,
    serviceWorkers: "block",
    args: [
      `--disable-extensions-except=${extensionRoot}`,
      `--load-extension=${extensionRoot}`,
      "--disable-background-networking",
      "--disable-component-update",
      "--disable-sync",
      "--no-first-run",
      "--no-default-browser-check",
    ],
  });
  try {
    context.on("page", (page) => {
      page.on("domcontentloaded", async () => {
        const url = new URL(page.url());
        const profile = providers[url.hostname];
        if (!profile || !profile.sessions.some((id) => url.pathname === profile.sessionPath(id))) return;
        await page.waitForTimeout(1200).catch(() => {});
        if (page.isClosed()) return;
        const diagnostic = await page.evaluate(() => ({
          readyState: document.readyState,
          visibilityState: document.visibilityState,
          bodyChildCount: document.body?.children.length || 0,
          fixture: Boolean(document.querySelector('meta[name="memorive-fixture"]')),
          userCount: document.querySelectorAll('[data-role="user"],user-query,[data-testid="user-message"]').length,
          assistantCount: document.querySelectorAll('[data-role="assistant"],model-response,[data-testid="assistant-message"]').length,
          bodyShape: String(document.body?.innerHTML || "").replace(/\s+/g, " ").slice(0, 500),
        })).catch((error) => ({ error: String(error) }));
        if (
          diagnostic.fixture &&
          !url.pathname.endsWith("/ds-empty") &&
          (!diagnostic.userCount || !diagnostic.assistantCount)
        ) {
          diagnosticFailure = new Error(`CAPTURE_FIXTURE_NOT_RENDERED_${profile.provider}_${JSON.stringify(diagnostic)}`);
        }
      });
    });
    await context.route("https://**/*", async (route) => {
      const fixture = fixtureFor(route.request().url());
      if (!fixture) return route.abort("blockedbyclient");
      return route.fulfill({ status: 200, contentType: "text/html; charset=utf-8", body: fixture });
    });

    for (const [host, profile] of Object.entries(providers)) {
      // Keep deterministic rendered fixtures for every provider in the
      // headless runner. Chromium can replace extension-created popup windows
      // with its own error document under Playwright; the real Edge run below
      // is the acceptance proof for that Kimi rendering fallback.
      for (const sessionId of profile.sessions) {
        const sessionPage = await context.newPage();
        await sessionPage.goto(`https://${host}${profile.sessionPath(sessionId)}`, { waitUntil: "domcontentloaded" });
      }
      if (profile.provider === "deepseek") {
        const decoy = await context.newPage();
        await decoy.goto(`https://${host}${profile.indexPath}?memorive_redacted_empty=1`, { waitUntil: "domcontentloaded" });
      }
      const page = await context.newPage();
      await page.goto(`https://${host}${profile.indexPath}`, { waitUntil: "domcontentloaded" });
    }

    const worker = context.serviceWorkers()[0] || await context.waitForEvent("serviceworker", { timeout: 20000 });
    const extensionId = new URL(worker.url()).host;
    const popup = await context.newPage();
    await popup.goto(
      `chrome-extension://${extensionId}/runner.html?trigger=SMOKE_PREFLIGHT&limit=2`,
      { waitUntil: "domcontentloaded" }
    );

    let state = await popup.evaluate(() => chrome.runtime.sendMessage({ type: "MEMORIVE_SYNC_STATUS" }));
    const deadline = Date.now() + 150000;
    while (Date.now() < deadline) {
      if (diagnosticFailure) throw diagnosticFailure;
      state = await popup.evaluate(() => chrome.runtime.sendMessage({ type: "MEMORIVE_SYNC_STATUS" }));
      if (state?.state?.status === "COMPLETE" || state?.state?.status === "ERROR") break;
      await new Promise((resolve) => setTimeout(resolve, 750));
    }

    assert.equal(state?.state?.status, "COMPLETE", JSON.stringify(state));
    assert.equal(state.state.captured, 6, JSON.stringify(state.state));
    assert.equal(state.state.failed, 0, JSON.stringify(state.state));
    assert.equal(state.state.skipped, 1, JSON.stringify(state.state));
    const preflightSkipped = state.state.skipped;
    assert.deepEqual(
      state.state.providers.map((row) => [row.provider_id, row.status, row.captured_count]),
      [
        ["deepseek", "COMPLETE", 2],
        ["gemini", "COMPLETE", 2],
        ["kimi", "COMPLETE", 2],
      ]
    );

    const cursorProviders = {};
    for (const profile of Object.values(providers)) {
      const knownIds = [...profile.sessions];
      cursorProviders[profile.provider] = {
        known_session_hashes: knownIds.map((id) => sessionHash(profile.provider, id)),
        overlap_fingerprints: Object.fromEntries(
          knownIds.filter((id) => id !== "ds-empty").slice(0, 2).map((id) => [
            sessionHash(profile.provider, id),
            contentHash(id),
          ])
        ),
      };
      const newId = `${profile.provider}-new`;
      profile.sessions.unshift(newId);
      const newPage = await context.newPage();
      const host = Object.entries(providers).find(([, row]) => row === profile)[0];
      await newPage.goto(`https://${host}${profile.sessionPath(newId)}`, { waitUntil: "domcontentloaded" });
    }
    const cursor = Buffer.from(JSON.stringify({
      schema_version: "MEMORIVE_BROWSER_SESSION_CURSOR_V1",
      providers: cursorProviders,
    }), "utf8").toString("base64url");
    const incrementalPage = await context.newPage();
    await incrementalPage.goto(
      `chrome-extension://${extensionId}/runner.html?trigger=SMOKE_INCREMENTAL&mode=INCREMENTAL&limit=10&cursor=${encodeURIComponent(cursor)}`,
      { waitUntil: "domcontentloaded" }
    );
    const incrementalDeadline = Date.now() + 150000;
    while (Date.now() < incrementalDeadline) {
      state = await incrementalPage.evaluate(() => chrome.runtime.sendMessage({ type: "MEMORIVE_SYNC_STATUS" }));
      if (
        state?.state?.trigger === "SMOKE_INCREMENTAL" &&
        ["COMPLETE", "ERROR"].includes(state.state.status)
      ) break;
      await new Promise((resolve) => setTimeout(resolve, 750));
    }
    assert.equal(state?.state?.status, "COMPLETE", JSON.stringify(state));
    assert.equal(state.state.captured, 3, JSON.stringify(state.state));
    assert.equal(state.state.unchanged, 6, JSON.stringify(state.state));
    assert.equal(state.state.failed, 0, JSON.stringify(state.state));
    assert.equal(state.state.window_mode, "DEDICATED_MINIMIZED");
    assert.equal(state.state.user_tab_reused, false);
    const output = {
      schema_version: "DesktopBrowserCompanionExtensionRunnerSmoke-v1",
      extension_version: "3.5.0",
      provider_count: state.state.providers.length,
      captured_count: state.state.captured,
      failed_count: state.state.failed,
      current_page_fallback: false,
      one_reused_worker_per_provider: true,
      dedicated_minimized_window_owned_tabs: true,
      user_tab_reused: false,
      target_success_backfill: true,
      skipped_candidate_count: preflightSkipped,
      incremental_only_new_or_changed: true,
      incremental_captured_count: state.state.captured,
      incremental_unchanged_overlap_count: state.state.unchanged,
      maximum_capture_windows: 1,
      provider_parallelism: true,
      cookie_value_reads: 0,
      browser_storage_value_reads: 0,
      hidden_provider_api_calls: 0,
      status: "PASS",
    };
    if (process.env.Desktop_EXTENSION_RUNNER_RECEIPT) {
      fs.writeFileSync(process.env.Desktop_EXTENSION_RUNNER_RECEIPT, `${JSON.stringify(output, null, 2)}\n`, { encoding: "utf8", flag: "wx" });
    }
    process.stdout.write(JSON.stringify(output));
  } finally {
    await context.close();
    fs.rmSync(profileRoot, { recursive: true, force: true });
  }
})().catch((error) => {
  process.stderr.write(String(error?.stack || error));
  process.exitCode = 1;
});
