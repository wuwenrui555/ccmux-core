"""Tests for state dataclasses — frozen, equality, fields."""

from __future__ import annotations

import dataclasses

import pytest

from ccmux_core.state import Blocked, Dead, Idle, State, Working


def test_idle_frozen_with_reason():
    s = Idle(reason="start")
    assert s.reason == "start"
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.reason = "stop"


def test_idle_equality_by_value():
    assert Idle(reason="stop") == Idle(reason="stop")
    assert Idle(reason="stop") != Idle(reason="start")


def test_working_optional_tool_name():
    assert Working(tool_name=None).tool_name is None
    assert Working(tool_name="Bash").tool_name == "Bash"
    assert Working(tool_name="Bash") == Working(tool_name="Bash")
    assert Working(tool_name="Bash") != Working(tool_name="Read")


def test_blocked_carries_kind_tool_and_input():
    b = Blocked(kind="permission", tool_name="Bash", tool_input={"command": "ls"})
    assert b.kind == "permission"
    assert b.tool_name == "Bash"
    assert b.tool_input == {"command": "ls"}


def test_blocked_kind_values():
    for kind in ("permission", "ask_user", "exit_plan_mode"):
        Blocked(kind=kind, tool_name="X", tool_input=None)


def test_dead_with_detail():
    d = Dead(reason="session_end", detail="user_exit")
    assert d.reason == "session_end"
    assert d.detail == "user_exit"


def test_dead_detail_defaults_to_none():
    d = Dead(reason="pane_lost")
    assert d.detail is None


def test_state_is_union_of_four():
    assert State is not None
    Idle(reason="start")
    Working(tool_name=None)
    Blocked(kind="permission", tool_name="X", tool_input=None)
    Dead(reason="session_end")


def test_blocked_has_request_id_default_none():
    b = Blocked(kind="permission", tool_name="Bash", tool_input={})
    assert b.request_id is None


def test_blocked_has_expired_default_false():
    b = Blocked(kind="permission", tool_name="Bash", tool_input={})
    assert b.expired is False


def test_blocked_accepts_request_id_and_expired():
    b = Blocked(
        kind="permission",
        tool_name="Bash",
        tool_input={},
        request_id="r-abc123",
        expired=True,
    )
    assert b.request_id == "r-abc123"
    assert b.expired is True


def test_blocked_equality_includes_request_id_and_expired():
    a = Blocked(kind="permission", tool_name="Bash", tool_input={}, request_id="r-1")
    b = Blocked(kind="permission", tool_name="Bash", tool_input={}, request_id="r-2")
    assert a != b
