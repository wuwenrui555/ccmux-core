"""Tests for ccmux-core CLI: list and watch subcommands."""

from __future__ import annotations

import json

from ccmux_core.cli import (
    _bindings_table,
    _event_body,
    _event_label,
    _header,
    _state_body,
    _state_label,
    _state_to_json,
    build_parser,
)
from ccmux_core.discover import TmuxBinding
from ccmux_core.state import Blocked, Dead, Idle, Working


def test_parser_list_subcommand():
    p = build_parser()
    args = p.parse_args(["list"])
    assert args.cmd == "list"


def test_parser_watch_subcommand_takes_session():
    p = build_parser()
    args = p.parse_args(["watch", "ccmux"])
    assert args.cmd == "watch"
    assert args.session == "ccmux"


def test_parser_version():
    p = build_parser()
    args = p.parse_args(["version"])
    assert args.cmd == "version"


def test_bindings_table_empty():
    out = _bindings_table([])
    assert "no live tmux sessions" in out.lower()


def test_bindings_table_single():
    out = _bindings_table(
        [
            TmuxBinding(
                tmux_session="ccmux",
                pane_id="%42",
                window_id="@0",
                primary_session_id="abc-12345",
                last_event_at="2026-05-11T01:55:42Z",
            )
        ]
    )
    assert "ccmux" in out
    assert "%42" in out
    assert "abc-12345" in out


def test_state_to_json_idle():
    s = json.loads(_state_to_json(Idle(reason="start")))
    assert s == {"type": "Idle", "reason": "start"}


def test_state_to_json_working():
    s = json.loads(_state_to_json(Working(tool_name="Bash")))
    assert s == {"type": "Working", "tool_name": "Bash"}


def test_state_to_json_blocked():
    s = json.loads(
        _state_to_json(
            Blocked(kind="permission", tool_name="Bash", tool_input={"x": 1})
        )
    )
    assert s == {
        "type": "Blocked",
        "kind": "permission",
        "tool_name": "Bash",
        "tool_input": {"x": 1},
    }


def test_state_to_json_dead():
    s = json.loads(_state_to_json(Dead(reason="pane_lost", detail=None)))
    assert s == {"type": "Dead", "reason": "pane_lost", "detail": None}


# ---------------------------------------------------------------------------
# Pretty mode helpers
# ---------------------------------------------------------------------------


def test_state_label_uppercase_class_name():
    assert _state_label(Idle(reason="stop")) == "IDLE"
    assert _state_label(Working(tool_name="Bash")) == "WORKING"
    assert (
        _state_label(Blocked(kind="permission", tool_name="Bash", tool_input=None))
        == "BLOCKED"
    )
    assert _state_label(Dead(reason="pane_lost")) == "DEAD"


def test_state_body_idle_shows_reason():
    assert _state_body(Idle(reason="stop"), None) == "reason=stop"


def test_state_body_working_with_tool_and_spinner():
    body = _state_body(Working(tool_name="Bash"), "Thinking… (3s)")
    assert "tool=Bash" in body
    assert "spinner=Thinking… (3s)" in body
    assert " · " in body


def test_state_body_working_without_spinner():
    assert _state_body(Working(tool_name="Bash"), None) == "tool=Bash"


def test_state_body_working_no_tool_no_spinner_marks_starting():
    assert _state_body(Working(tool_name=None), None) == "(starting)"


def test_state_body_blocked():
    body = _state_body(
        Blocked(kind="permission", tool_name="Bash", tool_input=None), None
    )
    assert body == "kind=permission · tool=Bash"


def test_state_body_dead_with_detail():
    body = _state_body(Dead(reason="session_end", detail="user_exit"), None)
    assert body == "reason=session_end · detail=user_exit"


def test_event_label_includes_type():
    assert _event_label({"event_type": "pre_tool_use"}) == "EVENT · pre_tool_use"


def test_event_body_pre_tool_use_shows_tool():
    ev = {"event_type": "pre_tool_use", "payload": {"tool_name": "Bash"}}
    assert _event_body(ev) == "tool=Bash"


def test_event_body_user_prompt_submit_shows_prompt():
    ev = {"event_type": "user_prompt_submit", "payload": {"prompt": "hello\nworld"}}
    # newlines collapsed via json.dumps escape
    assert _event_body(ev) == "hello\\nworld"


def test_header_layout():
    out = _header(
        ts="03:38:08",
        tmux_session="ccmux",
        window_id="@80",
        primary_sid="504921bb-1a7e",
        label="WORKING",
    )
    assert out == "[ 03:38:08 ccmux@80 504921bb ] WORKING"
