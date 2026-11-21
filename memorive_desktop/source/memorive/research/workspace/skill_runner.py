"""Running the memo-research Skill's scripts with the Python inside Memorive.exe.

Compatibility code: an agent on a computer without Python runs the Skill's commands as
"Memorive.exe --memo-bridge ..." (scripts/memo_bridge.py) or "Memorive.exe --memo-sources ..."
(scripts/sources.py) instead of "python <skill>/scripts/...". The callers are agents following
SKILL.md and the hand-off's START_HERE.md (transport.json "bridge"), not code in this repository;
product/desktop/app.py hands those two options to run(). DEVELOPING.md lists it under
"Compatibility code"; tests/test_skill_runners.py runs it. The second way without Python is the
Skill's own console program, bin/memo_bridge.exe (built from the Skill's c/ folder).

Memorive.exe is a window program: a shell does not always wait for it or show what it prints, so
START_HERE.md gives the command in a form that does (see platform/language/handoff.py).
"""
import runpy
import sys
from pathlib import Path

SCRIPTS = {'--memo-bridge': 'memo_bridge.py', '--memo-sources': 'sources.py'}


def integrations_root():
    """The integrations shipped with the app: beside Memorive.exe when packaged,
    memorive_desktop/integrations when run from source."""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent / 'integrations'
    return Path(__file__).resolve().parents[4] / 'integrations'


def run(option, args):
    """Run the script of option (--memo-bridge or --memo-sources) with args as its command line;
    returns its exit code."""
    scripts = integrations_root() / 'skills' / 'memo-research' / 'scripts'
    script = scripts / SCRIPTS[option]
    sys.path.insert(0, str(scripts))   # the scripts import each other (drafts.py)
    sys.argv = [str(script), *args]
    try:
        runpy.run_path(str(script), run_name='__main__')
    except SystemExit as exit:
        return exit.code if isinstance(exit.code, int) else (0 if exit.code is None else 1)
    return 0
