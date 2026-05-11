"""End-to-end tests for Backend with patched upstream streams.

Test strategy: replace ``EventStream``, ``MessageStream``,
``SpinnerMonitor``, and the tmux subprocess at the import-site
inside ``ccmux_core.backend``. Drive scenarios and assert
iterator outputs.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from ccmux_core.backend import Backend
from ccmux_core.state import Dead, Idle, Working


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeEventStream:
    """In-memory async iterator over a pre-baked list of events."""

    instances: list["_FakeEventStream"] = []

    def __init__(
        self,
        events: list[dict] | None = None,
        *,
        hold_at_end: bool = True,
        **_ignored,
    ):
        self._events = list(events) if events is not None else []
        self._hold = hold_at_end
        self._cancel_event = asyncio.Event()
        self.closed = False
        _FakeEventStream.instances.append(self)

    def close(self):
        self.closed = True
        self._cancel_event.set()

    async def __aiter__(self) -> AsyncIterator[dict]:
        for ev in self._events:
            if self.closed:
                return
            yield ev
        if self._hold:
            await self._cancel_event.wait()


class _FakeMessageStream:
    def __init__(self, **_ignored):
        self._cancel_event = asyncio.Event()
        self.closed = False

    def close(self):
        self.closed = True
        self._cancel_event.set()

    async def __aiter__(self):
        await self._cancel_event.wait()
        return
        yield  # unreachable


class _FakeSpinnerMonitor:
    instances: list["_FakeSpinnerMonitor"] = []

    def __init__(self, pane_id: str, poll_interval: float | None = None):
        self.pane_id = pane_id
        self._items: list = []
        self._cancel_event = asyncio.Event()
        _FakeSpinnerMonitor.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self._cancel_event.set()

    def feed(self, item):
        self._items.append(item)

    async def __aiter__(self):
        i = 0
        while True:
            if i < len(self._items):
                yield self._items[i]
                i += 1
            else:
                await asyncio.sleep(0.02)
                if self._cancel_event.is_set():
                    return


@pytest.fixture(autouse=True)
def _reset_fakes(monkeypatch):
    _FakeEventStream.instances.clear()
    _FakeSpinnerMonitor.instances.clear()

    import ccmux_core.backend as bk

    monkeypatch.setattr(bk, "EventStream", _FakeEventStream)
    monkeypatch.setattr(bk, "MessageStream", _FakeMessageStream)
    monkeypatch.setattr(bk, "SpinnerMonitor", _FakeSpinnerMonitor)

    class _OK:
        returncode = 0
        stdout = "node\n"
        stderr = ""

    monkeypatch.setattr(bk.subprocess, "run", lambda *a, **kw: _OK())
    yield


def _ev(
    et,
    *,
    sid="S1",
    payload=None,
    ts="2026-05-10T00:00:00+00:00",
    tmux="ccmux",
    pane="%1",
    window="@0",
):
    return {
        "event_type": et,
        "timestamp": ts,
        "claude": {"session_id": sid},
        "tmux": {"session_name": tmux, "pane_id": pane, "window_id": window},
        "payload": payload or {},
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backend_emits_initial_idle_start(monkeypatch):
    import ccmux_core.backend as bk

    later_ts = "2099-12-31T23:59:59+00:00"
    monkeypatch.setattr(
        bk, "EventStream",
        lambda **kw: _FakeEventStream([_ev("session_start", ts=later_ts)]),
    )

    async with Backend(tmux_session="ccmux", pane_id="%1") as b:
        out = []

        async def consume():
            async for s in b.states():
                out.append(s)
                if len(out) >= 1:
                    break

        await asyncio.wait_for(consume(), timeout=2.0)
        assert out[0] == Idle(reason="start")


@pytest.mark.asyncio
async def test_backend_full_turn_state_sequence(monkeypatch):
    import ccmux_core.backend as bk

    later_ts = "2099-12-31T23:59:59+00:00"
    events = [
        _ev("session_start", ts=later_ts),
        _ev("user_prompt_submit", ts=later_ts),
        _ev("pre_tool_use", payload={"tool_name": "Bash"}, ts=later_ts),
        _ev("post_tool_use", payload={"tool_name": "Bash"}, ts=later_ts),
        _ev("stop", ts=later_ts),
    ]
    monkeypatch.setattr(bk, "EventStream", lambda **kw: _FakeEventStream(events))

    async with Backend(tmux_session="ccmux", pane_id="%1") as b:
        out = []

        async def consume():
            async for s in b.states():
                out.append(s)
                if len(out) >= 5:
                    break

        await asyncio.wait_for(consume(), timeout=2.0)
        assert out == [
            Idle(reason="start"),
            Working(tool_name=None),
            Working(tool_name="Bash"),
            Working(tool_name=None),
            Idle(reason="stop"),
        ]


@pytest.mark.asyncio
async def test_backend_replay_then_live_baseline(monkeypatch):
    """Historical events drive state silently; live phase emits baseline."""
    import ccmux_core.backend as bk

    past_ts = "1970-01-01T00:00:00+00:00"
    later_ts = "2099-12-31T23:59:59+00:00"

    events = [
        _ev("session_start", ts=past_ts),
        _ev("user_prompt_submit", ts=past_ts),
        _ev("pre_tool_use", payload={"tool_name": "Bash"}, ts=past_ts),
        _ev("stop", ts=later_ts),
    ]
    monkeypatch.setattr(bk, "EventStream", lambda **kw: _FakeEventStream(events))

    async with Backend(tmux_session="ccmux", pane_id="%1") as b:
        out = []

        async def consume():
            async for s in b.states():
                out.append(s)
                if len(out) >= 2:
                    break

        await asyncio.wait_for(consume(), timeout=2.0)
        assert out == [Working(tool_name="Bash"), Idle(reason="stop")]


@pytest.mark.asyncio
async def test_backend_subagent_events_dont_affect_state(monkeypatch):
    import ccmux_core.backend as bk

    later_ts = "2099-12-31T23:59:59+00:00"
    events = [
        _ev("session_start", sid="S1", ts=later_ts),
        _ev("user_prompt_submit", sid="S1", ts=later_ts),
        _ev("pre_tool_use", sid="S1", payload={"tool_name": "Task"}, ts=later_ts),
        _ev("session_start", sid="SUB", ts=later_ts),
        _ev("user_prompt_submit", sid="SUB", ts=later_ts),
        _ev("session_end", sid="SUB", payload={"reason": "stop"}, ts=later_ts),
        _ev("post_tool_use", sid="S1", payload={"tool_name": "Task"}, ts=later_ts),
        _ev("stop", sid="S1", ts=later_ts),
    ]
    monkeypatch.setattr(bk, "EventStream", lambda **kw: _FakeEventStream(events))

    async with Backend(tmux_session="ccmux", pane_id="%1") as b:
        out = []

        async def consume():
            async for s in b.states():
                out.append(s)
                if len(out) >= 5:
                    break

        await asyncio.wait_for(consume(), timeout=2.0)
        assert out == [
            Idle(reason="start"),
            Working(tool_name=None),
            Working(tool_name="Task"),
            Working(tool_name=None),
            Idle(reason="stop"),
        ]


@pytest.mark.asyncio
async def test_backend_clear_does_not_emit_dead(monkeypatch):
    import ccmux_core.backend as bk

    later_ts = "2099-12-31T23:59:59+00:00"
    events = [
        _ev("session_start", sid="S1", ts=later_ts),
        _ev("session_end", sid="S1", payload={"reason": "clear"}, ts=later_ts),
        _ev("session_start", sid="S2", ts=later_ts),
    ]
    monkeypatch.setattr(bk, "EventStream", lambda **kw: _FakeEventStream(events))

    async with Backend(tmux_session="ccmux", pane_id="%1") as b:
        out = []

        async def consume():
            async for s in b.states():
                out.append(s)
                if len(out) >= 2:
                    break

        try:
            await asyncio.wait_for(consume(), timeout=0.5)
        except asyncio.TimeoutError:
            pass

    # Both session_starts produce Idle(start); the second is coalesced.
    assert out == [Idle(reason="start")]


# ---------------------------------------------------------------------------
# Safety net tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backend_process_probe_declares_dead(monkeypatch):
    """tmux list-panes returns no claude/node → process_gone fires."""
    import ccmux_core.backend as bk

    later_ts = "2099-12-31T23:59:59+00:00"
    events = [_ev("session_start", ts=later_ts)]
    monkeypatch.setattr(bk, "EventStream", lambda **kw: _FakeEventStream(events))

    class _NoClaude:
        returncode = 0
        stdout = "bash\nzsh\n"
        stderr = ""

    monkeypatch.setattr(bk.subprocess, "run", lambda *a, **kw: _NoClaude())

    async with Backend(
        tmux_session="ccmux",
        pane_id="%1",
        process_probe_startup_grace=0.05,
        process_probe_interval=0.05,
    ) as b:
        out = []

        async def consume():
            async for s in b.states():
                out.append(s)

        try:
            await asyncio.wait_for(consume(), timeout=2.0)
        except asyncio.TimeoutError:
            pass

    assert any(isinstance(s, Dead) and s.reason == "process_gone" for s in out)


@pytest.mark.asyncio
async def test_backend_spinner_grace_fires_from_working(monkeypatch):
    """Working + spinner absent ≥ grace seconds → Idle(interrupted)."""
    import ccmux_core.backend as bk

    later_ts = "2099-12-31T23:59:59+00:00"
    events = [
        _ev("session_start", ts=later_ts),
        _ev("user_prompt_submit", ts=later_ts),
        _ev("pre_tool_use", payload={"tool_name": "Bash"}, ts=later_ts),
    ]
    monkeypatch.setattr(bk, "EventStream", lambda **kw: _FakeEventStream(events))

    async with Backend(
        tmux_session="ccmux",
        pane_id="%1",
        spinner_grace=0.3,
        process_probe_startup_grace=10.0,
    ) as b:
        await asyncio.sleep(0.2)
        assert _FakeSpinnerMonitor.instances
        mon = _FakeSpinnerMonitor.instances[0]
        from ccmux_spinner.parser import Spinner as _Spinner

        mon.feed(_Spinner(text="Thinking...", todos=()))

        out = []

        async def consume():
            async for s in b.states():
                out.append(s)
                if any(
                    isinstance(x, Idle) and x.reason == "interrupted" for x in out
                ):
                    break

        await asyncio.wait_for(consume(), timeout=2.0)
        assert any(s == Idle(reason="interrupted") for s in out)
