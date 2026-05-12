"""ccmux-core CLI.

Subcommands:

* ``list`` — one-shot snapshot of currently-live tmux session bindings.
* ``watch <tmux_session>`` — stream the four observations to stdout
  as pretty blocks (default) or JSON Lines (``--json``).
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import shutil
import signal
import sys
import unicodedata
from datetime import UTC, datetime

from . import __version__
from .backend import Backend
from .config import pretty_width as _pretty_width
from .discover import TmuxBinding, list_live_tmux_bindings
from .state import Blocked, Dead, Idle, State, Working

_PRETTY_TRUNCATION_MARKER = "..."


# ---------------------------------------------------------------------------
# `list` subcommand
# ---------------------------------------------------------------------------


def _bindings_table(bindings: list[TmuxBinding]) -> str:
    if not bindings:
        return "(no live tmux sessions)"
    headers = [
        "TMUX_SESSION",
        "PANE_ID",
        "WINDOW_ID",
        "PRIMARY_SESSION_ID",
        "LAST_EVENT",
    ]
    rows = [
        [
            b.tmux_session,
            b.pane_id,
            b.window_id,
            b.primary_session_id,
            b.last_event_at,
        ]
        for b in bindings
    ]
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    lines = ["  ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True))]
    for r in rows:
        lines.append("  ".join(c.ljust(w) for c, w in zip(r, widths, strict=True)))
    return "\n".join(lines)


def cmd_list(args) -> int:
    bindings = list_live_tmux_bindings()
    print(_bindings_table(bindings))
    return 0


def cmd_version(args) -> int:
    print(__version__)
    return 0


# ---------------------------------------------------------------------------
# JSON serializers (used by --json mode)
# ---------------------------------------------------------------------------


def _state_to_json(state: State) -> str:
    if isinstance(state, Idle):
        return json.dumps({"type": "Idle", "reason": state.reason})
    if isinstance(state, Working):
        return json.dumps({"type": "Working", "tool_name": state.tool_name})
    if isinstance(state, Blocked):
        return json.dumps(
            {
                "type": "Blocked",
                "kind": state.kind,
                "tool_name": state.tool_name,
                "tool_input": state.tool_input,
            }
        )
    if isinstance(state, Dead):
        return json.dumps(
            {
                "type": "Dead",
                "reason": state.reason,
                "detail": state.detail,
            }
        )
    return json.dumps({"type": "Unknown"})


# ---------------------------------------------------------------------------
# Pretty layout helpers (mirror claude-tap watch-messages)
# ---------------------------------------------------------------------------


def _now_timestamp() -> str:
    """Wall-clock UTC ``HH:MM:SS.mmm`` for emit-side timestamping."""
    now = datetime.now(UTC)
    return now.strftime("%H:%M:%S") + f".{now.microsecond // 1000:03d}"


def _ts_short(ts: str | None) -> str:
    """Reduce an ISO timestamp to ``HH:MM:SS`` (UTC).

    When ``ts`` is None (state / spinner emissions that have no
    source-side timestamp), falls back to current wall-clock UTC
    so the header column always reads.
    """
    if not ts:
        return datetime.now(UTC).strftime("%H:%M:%S")
    raw = ts[:-1] + "+00:00" if ts.endswith("Z") else ts
    try:
        return datetime.fromisoformat(raw).strftime("%H:%M:%S")
    except (ValueError, TypeError):
        return ts[11:19] if len(ts) >= 19 else ts[:8]


def _ts_short_from_unix(ts: float | None) -> str:
    """Reduce a unix float timestamp to ``HH:MM:SS`` (UTC). None → current."""
    if not ts:
        return datetime.now(UTC).strftime("%H:%M:%S")
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%H:%M:%S")


def _visual_width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in ("F", "W") else 1 for ch in s)


def _visual_trim(s: str, width: int) -> str:
    if width <= 0:
        return ""
    w = 0
    out: list[str] = []
    for ch in s:
        cw = 2 if unicodedata.east_asian_width(ch) in ("F", "W") else 1
        if w + cw > width:
            break
        out.append(ch)
        w += cw
    return "".join(out)


def _trim_body(body: str) -> str:
    width = _pretty_width()
    if _visual_width(body) > width:
        keep = max(0, width - _visual_width(_PRETTY_TRUNCATION_MARKER))
        return _visual_trim(body, keep) + _PRETTY_TRUNCATION_MARKER
    return body


def _pretty_separator() -> str:
    """``─ HH:MM:SS.mmm ─...─`` to a visual width matching pretty_width."""
    now = _now_timestamp()
    prefix = f"─ {now} "
    rest = max(0, _pretty_width() - len(prefix))
    return prefix + "─" * rest


_ANSI = {
    "reset": "\x1b[0m",
    "dim": "\x1b[2m",
    "bold": "\x1b[1m",
    # state colors
    "green": "\x1b[32m",  # Idle
    "yellow": "\x1b[33m",  # Working
    "magenta": "\x1b[35m",  # Blocked
    "red_dim": "\x1b[2;31m",  # Dead
    # label colors
    "cyan": "\x1b[36m",  # STATE
    "blue": "\x1b[34m",  # EVENT
    "white_bold": "\x1b[1;37m",  # MESSAGE
    "gray": "\x1b[90m",  # SPINNER
}


def _state_summary(state) -> str:
    """Compact state label for the header. e.g. 'IDLE(stop)',
    'WORKING(Bash)', 'WORKING(--)', 'BLOCKED:perm', 'DEAD'."""
    if state is None:
        return "--"
    name = type(state).__name__.upper()
    if isinstance(state, Idle):
        return f"{name}({state.reason})"
    if isinstance(state, Working):
        tool = state.tool_name or "--"
        return f"{name}({tool})"
    if isinstance(state, Blocked):
        marker = "expired" if state.expired else state.kind
        return f"{name}({marker})"
    if isinstance(state, Dead):
        return name
    return name


def _color_for_state(state) -> str:
    if isinstance(state, Idle):
        return _ANSI["green"]
    if isinstance(state, Working):
        return _ANSI["yellow"]
    if isinstance(state, Blocked):
        return _ANSI["magenta"]
    if isinstance(state, Dead):
        return _ANSI["red_dim"]
    return ""


def _color_for_label(label: str) -> str:
    # label is "STATE · ..." / "EVENT · ..." / "MESSAGE · ..." / etc.
    head = label.split(" ", 1)[0].upper() if label else ""
    if head in {"IDLE", "WORKING", "BLOCKED", "DEAD"}:
        # state stream: color by state type
        return {
            "IDLE": _ANSI["green"],
            "WORKING": _ANSI["yellow"],
            "BLOCKED": _ANSI["magenta"],
            "DEAD": _ANSI["red_dim"],
        }.get(head, "")
    if head == "EVENT":
        return _ANSI["blue"]
    if head in {"ASSISTANT", "USER"}:
        return _ANSI["white_bold"]
    if head == "TOOL":
        return _ANSI["blue"]
    if head == "PERMISSION":
        return _ANSI["magenta"]
    if head == "SPINNER":
        return _ANSI["gray"]
    return ""


def _paint(text: str, color: str, *, enabled: bool) -> str:
    if not enabled or not color:
        return text
    return f"{color}{text}{_ANSI['reset']}"


def _header(
    *,
    ts: str,
    label: str,
    state: State | None = None,
    use_color: bool = False,
    # Backward-compat: callers from before the top-header refactor may
    # still pass these; they're ignored. New code should not set them.
    tmux_session: str | None = None,
    window_id: str | None = None,
    primary_sid: str | None = None,
) -> str:
    """``[ HH:MM:SS · WORKING(Bash) ] LABEL``.

    Compact per-block header. The tmux session / window / sid are
    NOT included anymore — those live in the fixed top header above
    the scroll region (``_top_header``). The state slot is omitted
    when ``state is None`` so we don't display ``· (waiting)`` clutter
    before any state has been observed.

    When ``use_color`` is true, the state slot and the label are
    wrapped in ANSI color escapes.
    """
    # tmux_session / window_id / primary_sid intentionally unused —
    # see docstring above. Kept in the signature for backward compat
    # with old callers (tests that haven't been updated yet).
    _ = (tmux_session, window_id, primary_sid)
    label_painted = _paint(label, _color_for_label(label), enabled=use_color)
    if state is None:
        return f"[ {ts} ] {label_painted}"
    summary = _state_summary(state)
    summary_painted = _paint(summary, _color_for_state(state), enabled=use_color)
    return f"[ {ts} · {summary_painted} ] {label_painted}"


def _top_header(
    tmux_session: str,
    window_id: str | None,
    primary_sid: str | None,
    width: int,
    *,
    use_color: bool = False,
) -> str:
    """``─── ccmux@80  504921bb ──────────...``.

    Drawn ONCE at the top of the scroll viewport so each per-block
    header doesn't need to repeat the session / sid. Width-padded to
    fill the line."""
    sid = (primary_sid or "")[:8] or "--------"
    tmux_frag = f"{tmux_session}{window_id or ''}"
    label = f"─── {tmux_frag}  {sid} "
    fill_count = max(0, width - _visual_width(label))
    line = label + "─" * fill_count
    if use_color:
        return f"{_ANSI['bold']}{line}{_ANSI['reset']}"
    return line


# ----- per-stream formatters --------------------------------------------


def _state_label(state: State) -> str:
    return type(state).__name__.upper()


def _state_body(state: State, latest_spinner_text: str | None) -> str:
    if isinstance(state, Idle):
        return f"reason={state.reason}"
    if isinstance(state, Working):
        parts = []
        if state.tool_name is not None:
            parts.append(f"tool={state.tool_name}")
        if latest_spinner_text:
            parts.append(f"spinner={latest_spinner_text}")
        return " · ".join(parts) if parts else "(starting)"
    if isinstance(state, Blocked):
        return f"kind={state.kind} · tool={state.tool_name}"
    if isinstance(state, Dead):
        body = f"reason={state.reason}"
        if state.detail:
            body += f" · detail={state.detail}"
        return body
    return "?"


def _event_label(event: dict) -> str:
    """Compact label for a hook event.

    For tool-related hooks, the tool name goes into the label so the
    body can stay clean JSON (mirroring the L1 ``TOOL · <name>`` /
    ``PERMISSION · <name>`` shape):

        EVENT · pre_tool_use · Bash
        EVENT · post_tool_use · Bash
        EVENT · permission_request · Bash

    For non-tool hooks, just ``EVENT · <type>``.
    """
    et = event.get("event_type", "?")
    tool = (event.get("payload") or {}).get("tool_name")
    if tool and et in ("pre_tool_use", "post_tool_use", "permission_request"):
        return f"EVENT · {et} · {tool}"
    return f"EVENT · {et}"


def _event_body(event: dict) -> str:
    """Body for a hook event — just the relevant payload content.

    Mirrors the L1 message body style (no ``tool=...`` prefix; the
    tool name is in the label via :func:`_event_label`).

        pre_tool_use / permission_request → json.dumps(tool_input)
        post_tool_use                     → json.dumps(tool_response)
        user_prompt_submit                → prompt text (escaped)
        stop                              → last_assistant_message
        notification                      → message text
        session_end                       → reason=<...>
        session_start                     → ""
    """
    payload = event.get("payload") or {}
    et = event.get("event_type", "")
    if et == "user_prompt_submit":
        return json.dumps(payload.get("prompt", ""), ensure_ascii=False)[1:-1]
    if et in ("pre_tool_use", "permission_request"):
        tool_input = payload.get("tool_input")
        return json.dumps(tool_input, ensure_ascii=False) if tool_input else ""
    if et == "post_tool_use":
        tool_response = payload.get("tool_response")
        return (
            json.dumps(tool_response, ensure_ascii=False)
            if tool_response is not None
            else ""
        )
    if et == "notification":
        return json.dumps(payload.get("message", ""), ensure_ascii=False)[1:-1]
    if et == "stop":
        last = payload.get("last_assistant_message", "")
        return json.dumps(last, ensure_ascii=False)[1:-1]
    if et == "session_end":
        return f"reason={payload.get('reason', '')!r}"
    if et == "session_start":
        return ""
    return ""


def _l1_message_label(msg) -> str:
    """Compact label for a Message union member.

    USER / ASSISTANT / TOOL · <name> / TOOL · <name> · error /
    PERMISSION · <name>.
    """
    from .message import (
        AssistantText,
        PermissionRequest,
        ToolCall,
        ToolResult,
        UserPrompt,
    )

    if isinstance(msg, UserPrompt):
        return "USER"
    if isinstance(msg, AssistantText):
        return "ASSISTANT"
    if isinstance(msg, ToolCall):
        return f"TOOL · {msg.tool_name}"
    if isinstance(msg, ToolResult):
        return f"TOOL · {msg.tool_name}" + (" · error" if msg.is_error else "")
    if isinstance(msg, PermissionRequest):
        return f"PERMISSION · {msg.tool_name}"
    return "?"


def _l1_message_body(msg) -> str:
    """Body text for a Message union member."""
    from .message import (
        AssistantText,
        PermissionRequest,
        ToolCall,
        ToolResult,
        UserPrompt,
    )

    if isinstance(msg, (UserPrompt, AssistantText)):
        # Single-line representation; trim by caller via _trim_body.
        return json.dumps(msg.text, ensure_ascii=False)[1:-1]
    if isinstance(msg, ToolCall):
        return json.dumps(msg.tool_input, ensure_ascii=False)
    if isinstance(msg, ToolResult):
        if isinstance(msg.output, str):
            return json.dumps(msg.output, ensure_ascii=False)[1:-1]
        return json.dumps(msg.output, ensure_ascii=False)
    if isinstance(msg, PermissionRequest):
        return json.dumps(msg.tool_input, ensure_ascii=False)
    return ""


def _overwrite_prefix(
    *,
    is_spinner_now: bool,
    last_was_spinner: bool,
    can_overwrite: bool,
    last_block_lines: int,
) -> str:
    """Return the ANSI prefix (or empty) that moves the cursor up and
    clears the previous spinner block when the next emit is also a
    spinner. Extracted from the inner loop in ``_watch_async`` so it
    can be unit-tested without spawning a Backend.
    """
    if is_spinner_now and last_was_spinner and can_overwrite:
        return f"\x1b[{last_block_lines}A\x1b[J"
    return ""


def _spinner_label(activity) -> str:
    if activity is None:
        return "SPINNER · none"
    return f"SPINNER · {type(activity).__name__}"


def _spinner_body(activity) -> str:
    if activity is None:
        return ""
    body = activity.text
    todos = getattr(activity, "todos", ()) or ()
    if todos:
        body += f" · todos={list(todos)}"
    return body


def _pretty_block(
    *,
    label: str,
    body: str,
    ts: str,
    tmux_session: str,
    window_id: str | None,
    primary_sid: str | None,
    state: State | None = None,
    use_color: bool = False,
) -> str:
    lines = [
        _pretty_separator(),
        _header(
            ts=ts,
            tmux_session=tmux_session,
            window_id=window_id,
            primary_sid=primary_sid,
            label=label,
            state=state,
            use_color=use_color,
        ),
        _trim_body(body),
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Bottom status bar (DECSTBM scroll region)
# ---------------------------------------------------------------------------


def _status_separator(width: int, *, state=None, use_color: bool = False) -> str:
    """Top separator line for the status bar.

    ``─── STATUS ──...──`` padded out to ``width`` visual cells.
    When ``use_color`` is True, the whole line is rendered bold and
    tinted with ``state``'s color (matches the state cell so the
    bar's tone tracks session state).
    """
    label = "─── STATUS "
    line = label + "─" * max(0, width - _visual_width(label))
    if use_color:
        color = _color_for_state(state) if state is not None else ""
        return f"{_ANSI['bold']}{color}{line}{_ANSI['reset']}"
    return line


def _hook_age_seconds(ts: str | None) -> int | None:
    """Seconds elapsed since ``ts`` (ISO-8601). None if unparseable."""
    if not ts:
        return None
    raw = ts[:-1] + "+00:00" if ts.endswith("Z") else ts
    try:
        when = datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    delta = datetime.now(UTC) - when
    return max(0, int(delta.total_seconds()))


def _render_status_lines(
    *,
    state,
    latest_hook_event: dict | None,
    latest_spinner_activity,
    use_color: bool,
    width: int,
    tmux_session: str = "",
    window_id: str | None = None,
    primary_sid: str | None = None,
) -> list[str]:
    """Returns exactly 8 content lines for the fixed 10-row status bar.

    Layout (combined with the separator + leading blank prepended by
    _emit_status):
      1: blank                              ← prepended
      2: separator                          ← prepended
      3: state=<SUMMARY>                    ← lines[0]
      4: (blank)                            ← lines[1]
      5: [ ts tmux@win sid ] EVENT · type   ← lines[2]
      6: <event body>  (Xs ago)             ← lines[3]
      7: (blank)                            ← lines[4]
      8: spinner=<first line>               ← lines[5]
      9: <todos as json.dumps([...])> or ""  ← lines[6]
      10: (blank)                           ← lines[7]
    """
    # state
    if state is None:
        summary = "(waiting)"
        state_color = ""
    else:
        summary = _state_summary(state)
        state_color = _color_for_state(state) if use_color else ""
    if use_color and state_color:
        state_line = f"state={_paint(summary, state_color, enabled=True)}"
    else:
        state_line = f"state={summary}"

    # hook (2 rows: header + body, always present)
    if latest_hook_event is None:
        hook_header = "(no hook events yet)"
        hook_body = ""
    else:
        ev_ts = _ts_short(latest_hook_event.get("timestamp"))
        hook_header = _header(
            ts=ev_ts,
            tmux_session=tmux_session,
            window_id=window_id,
            primary_sid=primary_sid,
            label=_event_label(latest_hook_event),
            state=None,  # state is on its own line; don't duplicate
            use_color=use_color,
        )
        hook_body = _event_body(latest_hook_event)
        age = _hook_age_seconds(latest_hook_event.get("timestamp"))
        if age is not None:
            hook_body = f"{hook_body}  ({age}s ago)" if hook_body else f"({age}s ago)"

    # spinner single line (collapse multi-line text to its first line)
    if latest_spinner_activity is None:
        spinner_line = "spinner=(none)"
    else:
        text = getattr(latest_spinner_activity, "text", "") or ""
        first = text.split("\n", 1)[0] if text else ""
        spinner_line = f"spinner={first}"

    # todos single line as json.dumps; "" when no todos
    if latest_spinner_activity is None:
        todos_line = ""
    else:
        todos = getattr(latest_spinner_activity, "todos", ()) or ()
        if todos:
            todo_strs = [getattr(t, "text", None) or str(t) for t in todos]
            todos_line = json.dumps(todo_strs, ensure_ascii=False)
        else:
            todos_line = ""

    # Body-style rows (hook body, todos) cap at _pretty_width() with
    # ellipsis truncation so they stay readable even on wide terminals.
    # The state / spinner / header rows stay short by construction and
    # use the full terminal width trim only as a hard backstop.
    hook_body = _trim_body(hook_body) if hook_body else hook_body
    todos_line = _trim_body(todos_line) if todos_line else todos_line

    rows = [
        state_line,
        "",
        hook_header,
        hook_body,
        "",
        spinner_line,
        todos_line,
        "",
    ]
    return [_visual_trim(line, width) for line in rows]


def _terminal_size() -> tuple[int, int]:
    """``(rows, cols)`` from ``shutil.get_terminal_size`` with a safe
    fallback if the terminal can't be queried.
    """
    sz = shutil.get_terminal_size(fallback=(80, 24))
    return sz.lines, sz.columns


def _emit_status(ctx: dict) -> None:
    """Recompute status content, adjust scroll region if its height
    changed, and redraw the status area at the bottom of the terminal.

    No-op when status is disabled.
    """
    if not ctx.get("status_enabled"):
        return

    rows, cols = _terminal_size()

    lines = _render_status_lines(
        state=ctx["current_state"],
        latest_hook_event=ctx["latest_hook_event"],
        latest_spinner_activity=ctx["latest_spinner_activity"],
        use_color=ctx["color_enabled"],
        width=cols,
        tmux_session=ctx["tmux_session"],
        window_id=ctx["window_id"],
        primary_sid=ctx["primary_sid"],
    )
    sep = _status_separator(
        cols,
        state=ctx["current_state"],
        use_color=ctx["color_enabled"],
    )
    # Status bar fixed at 10 rows:
    #   1: blank (above separator)
    #   2: separator
    #   3-10: 8 content lines from _render_status_lines
    full = ["", sep, *lines]
    # Defensive: enforce 10-row contract
    assert len(full) == 10, f"status bar must be 10 rows, got {len(full)}"
    new_height = len(full)
    old_height = ctx["status_height"]

    out: list[str] = []

    # Reserve 2 rows at the TOP for the session header
    # (line 1: "─── session sid ──...", line 2: blank).
    top_reserved = 2

    # Always re-draw the top header (cheap; covers terminal resize).
    out.append("\x1b[s")
    out.append("\x1b[1;1H")
    out.append("\x1b[2K")
    out.append(
        _top_header(
            ctx["tmux_session"],
            ctx["window_id"],
            ctx["primary_sid"],
            cols,
            use_color=ctx["color_enabled"],
        )
    )
    out.append("\x1b[2;1H")
    out.append("\x1b[2K")
    out.append("\x1b[u")

    # If height changed, re-scope scroll region.
    if new_height != old_height:
        out.append("\x1b[s")
        out.append("\x1b[r")  # reset region so we can clear old area
        if old_height > 0:
            old_start = max(top_reserved + 1, rows - old_height + 1)
            out.append(f"\x1b[{old_start};1H")
            out.append("\x1b[J")
        # Scroll region excludes both the top header rows and the status bar rows.
        scroll_top = top_reserved + 1
        scroll_bottom = max(scroll_top, rows - new_height)
        out.append(f"\x1b[{scroll_top};{scroll_bottom}r")
        ctx["status_height"] = new_height
        out.append("\x1b[u")

    # Redraw the status content at its current position.
    status_start = max(1, rows - new_height + 1)
    out.append("\x1b[s")
    for i, line in enumerate(full):
        row = status_start + i
        out.append(f"\x1b[{row};1H")
        out.append("\x1b[2K")
        out.append(line)
    out.append("\x1b[u")

    sys.stdout.write("".join(out))
    sys.stdout.flush()


def _teardown_status(ctx: dict) -> None:
    """Reset the scroll region and park the cursor below the status
    area on exit. Safe to call when the status bar was never enabled.
    """
    if not ctx.get("status_enabled"):
        return
    rows, _cols = _terminal_size()
    out = [
        "\x1b[r",  # reset scroll region
        f"\x1b[{rows};1H",  # cursor at last row
        "\n",  # advance past the status area (shell prompt lands here)
    ]
    sys.stdout.write("".join(out))
    sys.stdout.flush()
    ctx["status_enabled"] = False
    ctx["status_height"] = 0


# ---------------------------------------------------------------------------
# `watch` subcommand
# ---------------------------------------------------------------------------


async def _watch_async(
    session: str,
    pretty: bool,
    no_color: bool = False,
    no_status: bool = False,
) -> int:
    bindings = list_live_tmux_bindings()
    match = next((b for b in bindings if b.tmux_session == session), None)
    if match is None:
        print(
            f"ccmux-core: no live tmux session named {session!r}",
            file=sys.stderr,
        )
        return 1

    status_enabled = pretty and sys.stdout.isatty() and not no_color and not no_status

    # Shared mutable context for pretty formatting.
    ctx = {
        "tmux_session": match.tmux_session,
        "window_id": match.window_id,
        "primary_sid": match.primary_session_id,
        "latest_spinner_text": None,  # type: str | None
        "current_state": None,  # type: State | None
        # In-place spinner-refresh state. ``last_emit_was_spinner`` is
        # True when the most recent pretty block printed was a SPINNER
        # block; ``spinner_block_lines`` is its line count (including
        # the trailing blank). When the next emit is also a SPINNER
        # and stdout is a tty, we move the cursor up and overwrite
        # instead of appending — so consecutive spinner ticks look like
        # one updating block rather than a wall of duplicates.
        "last_emit_was_spinner": False,
        "spinner_block_lines": 0,
        "color_enabled": pretty and sys.stdout.isatty() and not no_color,
        # Bottom status bar (DECSTBM scroll region).
        "status_enabled": status_enabled,
        "status_height": 0,
        "latest_hook_event": None,  # type: dict | None
        "latest_spinner_activity": None,  # type: Activity | None
    }

    can_overwrite = pretty and sys.stdout.isatty()

    def _emit_pretty(
        label: str, body: str, ts: str | None, is_spinner: bool = False
    ) -> None:
        # ``ts`` here is already an ``HH:MM:SS`` short string (or None,
        # meaning "use wall-clock now"). Call sites convert from
        # whatever source format they have (ISO string, unix float)
        # via ``_ts_short`` / ``_ts_short_from_unix`` before calling.
        ts_short = ts if ts is not None else _ts_short(None)
        block = _pretty_block(
            label=label,
            body=body,
            ts=ts_short,
            tmux_session=ctx["tmux_session"],
            window_id=ctx["window_id"],
            primary_sid=ctx["primary_sid"],
            state=ctx["current_state"],
            use_color=ctx["color_enabled"],
        )
        # Block is N text lines joined by \n, then we append a blank
        # line. Total occupied terminal lines = N + 1.
        block_lines = block.count("\n") + 1 + 1

        # Overwrite the previous spinner block in place when this emit
        # is also a spinner. Anything else (state / event / message)
        # breaks the run and appends normally, resetting the marker.
        prefix = _overwrite_prefix(
            is_spinner_now=is_spinner,
            last_was_spinner=ctx["last_emit_was_spinner"],
            can_overwrite=can_overwrite,
            last_block_lines=ctx["spinner_block_lines"],
        )
        if prefix:
            sys.stdout.write(prefix)

        print(block, flush=True)
        # Trailing blank between blocks — gives breathing room and
        # also serves as the visible "blank above" the status bar
        # separator when the bar is active.
        print(flush=True)

        if is_spinner:
            ctx["last_emit_was_spinner"] = True
            ctx["spinner_block_lines"] = block_lines
        else:
            ctx["last_emit_was_spinner"] = False

    # Clear the screen (including scrollback) so watch starts on a
    # fresh canvas. Only when status bar is active — non-TTY / JSON
    # mode should not clobber the parent terminal.
    if ctx["status_enabled"]:
        # \x1b[H = cursor home, \x1b[2J = erase display, \x1b[3J = erase scrollback.
        # Then park cursor at row 3 (below the 2-row top header that
        # _emit_status will draw) so the first log block lands in
        # the scroll region rather than clobbering the top header.
        sys.stdout.write("\x1b[H\x1b[2J\x1b[3J\x1b[3;1H")
        sys.stdout.flush()

    # Install SIGWINCH so the bar re-renders to the new size on
    # terminal resize. Linux-only; wrap for non-Unix safety. Closure
    # captures ctx so the handler can re-emit.
    sigwinch_installed = False
    if ctx["status_enabled"]:
        try:
            loop = asyncio.get_running_loop()

            def _on_resize() -> None:
                _emit_status(ctx)

            loop.add_signal_handler(signal.SIGWINCH, _on_resize)
            sigwinch_installed = True
        except (AttributeError, NotImplementedError, ValueError):
            sigwinch_installed = False

    # Initial placeholder render so the user sees the bar reserved.
    if ctx["status_enabled"]:
        _emit_status(ctx)

    try:
        async with Backend(tmux_session=session, pane_id=match.pane_id) as b:

            async def pump_states():
                async for s in b.states():
                    ctx["current_state"] = s
                    if pretty and not ctx["status_enabled"]:
                        # With status bar active, current state lives in
                        # the bar (state= cell) — skip the scrolling log
                        # block to keep the log focused on messages.
                        _emit_pretty(
                            _state_label(s),
                            _state_body(s, ctx["latest_spinner_text"]),
                            ts=None,
                        )
                    elif not pretty:
                        obj = json.loads(_state_to_json(s))
                        obj["stream"] = "state"
                        print(json.dumps(obj), flush=True)
                    _emit_status(ctx)

            async def pump_events():
                async for ev in b.events():
                    # Update primary_sid in case session rebound (e.g., /clear).
                    sid = (ev.get("claude") or {}).get("session_id")
                    if sid:
                        ctx["primary_sid"] = sid
                    ctx["latest_hook_event"] = ev
                    if pretty and not ctx["status_enabled"]:
                        # With status bar active, latest hook lives in
                        # the bar (hook= cell) — skip the scrolling log
                        # block to keep the log focused on messages.
                        _emit_pretty(
                            _event_label(ev),
                            _event_body(ev),
                            ts=_ts_short(ev.get("timestamp")),
                        )
                    elif not pretty:
                        print(
                            json.dumps({"stream": "event", **ev}, ensure_ascii=False),
                            flush=True,
                        )
                    _emit_status(ctx)

            async def pump_messages():
                async for msg in b.messages():
                    if pretty:
                        _emit_pretty(
                            _l1_message_label(msg),
                            _l1_message_body(msg),
                            ts=_ts_short_from_unix(msg.timestamp),
                        )
                    else:
                        d = dataclasses.asdict(msg)
                        d["kind"] = type(msg).__name__
                        print(
                            json.dumps({"stream": "message", **d}, ensure_ascii=False),
                            flush=True,
                        )
                    # Refresh status so the hook "age" timestamp stays
                    # current as messages stream in.
                    _emit_status(ctx)

            async def pump_spinners():
                async for a in b.spinners():
                    # Cache for state body inclusion.
                    if a is not None and hasattr(a, "text"):
                        ctx["latest_spinner_text"] = a.text
                    elif a is None:
                        ctx["latest_spinner_text"] = None
                    ctx["latest_spinner_activity"] = a
                    if pretty and not ctx["status_enabled"]:
                        # When the status bar is active it already
                        # shows the live spinner — skip the in-pane
                        # spinner block to keep the scrolling log
                        # free of spinner spam.
                        _emit_pretty(
                            _spinner_label(a),
                            _spinner_body(a),
                            ts=None,
                            is_spinner=True,
                        )
                    elif not pretty:
                        if a is None:
                            obj = {"stream": "spinner", "type": "none"}
                        else:
                            obj = {
                                "stream": "spinner",
                                "type": type(a).__name__,
                                **dataclasses.asdict(a),
                            }
                        print(json.dumps(obj, ensure_ascii=False), flush=True)
                    _emit_status(ctx)

            await asyncio.gather(
                pump_states(),
                pump_events(),
                pump_messages(),
                pump_spinners(),
            )
    finally:
        # Always reset the scroll region — leaving DECSTBM set after
        # exit would leave the user's shell unable to use the bottom
        # rows. Runs on KeyboardInterrupt / exception too.
        if sigwinch_installed:
            try:
                asyncio.get_running_loop().remove_signal_handler(signal.SIGWINCH)
            except (AttributeError, NotImplementedError, ValueError, RuntimeError):
                pass
        _teardown_status(ctx)
    return 0


def cmd_watch(args) -> int:
    try:
        return asyncio.run(
            _watch_async(
                args.session,
                pretty=not args.json,
                no_color=getattr(args, "no_color", False),
                no_status=getattr(args, "no_status", False),
            )
        )
    except KeyboardInterrupt:
        return 0


# ---------------------------------------------------------------------------
# Argparse wiring
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ccmux-core")
    sub = p.add_subparsers(dest="cmd")

    p_list = sub.add_parser("list", help="List live tmux session bindings")
    p_list.set_defaults(fn=cmd_list)

    p_watch = sub.add_parser(
        "watch", help="Stream all four observations for one tmux session"
    )
    p_watch.add_argument("session", help="tmux session name")
    p_watch.add_argument(
        "--json",
        action="store_true",
        help="One JSON object per line instead of pretty blocks",
    )
    p_watch.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI colors even on a TTY",
    )
    p_watch.add_argument(
        "--no-status",
        action="store_true",
        help="Disable the bottom status bar (default: enabled on a TTY)",
    )
    p_watch.set_defaults(fn=cmd_watch)

    p_version = sub.add_parser("version", help="Print package version")
    p_version.set_defaults(fn=cmd_version)

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not hasattr(args, "fn"):
        parser.print_help()
        return 2
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
