# memo_bridge.exe: the Skill's bridge without Python

This folder is the C version of `scripts/memo_bridge.py` and `scripts/drafts.py`. It is
compatibility code: an agent on a computer without Python runs
`<skill>/bin/memo_bridge.exe find handoff.json "..."` with the same commands, the same output
and the same files as `python <skill>/scripts/memo_bridge.py find handoff.json "..."`.

- **Who calls it.** Agents that follow `SKILL.md` and a hand-off's `START_HERE.md`. No code in
  this repository calls it, so a search for its callers finds none: do not remove it for that.
  `DEVELOPING.md` lists it under "Compatibility code".
- **What keeps it right.** `tests/test_skill_runners.py` builds it and runs it next to the Python
  version on one hand-off: what each prints, the files each writes and each answer's request_id
  must be the same. When the Python scripts change, change these files too.
- **Building.** `python tools/build_skill_bridge.py OUT.exe` (Visual Studio's C compiler, found
  with vswhere). The release build puts it at `bin/memo_bridge.exe` in the Skill shipped beside
  Memorive.exe.
- **Not ported.** `bind`: it needs PyMuPDF through `scripts/sources.py`. Use Python or
  `Memorive.exe --memo-bridge` for it.
- **The other way without Python.** `Memorive.exe --memo-bridge ...` runs `scripts/memo_bridge.py`
  with Memorive's own Python (`source/memorive/research/workspace/skill_runner.py`). Memorive.exe
  is a window program: in PowerShell end that command with `| Out-String`. memo_bridge.exe is a
  console program and needs nothing like that.

| File | Part of the Python version |
| --- | --- |
| `bridge.h` | the shared types and functions |
| `text.c` | text as code points, the Unicode classes of `str` and `re`, `drafts._even`, `locate`, `closest`, `numbers`, `stated_values` |
| `json.c` | `json.loads` and `json.dumps(..., ensure_ascii=False)` as Python writes it |
| `sha256.c` | `hashlib.sha256` (the answer's request_id) |
| `drafts.c` | `drafts.apply` and its marks, claims and problems |
| `memo_bridge.c` | `Memo.call`, `find`, `read`, `answer`, `return`, `context` |
