"""ccmux-core CLI.

Subcommands:

* ``list`` — one-shot snapshot of currently-live tmux session bindings.
* ``watch <tmux_session>`` — stream the four observations to stdout
  as pretty blocks (default) or JSON Lines (``--json``).
* ``web <tmux_session>`` — serve a live HTML viewer of the same
  observations over HTTP, suitable for Tailscale-tailnet access
  from a phone / laptop.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import sys
import unicodedata
from datetime import UTC, datetime

from claude_tap.config import pretty_width as _pretty_width

from . import __version__
from .backend import Backend
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


def _header(
    *,
    ts: str,
    tmux_session: str,
    window_id: str | None,
    primary_sid: str | None,
    label: str,
) -> str:
    """``[ HH:MM:SS ccmux@80 504921bb ] LABEL``."""
    sid = (primary_sid or "")[:8] or "--------"
    tmux_frag = f"{tmux_session}{window_id or ''}"
    return f"[ {ts} {tmux_frag} {sid} ] {label}"


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
    return " · ".join(parts)


def _message_body(msg) -> str:
    body = json.dumps(msg.text or "", ensure_ascii=False)[1:-1]
    if msg.image_data:
        n = len(msg.image_data)
        body += f" [+{n} image{'s' if n != 1 else ''}]"
    return body


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
) -> str:
    lines = [
        _pretty_separator(),
        _header(
            ts=ts,
            tmux_session=tmux_session,
            window_id=window_id,
            primary_sid=primary_sid,
            label=label,
        ),
        _trim_body(body),
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# `watch` subcommand
# ---------------------------------------------------------------------------


async def _watch_async(session: str, pretty: bool) -> int:
    bindings = list_live_tmux_bindings()
    match = next((b for b in bindings if b.tmux_session == session), None)
    if match is None:
        print(
            f"ccmux-core: no live tmux session named {session!r}",
            file=sys.stderr,
        )
        return 1

    # Shared mutable context for pretty formatting.
    ctx = {
        "tmux_session": match.tmux_session,
        "window_id": match.window_id,
        "primary_sid": match.primary_session_id,
        "latest_spinner_text": None,  # type: str | None
    }

    def _emit_pretty(label: str, body: str, ts: str | None) -> None:
        print(
            _pretty_block(
                label=label,
                body=body,
                ts=_ts_short(ts),
                tmux_session=ctx["tmux_session"],
                window_id=ctx["window_id"],
                primary_sid=ctx["primary_sid"],
            ),
            flush=True,
        )
        print(flush=True)

    async with Backend(tmux_session=session, pane_id=match.pane_id) as b:

        async def pump_states():
            async for s in b.states():
                if pretty:
                    _emit_pretty(
                        _state_label(s),
                        _state_body(s, ctx["latest_spinner_text"]),
                        ts=None,
                    )
                else:
                    obj = json.loads(_state_to_json(s))
                    obj["stream"] = "state"
                    print(json.dumps(obj), flush=True)

        async def pump_events():
            async for ev in b.events():
                # Update primary_sid in case session rebound (e.g., /clear).
                sid = (ev.get("claude") or {}).get("session_id")
                if sid:
                    ctx["primary_sid"] = sid
                if pretty:
                    _emit_pretty(
                        _event_label(ev),
                        _event_body(ev),
                        ts=ev.get("timestamp"),
                    )
                else:
                    print(
                        json.dumps({"stream": "event", **ev}, ensure_ascii=False),
                        flush=True,
                    )

        async def pump_messages():
            async for msg in b.messages():
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

        async def pump_spinners():
            async for a in b.spinners():
                # Cache for state body inclusion.
                if a is not None and hasattr(a, "text"):
                    ctx["latest_spinner_text"] = a.text
                elif a is None:
                    ctx["latest_spinner_text"] = None
                if pretty:
                    _emit_pretty(
                        _spinner_label(a),
                        _spinner_body(a),
                        ts=None,
                    )
                else:
                    if a is None:
                        obj = {"stream": "spinner", "type": "none"}
                    else:
                        obj = {
                            "stream": "spinner",
                            "type": type(a).__name__,
                            **dataclasses.asdict(a),
                        }
                    print(json.dumps(obj, ensure_ascii=False), flush=True)

        await asyncio.gather(
            pump_states(),
            pump_events(),
            pump_messages(),
            pump_spinners(),
        )
    return 0


def cmd_watch(args) -> int:
    try:
        return asyncio.run(_watch_async(args.session, pretty=not args.json))
    except KeyboardInterrupt:
        return 0


# ---------------------------------------------------------------------------
# Argparse wiring
# ---------------------------------------------------------------------------


def cmd_web(args) -> int:
    from .web import serve_web

    try:
        return asyncio.run(serve_web(args.session, host=args.host, port=args.port))
    except KeyboardInterrupt:
        return 0


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
    p_watch.set_defaults(fn=cmd_watch)

    p_web = sub.add_parser(
        "web",
        help="Serve a live HTML viewer of the streams over HTTP (Tailscale-friendly)",
    )
    p_web.add_argument("session", help="tmux session name")
    p_web.add_argument("--host", default="0.0.0.0", help="bind host (default: 0.0.0.0)")
    p_web.add_argument(
        "--port", type=int, default=8765, help="bind port (default: 8765)"
    )
    p_web.set_defaults(fn=cmd_web)

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
