"""Provider-specific thin adapters for the Execution execution core."""

from .codex_cli import (
    CodexAdapterViolation,
    CodexCliAdapter,
    CodexCliInvocation,
    CodexCliPolicy,
)
from .claude_code import (
    ClaudeCodeInvocation,
    ClaudeCodePolicy,
    ClaudeCodeViolation,
)
from .codebuddy_code import (
    CodeBuddyCodeInvocation,
    CodeBuddyCodePolicy,
    CodeBuddyCodeViolation,
)
from .github_copilot_cli import (
    GitHubCopilotCLIInvocation,
    GitHubCopilotCLIPolicy,
    GitHubCopilotCLIViolation,
)
from .gemini_cli import (
    GeminiCliInvocation,
    GeminiCliPolicy,
    GeminiCliViolation,
)
from .qwen_code import (
    QwenCodeInvocation,
    QwenCodePolicy,
    QwenCodeViolation,
)
from .kimi_code import (
    KimiCodeInvocation,
    KimiCodePolicy,
    KimiCodeViolation,
)

__all__ = [
    "CodexAdapterViolation",
    "CodexCliAdapter",
    "CodexCliInvocation",
    "CodexCliPolicy",
    "ClaudeCodeInvocation",
    "ClaudeCodePolicy",
    "ClaudeCodeViolation",
    "CodeBuddyCodeInvocation",
    "CodeBuddyCodePolicy",
    "CodeBuddyCodeViolation",
    "GitHubCopilotCLIInvocation",
    "GitHubCopilotCLIPolicy",
    "GitHubCopilotCLIViolation",
    "GeminiCliInvocation",
    "GeminiCliPolicy",
    "GeminiCliViolation",
    "QwenCodeInvocation",
    "QwenCodePolicy",
    "QwenCodeViolation",
    "KimiCodeInvocation",
    "KimiCodePolicy",
    "KimiCodeViolation",
]
