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
    if head == "ASSISTANT" or head == "USER":
        return _ANSI["white_bold"]
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
    tmux_session: str,
    window_id: str | None,
    primary_sid: str | None,
    label: str,
    state: State | None = None,
    use_color: bool = False,
) -> str:
    """``[ HH:MM:SS ccmux@80 504921bb · WORKING(Bash) ] LABEL``.

    The state slot (after the sid, separated by `` · ``) is omitted when
    ``state is None`` to keep the legacy layout for the first emit before
    any state has arrived. When ``use_color`` is true, the state slot and
    the label are wrapped in ANSI color escapes.
    """
    sid = (primary_sid or "")[:8] or "--------"
    tmux_frag = f"{tmux_session}{window_id or ''}"
    label_painted = _paint(label, _color_for_label(label), enabled=use_color)
    if state is None:
        return f"[ {ts} {tmux_frag} {sid} ] {label_painted}"
    summary = _state_summary(state)
    summary_painted = _paint(summary, _color_for_state(state), enabled=use_color)
    return f"[ {ts} {tmux_frag} {sid} · {summary_painted} ] {label_painted}"


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
    return f"EVENT · {event.get('event_type', '?')}"


def _event_body(event: dict) -> str:
    payload = event.get("payload") or {}
    et = event.get("event_type", "")
    if et == "user_prompt_submit":
        return json.dumps(payload.get("prompt", ""), ensure_ascii=False)[1:-1]
    if et in ("pre_tool_use", "post_tool_use"):
        return f"tool={payload.get('tool_name', '?')}"
    if et == "permission_request":
        return f"tool={payload.get('tool_name', '?')}"
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


def _message_label(msg) -> str:
    parts = [(msg.role or "?").upper()]
    if msg.content_type and msg.content_type != "text":
        parts.append(msg.content_type)
    if msg.tool_name:
        parts.append(msg.tool_name)
    # Surface claude-tap's `source` field (v0.2.1+) so a viewer can
    # see at a glance whether an assistant text is a final reply
    # (source="hook") or mid-turn narration (source="transcript").
    src = getattr(msg, "source", None)
    if src:
        parts.append(src)
    return " · ".join(parts)


def _message_body(msg) -> str:
    body = json.dumps(msg.text or "", ensure_ascii=False)[1:-1]
    if msg.image_data:
        n = len(msg.image_data)
        body += f" [+{n} image{'s' if n != 1 else ''}]"
    return body


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
    """Render status bar content as a list of lines (no leading separator).

    Layout::

        (blank)
        state=<STATE_SUMMARY>
        (blank)
        [ HH:MM:SS tmux@win sid · STATE ] EVENT · <event_type>
        <event body>
        (blank)
        spinner=<line1>
                <line2>  (if multi-line)
        <todo line 1, verbatim from spinner.todos>
        <todo line 2, verbatim from spinner.todos>

    Returns one line per row, NOT including the top separator. Each
    line is plain text trimmed to ``width`` visual columns.
    """
    out: list[str] = []

    # --- blank --
    out.append("")

    # --- state line ---
    if state is None:
        state_line = "state=(waiting)"
    else:
        summary = _state_summary(state)
        if use_color:
            color = _color_for_state(state)
            summary = _paint(summary, color, enabled=True)
        state_line = f"state={summary}"
    out.append(state_line)

    # --- blank ---
    out.append("")

    # --- hook event block (2 lines: header + body) ---
    if latest_hook_event is None:
        out.append("(no hook events yet)")
        out.append("")  # body placeholder
    else:
        ev_ts = _ts_short(latest_hook_event.get("timestamp"))
        # Skip the state slot in the header — state already lives on
        # its own dedicated line above, so embedding it here would
        # duplicate (and produce e.g. '[ ... · IDLE(stop) ] EVENT · stop').
        hook_header = _header(
            ts=ev_ts,
            tmux_session=tmux_session,
            window_id=window_id,
            primary_sid=primary_sid,
            label=_event_label(latest_hook_event),
            state=None,
            use_color=use_color,
        )
        hook_body = _event_body(latest_hook_event)
        # Append the age suffix to the body so the user can still see
        # how long ago the hook fired (the header has the event's own
        # timestamp; age gives quick relative reference).
        age = _hook_age_seconds(latest_hook_event.get("timestamp"))
        if age is not None:
            hook_body = f"{hook_body}  ({age}s ago)" if hook_body else f"({age}s ago)"
        out.append(hook_header)
        out.append(hook_body)

    # --- blank ---
    out.append("")

    # --- spinner block ---
    if latest_spinner_activity is None:
        out.append("spinner=(none)")
    else:
        text = getattr(latest_spinner_activity, "text", "") or ""
        spinner_lines = text.split("\n")
        if not spinner_lines:
            spinner_lines = [""]
        out.append(f"spinner={spinner_lines[0]}")
        for cont in spinner_lines[1:]:
            out.append(f"        {cont}")

        # ccmux-spinner's Spinner.todos is tuple[str, ...] where each
        # string already carries claude TUI's native markers
        # (⎿ / ◻ / ✔ / "… +N completed"). Emit verbatim — prepending
        # our own ☐ here would double-mark every line.
        for item in getattr(latest_spinner_activity, "todos", ()) or ():
            text_part = getattr(item, "text", None) or str(item)
            out.append(text_part)

    return [_visual_trim(line, width) for line in out]


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
    # Status bar shape: separator · content · blank.
    # _render_status_lines already starts with its own blank, so we
    # don't add one between separator and content here.
    # The blank ABOVE the separator comes naturally from each log
    # block's trailing print() in the scroll region — we don't add
    # one or we'd get 2-vs-1 asymmetry. The trailing blank below is
    # appended explicitly here.
    full = [sep, *lines, ""]
    new_height = len(full)
    old_height = ctx["status_height"]

    out: list[str] = []

    # If height changed, re-scope scroll region.
    if new_height != old_height:
        out.append("\x1b[s")
        out.append("\x1b[r")  # reset region so we can clear old area
        if old_height > 0:
            old_start = max(1, rows - old_height + 1)
            out.append(f"\x1b[{old_start};1H")
            out.append("\x1b[J")
        scroll_bottom = max(1, rows - new_height)
        out.append(f"\x1b[1;{scroll_bottom}r")
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
        block = _pretty_block(
            label=label,
            body=body,
            ts=_ts_short(ts),
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
        sys.stdout.write("\x1b[H\x1b[2J\x1b[3J")
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
                            ts=ev.get("timestamp"),
                        )
                    elif not pretty:
                        print(
                            json.dumps({"stream": "event", **ev}, ensure_ascii=False),
                            flush=True,
                        )
                    _emit_status(ctx)

            async def pump_messages():
                async for msg in b.transcript_items():
                    if pretty:
                        _emit_pretty(
                            _message_label(msg),
                            _message_body(msg),
                            ts=msg.timestamp,
                        )
                    else:
                        d = dataclasses.asdict(msg)
                        if d.get("image_data"):
                            import base64

                            d["image_data"] = [
                                (mt, base64.b64encode(bts).decode("ascii"))
                                for (mt, bts) in d["image_data"]
                            ]
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
