"""TmuxBinding dataclass + discovery helpers.

Two helpers:

* :func:`list_live_tmux_bindings` — synchronous one-shot scan of
  ``events.jsonl`` that returns the current per-tmux-session
  bindings.
* :func:`discover_tmux_sessions` — async iterator yielding
  bindings as new tmux sessions appear.

Both rely on the primary-session-tracking rules in the design
spec: subagent session_ids never override an existing primary,
``/clear``'s ``session_end`` clears primary in expectation of a
rebind from the next ``session_start``, ``prompt_input_exit``
keeps primary intact.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from claude_tap.config import events_path as _default_events_path


@dataclass(frozen=True)
class TmuxBinding:
    """Current mapping for one tmux session.

    Produced by :func:`list_live_tmux_bindings` (snapshot) and
    :func:`discover_tmux_sessions` (stream).

    ``current_session_id is None`` indicates the binding has been
    observed before but no Claude session is currently attached
    (post-``session_end`` or post-``/clear``). ``session_id_history``
    is the first-seen-order, deduplicated list of every Claude
    session id this tmux session has hosted. ``ended_at`` is the
    timestamp of the most recent ``current → None`` transition or
    ``None`` if currently attached.
    """

    tmux_session: str
    pane_id: str
    window_id: str
    current_session_id: str | None
    session_id_history: tuple[str, ...]
    first_seen_at: str
    last_event_at: str
    ended_at: str | None


def list_live_tmux_bindings(
    events_path: Path | None = None,
) -> list[TmuxBinding]:
    """One-shot snapshot of currently-live tmux session bindings.

    Reads events.jsonl, processes every entry, returns the current
    bindings list. "Live" means the most recent state for the tmux
    session leaves ``current_session_id`` set (not in a post-/clear
    gap, not after a fatal session_end).

    Returns ``[]`` if events.jsonl does not exist, is empty, or
    contains no live bindings. Skips malformed JSONL lines.
    """
    path = events_path if events_path is not None else _default_events_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []

    bindings: dict[str, _MutableBinding] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        _step(bindings, ev)

    return [
        TmuxBinding(
            tmux_session=b.tmux_session,
            pane_id=b.pane_id,
            window_id=b.window_id,
            current_session_id=b.current_session_id,
            session_id_history=tuple(b.session_id_history),
            first_seen_at=b.first_seen_at,
            last_event_at=b.last_event_at,
            ended_at=b.ended_at,
        )
        for b in bindings.values()
        if b.current_session_id is not None
    ]


async def discover_tmux_sessions(
    events_path: Path | None = None,
    include_existing: bool = True,
) -> AsyncIterator[TmuxBinding]:
    """Yield :class:`TmuxBinding` on each new tmux session entering an
    observable state.

    ``include_existing=True`` (default): first yields the result of
    :func:`list_live_tmux_bindings`, then tails events.jsonl for
    genuinely new ones.

    ``include_existing=False``: only yields on tmux session names
    first seen in events.jsonl entries written after subscribe.

    Rebinds (a tmux session's primary changing via /clear) are NOT
    re-yielded; existing Backend instances follow rebinds
    internally and surface them via b.states().
    """
    path = events_path if events_path is not None else _default_events_path()
    yielded: set[str] = set()

    if include_existing:
        for binding in list_live_tmux_bindings(events_path=path):
            yielded.add(binding.tmux_session)
            yield binding

    bindings: dict[str, _MutableBinding] = {}
    while not path.exists():
        await asyncio.sleep(0.1)

    with open(path, encoding="utf-8") as f:
        f.seek(0, 2)  # EOF
        buf = ""
        while True:
            line = f.readline()
            if not line:
                await asyncio.sleep(0.1)
                continue
            buf += line
            if not buf.endswith("\n"):
                continue
            try:
                ev = json.loads(buf.rstrip("\n"))
            except json.JSONDecodeError:
                buf = ""
                continue
            buf = ""
            _step(bindings, ev)
            for tmux_session, b in list(bindings.items()):
                if tmux_session not in yielded and b.current_session_id is not None:
                    yielded.add(tmux_session)
                    yield TmuxBinding(
                        tmux_session=b.tmux_session,
                        pane_id=b.pane_id,
                        window_id=b.window_id,
                        current_session_id=b.current_session_id,
                        session_id_history=tuple(b.session_id_history),
                        first_seen_at=b.first_seen_at,
                        last_event_at=b.last_event_at,
                        ended_at=b.ended_at,
                    )


# ---------------------------------------------------------------------------
# Internal: mutable binding record + per-event step
# ---------------------------------------------------------------------------


@dataclass
class _MutableBinding:
    tmux_session: str
    pane_id: str
    window_id: str
    current_session_id: str | None
    session_id_history: list[str]
    first_seen_at: str
    last_event_at: str
    ended_at: str | None


def _step(bindings: dict[str, _MutableBinding], event: dict) -> None:
    """Apply one event to the bindings dict in place.

    Mirrors the primary-tracking rules in state_machine.apply().
    """
    tmux = event.get("tmux") or {}
    tmux_session = tmux.get("session_name")
    if not tmux_session:
        return
    sid = (event.get("claude") or {}).get("session_id", "")
    if not sid:
        return
    et = event.get("event_type", "")
    payload = event.get("payload") or {}
    ts = event.get("timestamp", "")

    b = bindings.get(tmux_session)

    if et == "session_start":
        if b is None:
            bindings[tmux_session] = _MutableBinding(
                tmux_session=tmux_session,
                pane_id=tmux.get("pane_id", ""),
                window_id=tmux.get("window_id", ""),
                current_session_id=sid,
                session_id_history=[sid],
                first_seen_at=ts,
                last_event_at=ts,
                ended_at=None,
            )
            return
        if b.current_session_id is None:
            # Re-attach after end / clear. Append sid to history if new.
            if sid not in b.session_id_history:
                b.session_id_history.append(sid)
            b.current_session_id = sid
            b.pane_id = tmux.get("pane_id", b.pane_id)
            b.window_id = tmux.get("window_id", b.window_id)
            b.ended_at = None
            b.last_event_at = ts
            return
        # current set already — subagent / duplicate event
        b.last_event_at = ts
        return

    if et == "session_end":
        if b is None or sid != b.current_session_id:
            if b is not None:
                b.last_event_at = ts
            return
        reason = payload.get("reason", "")
        if reason == "clear":
            b.current_session_id = None
            b.ended_at = ts
            b.last_event_at = ts
            return
        if reason == "prompt_input_exit":
            b.last_event_at = ts
            return
        # Other reason (clean exit, etc.) — preserve entry, mark ended.
        b.current_session_id = None
        b.ended_at = ts
        b.last_event_at = ts
        return

    if b is None:
        return
    if sid == b.current_session_id:
        b.last_event_at = ts
        b.pane_id = tmux.get("pane_id", b.pane_id)
        b.window_id = tmux.get("window_id", b.window_id)
    else:
        b.last_event_at = ts


def _atomic_write(
    path: Path,
    lock_path: Path,
    data: dict,
) -> None:
    """Write ``data`` to ``path`` atomically, serialized through ``lock_path``.

    Uses an advisory ``fcntl.flock`` on ``lock_path`` for the duration
    of the write so that concurrent writers (the tracker + a manual
    ``bindings snapshot``) cannot race. Readers do not lock; the
    ``os.replace`` is atomic from their perspective.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(lock_path, "w") as lock_f:
        fcntl.flock(lock_f, fcntl.LOCK_EX)
        try:
            tmp.write_bytes(serialized)
            os.replace(tmp, path)
        finally:
            fcntl.flock(lock_f, fcntl.LOCK_UN)


def load_bindings(path: Path) -> dict[str, dict]:
    """Read the bindings JSON file from disk. Returns ``{}`` if missing.

    Readers do not need to take ``flock`` because writers use
    ``os.replace`` for atomic visibility.
    """
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _mutable_to_dict(b: _MutableBinding) -> dict:
    return {
        "pane_id": b.pane_id,
        "window_id": b.window_id,
        "current_session_id": b.current_session_id,
        "session_id_history": list(b.session_id_history),
        "first_seen_at": b.first_seen_at,
        "last_event_at": b.last_event_at,
        "ended_at": b.ended_at,
    }


def _snapshot_structural(
    bindings: dict[str, _MutableBinding],
) -> tuple[tuple, ...]:
    """Hashable fingerprint of the ``structural`` parts of the binding state.

    Excludes ``last_event_at``, which mutates on every event but does
    not warrant a disk write.
    """
    return tuple(
        (
            name,
            b.pane_id,
            b.window_id,
            b.current_session_id,
            tuple(b.session_id_history),
            b.first_seen_at,
            b.ended_at,
        )
        for name, b in sorted(bindings.items())
    )


def snapshot(
    events_path: Path | None = None,
    bindings_path: Path | None = None,
    lock_path: Path | None = None,
) -> int:
    """One-shot full rebuild of the bindings file from ``events.jsonl``.

    Walks the entire event log, folds via :func:`_step`, atomic-writes
    the resulting dict. Returns the number of tmux sessions written.
    """
    from .config import ccmux_core_dir

    events_path = events_path if events_path is not None else _default_events_path()
    bindings_path = (
        bindings_path
        if bindings_path is not None
        else ccmux_core_dir() / "bindings.json"
    )
    lock_path = (
        lock_path
        if lock_path is not None
        else bindings_path.with_suffix(bindings_path.suffix + ".lock")
    )

    bindings: dict[str, _MutableBinding] = {}
    if events_path.exists():
        for raw_line in events_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            _step(bindings, ev)

    payload = {name: _mutable_to_dict(b) for name, b in bindings.items()}
    _atomic_write(bindings_path, lock_path, payload)
    return len(payload)


class BindingsTracker:
    """Live updater for ~/.ccmux-core/bindings.json.

    Runs as an async context manager inside a long-running consumer.
    On entry, loads any existing bindings.json into memory, seeks
    ``events.jsonl`` to EOF, starts a background task that tails new
    events and folds them via :func:`_step`. On every structural
    change (new entry, pane/window/current_session_id change, history
    append), atomically rewrites bindings.json. On exit, force-flushes.

    Bind a consumer like::

        async with BindingsTracker() as _tracker:
            await app.run_polling()
    """

    def __init__(
        self,
        events_path: Path | None = None,
        bindings_path: Path | None = None,
        lock_path: Path | None = None,
        poll_interval: float = 0.1,
    ) -> None:
        from .config import ccmux_core_dir

        self._events_path = (
            events_path if events_path is not None else _default_events_path()
        )
        self._bindings_path = (
            bindings_path
            if bindings_path is not None
            else ccmux_core_dir() / "bindings.json"
        )
        self._lock_path = (
            lock_path
            if lock_path is not None
            else self._bindings_path.with_suffix(self._bindings_path.suffix + ".lock")
        )
        self._poll_interval = poll_interval
        self._bindings: dict[str, _MutableBinding] = {}
        self._task: asyncio.Task | None = None
        self._stop = False
        self._fh = None

    async def __aenter__(self) -> BindingsTracker:
        # Load existing bindings.json into our mutable mirror, if it exists.
        if self._bindings_path.exists():
            loaded = json.loads(self._bindings_path.read_text(encoding="utf-8"))
            for tmux_session, entry in loaded.items():
                self._bindings[tmux_session] = _MutableBinding(
                    tmux_session=tmux_session,
                    pane_id=entry.get("pane_id", ""),
                    window_id=entry.get("window_id", ""),
                    current_session_id=entry.get("current_session_id"),
                    session_id_history=list(entry.get("session_id_history", [])),
                    first_seen_at=entry.get("first_seen_at", ""),
                    last_event_at=entry.get("last_event_at", ""),
                    ended_at=entry.get("ended_at"),
                )
        # Open events.jsonl and seek to EOF synchronously so writes the
        # caller makes immediately after __aenter__ returns are visible.
        if self._events_path.exists():
            self._fh = open(self._events_path, encoding="utf-8")
            self._fh.seek(0, 2)  # EOF
        self._task = asyncio.create_task(self._run())
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self._stop = True
        try:
            if self._task is not None:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
                # Non-CancelledError exceptions from _run propagate out
                # of the await; the finally block below still runs.
        finally:
            if self._fh is not None:
                self._fh.close()
                self._fh = None
            # Final flush — covers any last_event_at-only updates accumulated.
            self._flush()

    async def _run(self) -> None:
        # Wait for events.jsonl to exist if not opened in __aenter__.
        while self._fh is None:
            if self._stop:
                return
            await asyncio.sleep(self._poll_interval)
            if self._events_path.exists():
                self._fh = open(self._events_path, encoding="utf-8")
                self._fh.seek(0, 2)  # EOF

        f = self._fh
        buf = ""
        while not self._stop:
            line = f.readline()
            if not line:
                await asyncio.sleep(self._poll_interval)
                continue
            buf += line
            if not buf.endswith("\n"):
                continue
            try:
                ev = json.loads(buf.rstrip("\n"))
            except json.JSONDecodeError:
                buf = ""
                continue
            buf = ""
            before = _snapshot_structural(self._bindings)
            _step(self._bindings, ev)
            after = _snapshot_structural(self._bindings)
            if before != after:
                self._flush()

    def _flush(self) -> None:
        payload = {name: _mutable_to_dict(b) for name, b in self._bindings.items()}
        _atomic_write(self._bindings_path, self._lock_path, payload)
