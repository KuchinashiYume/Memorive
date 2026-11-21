"""Build memo_bridge.exe, the C version of the memo-research Skill's scripts/memo_bridge.py.

Compatibility code: integrations/skills/memo-research/c/bridge.h says why the program exists and
who runs it. build_memorive.py calls build() for the packaged app, so the program ships as
integrations/skills/memo-research/bin/memo_bridge.exe beside Memorive.exe (and is copied with the
Skill into hand-off folders and the agents' skills folders); tests/test_skill_runners.py builds it
into a temporary folder. It needs Visual Studio's C compiler, found with vswhere.

    python tools/build_skill_bridge.py OUT.exe
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SOURCE = Path(__file__).absolute().parents[1] / 'integrations' / 'skills' / 'memo-research' / 'c'
VSWHERE = (Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)'))
           / 'Microsoft Visual Studio' / 'Installer' / 'vswhere.exe')


def compiler_environment():
    """The environment of Visual Studio's x64 C compiler (vcvars64.bat), or None without one."""
    if not VSWHERE.is_file():
        return None
    found = subprocess.run([str(VSWHERE), '-latest', '-products', '*', '-requires',
                            'Microsoft.VisualStudio.Component.VC.Tools.x86.x64', '-property', 'installationPath'],
                           capture_output=True, text=True, encoding='utf-8', errors='replace').stdout.split()
    if not found:
        return None
    vcvars = Path(' '.join(found)) / 'VC' / 'Auxiliary' / 'Build' / 'vcvars64.bat'
    if not vcvars.is_file():
        return None
    # cmd /u: the environment comes back as UTF-16, whatever the console code page.
    # A command line as one string: cmd does not read the quotes list2cmdline would escape.
    raw = subprocess.run(f'cmd /u /s /c ""{vcvars}" >nul && set"', capture_output=True).stdout
    env = {}
    for line in raw.decode('utf-16-le', errors='replace').splitlines():
        name, sep, value = line.partition('=')
        if sep and name:
            env[name] = value
    return env if 'INCLUDE' in env else None


def build(output):
    """Compile the C sources into output (a statically linked console program); returns its path."""
    env = compiler_environment()
    if env is None:
        raise RuntimeError('SKILL_BRIDGE_COMPILER_MISSING: Visual Studio with the C compiler is needed to build memo_bridge.exe')
    output = Path(output).absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as objects:
        # CreateProcess looks for cl.exe on this process's PATH, not the compiler environment's.
        cl = shutil.which('cl', path=next(v for k, v in env.items() if k.upper() == 'PATH'))
        run = subprocess.run([cl, '/nologo', '/O2', '/W3', '/utf-8', '/MT', f'/Fe:{output}', f'/Fo:{objects}\\',
                              *map(str, sorted(SOURCE.glob('*.c'))), '/link', 'normaliz.lib'],
                             env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', cwd=objects)
    if run.returncode or not output.is_file():
        raise RuntimeError('SKILL_BRIDGE_BUILD_FAILED:\n' + run.stdout + run.stderr)
    return output


if __name__ == '__main__':
    print(build(sys.argv[1]))
