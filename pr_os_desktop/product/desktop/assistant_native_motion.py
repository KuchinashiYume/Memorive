from __future__ import annotations

from dataclasses import dataclass
from typing import Final


# User-selected final interaction authority. The native Windows overlay exists
# only because a colour-keyed WebView2 child cannot be the reliable hit target;
# its timing and motion must remain a projection of this exact HTML source.
PART11_PETS_FINAL_SHA256: Final[str] = (
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
}


_TRANSFORM_KEYFRAMES: Final[dict[str, tuple[tuple[float, float, float, float, float, float], ...]]] = {
    # progress, x, y, rotation, scale, opacity
    "INTERACTION": (
        (0.00, 0.0, 0.0, 0.0, 1.00, 1.00),
        (0.24, 0.0, 1.0, -9.0, 1.00, 1.00),
        (0.62, 0.0, 1.0, -9.0, 1.00, 1.00),
        (0.82, 0.0, 0.0, 2.0, 1.00, 1.00),
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
}


_TIMING_CURVES: Final[dict[str, tuple[float, float, float, float]]] = {
    # Exact animation timing functions declared by the selected final
    # part11_pets.html. CSS applies the function independently between each
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
    transform_name = "INTERACTION" if normalized == "ATTENTION" else normalized
    values = _interpolate(
        _TRANSFORM_KEYFRAMES[transform_name],
        transform_progress,
        _TIMING_CURVES[transform_name],
    )
    return MotionSample(_TRANSIENT_FRAMES[normalized], *values)


def public_motion_contract() -> dict[str, object]:
    return {
        "source_sha256": PART11_PETS_FINAL_SHA256,
        "durations_ms": dict(MOTION_DURATIONS_MS),
        "auto_blink_ms": [AUTO_BLINK_MIN_MS, AUTO_BLINK_MAX_MS],
        "native_frame_interval_ms": NATIVE_ANIMATION_FRAME_INTERVAL_MS,
        "terminal_animation_coalesce_ms": TERMINAL_ANIMATION_COALESCE_MS,
        "single_shot": True,
    }
