# ccmux-core

> Per-tmux-session state machine + stream multiplexer for Claude Code observers.

ccmux-core composes [claude-tap](https://github.com/wuwenrui555/claude-tap)
(hook events + derived ClaudeMessages) with
[ccmux-spinner](https://github.com/wuwenrui555/ccmux-spinner)
(tmux pane spinner) into a single per-tmux-session async-context-manager.
Downstream consumers (Telegram relays, status HUDs, dashboards) subscribe
to four typed streams: `states / events / messages / spinners`.

## Status

v0.1 — early. See `docs/superpowers/specs/` for the design spec.

## Install

```bash
pip install ccmux-core
```

Or with uv:

```bash
uv add ccmux-core
```

## Quick start

```python
import asyncio
import ccmux_core

async def main():
    async with ccmux_core.Backend(tmux_session="ccmux", pane_id="%42") as b:
        async for state in b.states():
            print(state)

asyncio.run(main())
```

## Configuration

ccmux-core's runtime behavior is tuneable via environment variables.
Because ccmux-core composes claude-tap and ccmux-spinner internally,
some knobs that affect ccmux-core actually live in upstream packages
and are read by claude-tap or ccmux-spinner code; ccmux-core does not
proxy them. Each package owns its own `settings.env` file under its
state directory.

### What lives where

| Knob | Belongs to | Lives in | Default | Effect on `ccmux-core watch` |
|---|---|---|---|---|
| `CCMUX_CORE_DIR` | ccmux-core | shell or `~/.ccmux-core/settings.env` | `~/.ccmux-core` | Where ccmux-core's `settings.env` is loaded from. |
| `CCMUX_CORE_SPINNER_GRACE` | ccmux-core | `~/.ccmux-core/settings.env` | `3` | Seconds Working with a non-Spinner observation before falling back to `Idle(interrupted)`. |
| `CCMUX_CORE_PROCESS_PROBE_INTERVAL` | ccmux-core | `~/.ccmux-core/settings.env` | `10` | Seconds between `tmux list-panes` probes. |
| `CCMUX_CORE_PROCESS_PROBE_STARTUP_GRACE` | ccmux-core | `~/.ccmux-core/settings.env` | `10` | Seconds after Backend start during which no process probe runs. |
| `CCMUX_CORE_CLAUDE_PROC_NAMES` | ccmux-core | `~/.ccmux-core/settings.env` | `claude,node` | Foreground process names that count as "claude is alive". |
| `CCMUX_CORE_PRETTY_WIDTH` | ccmux-core | `~/.ccmux-core/settings.env` | `100` | Visual width (cells) of `ccmux-core watch` pretty mode blocks. |
| `CLAUDE_TAP_DIR` | claude-tap | shell or `~/.claude-tap/settings.env` | `~/.claude-tap` | Where `events.jsonl` lives — ccmux-core tails it. |
| `CLAUDE_TAP_POLL_INTERVAL` | claude-tap | `~/.claude-tap/settings.env` | `0.1` | `events.jsonl` tail cadence (seconds). Affects how fast ccmux-core sees new hooks. |
| `CLAUDE_TAP_POLL_MAX_DURATION` | claude-tap | `~/.claude-tap/settings.env` | `30` | Max seconds the scoped post-tool-use polling task runs. Raise for long turns where mid-turn text would otherwise be delayed. |
| `CCMUX_SPINNER_POLL_INTERVAL` | ccmux-spinner | `~/.ccmux-spinner/settings.env` | `0.5` | tmux pane capture cadence (seconds). Affects spinner detection latency. |

Not listed: knobs that exist in upstream packages but have **no effect**
when those packages are used through ccmux-core — e.g.,
`CLAUDE_TAP_PRETTY_WIDTH` controls only `claude-tap watch-messages`,
`CCMUX_SPINNER_PRETTY_WIDTH` controls only `ccmux-spinner watch`,
`CLAUDE_TAP_DECISION_TIMEOUT` is for permission-request decision
sockets which ccmux-core does not consume.

### Resolution order (per package)

For each `settings.env` file, lookup is:

1. Shell-exported env var (`export CCMUX_CORE_SPINNER_GRACE=2`) —
   always wins.
2. `./settings.env` in the current working directory (project-local).
3. `~/.<package>/settings.env` (global per package).

### Example: narrow `ccmux-core watch` in a tmux split

```bash
# ~/.ccmux-core/settings.env
CCMUX_CORE_PRETTY_WIDTH=80
```

This affects only `ccmux-core watch`. `claude-tap watch-messages`
keeps its own width controlled by `CLAUDE_TAP_PRETTY_WIDTH`.

## License

Apache 2.0.
