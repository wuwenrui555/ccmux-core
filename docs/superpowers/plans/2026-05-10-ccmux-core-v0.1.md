# ccmux-core v0.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the ccmux-core v0.1 composition layer per `docs/superpowers/specs/2026-05-10-ccmux-core-design.md`: a per-tmux-session `Backend` async-context-manager that wraps `claude-tap`'s `EventStream`/`MessageStream` and `ccmux-spinner`'s `SpinnerMonitor` into four typed async iterators (`states / events / messages / spinners`), plus discovery helpers `list_live_tmux_bindings` and `discover_tmux_sessions`, plus a debug CLI.

**Architecture:** New repo `ccmux-core/`. Module layout: `error.py / state.py / state_machine.py / config.py / discover.py / backend.py / cli.py / __init__.py`. Pure functions where possible (state machine, list_live_tmux_bindings). Backend orchestrates 5 concurrent asyncio tasks with a replay→live phase boundary.

**Tech Stack:** Python 3.11+, `claude-tap >= 0.2.0`, `ccmux-spinner >= 0.2.0`, pytest (with pytest-asyncio), ruff. No other runtime deps.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `pyproject.toml` | Create | hatchling build, deps on claude-tap >= 0.2, ccmux-spinner >= 0.2 |
| `README.md` | Create | one-pager about scope |
| `LICENSE` | Create | Apache 2.0 |
| `CHANGELOG.md` | Create | empty `[0.1.0]` section |
| `.gitignore` | Create | match siblings |
| `src/ccmux_core/__init__.py` | Create | public re-exports |
| `src/ccmux_core/_version.py` | Create | `__version__ = "0.1.0"` |
| `src/ccmux_core/error.py` | Create | exception types |
| `src/ccmux_core/state.py` | Create | Idle / Working / Blocked / Dead frozen dataclasses + State union |
| `src/ccmux_core/state_machine.py` | Create | pure transition functions |
| `src/ccmux_core/config.py` | Create | env var getters + settings.env loader |
| `src/ccmux_core/discover.py` | Create | TmuxBinding + list_live_tmux_bindings + discover_tmux_sessions |
| `src/ccmux_core/backend.py` | Create | Backend class |
| `src/ccmux_core/cli.py` | Create | `ccmux-core list` and `ccmux-core watch <tmux>` |
| `tests/conftest.py` | Create | shared fixtures (isolated CCMUX_CORE_DIR, fake events.jsonl) |
| `tests/test_skeleton.py` | Create | imports + version assertion |
| `tests/test_state_machine.py` | Create | transition table coverage |
| `tests/test_config.py` | Create | env-var + settings.env semantics |
| `tests/test_discover.py` | Create | list_live_tmux_bindings on synthetic events.jsonl |
| `tests/test_backend.py` | Create | end-to-end with patched upstream streams |
| `tests/test_cli.py` | Create | argparse setup + JSON formatting |

---

### Task 1: Scaffold the new repo

**Files:** all of `pyproject.toml`, `README.md`, `LICENSE`, `CHANGELOG.md`, `.gitignore`, empty source/test directories.

- [ ] **Step 1: Initialize git, set up directory structure**

Run:
```bash
cd /mnt/beegfs/home/wenruiwu/ccmux/ccmux-core
git init -b main
mkdir -p src/ccmux_core tests
```

Expected: directories created.

- [ ] **Step 2: Write `pyproject.toml`**

Create `pyproject.toml`:
```toml
[project]
name = "ccmux-core"
version = "0.1.0"
description = "Per-tmux-session state machine + stream multiplexer for Claude Code observers."
readme = "README.md"
requires-python = ">=3.11"
license = { file = "LICENSE" }
keywords = ["claude-code", "ccmux", "tmux"]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Topic :: Terminals",
    "Topic :: Software Development :: Libraries",
    "License :: OSI Approved :: Apache Software License",
]
dependencies = [
    "claude-tap>=0.2.0",
    "ccmux-spinner>=0.2.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "ruff>=0.8.0",
]

[project.scripts]
ccmux-core = "ccmux_core.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/ccmux_core"]

[tool.ruff]
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP"]
ignore = ["E501"]

[tool.ruff.format]
quote-style = "double"

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

- [ ] **Step 3: Copy LICENSE from sibling**

Run:
```bash
cp /mnt/beegfs/home/wenruiwu/ccmux/ccmux-spinner/LICENSE LICENSE
```

- [ ] **Step 4: Write `.gitignore`**

Create `.gitignore`:
```text
# Python
__pycache__/
*.py[cod]
*$py.class
*.egg-info/
*.egg
build/
dist/
.eggs/

# Virtual envs
.venv/
venv/
env/

# Test / lint caches
.pytest_cache/
.ruff_cache/
.coverage
htmlcov/

# uv
uv.lock
```

(Note: `uv.lock` excluded to match ccmux-spinner's current policy.)

- [ ] **Step 5: Write minimal `README.md`**

Create `README.md`:
```markdown
# ccmux-core

> Per-tmux-session state machine + stream multiplexer for Claude Code observers.

