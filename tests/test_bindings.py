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
            session_id_history=("S1",),
            first_seen_at="2026-05-10T00:00:00+00:00",
            last_event_at="2026-05-10T00:00:00+00:00",
            ended_at=None,
        )
    ]


def test_session_start_populates_new_fields(tmp_path):
    p = tmp_path / "events.jsonl"
    _write_events(p, [_ev("session_start", "S1", ts="T1")])
    [out] = list_live_tmux_bindings(events_path=p)
    assert out.current_session_id == "S1"
    assert out.session_id_history == ("S1",)
    assert out.first_seen_at == "T1"
    assert out.last_event_at == "T1"
    assert out.ended_at is None


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


def test_fatal_session_end_excludes_from_live_list(tmp_path):
    p = tmp_path / "events.jsonl"
    _write_events(
        p,
        [
            _ev("session_start", "S1"),
            _ev("session_end", "S1", payload={"reason": "error"}),
        ],
    )
    # Observable behavior unchanged: list_live filters out ended sessions
    # even though the internal dict now preserves the entry.
    assert list_live_tmux_bindings(events_path=p) == []


def test_session_end_preserves_entry():
    from ccmux_core.bindings import _MutableBinding, _step

    bindings: dict[str, _MutableBinding] = {}
    _step(bindings, _ev("session_start", "S1", ts="T1"))
    _step(bindings, _ev("session_end", "S1", payload={"reason": "exit"}, ts="T2"))

    assert "ccmux" in bindings, "entry must be preserved after session_end"
    entry = bindings["ccmux"]
    assert entry.current_session_id is None
    assert entry.ended_at == "T2"
    assert entry.session_id_history == ["S1"]


def test_history_appends_new_sid_on_reattach(tmp_path):
    p = tmp_path / "events.jsonl"
    _write_events(
        p,
        [
            _ev("session_start", "S1", ts="T1"),
            _ev("session_end", "S1", payload={"reason": "exit"}, ts="T2"),
            _ev("session_start", "S2", ts="T3"),
        ],
    )
    [out] = list_live_tmux_bindings(events_path=p)
    assert out.current_session_id == "S2"
    assert out.session_id_history == ("S1", "S2")
    assert out.ended_at is None


def test_history_dedups_resume_of_same_sid(tmp_path):
    p = tmp_path / "events.jsonl"
    _write_events(
        p,
        [
            _ev("session_start", "S1", ts="T1"),
            _ev("session_end", "S1", payload={"reason": "exit"}, ts="T2"),
            _ev("session_start", "S1", ts="T3"),  # --resume scenario
        ],
    )
    [out] = list_live_tmux_bindings(events_path=p)
    assert out.current_session_id == "S1"
    assert out.session_id_history == ("S1",), "resume must not duplicate"
    assert out.ended_at is None


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


def test_atomic_write_creates_file_with_payload(tmp_path):
    from ccmux_core.bindings import _atomic_write

    path = tmp_path / "bindings.json"
    lock = tmp_path / "bindings.lock"
    _atomic_write(path, lock, {"ccmux": {"pane_id": "%1"}})

    assert path.exists()
    assert json.loads(path.read_text()) == {"ccmux": {"pane_id": "%1"}}


def test_atomic_write_leaves_no_tmp_file_on_success(tmp_path):
    from ccmux_core.bindings import _atomic_write

    path = tmp_path / "bindings.json"
    lock = tmp_path / "bindings.lock"
    _atomic_write(path, lock, {"a": 1})

    leftover = list(tmp_path.glob("*.tmp"))
    assert leftover == [], f"unexpected tmp files: {leftover}"


def test_atomic_write_is_serialized_under_contention(tmp_path):
    """Two threads racing for the lock; final file is one writer's output, not interleaved."""
    import threading

    from ccmux_core.bindings import _atomic_write

    path = tmp_path / "bindings.json"
    lock = tmp_path / "bindings.lock"
    big_a = {"k": "A" * 10000}
    big_b = {"k": "B" * 10000}

    def w(payload):
        for _ in range(10):
            _atomic_write(path, lock, payload)

    threads = [
        threading.Thread(target=w, args=(big_a,)),
        threading.Thread(target=w, args=(big_b,)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # File is valid JSON and is exactly one of the two payloads
    # (whichever writer happened to be last).
    parsed = json.loads(path.read_text())
    assert parsed in (big_a, big_b)
