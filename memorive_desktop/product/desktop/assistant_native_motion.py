from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final


# User-selected final interaction authority. The native Windows overlay exists
# only because a colour-keyed WebView2 child cannot be the reliable hit target;
# its timing and motion must remain a projection of this exact HTML source.
ASSISTANT_PETS_FINAL_SHA256: Final[str] = (
    "2B7B841B07C1BBBF20A55F7E660D987C6439AFFFF8723865747713DEEC66959C"
)
AUTO_BLINK_MIN_MS: Final[int] = 8000
AUTO_BLINK_MAX_MS: Final[int] = 18000
NATIVE_ANIMATION_FRAME_INTERVAL_MS: Final[int] = 100
TERMINAL_ANIMATION_COALESCE_MS: Final[int] = 4000


@dataclass(frozen=True, slots=True)
class MotionSample:
    frame: str
    offset_x: float = 0.0
    offset_y: float = 0.0
    rotation_degrees: float = 0.0
    scale: float = 1.0
    opacity: float = 1.0
    done: bool = False


MOTION_DURATIONS_MS: Final[dict[str, int]] = {
    "INTERACTION": 1100,
    "BLINK": 3000,
    "WAKE": 900,
    "MESSAGE": 700,
    "WORKING": 900,
    "ATTENTION": 1100,
    "SUCCESS": 1400,
    "ERROR": 900,
    "GARBLED": 2000,  # EASTER EGG (彩蛋), intentional: five quick clicks show the face garbled (face_change_texts)
}


_TRANSFORM_KEYFRAMES: Final[dict[str, tuple[tuple[float, float, float, float, float, float], ...]]] = {
    # progress, x, y, rotation, scale, opacity
    "INTERACTION": (  # a small nod, no tilt
        (0.00, 0.0, 0.0, 0.0, 1.00, 1.00),
        (0.24, 0.0, 1.0, 0.0, 1.00, 1.00),
        (0.62, 0.0, 1.0, 0.0, 1.00, 1.00),
        (1.00, 0.0, 0.0, 0.0, 1.00, 1.00),
    ),
    "WAKE": (
        (0.00, 0.0, 8.0, 0.0, 0.90, 0.35),
        (0.42, 0.0, -4.0, 0.0, 1.04, 1.00),
        (0.70, 0.0, 1.0, 0.0, 0.99, 1.00),
        (1.00, 0.0, 0.0, 0.0, 1.00, 1.00),
    ),
    "MESSAGE": (
        (0.00, 0.0, 0.0, 0.0, 1.00, 1.00),
        (0.35, 0.0, -2.0, -2.0, 1.01, 1.00),
        (0.70, 0.0, 1.0, 1.0, 1.00, 1.00),
        (1.00, 0.0, 0.0, 0.0, 1.00, 1.00),
    ),
    "WORKING": (
        (0.00, 0.0, 0.0, 0.0, 1.00, 1.00),
        (0.12, -4.0, 0.0, -2.0, 1.00, 1.00),
        (0.24, 4.0, 0.0, 2.0, 1.00, 1.00),
        (0.36, -4.0, 0.0, -1.5, 1.00, 1.00),
        (0.48, 4.0, 0.0, 1.5, 1.00, 1.00),
        (0.62, -3.0, 0.0, -1.0, 1.00, 1.00),
        (0.76, 3.0, 0.0, 1.0, 1.00, 1.00),
        (0.90, -1.0, 0.0, 0.0, 1.00, 1.00),
        (1.00, 0.0, 0.0, 0.0, 1.00, 1.00),
    ),
    "SUCCESS": (
        (0.00, 0.0, 0.0, 0.0, 1.00, 1.00),
        (0.10, -5.0, 0.0, -3.0, 1.02, 1.00),
        (0.22, 5.0, 0.0, 3.0, 1.02, 1.00),
        (0.34, -4.0, 0.0, -2.0, 1.03, 1.00),
        (0.46, 4.0, 0.0, 2.0, 1.03, 1.00),
        (0.60, -3.0, 0.0, -1.0, 1.02, 1.00),
        (0.74, 3.0, 0.0, 1.0, 1.02, 1.00),
        (0.88, -1.0, 0.0, 0.0, 1.01, 1.00),
        (1.00, 0.0, 0.0, 0.0, 1.00, 1.00),
    ),
    "ERROR": (
        (0.00, 0.0, 0.0, 0.0, 1.00, 1.00),
        (0.16, -4.0, 0.0, 0.0, 1.00, 1.00),
        (0.32, 4.0, 0.0, 0.0, 1.00, 1.00),
        (0.48, -3.0, 0.0, 0.0, 1.00, 1.00),
        (0.64, 3.0, 0.0, 0.0, 1.00, 1.00),
        (0.82, -1.0, 0.0, 0.0, 1.00, 1.00),
        (1.00, 0.0, 0.0, 0.0, 1.00, 1.00),
    ),
}