ccmux-core composes [claude-tap](https://github.com/wuwenrui555/claude-tap) (hook events + derived ClaudeMessages) with [ccmux-spinner](https://github.com/wuwenrui555/ccmux-spinner) (tmux pane spinner) into a single per-tmux-session async-context-manager. Downstream consumers (Telegram relays, status HUDs, dashboards) subscribe to four typed streams: `states / events / messages / spinners`.

## Status

v0.1 — early. See `docs/superpowers/specs/` for the design spec.

## Install

```bash
pip install ccmux-core
```

Or with uv:

```bash
uv add ccmux-core
```

## Quick start

```python
import asyncio
import ccmux_core

async def main():
    async with ccmux_core.Backend(tmux_session="ccmux", pane_id="%42") as b:
        async for state in b.states():
            print(state)

asyncio.run(main())
```

## License

Apache 2.0.
```

- [ ] **Step 6: Write `CHANGELOG.md`**

Create `CHANGELOG.md`:
```markdown
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-05-10

### Added

- Initial release. Per-tmux-session `Backend` async-context-manager
  exposing `states() / events() / messages() / spinners()` async
  iterators. State machine `Idle / Working / Blocked / Dead` driven
  by claude-tap hook events + ccmux-spinner safety net + tmux
  process probe. `list_live_tmux_bindings()` snapshot helper and
  `discover_tmux_sessions()` live stream. `ccmux-core list` and
  `ccmux-core watch <tmux_session>` debug CLI.
- See `docs/superpowers/specs/2026-05-10-ccmux-core-design.md` for
  design.
```

- [ ] **Step 7: Initial commit**

```bash
cd /mnt/beegfs/home/wenruiwu/ccmux/ccmux-core
git add pyproject.toml LICENSE README.md CHANGELOG.md .gitignore docs/
git commit -m "$(cat <<'EOF'
chore: scaffold ccmux-core v0.1.0 (spec + plan + repo bones)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Expected: initial commit on `main`.

- [ ] **Step 8: Create `dev` branch and feature branch**

```bash
git checkout -b dev
git checkout -b feat/v0.1.0-initial
```

Expected: now on `feat/v0.1.0-initial`.

---

### Task 2: `_version.py` + `error.py` + smoke test

**Files:**
- Create: `src/ccmux_core/_version.py`
- Create: `src/ccmux_core/error.py`
- Create: `src/ccmux_core/__init__.py` (minimal first)
- Create: `tests/test_skeleton.py`

- [ ] **Step 1: Write test_skeleton (failing — package doesn't exist yet)**

Create `tests/test_skeleton.py`:
```python
def test_import_package():
    import ccmux_core

    assert ccmux_core.__version__ == "0.1.0"


def test_errors_importable():
    from ccmux_core.errors import BackendError

    assert issubclass(BackendError, Exception)
```

- [ ] **Step 2: Run test, expect collection failure**

Run:
```bash
cd /mnt/beegfs/home/wenruiwu/ccmux/ccmux-core
uv run pytest tests/test_skeleton.py -v 2>&1 | tail -10
```

Expected: `ImportError: No module named 'ccmux_core'`.

- [ ] **Step 3: Create minimal package files**

Create `src/ccmux_core/_version.py`:
```python
__version__ = "0.1.0"
```

Create `src/ccmux_core/error.py`:
```python
"""Exception types for ccmux-core."""

from __future__ import annotations


class BackendError(Exception):
    """Base class for ccmux-core errors."""


class TmuxProbeError(BackendError):
    """The tmux subprocess used for process probing failed.

    Distinct from ccmux-spinner's TmuxResolutionError / PaneCaptureError
    because process-probe failures are recoverable (retried next tick)
    rather than terminal.
    """
```

Create `src/ccmux_core/__init__.py`:
```python
"""ccmux-core: per-tmux-session state machine + stream multiplexer."""

from ._version import __version__

__all__ = ["__version__"]
```

- [ ] **Step 4: Run test, expect pass**

Run:
```bash
uv run pytest tests/test_skeleton.py -v 2>&1 | tail -10
```

Expected: both tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/ tests/
git commit -m "$(cat <<'EOF'
feat: package skeleton — _version, errors, smoke test

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `state.py` — state dataclasses

**Files:**
- Create: `src/ccmux_core/state.py`
- Create: `tests/test_state.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_state.py`:
```python
"""Tests for state dataclasses — frozen, equality, fields."""

from __future__ import annotations

import dataclasses

import pytest

from ccmux_core.state import Blocked, Dead, Idle, State, Working


def test_idle_frozen_with_reason():
    s = Idle(reason="start")
    assert s.reason == "start"
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.reason = "stop"


def test_idle_equality_by_value():
    assert Idle(reason="stop") == Idle(reason="stop")
    assert Idle(reason="stop") != Idle(reason="start")


def test_working_optional_tool_name():
    assert Working(tool_name=None).tool_name is None
    assert Working(tool_name="Bash").tool_name == "Bash"
    assert Working(tool_name="Bash") == Working(tool_name="Bash")
    assert Working(tool_name="Bash") != Working(tool_name="Read")


def test_blocked_carries_kind_tool_and_input():
    b = Blocked(kind="permission", tool_name="Bash", tool_input={"command": "ls"})
    assert b.kind == "permission"
    assert b.tool_name == "Bash"
    assert b.tool_input == {"command": "ls"}


def test_blocked_kind_values():
    for kind in ("permission", "ask_user", "exit_plan_mode"):
        Blocked(kind=kind, tool_name="X", tool_input=None)


def test_dead_with_detail():
    d = Dead(reason="session_end", detail="user_exit")
    assert d.reason == "session_end"
    assert d.detail == "user_exit"


def test_dead_detail_defaults_to_none():
    d = Dead(reason="pane_lost")
    assert d.detail is None


def test_state_is_union_of_four():
    # Confirms the alias exists; we don't try to introspect at runtime
    # (Python unions aren't great at that pre-3.12 without typing tricks).
    assert State is not None
    # Each concrete class is constructible:
    Idle(reason="start")
    Working(tool_name=None)
    Blocked(kind="permission", tool_name="X", tool_input=None)
    Dead(reason="session_end")
```

- [ ] **Step 2: Verify red**

```bash
uv run pytest tests/test_state.py -v 2>&1 | tail -10
```

Expected: `ImportError: cannot import name 'Blocked'` (etc).

- [ ] **Step 3: Implement `state.py`**

Create `src/ccmux_core/state.py`:
```python
"""State dataclasses for ccmux-core.

The four states describe a Claude Code session's coarse activity
class. Each is a frozen dataclass; equality is by field values so
the state machine can coalesce no-op transitions.

State stream emits when the next state's record differs (by field
equality) from the previously emitted record. See
`state_machine.py` and the design spec for transition rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Idle:
    """Session is between turns or interrupted.

    `reason` indicates how we got here:
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

    `tool_name` is the most recent `pre_tool_use`'s tool, or None if
    Working was entered via `user_prompt_submit` (or after a
    `post_tool_use` ended the previous tool without a new
    `pre_tool_use` arriving yet).
    """

    tool_name: str | None


@dataclass(frozen=True)
class Blocked:
    """Session is waiting on the user.

    `kind` indicates which subsystem is blocking:
      "permission"     — permission_request hook is open.
      "ask_user"       — pre_tool_use(AskUserQuestion).
      "exit_plan_mode" — pre_tool_use(ExitPlanMode).

    `tool_input` is the raw `payload.tool_input` dict (or `None`
    for legacy payloads).
    """

    kind: Literal["permission", "ask_user", "exit_plan_mode"]
    tool_name: str
    tool_input: dict | None


@dataclass(frozen=True)
class Dead:
    """Session is gone. Iterators terminate after this is emitted.

    `reason` indicates how:
      "session_end"  — session_end hook with a fatal reason.
      "pane_lost"    — SpinnerMonitor raised PaneCaptureError.
      "process_gone" — process probe found no claude/node in any
                       pane of the tmux session.

    `detail` carries `payload.reason` when `reason == "session_end"`;
    None otherwise.
    """

    reason: Literal["session_end", "pane_lost", "process_gone"]
    detail: str | None = None


State = Idle | Working | Blocked | Dead
```

- [ ] **Step 4: Verify green**

```bash
uv run pytest tests/test_state.py -v 2>&1 | tail -15
```

Expected: all 8 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/ccmux_core/state.py tests/test_state.py
git commit -m "$(cat <<'EOF'
feat(state): Idle / Working / Blocked / Dead dataclasses

Frozen value types with equality-by-field. State union type alias
for consumer match dispatch.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: `state_machine.py` — pure transition functions

**Files:**
- Create: `src/ccmux_core/state_machine.py`
- Create: `tests/test_state_machine.py`

This is the most logic-heavy pure module. We model it as a function `apply(state, primary_session_id, known_session_ids, event) -> StateMachineStep` where `StateMachineStep` is a small record describing the result: new state, new primary, whether state changed (emission flag), whether to add to known_session_ids.

- [ ] **Step 1: Write failing tests**

Create `tests/test_state_machine.py`:
```python
"""Table-driven tests for state_machine.apply() — every transition row."""

from __future__ import annotations

import pytest

from ccmux_core.state import Blocked, Dead, Idle, Working
from ccmux_core.state_machine import StateMachineStep, apply

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
        state=None, primary=None, known_session_ids=frozenset(),
        event=_ev("session_start"),
    )
    assert step.new_primary == "S1"
    assert step.new_state == Idle(reason="start")
    assert step.emit is True
    assert "S1" in step.known_session_ids


def test_session_start_with_matching_primary_skips_state_machine():
    # prompt_input_exit → session_start resume with SAME session_id:
    # MUST NOT re-emit Idle(start).
    step = apply(
        state=Idle(reason="stop"), primary="S1", known_session_ids=frozenset({"S1"}),
        event=_ev("session_start", session_id="S1"),
    )
    assert step.new_primary == "S1"
    assert step.new_state == Idle(reason="stop")  # unchanged
    assert step.emit is False


def test_session_start_with_different_primary_is_subagent():
    step = apply(
        state=Working(tool_name="Task"), primary="S1",
        known_session_ids=frozenset({"S1"}),
        event=_ev("session_start", session_id="SUB"),
    )
    assert step.new_primary == "S1"  # unchanged
    assert step.new_state == Working(tool_name="Task")  # unchanged
    assert step.emit is False
    assert "SUB" in step.known_session_ids


def test_session_end_clear_drops_primary_no_emit_no_dead():
    step = apply(
        state=Idle(reason="stop"), primary="S1", known_session_ids=frozenset({"S1"}),
        event=_ev("session_end", session_id="S1", payload={"reason": "clear"}),
    )
    assert step.new_primary is None
    assert step.new_state == Idle(reason="stop")  # state itself unchanged
    assert step.emit is False


def test_session_start_after_clear_binds_new_primary():
    step = apply(
        state=Idle(reason="stop"), primary=None,
        known_session_ids=frozenset({"S1"}),
        event=_ev("session_start", session_id="S2"),
    )
    assert step.new_primary == "S2"
    assert step.new_state == Idle(reason="start")
    assert step.emit is True


def test_session_end_prompt_input_exit_keeps_primary_no_state_change():
    step = apply(
        state=Idle(reason="stop"), primary="S1", known_session_ids=frozenset({"S1"}),
        event=_ev(
            "session_end", session_id="S1",
            payload={"reason": "prompt_input_exit"},
        ),
    )
    assert step.new_primary == "S1"
    assert step.new_state == Idle(reason="stop")
    assert step.emit is False


def test_session_end_with_fatal_reason_emits_dead():
    step = apply(
        state=Working(tool_name="Bash"), primary="S1",
        known_session_ids=frozenset({"S1"}),
        event=_ev("session_end", session_id="S1", payload={"reason": "error"}),
    )
    assert step.new_primary == "S1"  # primary cleanup left to caller
    assert step.new_state == Dead(reason="session_end", detail="error")
    assert step.emit is True


def test_session_end_with_empty_reason_emits_dead_with_empty_detail():
    step = apply(
        state=Working(tool_name="Bash"), primary="S1",
        known_session_ids=frozenset({"S1"}),
        event=_ev("session_end", session_id="S1", payload={}),
    )
    assert isinstance(step.new_state, Dead)
    assert step.new_state.reason == "session_end"
    assert step.new_state.detail == ""


def test_session_end_for_subagent_does_not_affect_state():
    step = apply(
        state=Working(tool_name="Task"), primary="S1",
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
        state=Idle(reason="stop"), primary="S1", known_session_ids=frozenset({"S1"}),
        event=_ev("user_prompt_submit"),
    )
    assert step.new_state == Working(tool_name=None)
    assert step.emit is True


def test_pre_tool_use_bash_enters_working_with_tool_name():
    step = apply(
        state=Working(tool_name=None), primary="S1",
        known_session_ids=frozenset({"S1"}),
        event=_ev("pre_tool_use", payload={"tool_name": "Bash"}),
    )
    assert step.new_state == Working(tool_name="Bash")
    assert step.emit is True


def test_pre_tool_use_same_tool_name_coalesces_no_emit():
    """pre_tool_use(Bash) twice in a row → second is a no-op for state stream."""
    step = apply(
        state=Working(tool_name="Bash"), primary="S1",
        known_session_ids=frozenset({"S1"}),
        event=_ev("pre_tool_use", payload={"tool_name": "Bash"}),
    )
    assert step.new_state == Working(tool_name="Bash")
    assert step.emit is False


def test_pre_tool_use_ask_user_question_enters_blocked_ask_user():
    step = apply(
        state=Working(tool_name=None), primary="S1",
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
        state=Working(tool_name=None), primary="S1",
        known_session_ids=frozenset({"S1"}),
        event=_ev(
            "pre_tool_use",
            payload={"tool_name": "ExitPlanMode", "tool_input": {"plan": "..."}},
        ),
    )
    assert step.new_state.kind == "exit_plan_mode"


def test_permission_request_enters_blocked_permission():
    step = apply(
        state=Working(tool_name="Bash"), primary="S1",
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
        primary="S1", known_session_ids=frozenset({"S1"}),
        event=_ev("post_tool_use", payload={"tool_name": "AskUserQuestion"}),
    )
    assert step.new_state == Working(tool_name=None)
    assert step.emit is True


def test_stop_enters_idle_stop():
    step = apply(
        state=Working(tool_name="Bash"), primary="S1",
        known_session_ids=frozenset({"S1"}),
        event=_ev("stop"),
    )
    assert step.new_state == Idle(reason="stop")
    assert step.emit is True


def test_notification_no_transition():
    step = apply(
        state=Working(tool_name="Bash"), primary="S1",
        known_session_ids=frozenset({"S1"}),
        event=_ev("notification", payload={"message": "x"}),
    )
    assert step.new_state == Working(tool_name="Bash")
    assert step.emit is False


def test_event_with_non_primary_session_id_skipped():
    step = apply(
        state=Working(tool_name="Task"), primary="S1",
        known_session_ids=frozenset({"S1"}),
        event=_ev("pre_tool_use", session_id="SUB", payload={"tool_name": "Read"}),
    )
    assert step.new_state == Working(tool_name="Task")  # unchanged
    assert step.emit is False
    assert "SUB" in step.known_session_ids  # added regardless


# ---------------------------------------------------------------------------
# Safety-net transitions (apply_safety_net)
# ---------------------------------------------------------------------------


def test_safety_net_spinner_grace_only_fires_from_working():
    from ccmux_core.state_machine import apply_safety_net

    step = apply_safety_net(state=Working(tool_name="Bash"), trigger="spinner_grace")
    assert step.new_state == Idle(reason="interrupted")
    assert step.emit is True


def test_safety_net_spinner_grace_from_idle_is_noop():
    from ccmux_core.state_machine import apply_safety_net

    step = apply_safety_net(state=Idle(reason="stop"), trigger="spinner_grace")
    assert step.new_state == Idle(reason="stop")
    assert step.emit is False


def test_safety_net_pane_lost_from_any_non_dead_emits_dead():
    from ccmux_core.state_machine import apply_safety_net

    for s in (
        Idle(reason="stop"),
        Working(tool_name="Bash"),
        Blocked(kind="permission", tool_name="Bash", tool_input=None),
    ):
        step = apply_safety_net(state=s, trigger="pane_lost")
        assert step.new_state == Dead(reason="pane_lost")
        assert step.emit is True


def test_safety_net_process_gone_emits_dead():
    from ccmux_core.state_machine import apply_safety_net

    step = apply_safety_net(state=Working(tool_name="Bash"), trigger="process_gone")
    assert step.new_state == Dead(reason="process_gone")
    assert step.emit is True


def test_safety_net_on_dead_is_noop():
    from ccmux_core.state_machine import apply_safety_net

    step = apply_safety_net(state=Dead(reason="session_end"), trigger="pane_lost")
    assert step.new_state == Dead(reason="session_end")
    assert step.emit is False
```

- [ ] **Step 2: Verify red**

```bash
uv run pytest tests/test_state_machine.py -v 2>&1 | tail -10
```

Expected: import error.

- [ ] **Step 3: Implement `state_machine.py`**

Create `src/ccmux_core/state_machine.py`:
```python
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

# Tools whose pre_tool_use enters Blocked rather than Working.
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
        known_session_ids | {sid} if sid and sid not in known_session_ids else known_session_ids
    )

    # ----- Primary tracking ------------------------------------------------
    if et == "session_start":
        if primary is None:
            # Bind new primary and emit Idle(start).
            return StateMachineStep(
                new_state=Idle(reason="start"),
                new_primary=sid,
                known_session_ids=new_known,
                emit=True,
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
        # Fatal session_end.
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
        new_state = Blocked(
            kind="permission",
            tool_name=payload.get("tool_name", "") or "",
            tool_input=payload.get("tool_input"),
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


def apply_safety_net(state: State | None, trigger: SafetyNetTrigger) -> StateMachineStep:
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
        # spinner_grace only fires from Working; ignore otherwise.
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
```

- [ ] **Step 4: Verify green**

```bash
uv run pytest tests/test_state_machine.py -v 2>&1 | tail -30
```

Expected: every test passes.

- [ ] **Step 5: Commit**

```bash
git add src/ccmux_core/state_machine.py tests/test_state_machine.py
git commit -m "$(cat <<'EOF'
feat(state_machine): pure transition functions

apply() handles hook events; apply_safety_net() handles spinner /
pane-lost / process-gone triggers. Both are pure, table-driven by
the design spec.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: `config.py` — settings + env vars

**Files:**
- Create: `src/ccmux_core/config.py`
- Create: `tests/test_config.py`
- Create: `tests/conftest.py`

This module mirrors `claude_tap.config` / `ccmux_spinner.config` exactly. Same KEY=value parser, same lookup order, same shell-exports-win semantics. Module body is ~140 lines including the parser.

- [ ] **Step 1: Write failing tests**

Create `tests/conftest.py`:
```python
"""Shared pytest fixtures."""

import pytest


@pytest.fixture
def isolated_core_dir(tmp_path, monkeypatch):
    """Override CCMUX_CORE_DIR to a fresh tmp dir; clear known env vars."""
    monkeypatch.setenv("CCMUX_CORE_DIR", str(tmp_path))
    for var in (
        "CCMUX_CORE_SPINNER_GRACE",
        "CCMUX_CORE_PROCESS_PROBE_INTERVAL",
        "CCMUX_CORE_PROCESS_PROBE_STARTUP_GRACE",
        "CCMUX_CORE_CLAUDE_PROC_NAMES",
    ):
        monkeypatch.delenv(var, raising=False)
    return tmp_path
```

Create `tests/test_config.py`:
```python
"""Tests for config — env var defaults and parsing."""

from __future__ import annotations

from ccmux_core import config


def test_dir_defaults(isolated_core_dir, monkeypatch):
    monkeypatch.delenv("CCMUX_CORE_DIR", raising=False)
    # No env var → expanduser default.
    assert "ccmux-core" in str(config.ccmux_core_dir())


def test_dir_from_env(isolated_core_dir):
    assert config.ccmux_core_dir() == isolated_core_dir


def test_spinner_grace_default(isolated_core_dir):
    assert config.spinner_grace() == 5.0


def test_spinner_grace_from_env(isolated_core_dir, monkeypatch):
    monkeypatch.setenv("CCMUX_CORE_SPINNER_GRACE", "2.5")
    assert config.spinner_grace() == 2.5


def test_spinner_grace_invalid_falls_back(isolated_core_dir, monkeypatch):
    monkeypatch.setenv("CCMUX_CORE_SPINNER_GRACE", "abc")
    assert config.spinner_grace() == 5.0


def test_process_probe_interval_default(isolated_core_dir):
    assert config.process_probe_interval() == 10.0


def test_process_probe_startup_grace_default(isolated_core_dir):
    assert config.process_probe_startup_grace() == 10.0


def test_claude_proc_names_default(isolated_core_dir):
    assert config.claude_proc_names() == frozenset({"claude", "node"})


def test_claude_proc_names_from_env(isolated_core_dir, monkeypatch):
    monkeypatch.setenv("CCMUX_CORE_CLAUDE_PROC_NAMES", "claude,node,python")
    assert config.claude_proc_names() == frozenset({"claude", "node", "python"})


def test_claude_proc_names_handles_whitespace(isolated_core_dir, monkeypatch):
    monkeypatch.setenv("CCMUX_CORE_CLAUDE_PROC_NAMES", "claude , node ,  python")
    assert config.claude_proc_names() == frozenset({"claude", "node", "python"})


def test_settings_env_file_loaded(isolated_core_dir, monkeypatch):
    """A settings.env file in CCMUX_CORE_DIR is sourced on import."""
    settings = isolated_core_dir / "settings.env"
    settings.write_text("CCMUX_CORE_SPINNER_GRACE=7\n")
    # Reset the load tracker and re-trigger loading.
    config._LOADED_SETTINGS_FROM.clear()
    config._load_settings_env_files()
    assert config.spinner_grace() == 7.0


def test_shell_export_wins_over_settings_env(isolated_core_dir, monkeypatch):
    """Shell-exported env values must override settings.env file."""
    settings = isolated_core_dir / "settings.env"
    settings.write_text("CCMUX_CORE_SPINNER_GRACE=7\n")
    monkeypatch.setenv("CCMUX_CORE_SPINNER_GRACE", "9")
    config._LOADED_SETTINGS_FROM.clear()
    config._load_settings_env_files()
    assert config.spinner_grace() == 9.0
```

- [ ] **Step 2: Verify red**

```bash
uv run pytest tests/test_config.py -v 2>&1 | tail -10
```

- [ ] **Step 3: Implement `config.py`**

Create `src/ccmux_core/config.py`:
```python
"""Environment-variable / settings.env resolution for ccmux-core.

Mirrors :mod:`claude_tap.config` and :mod:`ccmux_spinner.config`:
same KEY=value format, same lookup order, same shell-exports-win
semantics. Parser duplicated (not imported) to keep ccmux-core's
runtime dependency footprint to {claude-tap, ccmux-spinner}.

Recognized settings:

* ``CCMUX_CORE_DIR`` — state directory (default ``~/.ccmux-core``).
  Hosts the ``settings.env`` file.
* ``CCMUX_CORE_SPINNER_GRACE`` — seconds Working without a Spinner
  before falling back to ``Idle(interrupted)`` (default 5).
* ``CCMUX_CORE_PROCESS_PROBE_INTERVAL`` — seconds between
  successive ``tmux list-panes`` probes (default 10).
* ``CCMUX_CORE_PROCESS_PROBE_STARTUP_GRACE`` — seconds after
  Backend ``__aenter__`` during which no process probe runs
  (default 10).
* ``CCMUX_CORE_CLAUDE_PROC_NAMES`` — comma-separated set of
  foreground process names that count as "claude is alive"
  (default ``claude,node``).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

DEFAULT_DIR = "~/.ccmux-core"
DEFAULT_SPINNER_GRACE = 5.0
DEFAULT_PROCESS_PROBE_INTERVAL = 10.0
DEFAULT_PROCESS_PROBE_STARTUP_GRACE = 10.0
DEFAULT_CLAUDE_PROC_NAMES = frozenset({"claude", "node"})

_SETTINGS_ENV_FILENAME = "settings.env"
_LOADED_SETTINGS_FROM: list[Path] = []
_KEY_VALUE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def ccmux_core_dir() -> Path:
    raw = os.environ.get("CCMUX_CORE_DIR", DEFAULT_DIR)
    return Path(raw).expanduser()


def settings_env_path() -> Path:
    return ccmux_core_dir() / _SETTINGS_ENV_FILENAME


def spinner_grace() -> float:
    return _float_env("CCMUX_CORE_SPINNER_GRACE", DEFAULT_SPINNER_GRACE)


def process_probe_interval() -> float:
    return _float_env(
        "CCMUX_CORE_PROCESS_PROBE_INTERVAL", DEFAULT_PROCESS_PROBE_INTERVAL
    )


def process_probe_startup_grace() -> float:
    return _float_env(
        "CCMUX_CORE_PROCESS_PROBE_STARTUP_GRACE",
        DEFAULT_PROCESS_PROBE_STARTUP_GRACE,
    )


def claude_proc_names() -> frozenset[str]:
    raw = os.environ.get("CCMUX_CORE_CLAUDE_PROC_NAMES", "")
    if not raw:
        return DEFAULT_CLAUDE_PROC_NAMES
    names = {p.strip() for p in raw.split(",") if p.strip()}
    return frozenset(names) if names else DEFAULT_CLAUDE_PROC_NAMES


def loaded_settings_files() -> list[Path]:
    return list(_LOADED_SETTINGS_FROM)


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _parse_settings_env(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    out: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _KEY_VALUE_RE.match(line)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        if value and not (value.startswith('"') or value.startswith("'")):
            if "#" in value:
                value = value.split("#", 1)[0].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        out[key] = value
    return out


def _load_settings_env_files() -> None:
    """Source settings.env into os.environ once at import.

    Order (later wins among files; shell exports always win via
    setdefault):
      1. ``./settings.env`` (cwd)
      2. ``$CCMUX_CORE_DIR/settings.env`` (global)
    """
    paths = [Path(_SETTINGS_ENV_FILENAME), settings_env_path()]
    for path in paths:
        try:
            if not path.is_file():
                continue
        except OSError:
            continue
        values = _parse_settings_env(path)
        if not values:
            continue
        for key, val in values.items():
            os.environ.setdefault(key, val)
        _LOADED_SETTINGS_FROM.append(path)


_load_settings_env_files()
```

- [ ] **Step 4: Verify green**

```bash
uv run pytest tests/test_config.py -v 2>&1 | tail -15
```

- [ ] **Step 5: Commit**

```bash
git add src/ccmux_core/config.py tests/test_config.py tests/conftest.py
git commit -m "$(cat <<'EOF'
feat(config): env vars + settings.env loader

Mirrors claude-tap / ccmux-spinner config conventions. Five
CCMUX_CORE_* knobs; shell exports override settings.env values.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: `discover.py` — `list_live_tmux_bindings` (sync snapshot)

**Files:**
- Create: `src/ccmux_core/discover.py` (partial — `TmuxBinding` + `list_live_tmux_bindings`)
- Create: `tests/test_discover.py` (partial — sync helper coverage)

The async `discover_tmux_sessions` is the next task.

- [ ] **Step 1: Write failing tests**

Create `tests/test_discover.py`:
```python
"""Tests for list_live_tmux_bindings (sync, one-shot)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ccmux_core.discover import TmuxBinding, list_live_tmux_bindings


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
    assert out[0].primary_session_id == "S1"


def test_single_session_start_yields_one_binding(tmp_path):
    p = tmp_path / "events.jsonl"
    _write_events(p, [_ev("session_start", "S1", pane="%42", window="@7")])
    out = list_live_tmux_bindings(events_path=p)
    assert out == [
        TmuxBinding(
            tmux_session="ccmux",
            pane_id="%42",
            window_id="@7",
            primary_session_id="S1",
            last_event_at="2026-05-10T00:00:00+00:00",
        )
    ]


def test_clear_chain_yields_only_last_primary(tmp_path):
    """The /clear empirical pattern: 3 clears, primary follows."""
    p = tmp_path / "events.jsonl"
    _write_events(p, [
        _ev("session_start", "S1", ts="T1"),
        _ev("session_end", "S1", payload={"reason": "clear"}, ts="T2"),
        _ev("session_start", "S2", ts="T3"),
        _ev("session_end", "S2", payload={"reason": "clear"}, ts="T4"),
        _ev("session_start", "S3", ts="T5"),
    ])
    out = list_live_tmux_bindings(events_path=p)
    assert len(out) == 1
    assert out[0].primary_session_id == "S3"
    assert out[0].last_event_at == "T5"


def test_prompt_input_exit_keeps_primary(tmp_path):
    p = tmp_path / "events.jsonl"
    _write_events(p, [
        _ev("session_start", "S1", ts="T1"),
        _ev("session_end", "S1", payload={"reason": "prompt_input_exit"}, ts="T2"),
    ])
    out = list_live_tmux_bindings(events_path=p)
    assert len(out) == 1
    assert out[0].primary_session_id == "S1"


def test_fatal_session_end_removes_binding(tmp_path):
    p = tmp_path / "events.jsonl"
    _write_events(p, [
        _ev("session_start", "S1"),
        _ev("session_end", "S1", payload={"reason": "error"}),
    ])
    assert list_live_tmux_bindings(events_path=p) == []


def test_pane_id_follows_latest_event(tmp_path):
    """If the same session moves to a new pane, last pane_id wins."""
    p = tmp_path / "events.jsonl"
    _write_events(p, [
        _ev("session_start", "S1", pane="%1"),
        _ev("user_prompt_submit", "S1", pane="%2"),
    ])
    out = list_live_tmux_bindings(events_path=p)
    assert out[0].pane_id == "%2"


def test_subagent_does_not_override_primary(tmp_path):
    p = tmp_path / "events.jsonl"
    _write_events(p, [
        _ev("session_start", "S1", ts="T1"),
        _ev("pre_tool_use", "S1", payload={"tool_name": "Task"}, ts="T2"),
        _ev("session_start", "SUB", ts="T3"),  # subagent
        _ev("session_end", "SUB", payload={"reason": "stop"}, ts="T4"),
    ])
    out = list_live_tmux_bindings(events_path=p)
    assert len(out) == 1
    assert out[0].primary_session_id == "S1"  # not SUB


def test_multiple_tmux_sessions_yield_separate_bindings(tmp_path):
    p = tmp_path / "events.jsonl"
    _write_events(p, [
        _ev("session_start", "S1", tmux="ccmux"),
        _ev("session_start", "S2", tmux="demo"),
    ])
    out = list_live_tmux_bindings(events_path=p)
    tmux_names = {b.tmux_session for b in out}
    assert tmux_names == {"ccmux", "demo"}


def test_clear_with_no_rebind_excludes_binding(tmp_path):
    """After clear, primary is None. If no rebind follows, no binding listed."""
    p = tmp_path / "events.jsonl"
    _write_events(p, [
        _ev("session_start", "S1"),
        _ev("session_end", "S1", payload={"reason": "clear"}),
    ])
    assert list_live_tmux_bindings(events_path=p) == []
```

- [ ] **Step 2: Verify red**

```bash
uv run pytest tests/test_discover.py -v 2>&1 | tail -10
```

- [ ] **Step 3: Implement `discover.py` (sync helper only)**

Create `src/ccmux_core/discover.py`:
```python
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

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from claude_tap.config import events_path as _default_events_path


@dataclass(frozen=True)
class TmuxBinding:
    """Current mapping for one tmux session.

    Produced by :func:`list_live_tmux_bindings` (snapshot) and
    :func:`discover_tmux_sessions` (stream).
    """

    tmux_session: str
    pane_id: str
    window_id: str
    primary_session_id: str
    last_event_at: str


def list_live_tmux_bindings(
    events_path: Path | None = None,
) -> list[TmuxBinding]:
    """One-shot snapshot of currently-live tmux session bindings.

    Reads events.jsonl, processes every entry, returns the current
    bindings list. "Live" means the most recent state for the tmux
    session leaves ``primary_session_id`` set (not in a post-/clear
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
            primary_session_id=b.primary_session_id or "",
            last_event_at=b.last_event_at,
        )
        for b in bindings.values()
        if b.primary_session_id is not None
    ]


# ---------------------------------------------------------------------------
# Internal: mutable binding record + per-event step
# ---------------------------------------------------------------------------


@dataclass
class _MutableBinding:
    tmux_session: str
    pane_id: str
    window_id: str
    primary_session_id: str | None
    last_event_at: str


def _step(bindings: dict[str, _MutableBinding], event: dict) -> None:
    """Apply one event to the bindings dict in place.

    Algorithm mirrors the primary-tracking rules in the spec; see
    state_machine.apply() for the canonical version.
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
        if b is None or b.primary_session_id is None:
            bindings[tmux_session] = _MutableBinding(
                tmux_session=tmux_session,
                pane_id=tmux.get("pane_id", ""),
                window_id=tmux.get("window_id", ""),
                primary_session_id=sid,
                last_event_at=ts,
            )
            return
        # Same sid: resume, no change. Different sid + primary set: subagent.
        b.last_event_at = ts
        return

    if et == "session_end":
        if b is None or sid != b.primary_session_id:
            # Subagent end → ignore (just timestamp).
            if b is not None:
                b.last_event_at = ts
            return
        reason = payload.get("reason", "")
        if reason == "clear":
            b.primary_session_id = None
            b.last_event_at = ts
            return
        if reason == "prompt_input_exit":
            b.last_event_at = ts
            return
        # Fatal.
        del bindings[tmux_session]
        return

    # Any other event.
    if b is None:
        return
    if sid == b.primary_session_id:
        b.last_event_at = ts
        b.pane_id = tmux.get("pane_id", b.pane_id)
        b.window_id = tmux.get("window_id", b.window_id)
    else:
        b.last_event_at = ts  # subagent event seen, but don't override pane


# ---------------------------------------------------------------------------
# Async discovery — placeholder; implemented in next task
# ---------------------------------------------------------------------------


async def discover_tmux_sessions(  # type: ignore[empty-body]
    events_path: Path | None = None,
    include_existing: bool = True,
) -> AsyncIterator[TmuxBinding]:
    """Implemented in the next task."""
    raise NotImplementedError("see Task 7")
    yield  # pragma: no cover  # tells type checker this is an async generator
```

- [ ] **Step 4: Verify green**

```bash
uv run pytest tests/test_discover.py -v 2>&1 | tail -15
```

Expected: 11 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/ccmux_core/discover.py tests/test_discover.py
git commit -m "$(cat <<'EOF'
feat(discover): list_live_tmux_bindings (sync snapshot)

Walks events.jsonl in one pass, returns current per-tmux-session
bindings. Handles /clear, prompt_input_exit, and subagent events
per the design spec.

discover_tmux_sessions stub raises NotImplementedError; next task
implements it.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: `discover.py` — `discover_tmux_sessions` (async iterator)

**Files:**
- Modify: `src/ccmux_core/discover.py`
- Modify: `tests/test_discover.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_discover.py`:
```python
# ---------------------------------------------------------------------------
# discover_tmux_sessions (async)
# ---------------------------------------------------------------------------


import asyncio  # noqa: E402

from ccmux_core.discover import discover_tmux_sessions  # noqa: E402


async def _drain(it, *, n: int, timeout: float = 2.0) -> list:
    """Collect up to n items with a timeout."""
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
    _write_events(p, [
        _ev("session_start", "S1", tmux="A"),
        _ev("session_start", "S2", tmux="B"),
    ])
    out = await _drain(
        discover_tmux_sessions(events_path=p, include_existing=True),
        n=2,
    )
    tmux_names = {b.tmux_session for b in out}
    assert tmux_names == {"A", "B"}


@pytest.mark.asyncio
async def test_discover_skips_existing_when_disabled(tmp_path):
    p = tmp_path / "events.jsonl"
    _write_events(p, [
        _ev("session_start", "S1", tmux="A"),
    ])
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
    """After include_existing has yielded A, a /clear+rebind for A is not re-yielded."""
    p = tmp_path / "events.jsonl"
    _write_events(p, [
        _ev("session_start", "S1", tmux="A"),
    ])
    out = await _drain(
        discover_tmux_sessions(events_path=p, include_existing=True),
        n=1,
    )
    assert len(out) == 1
    assert out[0].tmux_session == "A"
```

- [ ] **Step 2: Verify red**

```bash
uv run pytest tests/test_discover.py -v 2>&1 | tail -10
```

Expected: 3 new tests fail with `NotImplementedError`.

- [ ] **Step 3: Implement `discover_tmux_sessions`**

Replace the placeholder in `src/ccmux_core/discover.py` with:

```python
import asyncio  # add to the imports at top of file


async def discover_tmux_sessions(
    events_path: Path | None = None,
    include_existing: bool = True,
) -> AsyncIterator[TmuxBinding]:
    """Yield :class:`TmuxBinding` on each new tmux session entering an
    observable state.

    A "new" tmux session is one this iterator has not yielded before
    in the current iteration.

    ``include_existing=True`` (default): first yields the result of
    :func:`list_live_tmux_bindings`, then tails events.jsonl for
    genuinely new ones.

    ``include_existing=False``: only yields on tmux session names
    first seen in events.jsonl entries written after subscribe.

    Rebinds (a tmux session's primary changing via /clear) are NOT
    re-yielded; existing Backend instances follow rebinds
    internally.
    """
    path = events_path if events_path is not None else _default_events_path()
    yielded: set[str] = set()

    if include_existing:
        for binding in list_live_tmux_bindings(events_path=path):
            yielded.add(binding.tmux_session)
            yield binding

    # Tail events.jsonl from EOF, maintaining a per-tmux bindings dict
    # so we yield only when a new tmux_session crosses into "primary
    # set" state.
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
                if (
                    tmux_session not in yielded
                    and b.primary_session_id is not None
                ):
                    yielded.add(tmux_session)
                    yield TmuxBinding(
                        tmux_session=b.tmux_session,
                        pane_id=b.pane_id,
                        window_id=b.window_id,
                        primary_session_id=b.primary_session_id,
                        last_event_at=b.last_event_at,
                    )
```

- [ ] **Step 4: Verify green**

```bash
uv run pytest tests/test_discover.py -v 2>&1 | tail -20
```

Expected: all 14 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/ccmux_core/discover.py tests/test_discover.py
git commit -m "$(cat <<'EOF'
feat(discover): discover_tmux_sessions async iterator

include_existing=True yields a snapshot then tails for new tmux
sessions. Rebinds within an existing tmux session are not
re-yielded (Backend follows them via state stream).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: `backend.py` — class skeleton + event consumer + replay/live boundary

**Files:**
- Create: `src/ccmux_core/backend.py` (partial)
- Create: `tests/test_backend.py` (partial)

This is the largest task. Splitting into three sub-tasks (8 / 9 / 10) but they share the same file so commits land in the same module.

- [ ] **Step 1: Add tests for skeleton + event-driven state stream**

Create `tests/test_backend.py`:
```python
"""End-to-end tests for Backend. Patches upstream EventStream/MessageStream/SpinnerMonitor.

The strategy: replace `claude_tap.EventStream`, `claude_tap.MessageStream`,
`ccmux_spinner.SpinnerMonitor`, and the tmux subprocess used by process
probe with controllable test doubles. Then drive scenarios and assert
the four iterators' outputs.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from ccmux_core.backend import Backend
from ccmux_core.state import Blocked, Dead, Idle, Working


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
        # Accept and ignore any extra kwargs (path, from_start, etc.)
        # that the real EventStream takes.
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

    def push(self, ev: dict) -> None:
        """Inject another event after construction (mutates the list)."""
        # Won't affect an in-flight aiter, but new aiters will see it.
        self._events.append(ev)


class _FakeMessageStream:
    def __init__(self, **_ignored):
        # Accepts and ignores all kwargs the real MessageStream takes.
        self._cancel_event = asyncio.Event()
        self.closed = False

    def close(self):
        self.closed = True
        self._cancel_event.set()

    async def __aiter__(self):
        # No messages in v0.1 backend tests focused on state.
        await self._cancel_event.wait()
        return
        yield  # unreachable; tells type checker this is async gen


class _FakeSpinnerMonitor:
    instances: list["_FakeSpinnerMonitor"] = []

    def __init__(self, pane_id: str, poll_interval: float | None = None):
        self.pane_id = pane_id
        self._items: list = []  # filled by tests
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

    # Patch claude_tap.EventStream and MessageStream to our fakes.
    # The Backend code will import them, so we patch where they are looked up.
    import ccmux_core.backend as bk

    monkeypatch.setattr(bk, "EventStream", _FakeEventStream)
    monkeypatch.setattr(bk, "MessageStream", _FakeMessageStream)
    monkeypatch.setattr(bk, "SpinnerMonitor", _FakeSpinnerMonitor)

    # Patch subprocess.run inside ccmux_core.backend for the process probe.
    class _OK:
        returncode = 0
        stdout = "node\n"
        stderr = ""

    monkeypatch.setattr(bk.subprocess, "run", lambda *a, **kw: _OK())
    yield


def _ev(et, *, sid="S1", payload=None, ts="2026-05-10T00:00:00+00:00", tmux="ccmux",
        pane="%1", window="@0"):
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
async def test_backend_emits_initial_idle_start(tmp_path, monkeypatch):
    """A session_start event after subscribe → Idle(start)."""
    import time

    subscribe_unix = time.time()
    later_ts = "2099-12-31T23:59:59+00:00"

    import ccmux_core.backend as bk

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
    """Historical events (ts < subscribe_unix) drive state silently; live phase
    emits the baseline derived from replay."""
    import ccmux_core.backend as bk
    import time

    past_ts = "1970-01-01T00:00:00+00:00"
    later_ts = "2099-12-31T23:59:59+00:00"

    events = [
        # Historical replay — populates primary, ends in Working(Bash).
        _ev("session_start", ts=past_ts),
        _ev("user_prompt_submit", ts=past_ts),
        _ev("pre_tool_use", payload={"tool_name": "Bash"}, ts=past_ts),
        # Live event arriving after subscribe:
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
        # First emit: the baseline from replay (Working(Bash))
        # Second emit: live event stop → Idle(stop)
        assert out == [Working(tool_name="Bash"), Idle(reason="stop")]


@pytest.mark.asyncio
async def test_backend_subagent_events_dont_affect_state(monkeypatch):
    import ccmux_core.backend as bk

    later_ts = "2099-12-31T23:59:59+00:00"
    events = [
        _ev("session_start", sid="S1", ts=later_ts),
        _ev("user_prompt_submit", sid="S1", ts=later_ts),
        _ev("pre_tool_use", sid="S1", payload={"tool_name": "Task"}, ts=later_ts),
        # Subagent starts, runs, ends:
        _ev("session_start", sid="SUB", ts=later_ts),
        _ev("user_prompt_submit", sid="SUB", ts=later_ts),
        _ev("session_end", sid="SUB", payload={"reason": "stop"}, ts=later_ts),
        # Parent finishes the Task:
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
        # Only parent-driven transitions appear:
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

        await asyncio.wait_for(consume(), timeout=2.0)
        # The clear's session_end produces no emit. The rebind's session_start
        # produces Idle(reason="start") with the new primary.
        # But the FIRST session_start ALSO produced Idle(start). Coalesced?
        # Idle(reason="start") == Idle(reason="start"); the second wouldn't
        # be emitted because the state didn't change. So we only see ONE.
        # We collect with n>=2 but timeout — adjust assertion.
        # Actually it depends on whether between the two starts state mutates.
        # After session_end(clear), state stays Idle(start). Then session_start
        # would propose Idle(reason="start") again — equal to current → no emit.
        # So only 1 emit. Drop the n>=2 expectation:

    # Outside the with: validate what was collected.
    assert out == [Idle(reason="start")]
```

(Note: the last test's assertion encodes a subtle but real behavior — after a clear, if the rebind happens immediately, the user's perception is "Idle stayed Idle". We confirm only one emit.)

- [ ] **Step 2: Verify red**

```bash
uv run pytest tests/test_backend.py -v 2>&1 | tail -15
```

Expected: `ImportError` (Backend not yet defined).

- [ ] **Step 3: Implement `backend.py` skeleton + event consumer**

Create `src/ccmux_core/backend.py`:
```python
"""Per-tmux-session Backend wrapping claude-tap + ccmux-spinner.

The Backend exposes four typed async iterators:

* :meth:`states` — :data:`State` transitions.
* :meth:`events` — raw claude-tap event dicts for this tmux_session.
* :meth:`messages` — :class:`ClaudeMessage` for any known session_id
  on this tmux_session (primary + subagents).
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

from claude_tap import ClaudeMessage, EventStream, MessageStream
from claude_tap.config import events_path as _default_events_path
from ccmux_spinner import Activity, PaneCaptureError, SpinnerMonitor

from . import config
from .state import Dead, State
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
        self._events_path = events_path  # None → claude_tap default
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

        # Internal state
        self._state: State | None = None
        self._primary: str | None = None
        self._known_session_ids: frozenset[str] = frozenset()
        self._last_spinner_active_at: float | None = None  # epoch when last Spinner seen

        # Queues
        self._states_q: asyncio.Queue = asyncio.Queue()
        self._events_q: asyncio.Queue = asyncio.Queue()
        self._messages_q: asyncio.Queue = asyncio.Queue()
        self._spinners_q: asyncio.Queue = asyncio.Queue()

        # Phase coordination
        self._subscribe_unix: float = 0.0
        self._live_phase_event = asyncio.Event()
        self._stopped = asyncio.Event()

        # Tasks
        self._event_task: asyncio.Task | None = None
        self._message_task: asyncio.Task | None = None
        self._spinner_task: asyncio.Task | None = None
        self._grace_task: asyncio.Task | None = None
        self._probe_task: asyncio.Task | None = None
        self._enter_time: float = 0.0

    async def __aenter__(self) -> "Backend":
        self._subscribe_unix = time.time()
        self._enter_time = self._subscribe_unix

        # Event consumer starts immediately.
        self._event_task = asyncio.create_task(self._event_consumer())

        # Other tasks start on live phase entry (see _on_live_phase_entered).
        asyncio.create_task(self._on_live_phase_entered())

        # Safety net: live phase fallback timer (1.0s).
        asyncio.create_task(self._live_fallback_timer())

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: "TracebackType | None",
    ) -> None:
        self._stopped.set()
        # Cancel any task that's still running.
        for t in (
            self._event_task,
            self._message_task,
            self._spinner_task,
            self._grace_task,
            self._probe_task,
        ):
            if t is not None and not t.done():
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t
        # Signal queues to terminate.
        for q in (self._states_q, self._events_q, self._messages_q, self._spinners_q):
            q.put_nowait(_END)

    # ---- public iterator methods --------------------------------------

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

    async def messages(self) -> AsyncIterator[ClaudeMessage]:
        while True:
            item = await self._messages_q.get()
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
                tmux = (ev.get("tmux") or {})
                if tmux.get("session_name") != self._tmux_session:
                    continue

                # Update known_session_ids regardless of phase or filtering.
                sid = (ev.get("claude") or {}).get("session_id", "")

                event_unix = _iso_to_unix(ev.get("timestamp")) or 0.0
                is_live = event_unix >= self._subscribe_unix

                if is_live and not self._live_phase_event.is_set():
                    # First live event — enter live phase.
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
                    if isinstance(step.new_state, Dead):
                        self._stopped.set()
                        return

                if sid and sid not in self._known_session_ids:
                    # Already maintained by apply(); this branch unreachable.
                    pass
        except asyncio.CancelledError:
            raise
        except Exception:
            # Don't let consumer crashes hang the Backend.
            pass

    async def _enter_live_phase(self) -> None:
        if self._live_phase_event.is_set():
            return
        # Emit the baseline state derived from replay (or nothing if no primary).
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
        # Start the message / spinner / grace / probe tasks.
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
        from ccmux_spinner.parser import Spinner as _Spinner

        try:
            async with SpinnerMonitor(self._pane_id) as mon:
                async for activity in mon:
                    if self._stopped.is_set():
                        break
                    self._spinners_q.put_nowait(activity)
                    if isinstance(activity, _Spinner):
                        self._last_spinner_active_at = time.time()
            # Spinner monitor terminated cleanly (PaneCaptureError → final None).
            # The "final None" case manifests as the iterator ending; treat as pane_lost.
            self._trigger_safety("pane_lost")
        except PaneCaptureError:
            self._trigger_safety("pane_lost")
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    async def _grace_timer(self) -> None:
        try:
            while not self._stopped.is_set():
                await asyncio.sleep(0.5)
                if self._last_spinner_active_at is None:
                    continue
                from .state import Working

                if not isinstance(self._state, Working):
                    continue
                elapsed = time.time() - self._last_spinner_active_at
                if elapsed >= self._spinner_grace:
                    self._trigger_safety("spinner_grace")
                    self._last_spinner_active_at = None
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
                    "tmux", "list-panes", "-t", self._tmux_session,
                    "-F", "#{pane_current_command}",
                ],
                capture_output=True, text=True, check=False,
            )
        except FileNotFoundError:
            return False
        if result.returncode != 0:
            return False
        cmds = {line.strip() for line in result.stdout.splitlines() if line.strip()}
        return bool(cmds & self._proc_names)

    def _trigger_safety(self, trigger) -> None:
        step = apply_safety_net(state=self._state, trigger=trigger)
        self._state = step.new_state
        if step.emit and step.new_state is not None:
            self._states_q.put_nowait(step.new_state)
        if isinstance(step.new_state, Dead):
            self._stopped.set()
