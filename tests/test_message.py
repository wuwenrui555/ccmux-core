"""Tests for L1 Message dataclasses."""

from __future__ import annotations

import dataclasses

import pytest

from ccmux_core.message import (
    AssistantText,
    Message,
    PermissionRequest,
    ToolCall,
    ToolResult,
    UserPrompt,
)


def test_user_prompt_frozen_with_fields():
    m = UserPrompt(text="hi", timestamp=1.0)
    assert m.text == "hi"
    assert m.timestamp == 1.0
    with pytest.raises(dataclasses.FrozenInstanceError):
        m.text = "x"


def test_assistant_text_equality_by_value():
    assert AssistantText(text="ok", timestamp=2.0) == AssistantText(
        text="ok", timestamp=2.0
    )


def test_tool_call_carries_input_dict():
    m = ToolCall(tool_name="Bash", tool_input={"command": "ls"}, timestamp=3.0)
    assert m.tool_input == {"command": "ls"}


def test_tool_result_has_is_error_flag():
    ok = ToolResult(tool_name="Bash", output="files", is_error=False, timestamp=4.0)
    err = ToolResult(tool_name="Bash", output="bad", is_error=True, timestamp=4.0)
    assert ok.is_error is False
    assert err.is_error is True


def test_permission_request_fields():
    m = PermissionRequest(tool_name="Bash", tool_input={"command": "rm"}, timestamp=5.0)
    assert m.tool_name == "Bash"
    assert m.tool_input == {"command": "rm"}


def test_message_union_includes_all_kinds():
    msgs: list[Message] = [
        UserPrompt(text="x", timestamp=0),
        AssistantText(text="y", timestamp=0),
        ToolCall(tool_name="T", tool_input={}, timestamp=0),
        ToolResult(tool_name="T", output="", is_error=False, timestamp=0),
        PermissionRequest(tool_name="T", tool_input={}, timestamp=0),
    ]
    assert len(msgs) == 5
