"""L1 normalized Message stream for ccmux-core.

The Message family is a deduplicated fusion of hook events and
parsed transcript items. Each Message kind is sourced from
exactly one upstream channel; see the L2 design spec for the
dedup source-of-truth table.

Each dataclass is frozen so it can be safely passed across async
boundaries and compared by value.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UserPrompt:
    """Sourced from events.user_prompt_submit."""

    text: str
    timestamp: float


@dataclass(frozen=True)
class AssistantText:
    """Sourced from transcript Assistant text content."""

    text: str
    timestamp: float


@dataclass(frozen=True)
class ToolCall:
    """Sourced from transcript Assistant tool_use blocks."""

    tool_name: str
    tool_input: dict
    timestamp: float


@dataclass(frozen=True)
class ToolResult:
    """Sourced from transcript tool_result blocks."""

    tool_name: str
    output: str
    is_error: bool
    timestamp: float


@dataclass(frozen=True)
class PermissionRequest:
    """Sourced from events.permission_request (no transcript fallback)."""

    tool_name: str
    tool_input: dict
    timestamp: float


Message = UserPrompt | AssistantText | ToolCall | ToolResult | PermissionRequest