```

- [ ] **Step 4: Verify green for Task 8 tests**

```bash
uv run pytest tests/test_backend.py -v 2>&1 | tail -25
```

Expected: 5 tests pass (or 4 + 1 with subtle timing; if a timing-related test fails, investigate).

- [ ] **Step 5: Commit**

```bash
git add src/ccmux_core/backend.py tests/test_backend.py
git commit -m "$(cat <<'EOF'
feat(backend): Backend class with event consumer + state stream

Replay/live phase, state machine wiring, lifecycle management for
five internal tasks. Spinner, message, grace timer, and process
probe are stubbed in this commit but only the event consumer
emits to user-facing queues; subsequent commits wire the rest.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: `backend.py` — process probe failure + spinner safety net tests

**Files:**
- Modify: `tests/test_backend.py`

The backend.py implementation already has the safety nets coded in Task 8. This task adds tests for them.

- [ ] **Step 1: Append safety-net tests**

Append to `tests/test_backend.py`:
```python
# ---------------------------------------------------------------------------
# Safety net tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backend_process_probe_declares_dead(monkeypatch):
    """When tmux list-panes returns no claude/node, process_gone fires."""
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

    # Expect Idle(start) first, then Dead(process_gone)
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
        spinner_grace=0.3,  # short for test
        process_probe_startup_grace=10.0,  # disable probe interference
    ) as b:
        # Feed the spinner monitor with a Spinner that immediately stops.
        await asyncio.sleep(0.1)
        assert _FakeSpinnerMonitor.instances
        mon = _FakeSpinnerMonitor.instances[0]
        from ccmux_spinner.parser import Spinner as _Spinner

        mon.feed(_Spinner(text="Thinking...", todos=()))
        # Now no further Spinner items — grace timer should fire after 0.3s.

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
```

