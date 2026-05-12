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
from typing import TYPE_CHECKING, Literal

from ccmux_spinner import Activity, PaneCaptureError, SpinnerMonitor
from claude_tap import ClaudeMessage, DecisionListener, EventStream, MessageStream
from claude_tap.config import events_path as _default_events_path

from . import config
from .keys import send_keys
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
        decision_sock_path: Path | None = None,
        decision_timeout: float = 120.0,
        spinner_grace: float | None = None,
        process_probe_interval: float | None = None,
        process_probe_startup_grace: float | None = None,
        claude_proc_names: frozenset[str] | None = None,
    ) -> None:
        self._tmux_session = tmux_session
        self._pane_id = pane_id
        self._events_path = events_path
        self._decision_sock_path = decision_sock_path
        self._decision_timeout = decision_timeout
        self._decision_listener: DecisionListener | None = None
        self._decision_task: asyncio.Task | None = None
        self._active_request_id: str | None = None
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

        # Prompts queued while state is Working / None (pre-live).
        # Flushed as one concatenated submission on the next Idle
        # transition. See `send_prompt` / `_flush_pending`.
        self._pending: list[str] = []

    async def __aenter__(self) -> Backend:
        self._subscribe_unix = time.time()
        self._event_task = asyncio.create_task(self._event_consumer())
        self._on_live_task = asyncio.create_task(self._on_live_phase_entered())
        self._fallback_task = asyncio.create_task(self._live_fallback_timer())
        self._decision_listener = DecisionListener(path=self._decision_sock_path)
        await self._decision_listener.__aenter__()
        self._decision_task = asyncio.create_task(self._decision_consumer())
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._stopped.set()
        # Stop the decision consumer and unbind the listener first so we
        # don't leave a socket file behind if anything else fails.
        if self._decision_task is not None and not self._decision_task.done():
            self._decision_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._decision_task
        if self._decision_listener is not None:
            with contextlib.suppress(Exception):
                await self._decision_listener.__aexit__(exc_type, exc, tb)
            self._decision_listener = None
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

    async def send_keys(
        self,
        keys: str | list[str],
        *,
        literal: bool = True,
    ) -> None:
        """Low-level key injection. Available in any state.

        Frontend uses this for TUI navigation after drop_to_tui() and
        for custom key combinations not covered by higher-level methods.

        Internally dispatches through :mod:`ccmux_core.keys` so the
        copy-mode-aware path (tmux send-keys vs TIOCSTI) is handled
        transparently. The blocking subprocess call is offloaded to a
        thread so the event loop stays responsive.
        """
        await asyncio.to_thread(
            send_keys,
            self._pane_id,
            keys,
            literal=literal,
        )

    @property
    def pending_count(self) -> int:
        """Number of prompts queued waiting for next Idle."""
        return len(self._pending)

    @property
    def pending_preview(self) -> str:
        """Concatenated preview of pending prompts (frontend display)."""
        return "\n\n".join(self._pending)

    async def send_prompt(self, text: str) -> None:
        """Send a prompt to claude.

        State-dispatched:
          Idle    → defensive C-u, then send text + Enter
          Working → append to pending queue; flushed on next Idle
          Blocked → raise BlockedError
          Dead    → raise DeadError
          None    → queue (pre-live-phase)
        """
        from .error import BlockedError, DeadError
        from .state import Blocked, Dead, Idle, Working

        state = self._state
        if isinstance(state, Idle):
            await self.send_keys("C-u", literal=False)
            await self.send_keys(text, literal=True)
            await self.send_keys("Enter", literal=False)
        elif isinstance(state, Working):
            self._pending.append(text)
        elif isinstance(state, Blocked):
            raise BlockedError(
                "Cannot send_prompt while Blocked — respond to the active "
                f"{state.kind} dialog first."
            )
        elif isinstance(state, Dead):
            raise DeadError(f"Session is Dead ({state.reason}); cannot send_prompt.")
        else:
            # state is None (pre-live-phase): queue
            self._pending.append(text)

    async def interrupt(self) -> None:
        """Abort the current Working state and clear the pending queue.

        No-op outside Working. Sends Esc (which causes claude code to
        restore the prompt to the TUI input buffer) followed by Ctrl-U
        (clears that buffer so future send_prompt isn't appended onto
        leftover text), then clears self._pending.
        """
        from .state import Working

        if not isinstance(self._state, Working):
            return
        await self.send_keys("Escape", literal=False)
        await self.send_keys("C-u", literal=False)
        self._pending.clear()

    async def respond_permission(
        self,
        *,
        decision: Literal["allow", "deny"],
        mode: Literal["once", "always", "all", "bypass"] | None = None,
        message: str | None = None,
    ) -> None:
        """Respond to a Blocked(kind='permission') dialog.

        Modes (decision='allow'):
          'once'   → behavior:allow (no persistent rule)
          'always' → behavior:allow + updatedPermissions from suggestions
          'all'    → same as always but broader scope (carries suggestions)
          'bypass' → behavior:allow (claude must have been launched with
                     --allow-dangerously-skip-permissions for this to take
                     effect; we just emit allow)

        decision='deny' → behavior:deny + message (with fallback if none given)
        """
        from .error import (
            BlockedExpiredError,
            WrongBlockedKindError,
            WrongStateError,
        )
        from .state import Blocked

        state = self._state
        if not isinstance(state, Blocked):
            raise WrongStateError(
                f"respond_permission requires Blocked state, got {type(state).__name__}"
            )
        if state.kind != "permission":
            raise WrongBlockedKindError(
                f"respond_permission requires kind='permission', got kind={state.kind!r}"
            )
        if state.expired:
            raise BlockedExpiredError(
                "decision.sock path is expired; use send_keys to navigate the TUI."
            )

        inner: dict = {"behavior": decision}
        if decision == "deny":
            inner["message"] = message or "User denied permission via ccmux."
        elif decision == "allow":
            if mode in ("always", "all"):
                suggestions = (state.tool_input or {}).get(
                    "permission_suggestions"
                ) or []
                if suggestions:
                    inner["updatedPermissions"] = suggestions
            # mode in ("once", "bypass", None) → just behavior:allow

        decision_json = {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": inner,
            }
        }
        assert self._decision_listener is not None, "listener not bound"
        assert state.request_id is not None, "no request_id on Blocked state"
        await self._decision_listener.respond(state.request_id, decision_json)

    async def respond_exit_plan(
        self,
        *,
        mode: Literal["manual", "autoAccept", "ultraplan", "deny"],
        feedback: str | None = None,
    ) -> None:
        """Respond to a Blocked(kind='exit_plan_mode') dialog.

        Modes:
          'manual'     → allow + updatedInput (the original plan)
          'autoAccept' → allow + updatedInput + setMode auto for session
          'ultraplan'  → deny + message (claude has no native ultraplan,
                         so this becomes a 'try again with ultraplan' hint)
          'deny'       → deny + message (with optional feedback)

        If `feedback` is non-empty, it overrides mode and becomes a
        deny+message regardless.
        """
        from .error import (
            BlockedExpiredError,
            WrongBlockedKindError,
            WrongStateError,
        )
        from .state import Blocked

        state = self._state
        if not isinstance(state, Blocked):
            raise WrongStateError(
                f"respond_exit_plan requires Blocked state, got {type(state).__name__}"
            )
        if state.kind != "exit_plan_mode":
            raise WrongBlockedKindError(
                f"respond_exit_plan requires kind='exit_plan_mode', got kind={state.kind!r}"
            )
        if state.expired:
            raise BlockedExpiredError(
                "decision.sock path is expired; use send_keys to navigate the TUI."
            )

        feedback_clean = (feedback or "").strip()
        inner: dict

        if feedback_clean:
            inner = {
                "behavior": "deny",
                "message": (
                    "User rejected the plan via ccmux and wants this change: "
                    f"{feedback_clean}"
                ),
            }
        elif mode == "deny":
            inner = {
                "behavior": "deny",
                "message": "User rejected the plan via ccmux.",
            }
        elif mode == "ultraplan":
            inner = {
                "behavior": "deny",
                "message": (
                    "User chose Ultraplan via ccmux. Refine this plan with "
                    "Ultraplan on Claude Code on the web."
                ),
            }
        else:
            # manual or autoAccept → allow
            inner = {
                "behavior": "allow",
                "updatedInput": state.tool_input or {},
            }
            if mode == "autoAccept":
                inner["updatedPermissions"] = [
                    {"type": "setMode", "mode": "auto", "destination": "session"}
                ]

        decision_json = {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": inner,
            }
        }
        assert self._decision_listener is not None, "listener not bound"
        assert state.request_id is not None, "no request_id on Blocked state"
        await self._decision_listener.respond(state.request_id, decision_json)

    async def respond_question(self, selections: list[str]) -> None:
        """Respond to a Blocked(kind='ask_user') AskUserQuestion dialog.

        selections is positional — selections[i] is the user's chosen
        option label for questions[i]. If selections is longer than
        questions, extras get 'Answer N' (1-based) as the key, matching
        cmux behavior.

        Each entry is an option's label string. Multi-select within a
        single question should be joined by the caller (e.g. ", ") before
        passing — this mirrors cmux's claudeAskUserQuestionInput logic.
        """
        from .error import (
            BlockedExpiredError,
            WrongBlockedKindError,
            WrongStateError,
        )
        from .state import Blocked

        state = self._state
        if not isinstance(state, Blocked):
            raise WrongStateError(
                f"respond_question requires Blocked state, got {type(state).__name__}"
            )
        if state.kind != "ask_user":
            raise WrongBlockedKindError(
                f"respond_question requires kind='ask_user', got kind={state.kind!r}"
            )
        if state.expired:
            raise BlockedExpiredError(
                "decision.sock path is expired; use send_keys to navigate the TUI."
            )

        tool_input = dict(state.tool_input or {})
        questions = tool_input.get("questions") or []
        answers: dict[str, str] = {}
        for idx, selection in enumerate(selections):
            if idx < len(questions):
                key = (questions[idx].get("question") or f"Answer {idx + 1}").strip()
                if not key:
                    key = f"Answer {idx + 1}"
            else:
                key = f"Answer {idx + 1}"
            answers[key] = selection
        tool_input["answers"] = answers

        decision_json = {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {
                    "behavior": "allow",
                    "updatedInput": tool_input,
                },
            }
        }
        assert self._decision_listener is not None, "listener not bound"
        assert state.request_id is not None, "no request_id on Blocked state"
        await self._decision_listener.respond(state.request_id, decision_json)

    async def _flush_pending(self, *, new_state: State | None) -> None:
        """Called whenever we emit a new state. If the new state is
        Idle and the queue is non-empty, send the queued prompts as
        one concatenated submission (separated by ``"\\n\\n"``)."""
        from .state import Idle

        if not isinstance(new_state, Idle):
            return
        if not self._pending:
            return
        combined = "\n\n".join(self._pending)
        self._pending.clear()
        await self.send_keys("C-u", literal=False)
        await self.send_keys(combined, literal=True)
        await self.send_keys("Enter", literal=False)

    # ---- internal tasks ------------------------------------------------

    async def _decision_consumer(self) -> None:
        """Pull DecisionRequests from the listener and route by session_id.

        If the request's session_id matches our primary, stash the
        request_id onto self._active_request_id so respond_* methods
        can route to it. If it doesn't match, respond with {} immediately
        to release the hook (this Backend doesn't own that session)."""
        if self._decision_listener is None:
            return
        try:
            async for req in self._decision_listener:
                if self._stopped.is_set():
                    break
                if req.session_id != self._primary:
                    # not ours — release the hook so claude falls through to TUI
                    await self._decision_listener.respond(req.request_id, {})
                    continue
                # ours — stash the request_id; respond_* will consume it
                self._active_request_id = req.request_id
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

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
                        await self._flush_pending(new_state=step.new_state)
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
            await self._flush_pending(new_state=self._state)
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
            await self._trigger_safety("pane_lost")
        except PaneCaptureError:
            await self._trigger_safety("pane_lost")
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
                    await self._trigger_safety("spinner_grace")
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
                await self._trigger_safety("process_gone")
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

    async def _trigger_safety(self, trigger: str) -> None:
        step = apply_safety_net(state=self._state, trigger=trigger)  # type: ignore[arg-type]
        self._state = step.new_state
        if step.emit and step.new_state is not None:
            self._states_q.put_nowait(step.new_state)
            # Await flush directly so callers can rely on completion
            # before teardown. _flush_pending is a no-op when
            # new_state isn't Idle or when queue is empty.
            await self._flush_pending(new_state=step.new_state)
        if isinstance(step.new_state, Dead):
            self._stopped.set()
