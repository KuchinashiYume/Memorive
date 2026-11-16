"""Where the exam packs are, and which package holds the code that scores them.

The packs are not in the public repository. A private build puts them in
memorive_model_evaluation/assets: inside the packaged app (sys._MEIPASS) or next to the source
folder. The scoring code is memorive.exams.evaluation. When no local packs are there but another
build of the scoring code is installed as the package model_evaluation_quality_sentinel, that
package and its own assets are used instead.

The desktop service builds the exam executors from exam_package() and passes each one the paths of
its pack (memorive_desktop_service/service_main.py); the scoring modules' own defaults for manual
runs use local_assets_root().
"""
from __future__ import annotations

from importlib.util import find_spec
from pathlib import Path
import sys

LOCAL_PACKAGE = "memorive.exams.evaluation"
EXTERNAL_PACKAGE = "model_evaluation_quality_sentinel"
ASSET_FOLDER = "memorive_model_evaluation"
# memorive/exams/resources.py -> the source folder (in the packaged app: <_MEIPASS>/source).
SOURCE_ROOT = Path(__file__).resolve().parents[2]


def local_assets_root() -> Path:
    """memorive_model_evaluation/assets: in the packaged app first, then next to the source folder;
    the first that exists, the one next to the source folder when neither does."""
    candidates = []
    frozen_root = getattr(sys, "_MEIPASS", None)
    if isinstance(frozen_root, str) and frozen_root:
        candidates.append(Path(frozen_root).resolve() / ASSET_FOLDER / "assets")
    candidates.append(SOURCE_ROOT / ASSET_FOLDER / "assets")
    return next((path for path in candidates if path.is_dir()), candidates[-1])


def exam_package() -> tuple[str, Path]:
    """The name of the package with the scoring code, and the folder of its exam packs."""
    local = local_assets_root()
    if local.is_dir():
        return LOCAL_PACKAGE, local
    formal = find_spec(EXTERNAL_PACKAGE)
    locations = tuple(formal.submodule_search_locations or ()) if formal else ()
    if len(locations) == 1 and (Path(locations[0]) / "card_reviewer_exam.py").is_file():
        return EXTERNAL_PACKAGE, Path(locations[0]).resolve() / "assets"
    return LOCAL_PACKAGE, local
