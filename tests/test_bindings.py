"""Tests for list_live_tmux_bindings (sync) and discover_tmux_sessions (async)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from ccmux_core.bindings import (
    TmuxBinding,
    discover_tmux_sessions,
    list_live_tmux_bindings,
)


def _ev(
    et: str,
    sid: str,
    tmux: str = "ccmux",
    pane: str = "%1",
    window: str = "@0",
    payload: dict | None = None,
    ts: str = "2026-05-10T00:00:00+00:00",
) -> dict:
    return {
        "event_type": et,
        "timestamp": ts,
        "claude": {"session_id": sid},
        "tmux": {"session_name": tmux, "pane_id": pane, "window_id": window},
        "payload": payload or {},
    }


def _write_events(path: Path, events: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")


def test_tmuxbinding_has_new_fields():
    from ccmux_core.bindings import TmuxBinding

    tb = TmuxBinding(
        tmux_session="ccmux",
        pane_id="%1",
        window_id="@0",
        current_session_id="sid-1",
        session_id_history=("sid-1",),
        first_seen_at="2026-05-12T00:00:00Z",
        last_event_at="2026-05-12T00:00:01Z",
        ended_at=None,
    )
    assert tb.current_session_id == "sid-1"
    assert tb.session_id_history == ("sid-1",)
    assert tb.ended_at is None


# ---------------------------------------------------------------------------
# list_live_tmux_bindings
# ---------------------------------------------------------------------------


def test_no_file_returns_empty(tmp_path):
    out = list_live_tmux_bindings(events_path=tmp_path / "nope.jsonl")
    assert out == []


def test_empty_file_returns_empty(tmp_path):
    p = tmp_path / "events.jsonl"
    p.write_text("")
    assert list_live_tmux_bindings(events_path=p) == []


def test_malformed_lines_skipped(tmp_path):
    p = tmp_path / "events.jsonl"
    p.write_text("not json\n" + json.dumps(_ev("session_start", "S1")) + "\n")
    out = list_live_tmux_bindings(events_path=p)
    assert len(out) == 1
    assert out[0].current_session_id == "S1"


def test_single_session_start_yields_one_binding(tmp_path):
    p = tmp_path / "events.jsonl"
    _write_events(p, [_ev("session_start", "S1", pane="%42", window="@7")])
    out = list_live_tmux_bindings(events_path=p)
    assert out == [
        TmuxBinding(
            tmux_session="ccmux",
            pane_id="%42",
            window_id="@7",
            current_session_id="S1",
            session_id_history=(),
            first_seen_at="2026-05-10T00:00:00+00:00",
            last_event_at="2026-05-10T00:00:00+00:00",
            ended_at=None,
        )
    ]


def test_clear_chain_yields_only_last_primary(tmp_path):
    p = tmp_path / "events.jsonl"
    _write_events(
        p,
        [
            _ev("session_start", "S1", ts="T1"),
            _ev("session_end", "S1", payload={"reason": "clear"}, ts="T2"),
            _ev("session_start", "S2", ts="T3"),
            _ev("session_end", "S2", payload={"reason": "clear"}, ts="T4"),
            _ev("session_start", "S3", ts="T5"),
        ],
    )
    out = list_live_tmux_bindings(events_path=p)
    assert len(out) == 1
    assert out[0].current_session_id == "S3"
    assert out[0].last_event_at == "T5"


def test_prompt_input_exit_keeps_primary(tmp_path):
    p = tmp_path / "events.jsonl"
    _write_events(
        p,
        [
            _ev("session_start", "S1", ts="T1"),
            _ev("session_end", "S1", payload={"reason": "prompt_input_exit"}, ts="T2"),
        ],
    )
    out = list_live_tmux_bindings(events_path=p)
    assert len(out) == 1
    assert out[0].current_session_id == "S1"


def test_fatal_session_end_removes_binding(tmp_path):
    p = tmp_path / "events.jsonl"
    _write_events(
        p,
        [
            _ev("session_start", "S1"),
            _ev("session_end", "S1", payload={"reason": "error"}),
        ],
    )
    assert list_live_tmux_bindings(events_path=p) == []


def test_pane_id_follows_latest_event(tmp_path):
    p = tmp_path / "events.jsonl"
    _write_events(
        p,
        [
            _ev("session_start", "S1", pane="%1"),
            _ev("user_prompt_submit", "S1", pane="%2"),
        ],
    )
    out = list_live_tmux_bindings(events_path=p)
    assert out[0].pane_id == "%2"


def test_subagent_does_not_override_primary(tmp_path):
    p = tmp_path / "events.jsonl"
    _write_events(
        p,
        [
            _ev("session_start", "S1", ts="T1"),
            _ev("pre_tool_use", "S1", payload={"tool_name": "Task"}, ts="T2"),
            _ev("session_start", "SUB", ts="T3"),
            _ev("session_end", "SUB", payload={"reason": "stop"}, ts="T4"),
        ],
    )
    out = list_live_tmux_bindings(events_path=p)
    assert len(out) == 1
    assert out[0].current_session_id == "S1"


def test_multiple_tmux_sessions_yield_separate_bindings(tmp_path):
    p = tmp_path / "events.jsonl"
    _write_events(
        p,
        [
            _ev("session_start", "S1", tmux="ccmux"),
            _ev("session_start", "S2", tmux="demo"),
        ],
    )
    out = list_live_tmux_bindings(events_path=p)
    tmux_names = {b.tmux_session for b in out}
    assert tmux_names == {"ccmux", "demo"}


def test_clear_with_no_rebind_excludes_binding(tmp_path):
    p = tmp_path / "events.jsonl"
    _write_events(
        p,
        [
            _ev("session_start", "S1"),
            _ev("session_end", "S1", payload={"reason": "clear"}),
        ],
    )
    assert list_live_tmux_bindings(events_path=p) == []


# ---------------------------------------------------------------------------
# discover_tmux_sessions
# ---------------------------------------------------------------------------


async def _drain(it, *, n: int, timeout: float = 2.0) -> list:
    out = []

    async def _consume():
        async for item in it:
            out.append(item)
            if len(out) >= n:
                break

    await asyncio.wait_for(_consume(), timeout=timeout)
    return out


@pytest.mark.asyncio
async def test_discover_include_existing_yields_initial_snapshot(tmp_path):
    p = tmp_path / "events.jsonl"
    _write_events(
        p,
        [
            _ev("session_start", "S1", tmux="A"),
            _ev("session_start", "S2", tmux="B"),
        ],
    )
    out = await _drain(
        discover_tmux_sessions(events_path=p, include_existing=True),
        n=2,
    )
    tmux_names = {b.tmux_session for b in out}
    assert tmux_names == {"A", "B"}


@pytest.mark.asyncio
async def test_discover_skips_existing_when_disabled(tmp_path):
    p = tmp_path / "events.jsonl"
    _write_events(
        p,
        [
            _ev("session_start", "S1", tmux="A"),
        ],
    )
    received = []

    async def collect():
        async for b in discover_tmux_sessions(events_path=p, include_existing=False):
            received.append(b)

    task = asyncio.create_task(collect())
    await asyncio.sleep(0.3)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert received == []


@pytest.mark.asyncio
async def test_discover_does_not_yield_same_tmux_twice(tmp_path):
    p = tmp_path / "events.jsonl"
    _write_events(
        p,
        [
            _ev("session_start", "S1", tmux="A"),
        ],
    )
    out = await _drain(
        discover_tmux_sessions(events_path=p, include_existing=True),
        n=1,
    )
    assert len(out) == 1
    assert out[0].tmux_session == "A"