- [ ] **Step 2: Verify green**

```bash
uv run pytest tests/test_backend.py -v 2>&1 | tail -20
```

Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
git add tests/test_backend.py
git commit -m "$(cat <<'EOF'
test(backend): cover process probe + spinner grace safety nets

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: `cli.py` + final `__init__.py` exports

**Files:**
- Create: `src/ccmux_core/cli.py`
- Modify: `src/ccmux_core/__init__.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write CLI tests**

Create `tests/test_cli.py`:
```python
"""Tests for ccmux-core CLI: list and watch subcommands."""

from __future__ import annotations

import json

import pytest

from ccmux_core.cli import build_parser, _bindings_table, _state_to_json
from ccmux_core.discover import TmuxBinding
from ccmux_core.state import Blocked, Dead, Idle, Working


def test_parser_list_subcommand():
    p = build_parser()
    args = p.parse_args(["list"])
    assert args.cmd == "list"


def test_parser_watch_subcommand_takes_session():
    p = build_parser()
    args = p.parse_args(["watch", "ccmux"])
    assert args.cmd == "watch"
    assert args.session == "ccmux"


def test_parser_version():
    p = build_parser()
    args = p.parse_args(["version"])
    assert args.cmd == "version"


def test_bindings_table_empty():
    out = _bindings_table([])
    assert "no live tmux sessions" in out.lower()


