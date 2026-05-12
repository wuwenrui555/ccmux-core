"""State dataclasses for ccmux-core.

The four states describe a Claude Code session's coarse activity
class. Each is a frozen dataclass; equality is by field values so
the state machine can coalesce no-op transitions.

State stream emits when the next state's record differs (by field
equality) from the previously emitted record. See
``state_machine.py`` and the design spec for transition rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Idle:
    """Session is between turns or interrupted.

    ``reason`` indicates how we got here:
      "start"       — session just started (session_start), or
                      rebound after /clear.
      "stop"        — last turn ended cleanly (stop hook).
      "interrupted" — spinner grace timer fired (Esc-interrupt
                      safety net).
    """

    reason: Literal["start", "stop", "interrupted"]


@dataclass(frozen=True)
class Working:
    """Session is processing a turn.

    ``tool_name`` is the most recent ``pre_tool_use``'s tool, or
    None if Working was entered via ``user_prompt_submit`` (or
    after a ``post_tool_use`` ended the previous tool without a
    new ``pre_tool_use`` arriving yet).
    """

    tool_name: str | None


@dataclass(frozen=True)
class Blocked:
    """Session is waiting on the user.

    ``kind`` indicates which subsystem is blocking:
      "permission"     — permission_request for a side-effecting tool.
      "ask_user"       — permission_request for AskUserQuestion.
      "exit_plan_mode" — permission_request for ExitPlanMode.

    ``tool_input`` is the raw ``payload.tool_input`` dict (or None
    for legacy payloads). For ``ask_user``: contains the
    ``questions`` array. For ``exit_plan_mode``: contains the
    ``plan`` markdown.

    ``request_id`` is set when the block came from a
    ``permission_request`` event whose payload carried a
    ``request_id`` (used to route the socket response in v0.2 L2).
    None for blocks observed via ``pre_tool_use`` only.

    ``expired`` is True after a ``drop_to_tui()`` call or a
    decision-socket timeout. When True, structured ``respond_*``
    methods must raise; only ``send_keys()`` works.
    """

    kind: Literal["permission", "ask_user", "exit_plan_mode"]
    tool_name: str
    tool_input: dict | None
    request_id: str | None = None
    expired: bool = False


@dataclass(frozen=True)
class Dead:
    """Session is gone. Iterators terminate after this is emitted.

    ``reason`` indicates how:
      "session_end"  — session_end hook with a fatal reason.
      "pane_lost"    — SpinnerMonitor raised PaneCaptureError.
      "process_gone" — process probe found no claude/node in any
                       pane of the tmux session.

    ``detail`` carries ``payload.reason`` when
    ``reason == "session_end"``; None otherwise.
    """

    reason: Literal["session_end", "pane_lost", "process_gone"]
    detail: str | None = None


State = Idle | Working | Blocked | Dead