_TRANSIENT_FRAMES: Final[dict[str, str]] = {
    "INTERACTION": "interaction",
    "WAKE": "interaction",
    "MESSAGE": "interaction",
    "WORKING": "working",
    "ATTENTION": "interaction",
    "SUCCESS": "success",
    "ERROR": "idle",
    "GARBLED": "interaction",
}


_TIMING_CURVES: Final[dict[str, tuple[float, float, float, float]]] = {
    # Exact animation timing functions declared by the selected final
    # assistant_pets.html. CSS applies the function independently between each
    # pair of keyframes.
    "INTERACTION": (0.2, 0.8, 0.2, 1.0),
    "WAKE": (0.2, 0.8, 0.2, 1.0),
    "MESSAGE": (0.2, 0.8, 0.2, 1.0),
    "WORKING": (0.36, 0.07, 0.19, 0.97),
    "SUCCESS": (0.36, 0.07, 0.19, 0.97),
    "ERROR": (0.36, 0.07, 0.19, 0.97),
}


def _cubic_bezier_progress(
    progress: float,
    control_x1: float,
    control_y1: float,
    control_x2: float,
    control_y2: float,
) -> float:
    bounded = min(1.0, max(0.0, progress))
    if bounded in {0.0, 1.0}:
        return bounded

    def coordinate(parameter: float, first: float, second: float) -> float:
        inverse = 1.0 - parameter
        return (
            3.0 * inverse * inverse * parameter * first
            + 3.0 * inverse * parameter * parameter * second
            + parameter * parameter * parameter
        )

    lower = 0.0
    upper = 1.0
    parameter = bounded
    for _ in range(18):
        measured_x = coordinate(parameter, control_x1, control_x2)
        if abs(measured_x - bounded) <= 1e-7:
            break
        if measured_x < bounded:
            lower = parameter
        else:
            upper = parameter
        parameter = (lower + upper) / 2.0
    return coordinate(parameter, control_y1, control_y2)


def _interpolate(
    keyframes: tuple[tuple[float, float, float, float, float, float], ...],
    progress: float,
    timing_curve: tuple[float, float, float, float],
) -> tuple[float, float, float, float, float]:
    bounded = min(1.0, max(0.0, progress))
    left = keyframes[0]
    right = keyframes[-1]
    for candidate in keyframes[1:]:
        right = candidate
        if bounded <= candidate[0]:
            break
        left = candidate
    span = right[0] - left[0]
    ratio = 0.0 if span <= 0 else (bounded - left[0]) / span
    ratio = _cubic_bezier_progress(ratio, *timing_curve)
    return tuple(left[index] + (right[index] - left[index]) * ratio for index in range(1, 6))  # type: ignore[return-value]


def base_frame_for_presentation(animation: str, reason_code: str) -> str:
    normalized = animation.upper()
    if normalized == "WORKING" or reason_code == "JOB_RUNNING":
        return "working"
    if normalized == "SLEEP":
        return "blink-closed"
    if normalized == "DRAG":
        return "interaction"
    return "idle"


def should_suppress_presentation_during_auto_blink(
    *, animation_locked: bool, animation_source: str | None, animation: str
) -> bool:
    """A blink never becomes a queue for a delayed work/message animation."""

    if not isinstance(animation_locked, bool) or not isinstance(animation, str):
        raise ValueError("ASSISTANT_BLINK_QUEUE_INPUT_INVALID")
    return (
        animation_locked
        and animation_source == "auto"
        and animation.upper() in {"STATIC", "BLINK", "MESSAGE", "WORKING"}
    )