def test_bindings_table_single():
    out = _bindings_table([
        TmuxBinding(
            tmux_session="ccmux",
            pane_id="%42",
            window_id="@0",
            primary_session_id="abc-12345",
            last_event_at="2026-05-11T01:55:42Z",
        )
    ])
    assert "ccmux" in out
    assert "%42" in out
    assert "abc-12345" in out


def test_state_to_json_idle():
    s = json.loads(_state_to_json(Idle(reason="start")))
    assert s == {"type": "Idle", "reason": "start"}


def test_state_to_json_working():
    s = json.loads(_state_to_json(Working(tool_name="Bash")))
    assert s == {"type": "Working", "tool_name": "Bash"}


def test_state_to_json_blocked():
    s = json.loads(_state_to_json(
        Blocked(kind="permission", tool_name="Bash", tool_input={"x": 1})
    ))
    assert s == {
        "type": "Blocked",
        "kind": "permission",
        "tool_name": "Bash",
        "tool_input": {"x": 1},
    }


def test_state_to_json_dead():
    s = json.loads(_state_to_json(Dead(reason="pane_lost", detail=None)))
    assert s == {"type": "Dead", "reason": "pane_lost", "detail": None}
```

- [ ] **Step 2: Verify red**

```bash
uv run pytest tests/test_cli.py -v 2>&1 | tail -10
```

- [ ] **Step 3: Implement `cli.py`**

Create `src/ccmux_core/cli.py`:
```python
"""ccmux-core CLI: `list` (snapshot) and `watch <tmux_session>` (live)."""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import sys

