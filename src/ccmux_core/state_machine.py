"""Pure transition functions for ccmux-core's state machine.

Two entry points:

* :func:`apply` — handle a claude-tap hook event. Returns a
  :class:`StateMachineStep` describing the new state, new primary
  session id, the (possibly grown) ``known_session_ids`` set, and
  whether the resulting state should be emitted to consumers.

* :func:`apply_safety_net` — handle a safety-net trigger
  (``spinner_grace`` / ``pane_lost`` / ``process_gone``) from the
  Backend's internal tasks. Same return shape.

Both functions are pure: they read ``event`` dicts and return new
records. The Backend wraps them to manage state across events and
to drive emission.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .state import Blocked, Dead, Idle, State, Working

# Tools whose tool_name → Blocked.kind mapping is non-default.
# Used by both pre_tool_use (to enter Blocked instead of Working)
# and permission_request (to pick the right Blocked.kind).
_BLOCKING_TOOLS: dict[str, Literal["ask_user", "exit_plan_mode"]] = {
    "AskUserQuestion": "ask_user",
    "ExitPlanMode": "exit_plan_mode",
}


@dataclass(frozen=True)
class StateMachineStep:
    """Result of one transition.

    Attributes
    ----------
    new_state
        The state after this event. May equal the previous state
        (in which case ``emit`` is False).
    new_primary
        The bound primary session_id after this event. May be None
        between a /clear's session_end and the next session_start.
    known_session_ids
        Updated set of all session_ids ever observed for this tmux
        session. The Backend uses this to filter messages.
    emit
        True if the state stream should yield ``new_state``. False
        for no-op transitions, subagent-only events, and primary-
        tracking actions that don't move state.
    """

    new_state: State | None
    new_primary: str | None
    known_session_ids: frozenset[str]
    emit: bool


SafetyNetTrigger = Literal["spinner_grace", "pane_lost", "process_gone"]


def apply(
    state: State | None,
    primary: str | None,
    known_session_ids: frozenset[str],
    event: dict,
) -> StateMachineStep:
    """Apply one hook event to (state, primary, known)."""
    et = event.get("event_type", "")
    sid = (event.get("claude") or {}).get("session_id", "")
    payload = event.get("payload") or {}

    # Always add to known_session_ids if we have an id.
    new_known = (
        known_session_ids | {sid}
        if sid and sid not in known_session_ids
        else known_session_ids
    )

    # ----- Primary tracking ------------------------------------------------
    if et == "session_start":
        if primary is None:
            new = Idle(reason="start")
            return StateMachineStep(
                new_state=new,
                new_primary=sid,
                known_session_ids=new_known,
                emit=(new != state),
            )
        if sid == primary:
            # Resume after prompt_input_exit: no rebind, no state change.
            return StateMachineStep(
                new_state=state,
                new_primary=primary,
                known_session_ids=new_known,
                emit=False,
            )
        # Different sid + primary set → subagent.
        return StateMachineStep(
            new_state=state,
            new_primary=primary,
            known_session_ids=new_known,
            emit=False,
        )

    if et == "session_end":
        if sid != primary:
            # Subagent end → ignore.
            return StateMachineStep(
                new_state=state,
                new_primary=primary,
                known_session_ids=new_known,
                emit=False,
            )
        reason = payload.get("reason", "")
        if reason == "clear":
            return StateMachineStep(
                new_state=state,
                new_primary=None,
                known_session_ids=new_known,
                emit=False,
            )
        if reason == "prompt_input_exit":
            return StateMachineStep(
                new_state=state,
                new_primary=primary,
                known_session_ids=new_known,
                emit=False,
            )
        return StateMachineStep(
            new_state=Dead(reason="session_end", detail=reason),
            new_primary=primary,
            known_session_ids=new_known,
            emit=True,
        )

    # All other event types only matter when sid == primary.
    if sid != primary:
        return StateMachineStep(
            new_state=state,
            new_primary=primary,
            known_session_ids=new_known,
            emit=False,
        )

    new_state: State | None = state

    if et == "user_prompt_submit":
        new_state = Working(tool_name=None)
    elif et == "pre_tool_use":
        tool = payload.get("tool_name", "") or ""
        if tool in _BLOCKING_TOOLS:
            new_state = Blocked(
                kind=_BLOCKING_TOOLS[tool],
                tool_name=tool,
                tool_input=payload.get("tool_input"),
            )
        else:
            new_state = Working(tool_name=tool or None)
    elif et == "permission_request":
        tool = payload.get("tool_name", "") or ""
        kind: Literal["permission", "ask_user", "exit_plan_mode"] = _BLOCKING_TOOLS.get(
            tool, "permission"
        )
        new_state = Blocked(
            kind=kind,
            tool_name=tool,
            tool_input=payload.get("tool_input"),
            request_id=payload.get("request_id") or None,
        )
    elif et == "post_tool_use":
        new_state = Working(tool_name=None)
    elif et == "stop":
        new_state = Idle(reason="stop")
    # else: notification or unknown — leave state unchanged.

    return StateMachineStep(
        new_state=new_state,
        new_primary=primary,
        known_session_ids=new_known,
        emit=(new_state != state) and (new_state is not None),
    )


def apply_safety_net(
    state: State | None,
    trigger: SafetyNetTrigger,
) -> StateMachineStep:
    """Apply a safety-net trigger to the current state.

    Safety nets only matter when state is not already Dead.
    """
    if isinstance(state, Dead):
        return StateMachineStep(
            new_state=state,
            new_primary=None,
            known_session_ids=frozenset(),
            emit=False,
        )

    if trigger == "spinner_grace":
        if isinstance(state, Working):
            return StateMachineStep(
                new_state=Idle(reason="interrupted"),
                new_primary=None,
                known_session_ids=frozenset(),
                emit=True,
            )
        return StateMachineStep(
            new_state=state,
            new_primary=None,
            known_session_ids=frozenset(),
            emit=False,
        )

    if trigger == "pane_lost":
        return StateMachineStep(
            new_state=Dead(reason="pane_lost"),
            new_primary=None,
            known_session_ids=frozenset(),
            emit=True,
        )

    if trigger == "process_gone":
        return StateMachineStep(
            new_state=Dead(reason="process_gone"),
            new_primary=None,
            known_session_ids=frozenset(),
            emit=True,
        )

    return StateMachineStep(
        new_state=state,
        new_primary=None,
        known_session_ids=frozenset(),
        emit=False,
    )
