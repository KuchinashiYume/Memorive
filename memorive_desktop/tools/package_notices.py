"""Use the same notice members in portable, full, delta and setup payloads."""
from pathlib import Path
import shutil

OMIT={'documentation-content-review.json','legacy-font-compatibility.json',
      'README_PUBLIC.md','README_PUBLIC.en.md','README_PUBLIC.ja.md'}


def members(source: Path):
    for path in sorted(source.rglob('*')):
        if (path.is_file() and '__pycache__' not in path.parts
                and path.suffix not in ('.py','.pyc','.pyo') and path.name not in OMIT):
            yield path,path.relative_to(source)


def package(source: Path, destination: Path):
    count=0
    for path,relative in members(source):
        target=destination/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists():
            raise FileExistsError('NOTICE_TARGET_ALREADY_EXISTS')
        shutil.copyfile(path,target);count+=1
    return count