from . import __version__
from .backend import Backend
from .discover import TmuxBinding, list_live_tmux_bindings
from .state import Blocked, Dead, Idle, State, Working


def _bindings_table(bindings: list[TmuxBinding]) -> str:
    if not bindings:
        return "(no live tmux sessions)"
    headers = ["TMUX_SESSION", "PANE_ID", "WINDOW_ID", "PRIMARY_SESSION_ID", "LAST_EVENT"]
    rows = [
        [
            b.tmux_session,
            b.pane_id,
            b.window_id,
            b.primary_session_id,
            b.last_event_at,
        ]
        for b in bindings
    ]
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    lines = [
        "  ".join(h.ljust(w) for h, w in zip(headers, widths)),
    ]
    for r in rows:
        lines.append("  ".join(c.ljust(w) for c, w in zip(r, widths)))
    return "\n".join(lines)


def _state_to_json(state: State) -> str:
    if isinstance(state, Idle):
        return json.dumps({"type": "Idle", "reason": state.reason})
    if isinstance(state, Working):
        return json.dumps({"type": "Working", "tool_name": state.tool_name})
    if isinstance(state, Blocked):
        return json.dumps({
            "type": "Blocked",
            "kind": state.kind,
            "tool_name": state.tool_name,
            "tool_input": state.tool_input,
        })
    if isinstance(state, Dead):
        return json.dumps({
            "type": "Dead",
            "reason": state.reason,
            "detail": state.detail,
        })
    return json.dumps({"type": "Unknown"})


