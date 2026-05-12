"""Per-tmux-session Backend wrapping claude-tap + ccmux-spinner.

The Backend exposes four typed async iterators:

* :meth:`states` — :data:`State` transitions.
* :meth:`events` — raw claude-tap event dicts for this tmux_session.
* :meth:`transcript_items` — :class:`ClaudeMessage` for any known
  session_id on this tmux_session (primary + subagents). These are
  raw transcript items from claude-tap; the v0.2 L1 layer adds a
  normalized ``messages()`` stream.
* :meth:`spinners` — :data:`Activity` snapshots from ccmux-spinner.

Internally it spawns five concurrent tasks (event / message /
spinner / grace / probe) and coordinates them around a
replay→live phase boundary. See the design spec for full semantics.
"""

from __future__ import annotations

import asyncio
import contextlib
import subprocess
import time
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from ccmux_spinner import Activity, PaneCaptureError, SpinnerMonitor
from claude_tap import ClaudeMessage, EventStream, MessageStream
from claude_tap.config import events_path as _default_events_path

from . import config
from .message import (
    Message,
    PermissionRequest,
    UserPrompt,
)
from .state import Dead, State, Working
from .state_machine import StateMachineStep, apply, apply_safety_net

if TYPE_CHECKING:
    from types import TracebackType


_END = object()


def _iso_to_unix(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts).timestamp()
    except (ValueError, TypeError):
        return None


