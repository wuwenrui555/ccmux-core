"""ccmux-core CLI: `list` (snapshot) and `watch <tmux_session>` (live)."""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import sys

from . import __version__
from .backend import Backend
from .discover import TmuxBinding, list_live_tmux_bindings
from .state import Blocked, Dead, Idle, State, Working


def _bindings_table(bindings: list[TmuxBinding]) -> str:
    if not bindings:
        return "(no live tmux sessions)"
    headers = ["TMUX_SESSION", "PANE_ID", "WINDOW_ID", "PRIMARY_SESSION_ID", "LAST_EVENT"]
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
    widths = [
        max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)
    ]
    lines = ["  ".join(h.ljust(w) for h, w in zip(headers, widths))]
    for r in rows:
        lines.append("  ".join(c.ljust(w) for c, w in zip(r, widths)))
    return "\n".join(lines)


def _state_to_json(state: State) -> str:
    if isinstance(state, Idle):
        return json.dumps({"type": "Idle", "reason": state.reason})
    if isinstance(state, Working):
        return json.dumps({"type": "Working", "tool_name": state.tool_name})
    if isinstance(state, Blocked):
        return json.dumps({
            "type": "Blocked",
            "kind": state.kind,
            "tool_name": state.tool_name,
            "tool_input": state.tool_input,
        })
    if isinstance(state, Dead):
        return json.dumps({
            "type": "Dead",
            "reason": state.reason,
            "detail": state.detail,
        })
    return json.dumps({"type": "Unknown"})


def cmd_list(args) -> int:
    bindings = list_live_tmux_bindings()
    print(_bindings_table(bindings))
    return 0


def cmd_version(args) -> int:
    print(__version__)
    return 0


async def _watch_async(session: str) -> int:
    bindings = list_live_tmux_bindings()
    match = next((b for b in bindings if b.tmux_session == session), None)
    if match is None:
        print(
            f"ccmux-core: no live tmux session named {session!r}",
            file=sys.stderr,
        )
        return 1

    async with Backend(tmux_session=session, pane_id=match.pane_id) as b:

        async def pump_states():
            async for s in b.states():
                obj = json.loads(_state_to_json(s))
                obj["stream"] = "state"
                print(json.dumps(obj), flush=True)

        async def pump_events():
            async for ev in b.events():
                print(
                    json.dumps({"stream": "event", **ev}, ensure_ascii=False),
                    flush=True,
                )

        async def pump_messages():
            async for msg in b.messages():
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
        return asyncio.run(_watch_async(args.session))
    except KeyboardInterrupt:
        return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ccmux-core")
    sub = p.add_subparsers(dest="cmd")

    p_list = sub.add_parser("list", help="List live tmux session bindings")
    p_list.set_defaults(fn=cmd_list)

    p_watch = sub.add_parser("watch", help="Dump all four streams for one tmux session")
    p_watch.add_argument("session", help="tmux session name")
    p_watch.set_defaults(fn=cmd_watch)

    p_version = sub.add_parser("version", help="Print version")
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