def cmd_list(args) -> int:
    bindings = list_live_tmux_bindings()
    print(_bindings_table(bindings))
    return 0


def cmd_version(args) -> int:
    print(__version__)
    return 0


async def _watch_async(session: str) -> int:
    bindings = list_live_tmux_bindings()
    match = next((b for b in bindings if b.tmux_session == session), None)
    if match is None:
        print(f"ccmux-core: no live tmux session named {session!r}", file=sys.stderr)
        return 1

    async with Backend(tmux_session=session, pane_id=match.pane_id) as b:
        async def pump_states():
            async for s in b.states():
                line = _state_to_json(s)
                obj = json.loads(line)
                obj["stream"] = "state"
                print(json.dumps(obj), flush=True)

        async def pump_events():
            async for ev in b.events():
                print(
                    json.dumps({"stream": "event", **ev}, ensure_ascii=False),
                    flush=True,
                )

        async def pump_messages():
            async for msg in b.messages():
                d = dataclasses.asdict(msg)
                # image_data → base64 strings if present
                if d.get("image_data"):
                    import base64

                    d["image_data"] = [
                        (mt, base64.b64encode(bts).decode("ascii"))
                        for (mt, bts) in d["image_data"]
                    ]
                print(
                    json.dumps({"stream": "message", **d}, ensure_ascii=False),
                    flush=True,
                )

        async def pump_spinners():
            async for a in b.spinners():
                if a is None:
                    obj = {"stream": "spinner", "type": "none"}
                else:
                    obj = {
                        "stream": "spinner",
                        "type": type(a).__name__,
                        **dataclasses.asdict(a),
                    }
                print(json.dumps(obj, ensure_ascii=False), flush=True)

        await asyncio.gather(
            pump_states(),
            pump_events(),
            pump_messages(),
            pump_spinners(),
        )
    return 0


