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

## License

Apache 2.0.
