"""End-to-end tests for Backend with patched upstream streams.

Test strategy: replace ``EventStream``, ``MessageStream``,
``SpinnerMonitor``, and the tmux subprocess at the import-site
inside ``ccmux_core.backend``. Drive scenarios and assert
iterator outputs.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

import pytest

from ccmux_core.backend import Backend
from ccmux_core.state import Dead, Idle, Working

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeEventStream:
    """In-memory async iterator over a pre-baked list of events."""

    instances: list[_FakeEventStream] = []

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
    """Test double mirroring ccmux-spinner v0.2.1 API surface.

    Exposes both the iterator and the new `current` / `last_pane_change_at`
    properties that ccmux-core's grace timer queries.
    """

    instances: list[_FakeSpinnerMonitor] = []

    def __init__(self, pane_id: str, poll_interval: float | None = None):
        self.pane_id = pane_id
        self._items: list = []
        self._cancel_event = asyncio.Event()
        self._current = None  # latest Activity (mirrors mon.current)
        self._last_pane_change_at: float = 0.0
        _FakeSpinnerMonitor.instances.append(self)

    @property
    def current(self):
        return self._current

    @property
    def last_pane_change_at(self) -> float:
        return self._last_pane_change_at

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self._cancel_event.set()

    def feed(self, item):
        """Queue an Activity to be yielded by the iterator AND update
        `current` to match. Also bumps `last_pane_change_at` so feeds
        look like pane changes by default. Tests that want to simulate
        a static pane can call `freeze_pane()` after feeding."""
        self._items.append(item)
        self._current = item
        self._last_pane_change_at = time.time()

    def freeze_pane(self):
        """Snapshot the current `last_pane_change_at` and stop advancing
        it. Use after `feed(None)` to simulate Esc-stop (no further pane
        changes). Tests that don't call this see pane_change_at refresh
        on every feed, which matches normal flow."""
        # No-op marker — tests can also just refrain from calling feed()
        # again. Kept as an explicit affordance for readability.

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


def test_backend_exposes_transcript_items_method():
    """L0 method is named `transcript_items`, not `messages`."""
    from ccmux_core import Backend

    # method exists
    assert callable(getattr(Backend, "transcript_items", None))
    # old name removed to avoid confusion with L1 messages()
    assert (
        not hasattr(Backend, "messages")
        or Backend.messages is not Backend.transcript_items
    )


@pytest.mark.asyncio
async def test_backend_emits_initial_idle_start(monkeypatch):
    import ccmux_core.backend as bk

    later_ts = "2099-12-31T23:59:59+00:00"
    monkeypatch.setattr(
        bk,
        "EventStream",
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
        except TimeoutError:
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
        except TimeoutError:
            pass

    assert any(isinstance(s, Dead) and s.reason == "process_gone" for s in out)


@pytest.mark.asyncio
async def test_backend_spinner_grace_fires_when_non_spinner_observed(monkeypatch):
    """Working + a Spinner followed by None held for ≥ grace seconds → Idle(interrupted).

    The grace mechanism keys on an explicit non-Spinner activity
    (None / IdleDecoration) being observed and persisting, not on
    Spinner-emit silence — because SpinnerMonitor coalesces unchanged
    Spinner text and may go quiet for tens of seconds while the
    spinner is in fact still visible in the pane.
    """
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

        # Feed Spinner first (so _has_seen_spinner becomes True), then
        # feed None to simulate Esc-style pane transition.
        mon.feed(_Spinner(text="Thinking...", todos=()))
        await asyncio.sleep(0.05)
        mon.feed(None)

        out = []

        async def consume():
            async for s in b.states():
                out.append(s)
                if any(isinstance(x, Idle) and x.reason == "interrupted" for x in out):
                    break

        await asyncio.wait_for(consume(), timeout=2.0)
        assert any(s == Idle(reason="interrupted") for s in out)


@pytest.mark.asyncio
async def test_backend_grace_does_not_fire_while_spinner_unchanged(monkeypatch):
    """Regression: a Spinner emit followed by SpinnerMonitor silence must NOT
    trigger Idle(interrupted) even after grace seconds.

    SpinnerMonitor only emits on text change; long stretches of unchanged
    spinner text produce zero emits even though the spinner is alive in
    the pane.
    """
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
        spinner_grace=0.2,
        process_probe_startup_grace=10.0,
    ) as b:
        await asyncio.sleep(0.1)
        assert _FakeSpinnerMonitor.instances
        mon = _FakeSpinnerMonitor.instances[0]
        from ccmux_spinner.parser import Spinner as _Spinner

        mon.feed(_Spinner(text="Transmuting...", todos=()))

        out = []

        async def consume():
            async for s in b.states():
                out.append(s)

        # Wait > spinner_grace; assert no Idle(interrupted) appears.
        try:
            await asyncio.wait_for(consume(), timeout=0.8)
        except TimeoutError:
            pass

        assert not any(isinstance(s, Idle) and s.reason == "interrupted" for s in out)


@pytest.mark.asyncio
async def test_backend_grace_does_not_fire_while_pane_changes_without_spinner(
    monkeypatch,
):
    """Regression for the streaming-response case: after Spinner is seen,
    a non-Spinner activity (None / IdleDecoration) does NOT trigger grace
    as long as the raw pane text is still advancing — that's how we
    distinguish 'streaming a long final reply' (pane changing, no spinner
    visible above chrome) from 'Esc-interrupted' (pane static, no
    spinner visible).
    """
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
        await asyncio.sleep(0.15)
        assert _FakeSpinnerMonitor.instances
        mon = _FakeSpinnerMonitor.instances[0]
        from ccmux_spinner.parser import Spinner as _Spinner

        # First we see a Spinner (CC starts thinking).
        mon.feed(_Spinner(text="Thinking...", todos=()))
        await asyncio.sleep(0.05)

        # Then CC starts streaming final reply: spinner is no longer
        # parseable (response text occupies the row), so SpinnerMonitor
        # yields None. But the pane is CHANGING — last_pane_change_at
        # keeps advancing.
        mon.feed(None)

        # Background bumper simulates a continuously-changing pane
        # throughout the rest of the test, mimicking streaming output.
        async def bump_pane_continuously():
            while True:
                mon._last_pane_change_at = time.time()
                await asyncio.sleep(0.05)

        bumper = asyncio.create_task(bump_pane_continuously())
        try:
            out = []

            async def consume():
                async for s in b.states():
                    out.append(s)

            # Wait well past spinner_grace (0.3s) with pane bumping. Grace
            # must NOT fire because pane is "still changing".
            try:
                await asyncio.wait_for(consume(), timeout=1.5)
            except TimeoutError:
                pass

            assert not any(
                isinstance(s, Idle) and s.reason == "interrupted" for s in out
            ), f"unexpected interrupted in {out}"
        finally:
            bumper.cancel()
            try:
                await bumper
            except (asyncio.CancelledError, Exception):
                pass


# ---------------------------------------------------------------------------
# L1 messages() tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_messages_emits_user_prompt_from_event(monkeypatch):
    """user_prompt_submit event → UserPrompt L1 message."""
    import ccmux_core.backend as bk
    from ccmux_core.message import UserPrompt

    later_ts = "2099-12-31T23:59:59+00:00"
    events = [
        _ev("session_start", ts=later_ts),
        _ev("user_prompt_submit", payload={"prompt": "hello"}, ts=later_ts),
    ]
    monkeypatch.setattr(bk, "EventStream", lambda **kw: _FakeEventStream(events))

    async with Backend(tmux_session="ccmux", pane_id="%1") as b:
        msgs = []

        async def collect():
            async for m in b.messages():
                msgs.append(m)
                if len(msgs) >= 1:
                    return

        await asyncio.wait_for(collect(), timeout=2.0)

    assert len(msgs) == 1
    assert isinstance(msgs[0], UserPrompt)
    assert msgs[0].text == "hello"


@pytest.mark.asyncio
async def test_messages_emits_permission_request_from_event(monkeypatch):
    """permission_request event → PermissionRequest L1 message."""
    import ccmux_core.backend as bk
    from ccmux_core.message import PermissionRequest

    later_ts = "2099-12-31T23:59:59+00:00"
    events = [
        _ev("session_start", ts=later_ts),
        _ev(
            "permission_request",
            payload={
                "tool_name": "Bash",
                "tool_input": {"command": "ls"},
                "request_id": "r-1",
            },
            ts=later_ts,
        ),
    ]
    monkeypatch.setattr(bk, "EventStream", lambda **kw: _FakeEventStream(events))

    async with Backend(tmux_session="ccmux", pane_id="%1") as b:
        msgs = []

        async def collect():
            async for m in b.messages():
                msgs.append(m)
                if len(msgs) >= 1:
                    return

        await asyncio.wait_for(collect(), timeout=2.0)

    assert len(msgs) == 1
    assert isinstance(msgs[0], PermissionRequest)
    assert msgs[0].tool_name == "Bash"
    assert msgs[0].tool_input == {"command": "ls"}


@pytest.mark.asyncio
async def test_backend_send_keys_delegates_to_keys_module(tmp_path):
    """Backend.send_keys() should hand pane_id + keys + literal to keys.send_keys
    via asyncio.to_thread (so the subprocess doesn't block the loop)."""
    from unittest.mock import patch

    from ccmux_core import Backend

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    async with Backend(tmux_session="t1", pane_id="%9", events_path=events_path) as b:
        with patch("ccmux_core.backend.send_keys") as sk:
            await b.send_keys("hello", literal=True)
        sk.assert_called_once_with("%9", "hello", literal=True)


@pytest.mark.asyncio
async def test_backend_grace_fires_when_pane_static_and_no_spinner(monkeypatch):
    """Esc-after-streaming case: SpinnerMonitor.current is non-Spinner
    AND last_pane_change_at is stale → fire interrupted.
    """
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
        await asyncio.sleep(0.15)
        assert _FakeSpinnerMonitor.instances
        mon = _FakeSpinnerMonitor.instances[0]
        from ccmux_spinner.parser import Spinner as _Spinner

        mon.feed(_Spinner(text="Thinking...", todos=()))
        await asyncio.sleep(0.05)
        mon.feed(None)
        # Freeze last_pane_change_at by NOT bumping it further.
        frozen_at = mon._last_pane_change_at

        out = []

        async def consume():
            async for s in b.states():
                out.append(s)
                if any(isinstance(x, Idle) and x.reason == "interrupted" for x in out):
                    break

        await asyncio.wait_for(consume(), timeout=2.0)
        assert any(s == Idle(reason="interrupted") for s in out)
        # And ensure the freeze actually held (the test is meaningful):
        assert mon._last_pane_change_at == frozen_at


# ---------------------------------------------------------------------------
# send_prompt + concat queue tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_prompt_in_idle_sends_immediately(tmp_path):
    from unittest.mock import AsyncMock, patch

    from ccmux_core import Backend
    from ccmux_core.state import Idle

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Idle(reason="stop")
        with patch.object(b, "send_keys", new_callable=AsyncMock) as sk:
            await b.send_prompt("hello")
        # At minimum we expect: text "hello" sent literal, plus "Enter" key
        all_call_args = [c.args for c in sk.call_args_list]
        all_kwargs = [c.kwargs for c in sk.call_args_list]
        text_calls = [
            a
            for a, k in zip(all_call_args, all_kwargs, strict=False)
            if a and a[0] == "hello"
        ]
        assert text_calls, f"expected hello in calls; got {all_call_args}"
        enter_calls = [a for a in all_call_args if a and a[0] == "Enter"]
        assert enter_calls, f"expected Enter in calls; got {all_call_args}"


@pytest.mark.asyncio
async def test_send_prompt_in_working_queues_without_sending(tmp_path):
    from unittest.mock import AsyncMock, patch

    from ccmux_core import Backend
    from ccmux_core.state import Working

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Working(tool_name=None)
        with patch.object(b, "send_keys", new_callable=AsyncMock) as sk:
            await b.send_prompt("A")
            await b.send_prompt("B")
        sk.assert_not_called()
        assert b.pending_count == 2
        assert "A" in b.pending_preview
        assert "B" in b.pending_preview


@pytest.mark.asyncio
async def test_send_prompt_in_blocked_raises(tmp_path):
    from ccmux_core import Backend
    from ccmux_core.error import BlockedError
    from ccmux_core.state import Blocked

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Blocked(kind="permission", tool_name="Bash", tool_input={})
        with pytest.raises(BlockedError):
            await b.send_prompt("x")


@pytest.mark.asyncio
async def test_send_prompt_in_dead_raises(tmp_path):
    from ccmux_core import Backend
    from ccmux_core.error import DeadError
    from ccmux_core.state import Dead

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Dead(reason="session_end")
        with pytest.raises(DeadError):
            await b.send_prompt("x")


@pytest.mark.asyncio
async def test_flush_pending_concatenates_with_double_newline(tmp_path):
    from unittest.mock import AsyncMock, patch

    from ccmux_core import Backend
    from ccmux_core.state import Idle, Working

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Working(tool_name=None)
        await b.send_prompt("A")
        await b.send_prompt("B")
        with patch.object(b, "send_keys", new_callable=AsyncMock) as sk:
            await b._flush_pending(new_state=Idle(reason="stop"))
        sent_args = [c.args for c in sk.call_args_list]
        # one of the calls should send "A\n\nB" as literal text
        text_calls = [a for a in sent_args if a and a[0] == "A\n\nB"]
        assert text_calls, f"expected 'A\\n\\nB' in calls; got {sent_args}"
        assert b.pending_count == 0


@pytest.mark.asyncio
async def test_flush_pending_is_noop_when_queue_empty(tmp_path):
    from unittest.mock import AsyncMock, patch

    from ccmux_core import Backend
    from ccmux_core.state import Idle

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        with patch.object(b, "send_keys", new_callable=AsyncMock) as sk:
            await b._flush_pending(new_state=Idle(reason="stop"))
        sk.assert_not_called()


@pytest.mark.asyncio
async def test_flush_pending_is_noop_when_state_not_idle(tmp_path):
    from unittest.mock import AsyncMock, patch

    from ccmux_core import Backend
    from ccmux_core.state import Working

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._pending.append("X")
        with patch.object(b, "send_keys", new_callable=AsyncMock) as sk:
            await b._flush_pending(new_state=Working(tool_name=None))
        sk.assert_not_called()
        assert b.pending_count == 1  # queue preserved


@pytest.mark.asyncio
async def test_trigger_safety_spinner_grace_flushes_pending_to_idle(tmp_path):
    """When the safety net fires spinner_grace, transitioning to
    Idle, the pending queue must be flushed via await (not fire-and-forget)."""
    from unittest.mock import AsyncMock, patch

    from ccmux_core import Backend
    from ccmux_core.state import Working

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Working(tool_name=None)
        b._pending.append("queued-A")
        b._pending.append("queued-B")
        with patch.object(b, "send_keys", new_callable=AsyncMock) as sk:
            # _trigger_safety is now async and awaits flush
            await b._trigger_safety("spinner_grace")
        # one of the send_keys calls should be the concatenated literal
        sent_args = [c.args for c in sk.call_args_list]
        text_calls = [a for a in sent_args if a and a[0] == "queued-A\n\nqueued-B"]
        assert text_calls, f"expected concatenated flush; got {sent_args}"
        assert b.pending_count == 0


@pytest.mark.asyncio
async def test_interrupt_in_working_sends_esc_then_cu_and_clears_queue(tmp_path):
    from unittest.mock import AsyncMock, patch

    from ccmux_core import Backend
    from ccmux_core.state import Working

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Working(tool_name="Bash")
        b._pending = ["A", "B"]
        with patch.object(b, "send_keys", new_callable=AsyncMock) as sk:
            await b.interrupt()
        calls = [c.args for c in sk.call_args_list]
        kwargs = [c.kwargs for c in sk.call_args_list]
        # Esc and Ctrl-U both sent, both as named keys (literal=False)
        esc_calls = [
            (a, k)
            for a, k in zip(calls, kwargs, strict=False)
            if a and a[0] == "Escape"
        ]
        cu_calls = [
            (a, k) for a, k in zip(calls, kwargs, strict=False) if a and a[0] == "C-u"
        ]
        assert esc_calls, f"expected Escape; got {calls}"
        assert cu_calls, f"expected C-u; got {calls}"
        # queue cleared
        assert b.pending_count == 0


@pytest.mark.asyncio
async def test_interrupt_in_working_esc_precedes_cu(tmp_path):
    """Ordering: Esc must be sent BEFORE C-u, otherwise the
    restored prompt wouldn't be in the buffer to clear."""
    from unittest.mock import AsyncMock, patch

    from ccmux_core import Backend
    from ccmux_core.state import Working

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Working(tool_name=None)
        with patch.object(b, "send_keys", new_callable=AsyncMock) as sk:
            await b.interrupt()
        names = [c.args[0] for c in sk.call_args_list if c.args]
        # find indices of Escape and C-u
        i_esc = names.index("Escape")
        i_cu = names.index("C-u")
        assert i_esc < i_cu, f"Escape should precede C-u; got {names}"


@pytest.mark.asyncio
async def test_interrupt_in_idle_is_noop(tmp_path):
    from unittest.mock import AsyncMock, patch

    from ccmux_core import Backend
    from ccmux_core.state import Idle

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Idle(reason="stop")
        b._pending = ["X"]
        with patch.object(b, "send_keys", new_callable=AsyncMock) as sk:
            await b.interrupt()
        sk.assert_not_called()
        # queue NOT cleared in non-Working states
        assert b.pending_count == 1


@pytest.mark.asyncio
async def test_interrupt_in_blocked_is_noop(tmp_path):
    from unittest.mock import AsyncMock, patch

    from ccmux_core import Backend
    from ccmux_core.state import Blocked

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Blocked(kind="permission", tool_name="Bash", tool_input={})
        with patch.object(b, "send_keys", new_callable=AsyncMock) as sk:
            await b.interrupt()
        sk.assert_not_called()


@pytest.mark.asyncio
async def test_interrupt_in_dead_is_noop(tmp_path):
    from unittest.mock import AsyncMock, patch

    from ccmux_core import Backend
    from ccmux_core.state import Dead

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Dead(reason="session_end")
        with patch.object(b, "send_keys", new_callable=AsyncMock) as sk:
            await b.interrupt()
        sk.assert_not_called()


@pytest.mark.asyncio
async def test_decision_listener_bound_in_aenter_unbound_in_aexit(tmp_path):
    """Backend should bind decision.sock on __aenter__ and unbind on __aexit__."""
    from ccmux_core import Backend

    events_path = tmp_path / "events.jsonl"
    events_path.touch()
    sock_path = tmp_path / "decision.sock"

    async with Backend(
        tmux_session="t1",
        pane_id="%0",
        events_path=events_path,
        decision_sock_path=sock_path,
    ) as b:
        # while open, the unix socket file should exist (bound)
        assert sock_path.exists() or sock_path.is_socket()
        assert b._decision_listener is not None
    # after exit, socket file removed and listener is None
    assert not sock_path.exists()
    assert b._decision_listener is None


@pytest.mark.asyncio
async def test_respond_permission_allow_once(tmp_path):
    from unittest.mock import AsyncMock, MagicMock

    from ccmux_core import Backend
    from ccmux_core.state import Blocked

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Blocked(
            kind="permission",
            tool_name="Bash",
            tool_input={},
            request_id="r-1",
        )
        listener = MagicMock()
        listener.respond = AsyncMock()
        b._decision_listener = listener
        await b.respond_permission(decision="allow", mode="once")

    call_args = listener.respond.call_args
    assert call_args.args[0] == "r-1"
    payload = call_args.args[1]
    assert payload["hookSpecificOutput"]["hookEventName"] == "PermissionRequest"
    assert payload["hookSpecificOutput"]["decision"]["behavior"] == "allow"
    # allow + mode=once should NOT add updatedPermissions
    assert "updatedPermissions" not in payload["hookSpecificOutput"]["decision"]


@pytest.mark.asyncio
async def test_respond_permission_deny_carries_message(tmp_path):
    from unittest.mock import AsyncMock, MagicMock

    from ccmux_core import Backend
    from ccmux_core.state import Blocked

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Blocked(
            kind="permission",
            tool_name="Bash",
            tool_input={},
            request_id="r-2",
        )
        listener = MagicMock()
        listener.respond = AsyncMock()
        b._decision_listener = listener
        await b.respond_permission(decision="deny", message="no thanks")

    payload = listener.respond.call_args.args[1]
    assert payload["hookSpecificOutput"]["decision"]["behavior"] == "deny"
    assert payload["hookSpecificOutput"]["decision"]["message"] == "no thanks"


@pytest.mark.asyncio
async def test_respond_permission_deny_default_message(tmp_path):
    """deny without explicit message should still emit a fallback message."""
    from unittest.mock import AsyncMock, MagicMock

    from ccmux_core import Backend
    from ccmux_core.state import Blocked

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Blocked(
            kind="permission",
            tool_name="Bash",
            tool_input={},
            request_id="r-2",
        )
        listener = MagicMock()
        listener.respond = AsyncMock()
        b._decision_listener = listener
        await b.respond_permission(decision="deny")

    payload = listener.respond.call_args.args[1]
    dec = payload["hookSpecificOutput"]["decision"]
    assert dec["behavior"] == "deny"
    assert "message" in dec and dec["message"]  # non-empty fallback


@pytest.mark.asyncio
async def test_respond_permission_always_includes_updated_permissions(tmp_path):
    from unittest.mock import AsyncMock, MagicMock

    from ccmux_core import Backend
    from ccmux_core.state import Blocked

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Blocked(
            kind="permission",
            tool_name="Bash",
            tool_input={
                "permission_suggestions": [{"type": "addRule", "rule": "Bash:*"}]
            },
            request_id="r-3",
        )
        listener = MagicMock()
        listener.respond = AsyncMock()
        b._decision_listener = listener
        await b.respond_permission(decision="allow", mode="always")

    payload = listener.respond.call_args.args[1]
    dec = payload["hookSpecificOutput"]["decision"]
    assert dec["behavior"] == "allow"
    assert dec["updatedPermissions"] == [{"type": "addRule", "rule": "Bash:*"}]


@pytest.mark.asyncio
async def test_respond_permission_all_mode_includes_updated_permissions(tmp_path):
    """mode='all' also pulls from permission_suggestions (broader scope)."""
    from unittest.mock import AsyncMock, MagicMock

    from ccmux_core import Backend
    from ccmux_core.state import Blocked

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Blocked(
            kind="permission",
            tool_name="Bash",
            tool_input={"permission_suggestions": [{"type": "X"}]},
            request_id="r-4",
        )
        listener = MagicMock()
        listener.respond = AsyncMock()
        b._decision_listener = listener
        await b.respond_permission(decision="allow", mode="all")

    dec = listener.respond.call_args.args[1]["hookSpecificOutput"]["decision"]
    assert dec["updatedPermissions"] == [{"type": "X"}]


@pytest.mark.asyncio
async def test_respond_permission_bypass_does_not_add_updated_permissions(tmp_path):
    """bypass mode just allows (claude must already have --allow-dangerously-skip-permissions)."""
    from unittest.mock import AsyncMock, MagicMock

    from ccmux_core import Backend
    from ccmux_core.state import Blocked

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Blocked(
            kind="permission",
            tool_name="Bash",
            tool_input={"permission_suggestions": [{"type": "X"}]},
            request_id="r-5",
        )
        listener = MagicMock()
        listener.respond = AsyncMock()
        b._decision_listener = listener
        await b.respond_permission(decision="allow", mode="bypass")

    dec = listener.respond.call_args.args[1]["hookSpecificOutput"]["decision"]
    assert dec["behavior"] == "allow"
    assert "updatedPermissions" not in dec


@pytest.mark.asyncio
async def test_respond_permission_in_non_blocked_raises(tmp_path):
    from ccmux_core import Backend
    from ccmux_core.error import WrongStateError
    from ccmux_core.state import Idle

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Idle(reason="stop")
        with pytest.raises(WrongStateError):
            await b.respond_permission(decision="allow", mode="once")


@pytest.mark.asyncio
async def test_respond_permission_wrong_kind_raises(tmp_path):
    from ccmux_core import Backend
    from ccmux_core.error import WrongBlockedKindError
    from ccmux_core.state import Blocked

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Blocked(
            kind="ask_user",
            tool_name="AskUserQuestion",
            tool_input={},
            request_id="r-x",
        )
        with pytest.raises(WrongBlockedKindError):
            await b.respond_permission(decision="allow", mode="once")


@pytest.mark.asyncio
async def test_respond_permission_after_expired_raises(tmp_path):
    from ccmux_core import Backend
    from ccmux_core.error import BlockedExpiredError
    from ccmux_core.state import Blocked

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Blocked(
            kind="permission",
            tool_name="Bash",
            tool_input={},
            request_id="r-x",
            expired=True,
        )
        with pytest.raises(BlockedExpiredError):
            await b.respond_permission(decision="allow", mode="once")


@pytest.mark.asyncio
async def test_respond_exit_plan_manual(tmp_path):
    from unittest.mock import AsyncMock, MagicMock

    from ccmux_core import Backend
    from ccmux_core.state import Blocked

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    listener = MagicMock()
    listener.respond = AsyncMock()
    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Blocked(
            kind="exit_plan_mode",
            tool_name="ExitPlanMode",
            tool_input={"plan": "# Plan\n- step 1"},
            request_id="r-1",
        )
        b._decision_listener = listener
        await b.respond_exit_plan(mode="manual")

    dec = listener.respond.call_args.args[1]["hookSpecificOutput"]["decision"]
    assert dec["behavior"] == "allow"
    assert dec["updatedInput"] == {"plan": "# Plan\n- step 1"}
    # manual should NOT add setMode
    assert "updatedPermissions" not in dec


@pytest.mark.asyncio
async def test_respond_exit_plan_auto_accept_includes_set_mode(tmp_path):
    from unittest.mock import AsyncMock, MagicMock

    from ccmux_core import Backend
    from ccmux_core.state import Blocked

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    listener = MagicMock()
    listener.respond = AsyncMock()
    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Blocked(
            kind="exit_plan_mode",
            tool_name="ExitPlanMode",
            tool_input={"plan": "..."},
            request_id="r-2",
        )
        b._decision_listener = listener
        await b.respond_exit_plan(mode="autoAccept")

    dec = listener.respond.call_args.args[1]["hookSpecificOutput"]["decision"]
    assert dec["behavior"] == "allow"
    assert dec["updatedPermissions"] == [
        {"type": "setMode", "mode": "auto", "destination": "session"}
    ]


@pytest.mark.asyncio
async def test_respond_exit_plan_ultraplan_is_deny_with_message(tmp_path):
    from unittest.mock import AsyncMock, MagicMock

    from ccmux_core import Backend
    from ccmux_core.state import Blocked

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    listener = MagicMock()
    listener.respond = AsyncMock()
    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Blocked(
            kind="exit_plan_mode",
            tool_name="ExitPlanMode",
            tool_input={"plan": "..."},
            request_id="r-3",
        )
        b._decision_listener = listener
        await b.respond_exit_plan(mode="ultraplan")

    dec = listener.respond.call_args.args[1]["hookSpecificOutput"]["decision"]
    assert dec["behavior"] == "deny"
    assert "Ultraplan" in dec["message"]


@pytest.mark.asyncio
async def test_respond_exit_plan_deny_default_message(tmp_path):
    from unittest.mock import AsyncMock, MagicMock

    from ccmux_core import Backend
    from ccmux_core.state import Blocked

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    listener = MagicMock()
    listener.respond = AsyncMock()
    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Blocked(
            kind="exit_plan_mode",
            tool_name="ExitPlanMode",
            tool_input={"plan": "..."},
            request_id="r-4",
        )
        b._decision_listener = listener
        await b.respond_exit_plan(mode="deny")

    dec = listener.respond.call_args.args[1]["hookSpecificOutput"]["decision"]
    assert dec["behavior"] == "deny"
    assert "rejected" in dec["message"].lower()


@pytest.mark.asyncio
async def test_respond_exit_plan_feedback_overrides_mode(tmp_path):
    """If feedback is non-empty, it forces a deny regardless of mode."""
    from unittest.mock import AsyncMock, MagicMock

    from ccmux_core import Backend
    from ccmux_core.state import Blocked

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    listener = MagicMock()
    listener.respond = AsyncMock()
    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Blocked(
            kind="exit_plan_mode",
            tool_name="ExitPlanMode",
            tool_input={"plan": "..."},
            request_id="r-5",
        )
        b._decision_listener = listener
        # even mode=manual gets overridden by non-empty feedback
        await b.respond_exit_plan(mode="manual", feedback="add tests first")

    dec = listener.respond.call_args.args[1]["hookSpecificOutput"]["decision"]
    assert dec["behavior"] == "deny"
    assert "add tests first" in dec["message"]


@pytest.mark.asyncio
async def test_respond_exit_plan_in_wrong_kind_raises(tmp_path):
    from ccmux_core import Backend
    from ccmux_core.error import WrongBlockedKindError
    from ccmux_core.state import Blocked

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Blocked(
            kind="permission",
            tool_name="Bash",
            tool_input={},
            request_id="r-x",
        )
        with pytest.raises(WrongBlockedKindError):
            await b.respond_exit_plan(mode="manual")


@pytest.mark.asyncio
async def test_respond_exit_plan_in_non_blocked_raises(tmp_path):
    from ccmux_core import Backend
    from ccmux_core.error import WrongStateError
    from ccmux_core.state import Idle

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Idle(reason="stop")
        with pytest.raises(WrongStateError):
            await b.respond_exit_plan(mode="manual")


@pytest.mark.asyncio
async def test_respond_exit_plan_after_expired_raises(tmp_path):
    from ccmux_core import Backend
    from ccmux_core.error import BlockedExpiredError
    from ccmux_core.state import Blocked

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Blocked(
            kind="exit_plan_mode",
            tool_name="ExitPlanMode",
            tool_input={},
            request_id="r-x",
            expired=True,
        )
        with pytest.raises(BlockedExpiredError):
            await b.respond_exit_plan(mode="manual")


@pytest.mark.asyncio
async def test_respond_question_single_selection(tmp_path):
    from unittest.mock import AsyncMock, MagicMock

    from ccmux_core import Backend
    from ccmux_core.state import Blocked

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    listener = MagicMock()
    listener.respond = AsyncMock()
    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Blocked(
            kind="ask_user",
            tool_name="AskUserQuestion",
            tool_input={
                "questions": [
                    {"question": "Pick a DB", "options": [{"label": "postgres"}]},
                ]
            },
            request_id="r-1",
        )
        b._decision_listener = listener
        await b.respond_question(["postgres"])

    payload = listener.respond.call_args.args[1]
    dec = payload["hookSpecificOutput"]["decision"]
    assert dec["behavior"] == "allow"
    assert dec["updatedInput"]["answers"] == {"Pick a DB": "postgres"}
    # questions preserved alongside answers
    assert dec["updatedInput"]["questions"] == [
        {"question": "Pick a DB", "options": [{"label": "postgres"}]},
    ]


@pytest.mark.asyncio
async def test_respond_question_multiple_questions_positional(tmp_path):
    from unittest.mock import AsyncMock, MagicMock

    from ccmux_core import Backend
    from ccmux_core.state import Blocked

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    listener = MagicMock()
    listener.respond = AsyncMock()
    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Blocked(
            kind="ask_user",
            tool_name="AskUserQuestion",
            tool_input={
                "questions": [
                    {"question": "Q1", "options": [{"label": "a"}]},
                    {"question": "Q2", "options": [{"label": "b"}]},
                ]
            },
            request_id="r-2",
        )
        b._decision_listener = listener
        await b.respond_question(["a", "b"])

    answers = listener.respond.call_args.args[1]["hookSpecificOutput"]["decision"][
        "updatedInput"
    ]["answers"]
    assert answers == {"Q1": "a", "Q2": "b"}


@pytest.mark.asyncio
async def test_respond_question_extra_selections_use_fallback_key(tmp_path):
    """If selections has more entries than questions, the extras get
    'Answer N' as the key — matches cmux behavior."""
    from unittest.mock import AsyncMock, MagicMock

    from ccmux_core import Backend
    from ccmux_core.state import Blocked

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    listener = MagicMock()
    listener.respond = AsyncMock()
    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Blocked(
            kind="ask_user",
            tool_name="AskUserQuestion",
            tool_input={
                "questions": [
                    {"question": "Q1", "options": [{"label": "a"}]},
                ]
            },
            request_id="r-3",
        )
        b._decision_listener = listener
        await b.respond_question(["a", "b"])

    answers = listener.respond.call_args.args[1]["hookSpecificOutput"]["decision"][
        "updatedInput"
    ]["answers"]
    assert answers == {"Q1": "a", "Answer 2": "b"}


@pytest.mark.asyncio
async def test_respond_question_question_text_stripped(tmp_path):
    """Question keys should be stripped (whitespace shouldn't matter)."""
    from unittest.mock import AsyncMock, MagicMock

    from ccmux_core import Backend
    from ccmux_core.state import Blocked

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    listener = MagicMock()
    listener.respond = AsyncMock()
    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Blocked(
            kind="ask_user",
            tool_name="AskUserQuestion",
            tool_input={"questions": [{"question": "  spaced?  ", "options": []}]},
            request_id="r-4",
        )
        b._decision_listener = listener
        await b.respond_question(["yes"])

    answers = listener.respond.call_args.args[1]["hookSpecificOutput"]["decision"][
        "updatedInput"
    ]["answers"]
    assert "spaced?" in answers


@pytest.mark.asyncio
async def test_respond_question_in_wrong_kind_raises(tmp_path):
    from ccmux_core import Backend
    from ccmux_core.error import WrongBlockedKindError
    from ccmux_core.state import Blocked

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Blocked(
            kind="permission",
            tool_name="Bash",
            tool_input={},
            request_id="r-x",
        )
        with pytest.raises(WrongBlockedKindError):
            await b.respond_question(["x"])


@pytest.mark.asyncio
async def test_respond_question_in_non_blocked_raises(tmp_path):
    from ccmux_core import Backend
    from ccmux_core.error import WrongStateError
    from ccmux_core.state import Idle

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Idle(reason="stop")
        with pytest.raises(WrongStateError):
            await b.respond_question(["x"])


@pytest.mark.asyncio
async def test_respond_question_after_expired_raises(tmp_path):
    from ccmux_core import Backend
    from ccmux_core.error import BlockedExpiredError
    from ccmux_core.state import Blocked

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Blocked(
            kind="ask_user",
            tool_name="AskUserQuestion",
            tool_input={"questions": []},
            request_id="r-x",
            expired=True,
        )
        with pytest.raises(BlockedExpiredError):
            await b.respond_question(["x"])


@pytest.mark.asyncio
async def test_drop_to_tui_responds_empty_and_marks_expired(tmp_path):
    from unittest.mock import AsyncMock, MagicMock

    from ccmux_core import Backend
    from ccmux_core.state import Blocked

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    listener = MagicMock()
    listener.respond = AsyncMock()
    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Blocked(
            kind="permission",
            tool_name="Bash",
            tool_input={},
            request_id="r-1",
        )
        b._decision_listener = listener
        await b.drop_to_tui()
        # state should now be Blocked.expired=True
        assert isinstance(b._state, Blocked)
        assert b._state.expired is True
        # kind / tool_name / request_id preserved
        assert b._state.kind == "permission"
        assert b._state.request_id == "r-1"

    listener.respond.assert_called_once_with("r-1", {})


@pytest.mark.asyncio
async def test_drop_to_tui_outside_blocked_raises(tmp_path):
    from ccmux_core import Backend
    from ccmux_core.error import WrongStateError
    from ccmux_core.state import Idle

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Idle(reason="stop")
        with pytest.raises(WrongStateError):
            await b.drop_to_tui()


@pytest.mark.asyncio
async def test_drop_to_tui_when_already_expired_is_noop(tmp_path):
    from unittest.mock import AsyncMock, MagicMock

    from ccmux_core import Backend
    from ccmux_core.state import Blocked

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    listener = MagicMock()
    listener.respond = AsyncMock()
    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Blocked(
            kind="permission",
            tool_name="Bash",
            tool_input={},
            request_id="r-1",
            expired=True,
        )
        b._decision_listener = listener
        await b.drop_to_tui()
        # no-op — no second respond call
    listener.respond.assert_not_called()


@pytest.mark.asyncio
async def test_drop_to_tui_with_no_request_id_only_marks_expired(tmp_path):
    """If Blocked has no request_id (e.g. from pre_tool_use route),
    drop_to_tui still marks expired but doesn't try to respond."""
    from unittest.mock import AsyncMock, MagicMock

    from ccmux_core import Backend
    from ccmux_core.state import Blocked

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    listener = MagicMock()
    listener.respond = AsyncMock()
    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Blocked(
            kind="ask_user",
            tool_name="AskUserQuestion",
            tool_input={},
            request_id=None,  # no request_id
        )
        b._decision_listener = listener
        await b.drop_to_tui()
        assert b._state.expired is True
    listener.respond.assert_not_called()


@pytest.mark.asyncio
async def test_backend_state_property_exposes_current_state(tmp_path):
    """Backend.state returns the current State or None pre-live."""
    from ccmux_core import Backend
    from ccmux_core.state import Idle

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        # default: None pre-live (no events processed yet)
        assert b.state is None or hasattr(b.state, "reason")  # tolerant
        b._state = Idle(reason="stop")
        assert b.state == Idle(reason="stop")
