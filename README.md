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

ccmux-core's runtime behavior is tuneable via environment variables,
all under the `CCMUX_CORE_*` namespace, all in
`~/.ccmux-core/settings.env`. Knobs that belong conceptually to
upstream libraries (claude-tap, ccmux-spinner) are exposed as
**facade aliases** under the same namespace; ccmux-core mirrors them
to the corresponding upstream env var at import time so the
upstream libraries pick the values up.

### Native ccmux-core knobs

| Knob | Default | Effect |
|---|---|---|
| `CCMUX_CORE_DIR` | `~/.ccmux-core` | Where ccmux-core's `settings.env` is loaded from. |
| `CCMUX_CORE_SPINNER_GRACE` | `3` | Seconds Working with a non-Spinner observation before falling back to `Idle(interrupted)`. |
| `CCMUX_CORE_PROCESS_PROBE_INTERVAL` | `10` | Seconds between `tmux list-panes` probes. |
| `CCMUX_CORE_PROCESS_PROBE_STARTUP_GRACE` | `10` | Seconds after Backend start during which no process probe runs. |
| `CCMUX_CORE_CLAUDE_PROC_NAMES` | `claude,node` | Foreground process names that count as "claude is alive". |
| `CCMUX_CORE_PRETTY_WIDTH` | `100` | Visual width (cells) of `ccmux-core watch` pretty-mode blocks. |

### Facade aliases (mirrored to upstream)

| Alias | → Upstream var | Effect |
|---|---|---|
| `CCMUX_CORE_HOOK_POLL_INTERVAL` | `CLAUDE_TAP_POLL_INTERVAL` (default `0.1`) | `events.jsonl` tail cadence; affects hook-event visibility latency. |
| `CCMUX_CORE_HOOK_POLL_MAX_DURATION` | `CLAUDE_TAP_POLL_MAX_DURATION` (default `30`) | Max seconds the scoped post-tool-use polling task runs. Raise for long generations where mid-turn text would otherwise be delayed. |
| `CCMUX_CORE_SPINNER_POLL_INTERVAL` | `CCMUX_SPINNER_POLL_INTERVAL` (default `0.5`) | tmux pane capture cadence; affects spinner detection latency. |

### Resolution priority (highest first)

1. **Shell-exported env var** (e.g. `export CCMUX_CORE_SPINNER_GRACE=2`).
2. **`./settings.env`** in the current working directory (project-local).
3. **`~/.ccmux-core/settings.env`** (global per user).
4. **Upstream package's own `settings.env`** (`~/.claude-tap/settings.env`,
   `~/.ccmux-spinner/settings.env`) — applies only to the upstream-aliased
   knobs, and only when ccmux-core's facade alias is not set.
5. **Upstream package default**.

### Example

```bash
# ~/.ccmux-core/settings.env  ← one file, all ccmux-core tuning
CCMUX_CORE_PRETTY_WIDTH=80
CCMUX_CORE_HOOK_POLL_MAX_DURATION=600   # → CLAUDE_TAP_POLL_MAX_DURATION
```

The user's `~/.claude-tap/settings.env` and `~/.ccmux-spinner/settings.env`
files are then only relevant for those tools' standalone use
(`claude-tap watch-messages` etc.). When working through ccmux-core,
the facade aliases above are the single source of truth.

### Knobs not exposed

These exist in upstream packages but have no effect when used through
ccmux-core, so no facade alias is provided:

- `CLAUDE_TAP_PRETTY_WIDTH` (only used by `claude-tap watch-messages`)
- `CCMUX_SPINNER_PRETTY_WIDTH` (only used by `ccmux-spinner watch`)
- `CLAUDE_TAP_DECISION_TIMEOUT` (permission-request decision sockets;
  ccmux-core does not consume them)
- `CLAUDE_TAP_DIR` / `CCMUX_SPINNER_DIR` (plumbing-level; usually fixed
  per install)

## License

Apache 2.0.
