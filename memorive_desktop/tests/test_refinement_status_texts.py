"""The refinement's state reads the same on the settings page, the session page, the inbox and the task page.

Acceptance found internal codes where people read (a failed refinement showed
SESSION_REFINEMENT_FROZEN_SNAPSHOT_DRIFT), a queued refinement shown as running while the inbox ran nothing, and
the settings page calling a removed model "configured". The settings projection now names the chosen model's
state; a queued refinement says whether the inbox runs tasks; and one shared file (ui/integrated/shared/
refinement-status.js) turns a failure code into one sentence with the next step.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from memorive.sessions.controller.refinement_tasks import RefinementTasksMixin
from test_refinement_start_failure import Queued

UI = Path(__file__).resolve().parents[1] / "ui" / "integrated"


class _Settings(RefinementTasksMixin):
    def __init__(self, profile_ref, options, channel=True):
        self.profile_ref, self.options = profile_ref, options
        self._conversation_refinement_channel = SimpleNamespace(execute=lambda request: None) if channel else None
        self._refinement_task_manager = object()

    def _load_refinement_settings(self):
        return {"profile_ref": self.profile_ref, "include_tasks": True, "include_risks": True}

    def _configured_refinement_models(self):
        return self.options


def test_the_settings_projection_names_the_chosen_models_state():
    ready = [{"profile_ref": "api:1", "execution_eligible": True, "profile_kind": "API"}]
    assert _Settings("", ready)._refinement_settings_projection()["model_state"] == "UNSELECTED"
    assert _Settings("api:gone", ready)._refinement_settings_projection()["model_state"] == "REMOVED"
    assert _Settings("api:1", ready)._refinement_settings_projection()["model_state"] == "READY"
    unverified = [{"profile_ref": "api:1", "execution_eligible": False, "profile_kind": "API"}]
    assert _Settings("api:1", unverified)._refinement_settings_projection()["model_state"] == "UNAVAILABLE"


class _Page(Queued):
    """A session whose refinement waits in the queue, with the inbox's automatic processing off."""

    def __init__(self, root):
        super().__init__(root)
        self._refinement_root = root / "drafts"   # no saved versions

    def _refinement_auto_run_enabled(self):
        return False

    def _require_keys(self, accepted, keys, optional=None):
        pass


def test_a_queued_refinement_says_the_inbox_runs_nothing(tmp_path):
    from memorive.sessions.controller.product_controller import SessionsProductController

    page = _Page(tmp_path / "refinement")
    page._ensure_refinement_queue_task({"item_id": "item-1", "display_name": "A conversation",
                                        "local_projection_id": "conversation-1", "source_snapshot_sha256": "1" * 64,
                                        "selected_profile_ref": "model", "message_count": 1})
    projection = SessionsProductController._get_refinement(page, {"local_projection_id": "conversation-1"})
    assert (projection["status"], projection["auto_run"]) == ("TASK_QUEUED", False)


SENTENCES = r"""
global.Memorive = {};
require(process.argv[2]);
const text = Memorive.refinementStatus.failureText;
console.log(JSON.stringify({
  drift: text('SESSION_REFINEMENT_FROZEN_SNAPSHOT_DRIFT'),
  format: text('REFINEMENT_PROPOSAL_ITEM_COUNT_INVALID'),
  other: text('SOMETHING_ELSE'),
  // Every Chinese sentence quoted in the file.
  all: [...new Set(require('fs').readFileSync(process.argv[2], 'utf8')
    .match(/'[^']*[㐀-鿿][^']*'/g).map((quoted) => quoted.slice(1, -1)))],
}));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_failure_codes_read_as_sentences_in_every_language(tmp_path):
    runner = tmp_path / "sentences.js"
    runner.write_text(SENTENCES, encoding="utf-8")
    out = subprocess.run([shutil.which("node"), str(runner), str(UI / "shared" / "refinement-status.js")],
                         capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    result = json.loads(out.stdout.strip().splitlines()[-1])
    assert result["drift"] == "排队期间会话有了新内容，请重新精炼。"
    assert result["format"] == "模型返回的结果格式不对，请重试或换一个精炼模型。"
    assert result["other"] == "精炼没有完成，请重试。"
    # Every sentence of the file has an English and a Japanese entry in app.js.
    line = next(row for row in (UI / "shared" / "app.js").read_text(encoding="utf-8").splitlines()
                if row.strip().startswith("const translations = Object.freeze("))
    table = json.loads(line.strip()[len("const translations = Object.freeze("):-len(");")])
    missing = [sentence for sentence in result["all"]
               if sentence not in table["en-US"] or sentence not in table["ja-JP"]]
    assert len(result["all"]) >= 15 and missing == []
