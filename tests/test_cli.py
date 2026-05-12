"""Tests for ccmux-core CLI: list and watch subcommands."""

from __future__ import annotations

import json

from ccmux_core.cli import (
    _bindings_table,
    _event_body,
    _event_label,
    _header,
    _overwrite_prefix,
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


# ---------------------------------------------------------------------------
# In-place spinner overwrite
# ---------------------------------------------------------------------------


def test_overwrite_prefix_first_spinner_does_not_overwrite():
    out = _overwrite_prefix(
        is_spinner_now=True,
        last_was_spinner=False,
        can_overwrite=True,
        last_block_lines=4,
    )
    assert out == ""


def test_overwrite_prefix_consecutive_spinner_overwrites():
    out = _overwrite_prefix(
        is_spinner_now=True,
        last_was_spinner=True,
        can_overwrite=True,
        last_block_lines=4,
    )
    assert out == "\x1b[4A\x1b[J"


def test_overwrite_prefix_non_spinner_does_not_overwrite():
    # STATE arrives after a spinner: never overwrites.
    out = _overwrite_prefix(
        is_spinner_now=False,
        last_was_spinner=True,
        can_overwrite=True,
        last_block_lines=4,
    )
    assert out == ""


def test_overwrite_prefix_disabled_when_not_a_tty():
    # When stdout is piped, can_overwrite is False; never overwrites
    # so consumers downstream (e.g., file output, jq) see clean
    # consecutive blocks.
    out = _overwrite_prefix(
        is_spinner_now=True,
        last_was_spinner=True,
        can_overwrite=False,
        last_block_lines=4,
    )
    assert out == ""


def test_overwrite_prefix_uses_last_block_line_count():
    out = _overwrite_prefix(
        is_spinner_now=True,
        last_was_spinner=True,
        can_overwrite=True,
        last_block_lines=7,
    )
    assert "\x1b[7A" in out


# ---------------------------------------------------------------------------
# State summary + ANSI color
# ---------------------------------------------------------------------------


def test_state_summary_for_each_state():
    from ccmux_core.cli import _state_summary
    from ccmux_core.state import Blocked, Dead, Idle, Working

    assert _state_summary(None) == "--"
    assert _state_summary(Idle(reason="stop")) == "IDLE(stop)"
    assert _state_summary(Working(tool_name="Bash")) == "WORKING(Bash)"
    assert _state_summary(Working(tool_name=None)) == "WORKING(--)"
    assert (
        _state_summary(Blocked(kind="permission", tool_name="Bash", tool_input={}))
        == "BLOCKED(permission)"
    )
    assert (
        _state_summary(
            Blocked(kind="permission", tool_name="Bash", tool_input={}, expired=True)
        )
        == "BLOCKED(expired)"
    )
    assert _state_summary(Dead(reason="session_end")) == "DEAD"


def test_header_with_state_slot():
    from ccmux_core.cli import _header
    from ccmux_core.state import Working

    out = _header(
        ts="12:34:56",
        tmux_session="ccmux",
        window_id="@80",
        primary_sid="504921bb-...-...",
        label="ASSISTANT · hook",
        state=Working(tool_name="Bash"),
        use_color=False,
    )
    assert "[ 12:34:56 ccmux@80 504921bb · WORKING(Bash) ] ASSISTANT · hook" == out


def test_header_omits_state_slot_when_none():
    from ccmux_core.cli import _header

    out = _header(
        ts="12:34:56",
        tmux_session="ccmux",
        window_id="@80",
        primary_sid="504921bb",
        label="EVENT · session_start",
        state=None,
        use_color=False,
    )
    assert "[ 12:34:56 ccmux@80 504921bb ] EVENT · session_start" == out


def test_header_with_color_wraps_state_and_label():
    from ccmux_core.cli import _ANSI, _header
    from ccmux_core.state import Idle

    out = _header(
        ts="12:34:56",
        tmux_session="ccmux",
        window_id="@80",
        primary_sid="504921bb",
        label="IDLE",
        state=Idle(reason="stop"),
        use_color=True,
    )
    # state colored green
    assert _ANSI["green"] in out
    # reset present at least once
    assert _ANSI["reset"] in out


def test_color_for_label_known_heads():
    from ccmux_core.cli import _ANSI, _color_for_label

    assert _color_for_label("EVENT · stop") == _ANSI["blue"]
    assert _color_for_label("ASSISTANT · hook") == _ANSI["white_bold"]
    assert _color_for_label("SPINNER · Spinner") == _ANSI["gray"]
    assert _color_for_label("IDLE") == _ANSI["green"]
    assert _color_for_label("WORKING") == _ANSI["yellow"]
    assert _color_for_label("") == ""


# ---------------------------------------------------------------------------
# Bottom status bar
# ---------------------------------------------------------------------------


def test_render_status_lines_with_state_and_spinner():
    from ccmux_core.cli import _render_status_lines
    from ccmux_core.state import Working

    class _FakeActivity:
        text = "Working on a thing"
        todos = ()

    lines = _render_status_lines(
        state=Working(tool_name="Bash"),
        latest_hook_event={
            "event_type": "pre_tool_use",
            "timestamp": "2026-05-12T06:30:00+00:00",
            "payload": {"tool_name": "Bash"},
        },
        latest_spinner_activity=_FakeActivity(),
        use_color=False,
        width=80,
    )
    # at minimum: a state line, a hook line, a spinner line
    assert any("WORKING(Bash)" in line for line in lines)
    assert any("pre_tool_use" in line for line in lines)
    assert any("Working on a thing" in line for line in lines)


def test_render_status_lines_multi_line_spinner_takes_first_line_only():
    from ccmux_core.cli import _render_status_lines
    from ccmux_core.state import Working

    class _FakeActivity:
        text = "line one\nline two\nline three"
        todos = ()

    lines = _render_status_lines(
        state=Working(tool_name=None),
        latest_hook_event=None,
        latest_spinner_activity=_FakeActivity(),
        use_color=False,
        width=120,
    )
    assert any("line one" in line for line in lines)
    # only the first line is rendered now; the rest are dropped
    assert not any("line two" in line for line in lines)
    assert not any("line three" in line for line in lines)


def test_render_status_lines_with_todos():
    from ccmux_core.cli import _render_status_lines
    from ccmux_core.state import Working

    class _FakeActivity:
        text = "Working"
        todos = ("Run tests", "Check coverage", "Open PR")

    lines = _render_status_lines(
        state=Working(tool_name=None),
        latest_hook_event=None,
        latest_spinner_activity=_FakeActivity(),
        use_color=False,
        width=80,
    )
    # one line per todo
    for todo in ("Run tests", "Check coverage", "Open PR"):
        assert any(todo in line for line in lines), f"missing {todo!r}"


def test_render_status_lines_handles_none_state():
    from ccmux_core.cli import _render_status_lines

    lines = _render_status_lines(
        state=None,
        latest_hook_event=None,
        latest_spinner_activity=None,
        use_color=False,
        width=80,
    )
    # should still produce some lines without crashing
    assert lines


def test_status_separator_fills_width():
    from ccmux_core.cli import _status_separator, _visual_width

    sep = _status_separator(40)
    # starts with the STATUS label
    assert sep.startswith("─── STATUS")
    # total visual width matches
    assert _visual_width(sep) == 40


def test_status_separator_colored_is_bold_and_state_tinted():
    from ccmux_core.cli import _ANSI, _status_separator
    from ccmux_core.state import Working

    sep = _status_separator(40, state=Working(tool_name="Bash"), use_color=True)
    # bold + yellow (Working's color)
    assert _ANSI["bold"] in sep
    assert _ANSI["yellow"] in sep
    assert _ANSI["reset"] in sep


def test_status_separator_uncolored_when_state_is_none():
    from ccmux_core.cli import _ANSI, _status_separator

    sep = _status_separator(40, state=None, use_color=True)
    # bold still applied but no state color
    assert _ANSI["bold"] in sep
    # no state-specific color leaks in
    for k in ("green", "yellow", "magenta", "red_dim"):
        assert _ANSI[k] not in sep


def test_render_status_lines_emits_todos_as_json_array_single_line():
    """spinner.todos are emitted as one json.dumps line, not one-per-line."""
    from ccmux_core.cli import _render_status_lines
    from ccmux_core.state import Working

    class _FakeActivity:
        text = "Working"
        todos = ("⎿  ◻ Item one", "   ✔ Item two", "    … +17 completed")

    lines = _render_status_lines(
        state=Working(tool_name=None),
        latest_hook_event=None,
        latest_spinner_activity=_FakeActivity(),
        use_color=False,
        width=200,
    )
    # all three todos appear inside a single json line
    todos_lines = [
        line for line in lines if line.startswith("[") and "Item one" in line
    ]
    assert len(todos_lines) == 1, f"expected 1 json'd todos line; got: {todos_lines}"
    todos_line = todos_lines[0]
    for todo in ("⎿  ◻ Item one", "   ✔ Item two", "    … +17 completed"):
        assert todo in todos_line, f"missing {todo!r} in {todos_line!r}"


def test_render_status_lines_always_returns_8_rows():
    from ccmux_core.cli import _render_status_lines

    # No state, no hook, no spinner
    lines = _render_status_lines(
        state=None,
        latest_hook_event=None,
        latest_spinner_activity=None,
        use_color=False,
        width=80,
    )
    assert len(lines) == 8


def test_render_status_lines_8_rows_even_with_todos():
    from ccmux_core.cli import _render_status_lines
    from ccmux_core.state import Working

    class _FakeActivity:
        text = "Working"
        todos = tuple(f"todo {i}" for i in range(20))  # lots of todos

    lines = _render_status_lines(
        state=Working(tool_name="Bash"),
        latest_hook_event=None,
        latest_spinner_activity=_FakeActivity(),
        use_color=False,
        width=300,
    )
    # 8 rows regardless of how many todos
    assert len(lines) == 8


def test_parser_watch_accepts_no_status():
    p = build_parser()
    args = p.parse_args(["watch", "ccmux", "--no-status"])
    assert args.no_status is True


def test_parser_watch_no_status_default_false():
    p = build_parser()
    args = p.parse_args(["watch", "ccmux"])
    assert args.no_status is False


# ---------------------------------------------------------------------------
# L1 message formatters (used by watch's pump_messages)
# ---------------------------------------------------------------------------


def test_l1_message_label_for_each_type():
    from ccmux_core.cli import _l1_message_label
    from ccmux_core.message import (
        AssistantText,
        PermissionRequest,
        ToolCall,
        ToolResult,
        UserPrompt,
    )

    assert _l1_message_label(UserPrompt(text="x", timestamp=0)) == "USER"
    assert _l1_message_label(AssistantText(text="y", timestamp=0)) == "ASSISTANT"
    assert (
        _l1_message_label(ToolCall(tool_name="Bash", tool_input={}, timestamp=0))
        == "TOOL · Bash"
    )
    assert (
        _l1_message_label(
            ToolResult(tool_name="Bash", output="", is_error=False, timestamp=0)
        )
        == "TOOL · Bash"
    )
    assert (
        _l1_message_label(
            ToolResult(tool_name="Bash", output="", is_error=True, timestamp=0)
        )
        == "TOOL · Bash · error"
    )
    assert (
        _l1_message_label(
            PermissionRequest(tool_name="Bash", tool_input={}, timestamp=0)
        )
        == "PERMISSION · Bash"
    )


def test_l1_message_body_for_each_type():
    from ccmux_core.cli import _l1_message_body
    from ccmux_core.message import (
        AssistantText,
        PermissionRequest,
        ToolCall,
        ToolResult,
        UserPrompt,
    )

    assert _l1_message_body(UserPrompt(text="hi", timestamp=0)) == "hi"
    assert _l1_message_body(AssistantText(text="ok", timestamp=0)) == "ok"
    assert "command" in _l1_message_body(
        ToolCall(tool_name="Bash", tool_input={"command": "ls"}, timestamp=0)
    )
    assert "stdout" in _l1_message_body(
        ToolResult(tool_name="Bash", output="stdout text", is_error=False, timestamp=0)
    )
    # body is just the input, not the tool name
    pr_body = _l1_message_body(
        PermissionRequest(tool_name="Bash", tool_input={"command": "rm"}, timestamp=0)
    )
    assert "Bash" not in pr_body
    assert "command" in pr_body


def test_ts_short_from_unix():
    from ccmux_core.cli import _ts_short_from_unix

    # Falsy (None / 0.0) → wall-clock now (HH:MM:SS format).
    out_none = _ts_short_from_unix(None)
    assert len(out_none) == 8 and out_none[2] == ":" and out_none[5] == ":"
    # Real epoch: 1234567890.0 → 2009-02-13 23:31:30 UTC.
    assert _ts_short_from_unix(1234567890.0) == "23:31:30"


def test_color_for_label_includes_tool_and_permission():
    from ccmux_core.cli import _ANSI, _color_for_label

    assert _color_for_label("TOOL · Bash") == _ANSI["blue"]
    assert _color_for_label("PERMISSION · Bash") == _ANSI["magenta"]
