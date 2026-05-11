"""Table-driven tests for state_machine.apply() — every transition row."""

from __future__ import annotations

from ccmux_core.state import Blocked, Dead, Idle, Working
from ccmux_core.state_machine import apply, apply_safety_net


def _ev(
    event_type: str,
    *,
    session_id: str = "S1",
    payload: dict | None = None,
) -> dict:
    """Build a minimal hook event dict suitable for apply()."""
    return {
        "event_type": event_type,
        "timestamp": "2026-05-10T00:00:00+00:00",
        "claude": {"session_id": session_id},
        "tmux": {"session_name": "ccmux", "pane_id": "%1", "window_id": "@0"},
        "payload": payload or {},
    }


# ---------------------------------------------------------------------------
# Primary tracking
# ---------------------------------------------------------------------------


def test_first_session_start_binds_primary_and_emits_idle_start():
    step = apply(
        state=None,
        primary=None,
        known_session_ids=frozenset(),
        event=_ev("session_start"),
    )
    assert step.new_primary == "S1"
    assert step.new_state == Idle(reason="start")
    assert step.emit is True
    assert "S1" in step.known_session_ids


def test_session_start_with_matching_primary_skips_state_machine():
    step = apply(
        state=Idle(reason="stop"),
        primary="S1",
        known_session_ids=frozenset({"S1"}),
        event=_ev("session_start", session_id="S1"),
    )
    assert step.new_primary == "S1"
    assert step.new_state == Idle(reason="stop")
    assert step.emit is False


def test_session_start_with_different_primary_is_subagent():
    step = apply(
        state=Working(tool_name="Task"),
        primary="S1",
        known_session_ids=frozenset({"S1"}),
        event=_ev("session_start", session_id="SUB"),
    )
    assert step.new_primary == "S1"
    assert step.new_state == Working(tool_name="Task")
    assert step.emit is False
    assert "SUB" in step.known_session_ids


def test_session_end_clear_drops_primary_no_emit_no_dead():
    step = apply(
        state=Idle(reason="stop"),
        primary="S1",
        known_session_ids=frozenset({"S1"}),
        event=_ev("session_end", session_id="S1", payload={"reason": "clear"}),
    )
    assert step.new_primary is None
    assert step.new_state == Idle(reason="stop")
    assert step.emit is False


def test_session_start_after_clear_binds_new_primary():
    step = apply(
        state=Idle(reason="stop"),
        primary=None,
        known_session_ids=frozenset({"S1"}),
        event=_ev("session_start", session_id="S2"),
    )
    assert step.new_primary == "S2"
    assert step.new_state == Idle(reason="start")
    assert step.emit is True


def test_session_end_prompt_input_exit_keeps_primary_no_state_change():
    step = apply(
        state=Idle(reason="stop"),
        primary="S1",
        known_session_ids=frozenset({"S1"}),
        event=_ev(
            "session_end",
            session_id="S1",
            payload={"reason": "prompt_input_exit"},
        ),
    )
    assert step.new_primary == "S1"
    assert step.new_state == Idle(reason="stop")
    assert step.emit is False


def test_session_end_with_fatal_reason_emits_dead():
    step = apply(
        state=Working(tool_name="Bash"),
        primary="S1",
        known_session_ids=frozenset({"S1"}),
        event=_ev("session_end", session_id="S1", payload={"reason": "error"}),
    )
    assert step.new_state == Dead(reason="session_end", detail="error")
    assert step.emit is True


def test_session_end_with_empty_reason_emits_dead_with_empty_detail():
    step = apply(
        state=Working(tool_name="Bash"),
        primary="S1",
        known_session_ids=frozenset({"S1"}),
        event=_ev("session_end", session_id="S1", payload={}),
    )
    assert isinstance(step.new_state, Dead)
    assert step.new_state.reason == "session_end"
    assert step.new_state.detail == ""


def test_session_end_for_subagent_does_not_affect_state():
    step = apply(
        state=Working(tool_name="Task"),
        primary="S1",
        known_session_ids=frozenset({"S1", "SUB"}),
        event=_ev("session_end", session_id="SUB", payload={"reason": "error"}),
    )
    assert step.new_primary == "S1"
    assert step.new_state == Working(tool_name="Task")
    assert step.emit is False


# ---------------------------------------------------------------------------
# Hook-driven transitions on primary
# ---------------------------------------------------------------------------


def test_user_prompt_submit_enters_working_with_none_tool():
    step = apply(
        state=Idle(reason="stop"),
        primary="S1",
        known_session_ids=frozenset({"S1"}),
        event=_ev("user_prompt_submit"),
    )
    assert step.new_state == Working(tool_name=None)
    assert step.emit is True


