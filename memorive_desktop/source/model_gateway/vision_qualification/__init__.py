"""PAGE-RECOGNITION non-executing vision qualification seams."""

from .request_contracts import (
    VisionRequestContractError,
    build_presend_receipt,
    evaluate_presend_readiness,
    project_terminal_evidence,
)
from .wrappers import (
    LocalVisionAdapter,
    build_claude_api_projection,
    build_openai_api_image_projection,
    build_openai_cli_image_projection,
    inspect_cli_event_stream,
    not_configured_local_capability,
    vision_channel_doctor,
)

__all__ = [
    "LocalVisionAdapter",
    "VisionRequestContractError",
    "build_claude_api_projection",
    "build_openai_api_image_projection",
    "build_openai_cli_image_projection",
    "build_presend_receipt",
    "evaluate_presend_readiness",
    "inspect_cli_event_stream",
    "not_configured_local_capability",
    "project_terminal_evidence",
    "vision_channel_doctor",
]
