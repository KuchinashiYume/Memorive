"""Installing the memo-research Skill into the agents' skills folders (research/workspace/skill_install.py).

Compatibility code: the agents live outside this repository, so this test is what installs into each
agent's folder. Every agent the settings page offers is installed into a made-up home folder; a copy
already there is backed up; a chosen folder is remembered; a hand-off folder carries the Skill where
each agent looks in a project.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from memorive.research.workspace.skill_install import AGENTS, PROJECT_FOLDERS, SkillTargets, tree_hash

SKILL = Path(__file__).resolve().parents[1] / "integrations" / "skills" / "memo-research"


def test_the_agents_and_where_they_keep_skills():
    assert [a["id"] for a in AGENTS] == ["claude-code", "codex", "cursor", "gemini-cli", "github-copilot", "opencode",
                                         "deepseek-harness", "codebuddy"]
    assert PROJECT_FOLDERS == (".claude/skills", ".agents/skills", ".dsh/skills", ".codebuddy/skills")
    assert {a["id"]: a["skills"] for a in AGENTS}["deepseek-harness"] == ".dsh/skills"
    assert {a["id"]: a["skills"] for a in AGENTS}["codebuddy"] == ".codebuddy/skills"


def test_the_skill_is_installed_for_every_agent_present(tmp_path):
    home, data = tmp_path / "home", tmp_path / "data"
    for agent in AGENTS:
        (home / agent["home"]).mkdir(parents=True)
    targets = SkillTargets(SKILL, data, home)
    listed = targets.targets()
    assert [a["id"] for a in listed["agents"]] == [a["id"] for a in AGENTS]
    assert {a["state"] for a in listed["agents"]} == {"missing"}
    for agent in AGENTS:
        targets.install(agent=agent["id"])
    shipped = tree_hash(SKILL)
    for agent in AGENTS:
        folder = home / agent["skills"] / "memo-research"
        assert (folder / "SKILL.md").is_file() and tree_hash(folder) == shipped
    assert {a["state"] for a in targets.targets()["agents"]} == {"current"}


def test_only_agents_on_this_computer_are_listed_and_an_old_copy_is_backed_up(tmp_path):
    home, data = tmp_path / "home", tmp_path / "data"
    (home / ".codex").mkdir(parents=True)
    old = home / ".codebuddy" / "skills" / "memo-research"   # installed earlier, its agent folder kept
    old.mkdir(parents=True)
    (old / "SKILL.md").write_text("old version\n", encoding="utf-8")
    targets = SkillTargets(SKILL, data, home)
    listed = {a["id"]: a["state"] for a in targets.targets()["agents"]}
    assert listed == {"codex": "missing", "codebuddy": "different"}
    targets.install(agent="codebuddy")
    [backup] = (data / "skill_backups").iterdir()
    assert backup.name.endswith("-codebuddy")
    assert (backup / "memo-research" / "SKILL.md").read_text(encoding="utf-8") == "old version\n"
    assert {a["id"]: a["state"] for a in targets.targets()["agents"]}["codebuddy"] == "current"


def test_a_chosen_folder_is_installed_into_and_remembered(tmp_path):
    home, data, chosen = tmp_path / "home", tmp_path / "data", tmp_path / "my-agent" / "skills"
    targets = SkillTargets(SKILL, data, home)
    assert targets.targets() == {"skill": "memo-research", "agents": [], "folders": []}
    targets.choose(str(chosen))
    # Choosing the Skill's own folder means the folder of Skills it is in.
    targets.choose(str(chosen / "memo-research"))
    listed = SkillTargets(SKILL, data, home).targets()
    assert listed["folders"] == [{"path": str(chosen / "memo-research"), "folder": str(chosen), "state": "current"}]
    assert len(list((data / "skill_backups").iterdir())) == 1   # the second install moved the first aside
    assert targets.forget(str(chosen))["folders"] == []
    assert (chosen / "memo-research" / "SKILL.md").is_file()     # forgetting leaves the Skill where it is


def test_the_page_can_only_update_a_folder_already_chosen(tmp_path):
    # A new folder comes from the folder window (choose); a folder named by the page itself is refused and
    # nothing is written there or moved out of it.
    home, data, chosen, other = tmp_path / "home", tmp_path / "data", tmp_path / "chosen", tmp_path / "other"
    (other / "memo-research").mkdir(parents=True)
    (other / "memo-research" / "mine.txt").write_text("kept", encoding="utf-8")
    targets = SkillTargets(SKILL, data, home)
    with pytest.raises(ValueError, match="SKILL_FOLDER_NOT_CHOSEN"):
        targets.install(folder=str(other))
    assert [p.name for p in (other / "memo-research").iterdir()] == ["mine.txt"] and not (data / "skill_targets.json").exists()
    targets.choose(str(chosen))
    assert targets.install(folder=str(chosen))["folders"][0]["state"] == "current"   # updating a chosen folder