def auto_blink_completion_plan(
    *,
    animation_source: str | None,
    restore_frame: str,
    queued_animation_names: list[str] | tuple[str, ...],
) -> dict[str, object]:
    """Finish an automatic blink without turning it into a work animation.

    Low-priority work/message projections observed during a blink are stale by
    the time the cosmetic blink finishes and must not run as a chained motion.
    A genuinely urgent terminal/error presentation may still be consumed.
    """

    if animation_source != "auto":
        return {
            "restore_frame": restore_frame,
            "consume_queue": bool(queued_animation_names),
            "discarded_queue_count": 0,
        }
    low_priority = {"STATIC", "BLINK", "MESSAGE", "WORKING"}
    urgent_count = sum(
        1 for name in queued_animation_names if str(name).upper() not in low_priority
    )
    return {
        "restore_frame": restore_frame,
        "consume_queue": urgent_count > 0,
        "discarded_queue_count": len(queued_animation_names) - urgent_count,
    }


def sample_motion(motion: str, elapsed_ms: float, *, base_frame: str = "idle") -> MotionSample:
    normalized = motion.upper()
    if normalized == "BLINK":
        if elapsed_ms >= MOTION_DURATIONS_MS[normalized]:
            return MotionSample(base_frame, done=True)
        if elapsed_ms < 1200:
            frame = "blink-open"
        elif elapsed_ms < 1650:
            frame = "blink-closed"
        elif elapsed_ms < 2250:
            frame = "blink-open"
        elif elapsed_ms < 2500:
            frame = "blink-closed"
        else:
            frame = "blink-open"
        return MotionSample(frame)

    duration = MOTION_DURATIONS_MS.get(normalized)
    if duration is None:
        return MotionSample(base_frame, done=True)
    if elapsed_ms >= duration:
        return MotionSample(base_frame, done=True)

    # The success CSS shake lasts 1100 ms while the success expression remains
    # visible until the 1400 ms transient completes.
    transform_duration = 1100 if normalized == "SUCCESS" else duration
    transform_progress = min(max(elapsed_ms, 0.0), transform_duration) / transform_duration
    transform_name = "INTERACTION" if normalized in {"ATTENTION", "GARBLED"} else normalized
    values = _interpolate(
        _TRANSFORM_KEYFRAMES[transform_name],
        transform_progress,
        _TIMING_CURVES[transform_name],
    )
    return MotionSample(_TRANSIENT_FRAMES[normalized], *values)


# A face changes into the next one through an encoding animation: for a few frames the new face shows with its
# characters not yet decoded replaced by the glyphs of an encoding, decoded in that encoding's order. There are
# three fixed encodings, taken in ENCODING_ORDER, a fixed order that looks random. The main window plays the same
# three (ui/integrated/shared/decorations.js, encodings); change both together.
# Each encoding: (glyphs for narrow characters, glyphs for wide characters, decoding order).
# EASTER EGG (彩蛋), intentional: the glyphs look like garbled text (锟斤拷, 烫, 屯 are the classic mojibake) on
# purpose. They show for a few frames while a face changes; they are not an encoding bug, do not "fix" them.
ENCODINGS: Final[tuple[tuple[str, str, str], ...]] = (
    ("01", "０１", "left"),
    ("?#%&", "锟斤拷烫屯", "right"),
    (".:;'", "░▒▓█", "middle"),
)
ENCODING_ORDER: Final[tuple[int, ...]] = (1, 0, 2, 2, 1, 0, 2)
ENCODING_FRAMES: Final[int] = 3  # one frame per 100 ms animation tick
GARBLED_HOLD_FRAMES: Final[int] = 10  # the easter egg shows the garbled face for a second before it decodes


