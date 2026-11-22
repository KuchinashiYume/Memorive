"""The session page reads the refinement model again each time it is opened.

It read sessions.refinement_settings_get once, when the app started, so a model chosen in settings later
left "精炼对话" greyed out until a restart. The page's bridge (ui/integrated/routes/sessions/bridge.js) is
run with Node against stand-ins for the desktop service and the page view.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

BRIDGE = Path(__file__).resolve().parents[1] / "ui" / "integrated" / "routes" / "sessions" / "bridge.js"

HARNESS = r"""
const listeners = {};
global.CustomEvent = class { constructor(type, init) { this.type = type; this.detail = init?.detail; } };
global.window = {
  addEventListener: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); },
  dispatchEvent: (event) => { (listeners[event.type] || []).forEach((fn) => fn(event)); },
  pywebview: { api: { call: true, browser_companion_status: async () => ({}) } },
};
let configured = false;
const seen = [];
global.Memorive = {
  service: { call: async (method) => method === 'sessions.refinement_settings_get'
    ? { configured } : { records: [], record_count: 0, source_count: 0 } },
  sessionsView: { setRefinementConfiguration: (p) => seen.push(p.configured), setBrowserCompanionStatus() {},
                  replaceRows() {}, renderRows() { return {}; }, inspect: () => ({ record_count: 0 }) },
};
global.document = { querySelector: () => null };
require(process.argv[2]);
const settle = () => new Promise((resolve) => setTimeout(resolve, 20));
(async () => {
  await settle();                      // started: read once
  configured = true;                   // the model is chosen in settings
  window.dispatchEvent(new CustomEvent('desktop-integrated-route', { detail: { route: 'library' } }));
  window.dispatchEvent(new CustomEvent('desktop-integrated-route', { detail: { route: 'sessions' } }));
  await settle();
  console.log(JSON.stringify(seen));
})();
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_opening_the_session_page_reads_the_refinement_model_again(tmp_path):
    runner = tmp_path / "harness.js"
    runner.write_text(HARNESS, encoding="utf-8")
    out = subprocess.run([shutil.which("node"), str(runner), str(BRIDGE)], capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout.strip().splitlines()[-1]) == [False, True]