def test_pre_tool_use_bash_enters_working_with_tool_name():
    step = apply(
        state=Working(tool_name=None),
        primary="S1",
        known_session_ids=frozenset({"S1"}),
        event=_ev("pre_tool_use", payload={"tool_name": "Bash"}),
    )
    assert step.new_state == Working(tool_name="Bash")
    assert step.emit is True


def test_pre_tool_use_same_tool_name_coalesces_no_emit():
    step = apply(
        state=Working(tool_name="Bash"),
        primary="S1",
        known_session_ids=frozenset({"S1"}),
        event=_ev("pre_tool_use", payload={"tool_name": "Bash"}),
    )
    assert step.new_state == Working(tool_name="Bash")
    assert step.emit is False


def test_pre_tool_use_ask_user_question_enters_blocked_ask_user():
    step = apply(
        state=Working(tool_name=None),
        primary="S1",
        known_session_ids=frozenset({"S1"}),
        event=_ev(
            "pre_tool_use",
            payload={
                "tool_name": "AskUserQuestion",
                "tool_input": {"questions": ["A?", "B?"]},
            },
        ),
    )
    assert step.new_state == Blocked(
        kind="ask_user",
        tool_name="AskUserQuestion",
        tool_input={"questions": ["A?", "B?"]},
    )
    assert step.emit is True


def test_pre_tool_use_exit_plan_mode_enters_blocked_exit_plan_mode():
    step = apply(
        state=Working(tool_name=None),
        primary="S1",
        known_session_ids=frozenset({"S1"}),
        event=_ev(
            "pre_tool_use",
            payload={"tool_name": "ExitPlanMode", "tool_input": {"plan": "..."}},
        ),
    )
    assert step.new_state.kind == "exit_plan_mode"


def test_permission_request_enters_blocked_permission():
    step = apply(
        state=Working(tool_name="Bash"),
        primary="S1",
        known_session_ids=frozenset({"S1"}),
        event=_ev(
            "permission_request",
            payload={"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}},
        ),
    )
    assert step.new_state == Blocked(
        kind="permission",
        tool_name="Bash",
        tool_input={"command": "rm -rf /"},
    )
    assert step.emit is True


def test_post_tool_use_returns_to_working_none_tool():
    step = apply(
        state=Blocked(kind="ask_user", tool_name="AskUserQuestion", tool_input=None),
        primary="S1",
        known_session_ids=frozenset({"S1"}),
        event=_ev("post_tool_use", payload={"tool_name": "AskUserQuestion"}),
    )
    assert step.new_state == Working(tool_name=None)
    assert step.emit is True


def test_stop_enters_idle_stop():
    step = apply(
        state=Working(tool_name="Bash"),
        primary="S1",
        known_session_ids=frozenset({"S1"}),
        event=_ev("stop"),
    )
    assert step.new_state == Idle(reason="stop")
    assert step.emit is True


def test_notification_no_transition():
    step = apply(
        state=Working(tool_name="Bash"),
        primary="S1",
        known_session_ids=frozenset({"S1"}),
        event=_ev("notification", payload={"message": "x"}),
    )
    assert step.new_state == Working(tool_name="Bash")
    assert step.emit is False


def test_event_with_non_primary_session_id_skipped():
    step = apply(
        state=Working(tool_name="Task"),
        primary="S1",
        known_session_ids=frozenset({"S1"}),
        event=_ev("pre_tool_use", session_id="SUB", payload={"tool_name": "Read"}),
    )
    assert step.new_state == Working(tool_name="Task")
    assert step.emit is False
    assert "SUB" in step.known_session_ids


# ---------------------------------------------------------------------------
# Safety-net transitions
# ---------------------------------------------------------------------------


def test_safety_net_spinner_grace_only_fires_from_working():
    step = apply_safety_net(state=Working(tool_name="Bash"), trigger="spinner_grace")
    assert step.new_state == Idle(reason="interrupted")
    assert step.emit is True


def test_safety_net_spinner_grace_from_idle_is_noop():
    step = apply_safety_net(state=Idle(reason="stop"), trigger="spinner_grace")
    assert step.new_state == Idle(reason="stop")
    assert step.emit is False


def test_safety_net_pane_lost_from_any_non_dead_emits_dead():
    for s in (
        Idle(reason="stop"),
        Working(tool_name="Bash"),
        Blocked(kind="permission", tool_name="Bash", tool_input=None),
    ):
        step = apply_safety_net(state=s, trigger="pane_lost")
        assert step.new_state == Dead(reason="pane_lost")
        assert step.emit is True


def test_safety_net_process_gone_emits_dead():
    step = apply_safety_net(state=Working(tool_name="Bash"), trigger="process_gone")
    assert step.new_state == Dead(reason="process_gone")
    assert step.emit is True


def test_safety_net_on_dead_is_noop():
    step = apply_safety_net(state=Dead(reason="session_end"), trigger="pane_lost")
    assert step.new_state == Dead(reason="session_end")
    assert step.emit is False
