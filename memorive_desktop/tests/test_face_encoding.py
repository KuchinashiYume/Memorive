"""The kaomoji faces change through one of three fixed encoding animations, and five quick clicks show a face
garbled (an easter egg). The desk assistant (product/desktop/assistant_native_motion.py) and the main window
(ui/integrated/shared/decorations.js) each carry the encodings; these tests keep the two the same.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "product" / "desktop"
sys.path.insert(0, str(DESKTOP))

from assistant_native_motion import (  # noqa: E402
    ENCODING_FRAMES,
    ENCODING_ORDER,
    ENCODINGS,
    GARBLED_HOLD_FRAMES,
    _TRANSFORM_KEYFRAMES,
    encoding_frames,
    face_change_texts,
    garbled,
    sample_motion,
)

FACES = ["(>ω-)", "(・ω・)", "(-ω-)", "(・ω<)☆", "(・ω・)？", "(>ω<)ゞ", "(^ω^)✧", "(*≧ω≦)✓", "(×ω×)",
         "(´・ω・`)", "Ciallo～(∠・ω< )⌒☆"]


@pytest.mark.parametrize("encoding", range(len(ENCODINGS)))
@pytest.mark.parametrize("count", [3, 6])
def test_each_frame_decodes_more_of_the_face(encoding: int, count: int) -> None:
    for face in FACES:
        frames = encoding_frames(face, encoding, count)
        assert len(frames) == count
        for number, frame in enumerate(frames):
            assert len(frame) == len(face)
            same = sum(shown == wanted for shown, wanted in zip(frame, face))
            assert same >= len(face) * number // count
            assert all(shown == " " for shown, wanted in zip(frame, face) if wanted == " ")
        assert frames[0] != face


def test_the_three_encodings_look_different() -> None:
    first_frames = {encoding_frames("(・ω<)☆", encoding, 3)[0] for encoding in range(len(ENCODINGS))}
    assert len(first_frames) == 3
    assert sorted(set(ENCODING_ORDER)) == [0, 1, 2]


def test_a_face_change_plays_an_encoding_but_a_blink_or_the_same_face_does_not() -> None:
    assert face_change_texts("(>ω-)", "(・ω<)☆", "INTERACTION", 0) == encoding_frames(
        "(・ω<)☆", ENCODING_ORDER[0], ENCODING_FRAMES)
    assert face_change_texts("(>ω-)", "(・ω<)☆", "SUCCESS", 1) == encoding_frames(
        "(・ω<)☆", ENCODING_ORDER[1], ENCODING_FRAMES)
    assert face_change_texts("(>ω-)", "(・ω・)", "BLINK", 0) == ()
    assert face_change_texts("(>ω<)ゞ", "(>ω<)ゞ", "WORKING", 0) == ()


def test_the_easter_egg_shows_the_face_garbled_then_decodes_it() -> None:
    assert garbled("(>ω-)") == "(>蠅-)"
    assert garbled("(・ω・)") == "(銉幌夈兓)"
    texts = face_change_texts("(>ω-)", "(・ω<)☆", "GARBLED", 0)
    assert texts[:GARBLED_HOLD_FRAMES] == (garbled("(・ω<)☆"),) * GARBLED_HOLD_FRAMES
    assert texts[GARBLED_HOLD_FRAMES:] == encoding_frames("(・ω<)☆", 1, ENCODING_FRAMES)
    assert sample_motion("GARBLED", 0).frame == "interaction"
    assert sample_motion("GARBLED", 2000).done is True


def test_the_interaction_nods_without_tilting() -> None:
    assert all(keyframe[3] == 0 for keyframe in _TRANSFORM_KEYFRAMES["INTERACTION"])
    assert all(sample_motion("INTERACTION", ms).rotation_degrees == 0 for ms in range(0, 1100, 50))


NODE_HARNESS = r"""
const fs = require('fs');
global.window = { matchMedia: () => null, addEventListener() {} };
global.document = { readyState: 'loading', addEventListener() {} };
global.Memorive = {};
require('vm').runInThisContext(fs.readFileSync(process.argv[2], 'utf8'));
const cases = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify({
  frames: cases.map(([text, encoding, count]) => Memorive.decorations.encodingFrames(text, encoding, count)),
  garbled: cases.map(([text]) => Memorive.decorations.garbled(text)),
}));
"""


def test_the_main_window_plays_the_same_encodings_as_the_desk_assistant(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed")
    harness = tmp_path / "faces.cjs"
    harness.write_text(NODE_HARNESS, encoding="utf-8")
    cases = [[face, encoding, count] for face in FACES for encoding in range(len(ENCODINGS)) for count in (3, 6)]
    result = subprocess.run(
        [node, str(harness), str(ROOT / "ui" / "integrated" / "shared" / "decorations.js")],
        input=json.dumps(cases), capture_output=True, text=True, encoding="utf-8", timeout=60, check=True)
    web = json.loads(result.stdout)
    assert web["frames"] == [list(encoding_frames(face, encoding, count)) for face, encoding, count in cases]
    assert web["garbled"] == [garbled(face) for face, _, _ in cases]
