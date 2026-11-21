"""Installing the memo-research Skill shipped with Memorive into the agents' own skills folders.

Compatibility code: the agents (Claude Code, Codex, Cursor, Gemini CLI, GitHub Copilot, OpenCode,
DeepSeek Harness, CodeBuddy) read Skills from folders of their own; this module is how a Skill made
for one format reaches all of them. Its callers are the settings page (Agent and connections, through
the window methods memo_skill_targets, memo_skill_install, memo_skill_pick_folder and
memo_skill_forget in desktop/api/research_memo.py) and desktop_handoff in agents.py (SKILL_FOLDERS).
DEVELOPING.md lists it under "Compatibility code"; tests/test_skill_install.py installs into every
agent's folder. Do not remove an agent because nothing in this repository names its folder: the
agent outside the repository does.

An agent counts as present when its home folder exists (AGENTS[...]['home'] under the user's home).
Installing copies the shipped Skill folder into the agent's skills folder; a copy already there is
moved to the backups folder first. Nothing is installed or updated without the person's click.
"""
import hashlib
import json
import shutil
import time
from pathlib import Path

SKILL = 'memo-research'
IGNORED = shutil.ignore_patterns('__pycache__', '*.pyc')

# Where each agent keeps Skills: 'skills' under the user's home (installed by the settings page),
# 'project' inside a project folder (desktop_handoff copies the Skill there). Checked 2026-11-21:
# agentskills.io and the skills CLI agent table (unpkg.com/skills@1.5.9/README.md); DeepSeek Harness
# from its dsh setup notes, CodeBuddy from Tencent Cloud's CodeBuddy Skills documentation.
AGENTS = (
    {'id': 'claude-code', 'name': 'Claude Code', 'home': '.claude', 'skills': '.claude/skills', 'project': '.claude/skills'},
    {'id': 'codex', 'name': 'Codex', 'home': '.codex', 'skills': '.codex/skills', 'project': '.agents/skills'},
    {'id': 'cursor', 'name': 'Cursor', 'home': '.cursor', 'skills': '.cursor/skills', 'project': '.agents/skills'},
    {'id': 'gemini-cli', 'name': 'Gemini CLI', 'home': '.gemini', 'skills': '.gemini/skills', 'project': '.agents/skills'},
    {'id': 'github-copilot', 'name': 'GitHub Copilot', 'home': '.copilot', 'skills': '.copilot/skills', 'project': '.agents/skills'},
    {'id': 'opencode', 'name': 'OpenCode', 'home': '.config/opencode', 'skills': '.config/opencode/skills', 'project': '.agents/skills'},
    {'id': 'deepseek-harness', 'name': 'DeepSeek Harness', 'home': '.dsh', 'skills': '.dsh/skills', 'project': '.dsh/skills'},
    {'id': 'codebuddy', 'name': 'CodeBuddy', 'home': '.codebuddy', 'skills': '.codebuddy/skills', 'project': '.codebuddy/skills'},
)
# The project folders, in the table's order: a hand-off folder carries one copy of the Skill in each.
PROJECT_FOLDERS = tuple(dict.fromkeys(a['project'] for a in AGENTS))


def tree_hash(folder):
    """One hash of a Skill folder's files (paths and bytes), compiled Python left out."""
    digest = hashlib.sha256()
    for path in sorted(p for p in Path(folder).rglob('*') if p.is_file()
                       and '__pycache__' not in p.parts and p.suffix != '.pyc'):
        digest.update(path.relative_to(folder).as_posix().encode() + b'\0' + path.read_bytes() + b'\0')
    return digest.hexdigest()


def state(target, shipped_hash):
    """'missing', 'current' (the same files as the shipped Skill) or 'different'."""
    if not (Path(target) / 'SKILL.md').is_file():
        return 'missing'
    return 'current' if tree_hash(target) == shipped_hash else 'different'


class SkillTargets:
    """The agents and chosen folders the Skill can be installed into. shipped: the Skill folder that
    comes with Memorive; data: Memorive's folder for the chosen folders (skill_targets.json) and the
    backups; home: the user's home folder."""

    def __init__(self, shipped, data, home):
        self.shipped, self.data, self.home = Path(shipped), Path(data), Path(home)

    def _folders(self):
        path = self.data / 'skill_targets.json'
        return json.loads(path.read_text(encoding='utf-8'))['folders'] if path.is_file() else []

    def _save_folders(self, folders):
        self.data.mkdir(parents=True, exist_ok=True)
        (self.data / 'skill_targets.json').write_text(json.dumps({'folders': folders}, ensure_ascii=False, indent=1),
                                                       encoding='utf-8')

    def targets(self):
        """The agents present on this computer (or with the Skill already installed) and the chosen
        folders, each with where the Skill goes and its state."""
        shipped = tree_hash(self.shipped)
        agents = []
        for agent in AGENTS:
            target = self.home / agent['skills'] / SKILL
            if (self.home / agent['home']).is_dir() or target.is_dir():
                agents.append({'id': agent['id'], 'name': agent['name'], 'path': str(target), 'state': state(target, shipped)})
        folders = [{'path': str(Path(f) / SKILL), 'folder': f, 'state': state(Path(f) / SKILL, shipped)}
                   for f in self._folders()]
        return {'skill': SKILL, 'agents': agents, 'folders': folders}

    def choose(self, folder):
        """A skills folder the person picked in the folder window: remembered, then installed into. This is
        the only way a new folder is added; the page itself can name only folders already remembered."""
        skills = Path(folder)
        if skills.name == SKILL:   # the person chose the Skill's own folder, not the folder of Skills
            skills = skills.parent
        folders = self._folders()
        if str(skills) not in folders:
            self._save_folders(folders + [str(skills)])
        return self.install(folder=str(skills))

    def install(self, agent=None, folder=None):
        """Install the shipped Skill for one agent (its id) or into one remembered folder (see choose). A copy
        already there is moved to backups/<time>-<name>/ first."""
        if agent is not None:
            entry = next((a for a in AGENTS if a['id'] == agent), None)
            if entry is None:
                raise ValueError('SKILL_AGENT_UNKNOWN')
            skills, label = self.home / entry['skills'], entry['id']
        else:
            # A script in the page could otherwise copy the Skill into, and move a folder out of, any path.
            if folder not in self._folders():
                raise ValueError('SKILL_FOLDER_NOT_CHOSEN')
            skills = Path(folder)
            label = 'folder-' + hashlib.sha256(str(skills).encode()).hexdigest()[:8]
        target = skills / SKILL
        if target.exists():
            base = self.data / 'skill_backups' / (time.strftime('%Y%m%d-%H%M%S') + '-' + label)
            backup, n = base, 1
            while backup.exists():   # installed twice within a second
                backup, n = base.with_name(f'{base.name}-{n}'), n + 1
            backup.mkdir(parents=True)
            shutil.move(str(target), str(backup / SKILL))
        skills.mkdir(parents=True, exist_ok=True)
        shutil.copytree(self.shipped, target, ignore=IGNORED)
        return self.targets()

    def forget(self, folder):
        """Stop listing a chosen folder (the Skill stays in it)."""
        self._save_folders([f for f in self._folders() if f != folder])
        return self.targets()
