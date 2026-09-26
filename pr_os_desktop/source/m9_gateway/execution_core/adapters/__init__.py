"""Provider-specific thin adapters for the P05 execution core."""

from .codex_cli import (
    CodexAdapterViolation,
    CodexCliAdapter,
    CodexCliInvocation,
    CodexCliPolicy,
)
from .claude_code import (
    ClaudeCodeAdapter,
    ClaudeCodeInvocation,
    ClaudeCodePolicy,
    ClaudeCodeViolation,
)
from .codebuddy_code import (
    CodeBuddyCodeAdapter,
    CodeBuddyCodeInvocation,
    CodeBuddyCodePolicy,
    CodeBuddyCodeViolation,
)
from .github_copilot_cli import (
    GitHubCopilotCLIAdapter,
    GitHubCopilotCLIInvocation,
    GitHubCopilotCLIPolicy,
    GitHubCopilotCLIViolation,
)
from .gemini_cli import (
    GeminiCliAdapter,
    GeminiCliInvocation,
    GeminiCliPolicy,
    GeminiCliViolation,
)
from .qwen_code import (
    QwenCodeAdapter,
    QwenCodeInvocation,
    QwenCodePolicy,
    QwenCodeViolation,
)
from .kimi_code import (
    KimiCodeAdapter,
    KimiCodeInvocation,
    KimiCodePolicy,
    KimiCodeViolation,
)

__all__ = [
    "CodexAdapterViolation",
    "CodexCliAdapter",
    "CodexCliInvocation",
    "CodexCliPolicy",
    "ClaudeCodeAdapter",
    "ClaudeCodeInvocation",
    "ClaudeCodePolicy",
    "ClaudeCodeViolation",
    "CodeBuddyCodeAdapter",
    "CodeBuddyCodeInvocation",
    "CodeBuddyCodePolicy",
    "CodeBuddyCodeViolation",
    "GitHubCopilotCLIAdapter",
    "GitHubCopilotCLIInvocation",
    "GitHubCopilotCLIPolicy",
    "GitHubCopilotCLIViolation",
    "GeminiCliAdapter",
    "GeminiCliInvocation",
    "GeminiCliPolicy",
    "GeminiCliViolation",
    "QwenCodeAdapter",
    "QwenCodeInvocation",
    "QwenCodePolicy",
    "QwenCodeViolation",
    "KimiCodeAdapter",
    "KimiCodeInvocation",
    "KimiCodePolicy",
    "KimiCodeViolation",
]
