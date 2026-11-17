from __future__ import annotations

import hashlib
import sys
from pathlib import Path


ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
RUN_ROOT = ATTEMPT_ROOT.parents[1]
DESKTOP_ROOT = ATTEMPT_ROOT / "product" / "desktop"
sys.path.insert(0, str(DESKTOP_ROOT))

from assistant_native_motion import base_frame_for_presentation, sample_motion


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_part11_motion_samples_match_final_keyframes_and_restore_once() -> None:
    interaction = sample_motion("INTERACTION", 1100 * 0.24)
    assert interaction.frame == "interaction"
    assert interaction.offset_y == 1
    assert interaction.rotation_degrees == -9
    assert interaction.done is False

    interaction_hold = sample_motion("INTERACTION", 1100 * 0.62)
    assert interaction_hold.offset_y == 1
    assert interaction_hold.rotation_degrees == -9
    assert sample_motion("INTERACTION", 1100, base_frame="working").frame == "working"
    assert sample_motion("INTERACTION", 1100, base_frame="working").done is True

    assert sample_motion("BLINK", 1199).frame == "blink-open"
    assert sample_motion("BLINK", 1200).frame == "blink-closed"
    assert sample_motion("BLINK", 1650).frame == "blink-open"
    assert sample_motion("BLINK", 2250).frame == "blink-closed"
    assert sample_motion("BLINK", 2500).frame == "blink-open"
    assert sample_motion("BLINK", 3000).done is True

    working = sample_motion("WORKING", 900 * 0.12, base_frame="working")
    assert working.frame == "working"
    assert working.offset_x == -4
    assert working.rotation_degrees == -2
    assert sample_motion("WORKING", 900, base_frame="working").frame == "working"
    assert sample_motion("SUCCESS", 1400, base_frame="idle").done is True
    assert base_frame_for_presentation("WORKING", "JOB_RUNNING") == "working"


