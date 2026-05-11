"""Tests for ccmux-core CLI: list and watch subcommands."""

from __future__ import annotations

import json

from ccmux_core.cli import _bindings_table, _state_to_json, build_parser
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
    out = _bindings_table([
        TmuxBinding(
            tmux_session="ccmux",
            pane_id="%42",
            window_id="@0",
            primary_session_id="abc-12345",
            last_event_at="2026-05-11T01:55:42Z",
        )
    ])
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
    s = json.loads(_state_to_json(
        Blocked(kind="permission", tool_name="Bash", tool_input={"x": 1})
    ))
    assert s == {
        "type": "Blocked",
        "kind": "permission",
        "tool_name": "Bash",
        "tool_input": {"x": 1},
    }


def test_state_to_json_dead():
    s = json.loads(_state_to_json(Dead(reason="pane_lost", detail=None)))
    assert s == {"type": "Dead", "reason": "pane_lost", "detail": None}