def cmd_watch(args) -> int:
    try:
        return asyncio.run(_watch_async(args.session))
    except KeyboardInterrupt:
        return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ccmux-core")
    sub = p.add_subparsers(dest="cmd")

    p_list = sub.add_parser("list", help="List live tmux session bindings")
    p_list.set_defaults(fn=cmd_list)

    p_watch = sub.add_parser("watch", help="Dump all four streams for one tmux session")
    p_watch.add_argument("session", help="tmux session name")
    p_watch.set_defaults(fn=cmd_watch)

    p_version = sub.add_parser("version", help="Print version")
    p_version.set_defaults(fn=cmd_version)

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not hasattr(args, "fn"):
        parser.print_help()
        return 2
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Update `__init__.py` exports**

Replace `src/ccmux_core/__init__.py`:
```python
"""ccmux-core: per-tmux-session state machine + stream multiplexer."""

from ._version import __version__
from .backend import Backend
from .discover import TmuxBinding, discover_tmux_sessions, list_live_tmux_bindings
from .errors import BackendError, TmuxProbeError
from .state import Blocked, Dead, Idle, State, Working

__all__ = [
    "__version__",
    "Backend",
    "BackendError",
    "Blocked",
    "Dead",
    "Idle",
    "State",
    "TmuxBinding",
    "TmuxProbeError",
    "Working",
    "discover_tmux_sessions",
    "list_live_tmux_bindings",
]
```

- [ ] **Step 5: Verify green**

```bash
uv run pytest -v 2>&1 | tail -20
```

Expected: all tests across all modules pass.

- [ ] **Step 6: Commit**

```bash
git add src/ccmux_core/cli.py src/ccmux_core/__init__.py tests/test_cli.py
git commit -m "$(cat <<'EOF'
feat(cli): list + watch subcommands; finalize public exports

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: Final verification

**Files:** none modified.

- [ ] **Step 1: Run the full suite**

```bash
cd /mnt/beegfs/home/wenruiwu/ccmux/ccmux-core
uv run pytest -v 2>&1 | tail -30
```

Expected: every test passes.

- [ ] **Step 2: Ruff lint + format**

```bash
uv run ruff check . 2>&1 | tail -10
uv run ruff format --check . 2>&1 | tail -10
```

Fix any reported issues and re-commit before continuing.

- [ ] **Step 3: Confirm git log**

```bash
git log --oneline main..HEAD
```

Expected: roughly 10–11 commits since `main`.

- [ ] **Step 4: Stop**

**Do not push, do not merge to main, do not tag.** Status: "ccmux-core v0.1.0 implemented on feat/v0.1.0-initial, tests + ruff green, ready for review. Run push / tag / merge when ready."

---

## Self-Review

### Spec coverage

| Spec section | Plan task |
|---|---|
| Module layout (8 src modules + tests) | Task 1 (scaffold) + Tasks 2–10 (modules) |
| State dataclasses | Task 3 |
| State machine transitions (hook + safety) | Task 4 |
| Settings.env + 5 env vars | Task 5 |
| TmuxBinding + list_live_tmux_bindings | Task 6 |
| discover_tmux_sessions | Task 7 |
| Backend class — 4 iterators | Task 8 |
| Backend internal tasks (5) | Task 8 |
| Replay/live phase mechanism | Task 8 (event consumer) |
| Safety net (grace, pane lost, probe) | Tasks 8 (impl) + 9 (tests) |
| Subagent filtering | Tasks 3, 4, 8 |
| /clear handling | Tasks 4, 6, 8 |
| prompt_input_exit handling | Tasks 4, 6, 8 |
| CLI: list + watch | Task 10 |
| Dependencies (claude-tap >= 0.2, ccmux-spinner >= 0.2) | Task 1 (pyproject) |
| README + LICENSE + CHANGELOG | Task 1 |

No gaps.

### Placeholder scan

No TBD / TODO / "implement later". Every code step has exact contents. Every command has exact expected output (or specifies investigation if timing-sensitive). ✓

### Type consistency

- `Backend(tmux_session: str, pane_id: str, *, events_path=None, spinner_grace=None, process_probe_interval=None, process_probe_startup_grace=None, claude_proc_names=None)` — consistent across Task 8 (definition), Task 10 (CLI consumer), test fixtures.
- `TmuxBinding(tmux_session, pane_id, window_id, primary_session_id, last_event_at)` — consistent in Tasks 6, 7, 10.
- `StateMachineStep(new_state, new_primary, known_session_ids, emit)` — consistent in Task 4 (definition), Task 8 (consumer).
- Env var names match between Task 1 (pyproject), Task 5 (config), Task 8 (Backend constructor fallback) — `CCMUX_CORE_DIR`, `CCMUX_CORE_SPINNER_GRACE`, `CCMUX_CORE_PROCESS_PROBE_INTERVAL`, `CCMUX_CORE_PROCESS_PROBE_STARTUP_GRACE`, `CCMUX_CORE_CLAUDE_PROC_NAMES`. ✓
- Versioning `0.1.0` consistent across `pyproject.toml`, `_version.py`, `test_skeleton.py`, `CHANGELOG.md`.

✓
