"""Adapters that run a command-line model for the execution core, one module per CLI.

cli_common.py holds what six of them share; codex_cli.py has its own structure.
"""

from memorive.models.gateway.execution_core.adapters.codex_cli import (
    CodexAdapterViolation,
    CodexCliAdapter,
    CodexCliInvocation,
    CodexCliPolicy,
)
from memorive.models.gateway.execution_core.adapters.claude_code import (
    ClaudeCodeAdapter,
    ClaudeCodeInvocation,
    ClaudeCodePolicy,
    ClaudeCodeViolation,
)
from memorive.models.gateway.execution_core.adapters.codebuddy_code import (
    CodeBuddyCodeAdapter,
    CodeBuddyCodeInvocation,
    CodeBuddyCodePolicy,
    CodeBuddyCodeViolation,
)
from memorive.models.gateway.execution_core.adapters.github_copilot_cli import (
    GitHubCopilotCLIAdapter,
    GitHubCopilotCLIInvocation,
    GitHubCopilotCLIPolicy,
    GitHubCopilotCLIViolation,
)
from memorive.models.gateway.execution_core.adapters.gemini_cli import (
    GeminiCliAdapter,
    GeminiCliInvocation,
    GeminiCliPolicy,
    GeminiCliViolation,
)
from memorive.models.gateway.execution_core.adapters.qwen_code import (
    QwenCodeAdapter,
    QwenCodeInvocation,
    QwenCodePolicy,
    QwenCodeViolation,
)
from memorive.models.gateway.execution_core.adapters.kimi_code import (
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