class Backend:
    """Per-tmux-session async-context-manager. See module docstring."""

    def __init__(
        self,
        tmux_session: str,
        pane_id: str,
        *,
        events_path: Path | None = None,
        spinner_grace: float | None = None,
        process_probe_interval: float | None = None,
        process_probe_startup_grace: float | None = None,
        claude_proc_names: frozenset[str] | None = None,
    ) -> None:
        self._tmux_session = tmux_session
        self._pane_id = pane_id
        self._events_path = events_path
        self._spinner_grace = (
            spinner_grace if spinner_grace is not None else config.spinner_grace()
        )
        self._probe_interval = (
            process_probe_interval
            if process_probe_interval is not None
            else config.process_probe_interval()
        )
        self._probe_startup_grace = (
            process_probe_startup_grace
            if process_probe_startup_grace is not None
            else config.process_probe_startup_grace()
        )
        self._proc_names = (
            claude_proc_names
            if claude_proc_names is not None
            else config.claude_proc_names()
        )

        self._state: State | None = None
        self._primary: str | None = None
        self._known_session_ids: frozenset[str] = frozenset()
        # Reference to the live SpinnerMonitor instance, set when
        # `_spinner_consumer` enters its async-with. The grace timer
        # queries `mon.current` (latest classified Activity, regardless
        # of coalescing) and `mon.last_pane_change_at` (unix epoch of
        # last raw pane-text change). Combined, they distinguish:
        #   * spinner alive and ticking      → mon.current is Spinner
        #   * spinner alive with constant text → mon.current is Spinner
        #   * pane streaming (no spinner)    → mon.current not Spinner,
        #                                       but pane_change_at recent
        #   * truly static (Esc-interrupted) → mon.current not Spinner,
        #                                       pane_change_at stale
        # Only the last case fires Idle(interrupted).
        self._spinner_mon: SpinnerMonitor | None = None

        self._states_q: asyncio.Queue = asyncio.Queue()
        self._events_q: asyncio.Queue = asyncio.Queue()
        self._messages_q: asyncio.Queue = asyncio.Queue()
        self._spinners_q: asyncio.Queue = asyncio.Queue()
        # L1 normalized message stream: fan-in of `events` (UserPrompt,
        # PermissionRequest) + `transcript_items` (AssistantText,
        # ToolCall, ToolResult). See module docstring + L2 design spec.
        self._l1_messages_q: asyncio.Queue = asyncio.Queue()

        self._subscribe_unix: float = 0.0
        self._live_phase_event = asyncio.Event()
        self._stopped = asyncio.Event()

        self._event_task: asyncio.Task | None = None
        self._message_task: asyncio.Task | None = None
        self._spinner_task: asyncio.Task | None = None
        self._grace_task: asyncio.Task | None = None
        self._probe_task: asyncio.Task | None = None
        self._on_live_task: asyncio.Task | None = None
        self._fallback_task: asyncio.Task | None = None

    async def __aenter__(self) -> Backend:
        self._subscribe_unix = time.time()
        self._event_task = asyncio.create_task(self._event_consumer())
        self._on_live_task = asyncio.create_task(self._on_live_phase_entered())
        self._fallback_task = asyncio.create_task(self._live_fallback_timer())
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._stopped.set()
        for t in (
            self._event_task,
            self._message_task,
            self._spinner_task,
            self._grace_task,
            self._probe_task,
            self._on_live_task,
            self._fallback_task,
        ):
            if t is not None and not t.done():
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t
        for q in (
            self._states_q,
            self._events_q,
            self._messages_q,
            self._spinners_q,
            self._l1_messages_q,
        ):
            q.put_nowait(_END)

    async def states(self) -> AsyncIterator[State]:
        while True:
            item = await self._states_q.get()
            if item is _END:
                return
            yield item
            if isinstance(item, Dead):
                return

    async def events(self) -> AsyncIterator[dict]:
        while True:
            item = await self._events_q.get()
            if item is _END:
                return
            yield item

    async def transcript_items(self) -> AsyncIterator[ClaudeMessage]:
        """Raw transcript items (L0) for known session_ids on this tmux_session.

        Yields :class:`ClaudeMessage` instances exactly as emitted by
        claude-tap's ``MessageStream``, filtered to primary + subagent
        session_ids tracked on this tmux_session. v0.2 adds an L1
        ``messages()`` method that normalizes these into a unified
        message stream.
        """
        while True:
            item = await self._messages_q.get()
            if item is _END:
                return
            yield item

    async def messages(self) -> AsyncIterator[Message]:
        """L1 normalized message stream (dedup'd from events + transcript).

        Each :class:`Message` kind is sourced from exactly one upstream
        channel per the L2 design dedup table:

        * ``UserPrompt`` / ``PermissionRequest`` — from
          :meth:`events` (``user_prompt_submit`` / ``permission_request``).
        * ``AssistantText`` / ``ToolCall`` / ``ToolResult`` — from
          :meth:`transcript_items` (claude-tap ``ClaudeMessage``).

        ``Notification`` events are deliberately excluded — they are
        control-plane signals, not conversational content.
        """
        while True:
            item = await self._l1_messages_q.get()
            if item is _END:
                return
            yield item

    async def spinners(self) -> AsyncIterator[Activity]:
        while True:
            item = await self._spinners_q.get()
            if item is _END:
                return
            yield item

    # ---- internal tasks ------------------------------------------------

    async def _event_consumer(self) -> None:
        try:
            stream = EventStream(
                path=self._events_path or _default_events_path(),
                from_start=True,
            )
            async for ev in stream:
                if self._stopped.is_set():
                    break
                tmux = event_tmux = ev.get("tmux") or {}
                if event_tmux.get("session_name") != self._tmux_session:
                    continue

                event_unix = _iso_to_unix(ev.get("timestamp")) or 0.0
                is_live = event_unix >= self._subscribe_unix

                if is_live and not self._live_phase_event.is_set():
                    await self._enter_live_phase()

                step: StateMachineStep = apply(
                    state=self._state,
                    primary=self._primary,
                    known_session_ids=self._known_session_ids,
                    event=ev,
                )
                self._state = step.new_state
                self._primary = step.new_primary
                self._known_session_ids = step.known_session_ids

                if is_live:
                    self._events_q.put_nowait(ev)
                    if step.emit and step.new_state is not None:
                        self._states_q.put_nowait(step.new_state)
                    # L1 message fan-out: synthesize UserPrompt /
                    # PermissionRequest from the event stream (see
                    # dedup table in the L2 design spec). Other L1
                    # kinds are sourced from transcript items.
                    et = ev.get("event_type", "")
                    payload = ev.get("payload") or {}
                    if et == "user_prompt_submit":
                        self._l1_messages_q.put_nowait(
                            UserPrompt(
                                text=payload.get("prompt", "") or "",
                                timestamp=event_unix,
                            )
                        )
                    elif et == "permission_request":
                        self._l1_messages_q.put_nowait(
                            PermissionRequest(
                                tool_name=payload.get("tool_name", "") or "",
                                tool_input=payload.get("tool_input") or {},
                                timestamp=event_unix,
                            )
                        )
                    if isinstance(step.new_state, Dead):
                        self._stopped.set()
                        return
                # Silence the unused-variable warning:
                _ = tmux
        except asyncio.CancelledError:
            raise
        except Exception:
            # Don't let consumer crashes hang the Backend.
            pass

    async def _enter_live_phase(self) -> None:
        if self._live_phase_event.is_set():
            return
        if self._state is not None:
            self._states_q.put_nowait(self._state)
        self._live_phase_event.set()

    async def _live_fallback_timer(self) -> None:
        try:
            await asyncio.sleep(1.0)
            if not self._live_phase_event.is_set():
                await self._enter_live_phase()
        except asyncio.CancelledError:
            return

    async def _on_live_phase_entered(self) -> None:
        try:
            await self._live_phase_event.wait()
        except asyncio.CancelledError:
            return
        self._message_task = asyncio.create_task(self._message_consumer())
        self._spinner_task = asyncio.create_task(self._spinner_consumer())
        self._grace_task = asyncio.create_task(self._grace_timer())
        self._probe_task = asyncio.create_task(self._process_probe())

    async def _message_consumer(self) -> None:
        try:
            stream = MessageStream(
                events_path_=self._events_path,
                from_start=False,
            )
            async for msg in stream:
                if self._stopped.is_set():
                    break
                if msg.session_id in self._known_session_ids:
                    self._messages_q.put_nowait(msg)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    async def _spinner_consumer(self) -> None:
        try:
            async with SpinnerMonitor(self._pane_id) as mon:
                self._spinner_mon = mon
                async for activity in mon:
                    if self._stopped.is_set():
                        break
                    self._spinners_q.put_nowait(activity)
            # Iterator ended naturally — pane was lost.
            self._trigger_safety("pane_lost")
        except PaneCaptureError:
            self._trigger_safety("pane_lost")
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        finally:
            self._spinner_mon = None

    async def _grace_timer(self) -> None:
        """Fire ``Idle(interrupted)`` when (a) state is Working, (b) we
        have observed at least one Spinner since this timer started
        (so we know CC was alive), (c) the latest Activity is **not**
        Spinner, and (d) the raw pane text has been static for
        ``spinner_grace`` seconds.

        Two independent signals from SpinnerMonitor:
        * ``mon.current``: latest classified Activity, regardless of
          coalescing. Spinner ⇒ CC alive, abort timer.
        * ``mon.last_pane_change_at``: unix epoch of most recent raw
          pane-text change. Stale ⇒ pane is static; recent ⇒ pane is
          changing (streaming response, scrollback, etc.).

        Together they distinguish "spinner gone because pane is
        streaming" (don't fire) from "spinner gone and pane static"
        (fire interrupted).
        """
        from ccmux_spinner.parser import Spinner as _Spinner

        last_spinner_seen_at: float | None = None
        try:
            while not self._stopped.is_set():
                await asyncio.sleep(0.1)
                mon = self._spinner_mon
                if mon is None:
                    continue
                # Note "spinner alive right now" each tick we see one.
                if isinstance(mon.current, _Spinner):
                    last_spinner_seen_at = time.time()
                if not isinstance(self._state, Working):
                    continue
                if last_spinner_seen_at is None:
                    # Haven't confirmed CC was ever alive yet — wait.
                    continue
                if isinstance(mon.current, _Spinner):
                    # Spinner present right now — CC is alive.
                    continue
                # Spinner absent. Has the pane content been static?
                pane_change_age = time.time() - mon.last_pane_change_at
                if pane_change_age >= self._spinner_grace:
                    self._trigger_safety("spinner_grace")
                    last_spinner_seen_at = None  # don't re-fire
        except asyncio.CancelledError:
            raise

    async def _process_probe(self) -> None:
        try:
            await asyncio.sleep(self._probe_startup_grace)
            while not self._stopped.is_set():
                if self._probe_session_alive():
                    await asyncio.sleep(self._probe_interval)
                    continue
                self._trigger_safety("process_gone")
                return
        except asyncio.CancelledError:
            raise

    def _probe_session_alive(self) -> bool:
        """True if any pane in the tmux session has a foreground command in proc_names."""
        try:
            result = subprocess.run(
                [
                    "tmux",
                    "list-panes",
                    "-t",
                    self._tmux_session,
                    "-F",
                    "#{pane_current_command}",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            return False
        if result.returncode != 0:
            return False
        cmds = {line.strip() for line in result.stdout.splitlines() if line.strip()}
        return bool(cmds & self._proc_names)

    def _trigger_safety(self, trigger: str) -> None:
        step = apply_safety_net(state=self._state, trigger=trigger)  # type: ignore[arg-type]
        self._state = step.new_state
        if step.emit and step.new_state is not None:
            self._states_q.put_nowait(step.new_state)
        if isinstance(step.new_state, Dead):
            self._stopped.set()