def encoding_frames(text: str, encoding: int, count: int) -> tuple[str, ...]:
    """The count frames of one encoding animation that decodes into text (text itself comes after them)."""
    narrow, wide, order = ENCODINGS[encoding]
    characters = list(text)
    size = len(characters)
    if order == "left":
        positions = list(range(size))
    elif order == "right":
        positions = list(range(size - 1, -1, -1))
    else:
        positions = sorted(range(size), key=lambda index: (abs(2 * index - (size - 1)), index))
    place = {position: rank for rank, position in enumerate(positions)}
    frames = []
    for frame in range(count):
        decoded = size * frame // count
        shown = []
        for index, character in enumerate(characters):
            if place[index] < decoded or character == " ":
                shown.append(character)
            else:
                glyphs = narrow if ord(character) < 0x370 and character != "×" else wide
                shown.append(glyphs[(index * 3 + frame * 5 + encoding) % len(glyphs)])
        frames.append("".join(shown))
    return tuple(frames)


def garbled(text: str) -> str:
    """EASTER EGG (彩蛋), intentional: text as a program that reads UTF-8 as GBK shows it, e.g. (・ω・) as
    (銉幌夈兓); the main window's garbled() is the same. Shown only after five quick clicks; not an encoding bug."""
    return text.encode("utf-8").decode("gb18030", errors="replace").replace("\ufffd", "?")


def face_change_texts(shown: str, target: str, motion: str, count: int) -> tuple[str, ...]:
    """What the desk assistant shows, one per tick, before the face target when motion starts while it shows the
    face shown: the count-th encoding animation; for the easter egg (GARBLED) the garbled face first; nothing for a
    blink or when the face stays the same."""
    normalized = motion.upper()
    if normalized == "GARBLED":
        return (garbled(target),) * GARBLED_HOLD_FRAMES + encoding_frames(target, 1, ENCODING_FRAMES)
    if normalized == "BLINK" or shown == target:
        return ()
    return encoding_frames(target, ENCODING_ORDER[count % len(ENCODING_ORDER)], ENCODING_FRAMES)


# The desk assistant's frames and the expression pack position each shows (memorive/desktop/settings/expression_packs.py).
# app.py (configure_assistant_native) draws them from the pack in use: a text face on a white card, a picture as it is.
ASSISTANT_FRAME_POSITIONS = {
    "idle": "idle", "blink-open": "open", "blink-closed": "closed", "interaction": "interaction",
    "working": "working", "success": "success", "save-success": "saved", "error": "error",
    "message": "message", "drag": "drag", "sleep": "sleep",
}


def assistant_looks(pack: dict[str, Any] | None, mark: Path) -> dict[str, list[dict[str, Any]]]:
    """Each desk assistant frame's variants it can draw: {"text"} or {"pictures", "ms", "loop"}. Pictures GDI+ cannot
    read (WebP, SVG; they show in the main window only) are left out, a frame left with none shows idle's, and
    without a pack, or without an idle it can draw, every frame is the Memorive mark."""
    from memorive.desktop.settings.expression_packs import PET_TYPES
    drawable: dict[str, list[dict[str, Any]]] = {}
    for frame, position in ASSISTANT_FRAME_POSITIONS.items():
        variants = []
        for look in (pack or {}).get("looks", {}).get(position) or ():
            if "text" in look:
                variants.append({"text": look["text"]})
                continue
            files = [look["image"]] if "image" in look else look["frames"]
            if all(Path(name).suffix.lower() in PET_TYPES for name in files):
                variants.append({"pictures": [Path(pack["root"]) / name for name in files],
                                 "ms": look.get("ms", [0] * len(files)), "loop": look.get("loop", True)})
        drawable[frame] = variants
    idle = drawable["idle"] or [{"pictures": [mark], "ms": [0], "loop": False}]
    return {frame: variants or idle for frame, variants in drawable.items()}


def public_motion_contract() -> dict[str, object]:
    return {
        "source_sha256": ASSISTANT_PETS_FINAL_SHA256,
        "durations_ms": dict(MOTION_DURATIONS_MS),
        "auto_blink_ms": [AUTO_BLINK_MIN_MS, AUTO_BLINK_MAX_MS],
        "native_frame_interval_ms": NATIVE_ANIMATION_FRAME_INTERVAL_MS,
        "terminal_animation_coalesce_ms": TERMINAL_ANIMATION_COALESCE_MS,
        "single_shot": True,
    }
