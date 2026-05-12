"""End-to-end smoke test of Backend v0.2 L2 lifecycle.

Drives a synthesized event sequence through Backend and verifies
state transitions, the L1 messages stream, and the send_prompt
state guard work together as intended.

This test bypasses the autouse `_FakeEventStream` from conftest.py
sibling test_backend.py by monkeypatching the real `EventStream`
back into `ccmux_core.backend` (the autouse fixture in test_backend.py
applies to that module only, not here, but we restore the real
`claude_tap.EventStream` defensively in case fixtures evolve). We
write a real events.jsonl on disk and let the real EventStream
tail it.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from ccmux_core import Backend
from ccmux_core.message import UserPrompt
from ccmux_core.state import Idle, Working


def _write_event(path: Path, event: dict) -> None:
    """Append one JSONL event."""
    with path.open("a") as f:
        f.write(json.dumps(event) + "\n")


# Use a far-future timestamp so events sit on the "live" side of
# Backend's subscribe_unix filter — same trick used in test_backend.py.
_FUTURE_TS = "2099-12-31T23:59:59+00:00"


@pytest.mark.asyncio
async def test_lifecycle_session_start_prompt_stop(tmp_path, monkeypatch):
    """A common claude turn: session_start → user_prompt_submit →
    stop. Verifies that state walks Idle(start) → Working → Idle(stop)
    and that messages() emits a UserPrompt."""
    # Restore the real EventStream in case a sibling conftest /
    # test module has patched it. We need the real one to tail a
    # real file. MessageStream stays unpatched — Backend synthesizes
    # UserPrompt from the event stream, not from transcript items.
    from claude_tap import EventStream as _RealEventStream

    import ccmux_core.backend as bk

    monkeypatch.setattr(bk, "EventStream", _RealEventStream)

    # Subprocess.run is used for the process probe. Make it return
    # a claude-looking process list so the probe never fires Dead.
    class _OK:
        returncode = 0
        stdout = "node\n"
        stderr = ""

    monkeypatch.setattr(bk.subprocess, "run", lambda *a, **kw: _OK())

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    _write_event(
        events_path,
        {
            "event_type": "session_start",
            "timestamp": _FUTURE_TS,
            "claude": {"session_id": "s1"},
            "tmux": {"session_name": "t1", "pane_id": "%0", "window_id": "@0"},
            "payload": {},
        },
    )
    _write_event(
        events_path,
        {
            "event_type": "user_prompt_submit",
            "timestamp": _FUTURE_TS,
            "claude": {"session_id": "s1"},
            "tmux": {"session_name": "t1", "pane_id": "%0", "window_id": "@0"},
            "payload": {"prompt": "hello"},
        },
    )
    _write_event(
        events_path,
        {
            "event_type": "stop",
            "timestamp": _FUTURE_TS,
            "claude": {"session_id": "s1"},
            "tmux": {"session_name": "t1", "pane_id": "%0", "window_id": "@0"},
            "payload": {"last_assistant_message": "ok"},
        },
    )

    async with Backend(
        tmux_session="t1",
        pane_id="%0",
        events_path=events_path,
        decision_sock_path=tmp_path / "decision.sock",
    ) as b:
        with patch.object(b, "send_keys", new_callable=AsyncMock):
            states: list = []
            msgs: list = []

            async def collect_states():
                async for st in b.states():
                    states.append(st)
                    if isinstance(st, Idle) and st.reason == "stop":
                        return

            async def collect_messages():
                async for m in b.messages():
                    msgs.append(m)
                    if any(isinstance(x, UserPrompt) for x in msgs):
                        return

            await asyncio.wait_for(
                asyncio.gather(collect_states(), collect_messages()),
                timeout=5.0,
            )

            assert any(
                isinstance(s, Idle) and s.reason == "start" for s in states
            ), f"missing Idle(start) in {states}"
            assert any(
                isinstance(s, Working) for s in states
            ), f"missing Working in {states}"
            assert states[-1] == Idle(
                reason="stop"
            ), f"expected end at Idle(stop), got {states[-1]}"

            user_prompts = [m for m in msgs if isinstance(m, UserPrompt)]
            assert len(user_prompts) == 1
            assert user_prompts[0].text == "hello"

            # send_prompt from Idle should call send_keys at least
            # three times: C-u (clear), text, Enter (submit).
            b._state = Idle(reason="stop")
            await b.send_prompt("next prompt")
            assert b.send_keys.call_count >= 3
