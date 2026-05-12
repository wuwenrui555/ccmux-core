# ccmux-core v0.2 L2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `executing-plans-test-first` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend ccmux-core from v0.1 (four-stream observation library) to v0.2 with state-gated operations (send_prompt with concat queue, interrupt, respond_*, drop_to_tui, send_keys) and a normalized L1 message stream.

**Architecture:** Three-layer model. L0 = raw observations (events/transcript_items). L1 = derived signals (state + normalized messages). L2 = state-gated operations dispatched through key-injection layer (tmux send-keys by default, TIOCSTI fallback when pane is in copy mode). decision.sock listener binds per-Backend, routes permission responses by session_id.

**Tech Stack:** Python 3.11+, asyncio, dataclasses, pytest + pytest-asyncio. Subprocess for tmux. `fcntl.ioctl` for TIOCSTI. Upstream deps: `claude-tap>=0.2.1`, `ccmux-spinner>=0.2.1`.

**Reference spec:** [`2026-05-12-ccmux-core-l2-design.md`](../specs/2026-05-12-ccmux-core-l2-design.md)

---

## Task 1: Bump version, rename L0 `messages()` → `transcript_items()`

**Files:**
- Modify: `pyproject.toml` (version bump)
- Modify: `src/ccmux_core/_version.py` (version bump)
- Modify: `src/ccmux_core/backend.py` (rename method)
- Modify: `tests/test_backend.py` (update test references)
- Modify: `src/ccmux_core/__init__.py` (if it re-exports)

- [ ] **Step 1: Write the failing test for renamed method**

Add to `tests/test_backend.py`:

```python
def test_backend_exposes_transcript_items_method():
    """L0 method is named `transcript_items`, not `messages`."""
    from ccmux_core import Backend
    # method exists
    assert callable(getattr(Backend, "transcript_items", None))
    # old name removed to avoid confusion with L1 messages()
    assert not hasattr(Backend, "messages") or \
        Backend.messages is not Backend.transcript_items
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /mnt/beegfs/home/wenruiwu/ccmux/ccmux-core && uv run pytest tests/test_backend.py::test_backend_exposes_transcript_items_method -v`
Expected: FAIL (method named `messages`, not `transcript_items`)

- [ ] **Step 3: Rename `messages` → `transcript_items` in backend.py**

In `src/ccmux_core/backend.py`, find the existing `async def messages(self)` method (around line 169) and rename:

```python
async def transcript_items(self) -> AsyncIterator[ClaudeMessage]:
    while True:
        item = await self._messages_q.get()
        if item is _END:
            return
        yield item
```

Also rename internal references: `self._messages_q` stays as-is (internal name OK), but any docstrings mentioning "messages()" should mention "transcript_items()".

- [ ] **Step 4: Bump versions**

In `pyproject.toml`: change `version = "0.1.0"` to `version = "0.2.0"`.
In `src/ccmux_core/_version.py`: change `__version__ = "0.1.0"` to `__version__ = "0.2.0"`.

- [ ] **Step 5: Update any other callers**

Search for callers: `grep -rn "\.messages()" src/ tests/` and update each to `.transcript_items()`. Check `__init__.py` re-exports don't reference a removed `messages` symbol.

- [ ] **Step 6: Run full test suite, verify nothing else broke**

Run: `uv run pytest -x`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
refactor(api): rename L0 messages() to transcript_items()

Makes room for L1 messages() (normalized stream) in v0.2. Bumps
to 0.2.0 to signal the breaking rename.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Add `request_id` and `expired` fields to `Blocked` state

**Files:**
- Modify: `src/ccmux_core/state.py`
- Modify: `tests/test_state.py`

- [ ] **Step 1: Write failing tests for new Blocked fields**

Add to `tests/test_state.py`:

```python
def test_blocked_has_request_id_default_none():
    b = Blocked(kind="permission", tool_name="Bash", tool_input={})
    assert b.request_id is None

def test_blocked_has_expired_default_false():
    b = Blocked(kind="permission", tool_name="Bash", tool_input={})
    assert b.expired is False

def test_blocked_accepts_request_id_and_expired():
    b = Blocked(
        kind="permission",
        tool_name="Bash",
        tool_input={},
        request_id="r-abc123",
        expired=True,
    )
    assert b.request_id == "r-abc123"
    assert b.expired is True

def test_blocked_equality_includes_request_id_and_expired():
    a = Blocked(kind="permission", tool_name="Bash", tool_input={}, request_id="r-1")
    b = Blocked(kind="permission", tool_name="Bash", tool_input={}, request_id="r-2")
    assert a != b
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_state.py -v -k "request_id or expired"`
Expected: FAIL (unexpected keyword argument)

- [ ] **Step 3: Extend Blocked dataclass**

In `src/ccmux_core/state.py`, replace the `Blocked` definition with:

```python
@dataclass(frozen=True)
class Blocked:
    """Session is waiting on the user.

    ``kind`` indicates which subsystem is blocking:
      "permission"     — permission_request for a side-effecting tool.
      "ask_user"       — permission_request for AskUserQuestion.
      "exit_plan_mode" — permission_request for ExitPlanMode.

    ``tool_input`` is the raw ``payload.tool_input`` dict (or None
    for legacy payloads). For ``ask_user``: contains ``questions``
    array. For ``exit_plan_mode``: contains ``plan`` markdown.

    ``request_id`` is set when the block came from a permission_request
    event with a request_id (used to route the socket response).
    None for blocks observed via pre_tool_use only.

    ``expired`` is True after drop_to_tui() or socket timeout. When
    True, structured respond_* methods raise; only send_keys() works.
    """

    kind: Literal["permission", "ask_user", "exit_plan_mode"]
    tool_name: str
    tool_input: dict | None
    request_id: str | None = None
    expired: bool = False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_state.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ccmux_core/state.py tests/test_state.py
git commit -m "$(cat <<'EOF'
feat(state): add request_id and expired to Blocked

Required for L2 respond_* methods to route decisions through the
correct decision.sock request, and for drop_to_tui / socket
timeout to flag the structured response path as closed.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Carry `request_id` through state machine

**Files:**
- Modify: `src/ccmux_core/state_machine.py`
- Modify: `tests/test_state_machine.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_state_machine.py`:

```python
def test_permission_request_carries_request_id():
    """When permission_request payload has request_id, it propagates to Blocked."""
    from ccmux_core.state_machine import apply
    from ccmux_core.state import Blocked

    event = {
        "event_type": "permission_request",
        "claude": {"session_id": "s1"},
        "payload": {
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "request_id": "r-abc123",
        },
    }
    step = apply(state=None, primary="s1", known_session_ids=frozenset({"s1"}), event=event)
    assert isinstance(step.new_state, Blocked)
    assert step.new_state.request_id == "r-abc123"
```

- [ ] **Step 2: Run test, verify fail**

Run: `uv run pytest tests/test_state_machine.py::test_permission_request_carries_request_id -v`
Expected: FAIL (request_id is None or AttributeError)

- [ ] **Step 3: Update state_machine.py permission_request handler**

Find the `permission_request` branch in `apply()` (around line 161). Change:

```python
elif et == "permission_request":
    new_state = Blocked(
        kind="permission",
        tool_name=payload.get("tool_name", "") or "",
        tool_input=payload.get("tool_input"),
        request_id=payload.get("request_id") or None,
    )
```

Also: for `AskUserQuestion` and `ExitPlanMode` tool names within `permission_request`, route to the correct `kind`:

```python
elif et == "permission_request":
    tool = payload.get("tool_name", "") or ""
    kind: Literal["permission", "ask_user", "exit_plan_mode"]
    if tool == "AskUserQuestion":
        kind = "ask_user"
    elif tool == "ExitPlanMode":
        kind = "exit_plan_mode"
    else:
        kind = "permission"
    new_state = Blocked(
        kind=kind,
        tool_name=tool,
        tool_input=payload.get("tool_input"),
        request_id=payload.get("request_id") or None,
    )
```

- [ ] **Step 4: Write a second failing test for kind discrimination**

```python
def test_permission_request_for_ask_user_routes_to_ask_user_kind():
    from ccmux_core.state_machine import apply
    from ccmux_core.state import Blocked

    event = {
        "event_type": "permission_request",
        "claude": {"session_id": "s1"},
        "payload": {
            "tool_name": "AskUserQuestion",
            "tool_input": {"questions": []},
            "request_id": "r-1",
        },
    }
    step = apply(state=None, primary="s1", known_session_ids=frozenset({"s1"}), event=event)
    assert isinstance(step.new_state, Blocked)
    assert step.new_state.kind == "ask_user"

def test_permission_request_for_exit_plan_mode_routes_to_exit_plan_mode_kind():
    from ccmux_core.state_machine import apply
    from ccmux_core.state import Blocked

    event = {
        "event_type": "permission_request",
        "claude": {"session_id": "s1"},
        "payload": {
            "tool_name": "ExitPlanMode",
            "tool_input": {"plan": "..."},
            "request_id": "r-1",
        },
    }
    step = apply(state=None, primary="s1", known_session_ids=frozenset({"s1"}), event=event)
    assert isinstance(step.new_state, Blocked)
    assert step.new_state.kind == "exit_plan_mode"
```

- [ ] **Step 5: Run tests, verify pass**

Run: `uv run pytest tests/test_state_machine.py -v`
Expected: PASS (all)

- [ ] **Step 6: Verify no regressions in existing state machine tests**

Run: `uv run pytest tests/test_state_machine.py -v`
Expected: all green

- [ ] **Step 7: Commit**

```bash
git add src/ccmux_core/state_machine.py tests/test_state_machine.py
git commit -m "$(cat <<'EOF'
feat(state-machine): route permission_request by tool_name and carry request_id

Permission_request hook may fire with tool_name=AskUserQuestion or
ExitPlanMode, distinct from generic permission. State machine now
discriminates so frontends can render the right UI.

request_id flows through so the decision listener can route
responses back to the correct hook invocation.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Add L2 error types

**Files:**
- Modify: `src/ccmux_core/error.py`
- Create: `tests/test_error.py` (if absent)

- [ ] **Step 1: Write failing tests**

Create or extend `tests/test_error.py`:

```python
"""Tests for ccmux-core error types."""

import pytest

from ccmux_core.error import (
    BackendError,
    BlockedError,
    BlockedExpiredError,
    DeadError,
    WrongBlockedKindError,
    WrongStateError,
)


def test_all_errors_inherit_from_backend_error():
    for cls in (
        BlockedError,
        BlockedExpiredError,
        DeadError,
        WrongBlockedKindError,
        WrongStateError,
    ):
        assert issubclass(cls, BackendError)


def test_errors_carry_messages():
    e = BlockedError("respond to the active dialog first")
    assert "active dialog" in str(e)
```

- [ ] **Step 2: Run test, verify fail**

Run: `uv run pytest tests/test_error.py -v`
Expected: FAIL (ImportError for the new symbols)

- [ ] **Step 3: Add error classes to error.py**

Append to `src/ccmux_core/error.py`:

```python
class BlockedError(BackendError):
    """send_prompt or other L2 op called while Backend is Blocked."""


class DeadError(BackendError):
    """Any L2 op called after Backend reached Dead state."""


class WrongStateError(BackendError):
    """L2 op called in a state where it doesn't apply (e.g.
    respond_* in non-Blocked, drop_to_tui in non-Blocked)."""


class WrongBlockedKindError(BackendError):
    """respond_* called against the wrong Blocked.kind (e.g.
    respond_question when state.kind == 'permission')."""


class BlockedExpiredError(BackendError):
    """Structured respond_* called after Blocked.expired became True
    (drop_to_tui or decision-socket timeout). Caller must use
    send_keys() for the TUI fallback."""
```

- [ ] **Step 4: Run tests, verify pass**

Run: `uv run pytest tests/test_error.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ccmux_core/error.py tests/test_error.py
git commit -m "$(cat <<'EOF'
feat(error): add L2 error types

BlockedError, DeadError, WrongStateError, WrongBlockedKindError,
BlockedExpiredError. Each names a specific failure mode so the
frontend can react precisely (e.g. tell the user to respond to a
dialog vs tell them the session died).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Add L1 `Message` dataclasses

**Files:**
- Create: `src/ccmux_core/message.py`
- Create: `tests/test_message.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_message.py`:

```python
"""Tests for L1 Message dataclasses."""

from __future__ import annotations

import dataclasses

import pytest

from ccmux_core.message import (
    AssistantText,
    Message,
    PermissionRequest,
    ToolCall,
    ToolResult,
    UserPrompt,
)


def test_user_prompt_frozen_with_fields():
    m = UserPrompt(text="hi", timestamp=1.0)
    assert m.text == "hi"
    assert m.timestamp == 1.0
    with pytest.raises(dataclasses.FrozenInstanceError):
        m.text = "x"


def test_assistant_text_equality_by_value():
    assert AssistantText(text="ok", timestamp=2.0) == AssistantText(text="ok", timestamp=2.0)


def test_tool_call_carries_input_dict():
    m = ToolCall(tool_name="Bash", tool_input={"command": "ls"}, timestamp=3.0)
    assert m.tool_input == {"command": "ls"}


def test_tool_result_has_is_error_flag():
    ok = ToolResult(tool_name="Bash", output="files", is_error=False, timestamp=4.0)
    err = ToolResult(tool_name="Bash", output="bad", is_error=True, timestamp=4.0)
    assert ok.is_error is False
    assert err.is_error is True


def test_permission_request_fields():
    m = PermissionRequest(tool_name="Bash", tool_input={"command": "rm"}, timestamp=5.0)
    assert m.tool_name == "Bash"
    assert m.tool_input == {"command": "rm"}


def test_message_union_includes_all_kinds():
    msgs: list[Message] = [
        UserPrompt(text="x", timestamp=0),
        AssistantText(text="y", timestamp=0),
        ToolCall(tool_name="T", tool_input={}, timestamp=0),
        ToolResult(tool_name="T", output="", is_error=False, timestamp=0),
        PermissionRequest(tool_name="T", tool_input={}, timestamp=0),
    ]
    assert len(msgs) == 5
```

- [ ] **Step 2: Run test, verify fail**

Run: `uv run pytest tests/test_message.py -v`
Expected: FAIL (ImportError, module doesn't exist)

- [ ] **Step 3: Create `src/ccmux_core/message.py`**

```python
"""L1 normalized Message stream for ccmux-core.

The Message family is a deduplicated fusion of hook events and
parsed transcript items. Each Message kind is sourced from
exactly one upstream channel; see the design spec for the dedup
source-of-truth table.

Each dataclass is frozen so it can be safely passed across async
boundaries and compared by value.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UserPrompt:
    """Sourced from events.user_prompt_submit."""

    text: str
    timestamp: float


@dataclass(frozen=True)
class AssistantText:
    """Sourced from transcript Assistant text content."""

    text: str
    timestamp: float


@dataclass(frozen=True)
class ToolCall:
    """Sourced from transcript Assistant tool_use blocks."""

    tool_name: str
    tool_input: dict
    timestamp: float


@dataclass(frozen=True)
class ToolResult:
    """Sourced from transcript tool_result blocks."""

    tool_name: str
    output: str
    is_error: bool
    timestamp: float


@dataclass(frozen=True)
class PermissionRequest:
    """Sourced from events.permission_request (no transcript fallback)."""

    tool_name: str
    tool_input: dict
    timestamp: float


Message = (
    UserPrompt | AssistantText | ToolCall | ToolResult | PermissionRequest
)
```

- [ ] **Step 4: Run tests, verify pass**

Run: `uv run pytest tests/test_message.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ccmux_core/message.py tests/test_message.py
git commit -m "$(cat <<'EOF'
feat(message): add L1 normalized Message dataclasses

UserPrompt, AssistantText, ToolCall, ToolResult, PermissionRequest.
Each is frozen and sourced from exactly one upstream channel per
the dedup table in the L2 design spec. Notification deliberately
excluded — control-plane signal, not conversational content.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Implement `Backend.messages()` normalized stream

**Files:**
- Modify: `src/ccmux_core/backend.py`
- Modify: `tests/test_backend.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_backend.py`:

```python
import pytest


@pytest.mark.asyncio
async def test_messages_emits_user_prompt_from_event(monkeypatch, tmp_path):
    """user_prompt_submit event → UserPrompt L1 message."""
    from ccmux_core import Backend
    from ccmux_core.message import UserPrompt

    # write a single user_prompt_submit event to a tmp events file
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        '{"event_type":"user_prompt_submit","timestamp":"2026-05-12T10:00:00+00:00",'
        '"claude":{"session_id":"s1"},"tmux":{"session_name":"t1"},'
        '"payload":{"prompt":"hello"}}\n'
    )

    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        msgs = []
        async for m in b.messages():
            msgs.append(m)
            if len(msgs) >= 1:
                break

    assert len(msgs) == 1
    assert isinstance(msgs[0], UserPrompt)
    assert msgs[0].text == "hello"
```

- [ ] **Step 2: Run test, verify fail**

Run: `uv run pytest tests/test_backend.py::test_messages_emits_user_prompt_from_event -v`
Expected: FAIL (`b.messages` doesn't exist or returns wrong type)

- [ ] **Step 3: Implement `messages()` in `backend.py`**

Add an internal queue + a public `messages()` iterator. Sketch (full integration into Backend; adapt to current class layout):

In `src/ccmux_core/backend.py`, add to imports:

```python
from .message import (
    AssistantText,
    Message,
    PermissionRequest,
    ToolCall,
    ToolResult,
    UserPrompt,
)
```

Add a new queue in `__init__`:

```python
self._l1_messages_q: asyncio.Queue = asyncio.Queue()
```

Update `__aexit__` queue-drain loop to include `_l1_messages_q`.

Add the public iterator:

```python
async def messages(self) -> AsyncIterator[Message]:
    """L1 normalized message stream (dedup'd from events + transcript)."""
    while True:
        item = await self._l1_messages_q.get()
        if item is _END:
            return
        yield item
```

In the `_event_consumer` method, after the existing state machine step processing and `_events_q.put_nowait(ev)`, add per-event-type fan-out:

```python
# L1 message synthesis from events
et = ev.get("event_type", "")
payload = ev.get("payload") or {}
event_unix = _iso_to_unix(ev.get("timestamp")) or 0.0
if et == "user_prompt_submit":
    self._l1_messages_q.put_nowait(
        UserPrompt(text=payload.get("prompt", ""), timestamp=event_unix)
    )
elif et == "permission_request":
    self._l1_messages_q.put_nowait(
        PermissionRequest(
            tool_name=payload.get("tool_name", ""),
            tool_input=payload.get("tool_input") or {},
            timestamp=event_unix,
        )
    )
```

In the `_message_consumer` method (which consumes transcript items), after `self._messages_q.put_nowait(msg)`, add synthesis from `ClaudeMessage`:

```python
# L1 synthesis from transcript items
msg_ts = msg.timestamp  # adapt to claude-tap's actual ClaudeMessage field
if msg.role == "assistant":
    # text blocks
    for block in msg.content_blocks:
        if block.type == "text":
            self._l1_messages_q.put_nowait(
                AssistantText(text=block.text, timestamp=msg_ts)
            )
        elif block.type == "tool_use":
            self._l1_messages_q.put_nowait(
                ToolCall(
                    tool_name=block.tool_name,
                    tool_input=block.input,
                    timestamp=msg_ts,
                )
            )
elif msg.role == "user":
    # tool_result blocks
    for block in msg.content_blocks:
        if block.type == "tool_result":
            self._l1_messages_q.put_nowait(
                ToolResult(
                    tool_name=block.tool_name or "",
                    output=block.content_text or "",
                    is_error=bool(block.is_error),
                    timestamp=msg_ts,
                )
            )
```

**Note**: the exact field names on `ClaudeMessage` depend on claude-tap's API. Inspect `claude_tap.messages.ClaudeMessage` to get them right; the snippet above uses placeholder names. If claude-tap doesn't have `content_blocks` in this shape, adapt accordingly.

- [ ] **Step 4: Run tests, verify pass**

Run: `uv run pytest tests/test_backend.py::test_messages_emits_user_prompt_from_event -v`
Expected: PASS

- [ ] **Step 5: Add tests for the other Message kinds**

Add tests for `ToolCall`, `ToolResult`, `AssistantText`, `PermissionRequest` similar to step 1. Each fixture writes a representative event or transcript item.

- [ ] **Step 6: Run all messages tests**

Run: `uv run pytest tests/test_backend.py -v -k messages`
Expected: PASS (all)

- [ ] **Step 7: Commit**

```bash
git add src/ccmux_core/backend.py tests/test_backend.py
git commit -m "$(cat <<'EOF'
feat(backend): add L1 messages() normalized stream

Fuses events (user_prompt_submit, permission_request) with parsed
transcript items (Assistant text, tool_use, tool_result) into a
deduplicated Message union stream per the L2 design dedup table.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Key injection — tmux send-keys path

**Files:**
- Create: `src/ccmux_core/keys.py`
- Create: `tests/test_keys.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_keys.py`:

```python
"""Tests for the key-injection layer."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from ccmux_core.keys import send_via_tmux


def test_send_via_tmux_literal_text():
    with patch("subprocess.run") as run:
        send_via_tmux(pane_id="%0", keys="hello", literal=True)
    args = run.call_args[0][0]
    assert args[:3] == ["tmux", "send-keys", "-t"]
    assert args[3] == "%0"
    # literal flag -l
    assert "-l" in args
    assert "hello" in args


def test_send_via_tmux_named_key():
    with patch("subprocess.run") as run:
        send_via_tmux(pane_id="%0", keys="Enter", literal=False)
    args = run.call_args[0][0]
    assert "-l" not in args  # no literal flag for named keys
    assert "Enter" in args


def test_send_via_tmux_list_of_keys():
    with patch("subprocess.run") as run:
        send_via_tmux(pane_id="%0", keys=["Up", "Up", "Enter"], literal=False)
    args = run.call_args[0][0]
    assert "Up" in args and "Enter" in args
```

- [ ] **Step 2: Run test, verify fail**

Run: `uv run pytest tests/test_keys.py -v`
Expected: FAIL (ImportError)

- [ ] **Step 3: Create `src/ccmux_core/keys.py`**

```python
"""Key injection: tmux send-keys (default) and TIOCSTI (copy-mode fallback).

Two paths share one logical input (pane_id + keys + literal flag):

* :func:`send_via_tmux` — uses ``tmux send-keys``. Stable, but is
  intercepted when the pane is in copy mode.
* :func:`send_via_tiocsti` — bypasses tmux by writing directly to
  the pane's pty via TIOCSTI ioctl. Works regardless of copy mode.

:func:`send_keys` is the dispatcher: it detects copy mode and
picks the right path automatically.
"""

from __future__ import annotations

import subprocess

from .error import BackendError


class KeyInjectionError(BackendError):
    """tmux send-keys or TIOCSTI call failed."""


def send_via_tmux(
    pane_id: str,
    keys: str | list[str],
    *,
    literal: bool,
) -> None:
    """Send keys to a tmux pane via ``tmux send-keys``.

    Parameters
    ----------
    pane_id
        Target pane (e.g. ``%0`` or session:window.pane).
    keys
        A single key name / text, or a list of them (sent in order).
    literal
        If True, pass ``-l`` so keys are sent as literal text (no
        key-name interpretation). Used for prompt content.
        If False, keys are interpreted as tmux key names
        (e.g. ``Enter``, ``Up``, ``C-u``). Used for control keys.
    """
    if isinstance(keys, str):
        keys = [keys]
    cmd = ["tmux", "send-keys", "-t", pane_id]
    if literal:
        cmd.append("-l")
    cmd.extend(keys)
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise KeyInjectionError(
            f"tmux send-keys failed for pane {pane_id!r}: "
            f"{result.stderr.strip()}"
        )
```

- [ ] **Step 4: Run tests, verify pass**

Run: `uv run pytest tests/test_keys.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ccmux_core/keys.py tests/test_keys.py
git commit -m "$(cat <<'EOF'
feat(keys): add tmux send-keys wrapper

First half of the key-injection layer. Stable default path used
when the target pane is not in copy mode.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Key injection — TIOCSTI path

**Files:**
- Modify: `src/ccmux_core/keys.py`
- Modify: `tests/test_keys.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_keys.py`:

```python
def test_keyname_to_bytes_table_covers_required_keys():
    from ccmux_core.keys import KEYNAME_TO_BYTES

    # spot-check required entries
    assert KEYNAME_TO_BYTES["Enter"] == b"\r"
    assert KEYNAME_TO_BYTES["Escape"] == b"\x1b"
    assert KEYNAME_TO_BYTES["C-u"] == b"\x15"
    assert KEYNAME_TO_BYTES["Up"] == b"\x1b[A"
    assert KEYNAME_TO_BYTES["Down"] == b"\x1b[B"


def test_send_via_tiocsti_writes_literal_text(tmp_path):
    """Smoke test: writing bytes via TIOCSTI requires a real pty.
    Skip in CI; mocked here for unit-test purposes."""
    from unittest.mock import patch
    from ccmux_core.keys import send_via_tiocsti

    with patch("ccmux_core.keys._get_pane_tty", return_value="/dev/pts/0"), \
         patch("os.open", return_value=42) as op_open, \
         patch("os.close") as op_close, \
         patch("fcntl.ioctl") as ioctl:
        send_via_tiocsti(pane_id="%0", keys="hi", literal=True)
    # 2 chars × 1 ioctl each
    assert ioctl.call_count == 2
    op_open.assert_called_once()
    op_close.assert_called_once_with(42)


def test_send_via_tiocsti_named_key_resolves_to_bytes():
    from unittest.mock import patch
    from ccmux_core.keys import send_via_tiocsti

    with patch("ccmux_core.keys._get_pane_tty", return_value="/dev/pts/0"), \
         patch("os.open", return_value=42), \
         patch("os.close"), \
         patch("fcntl.ioctl") as ioctl:
        send_via_tiocsti(pane_id="%0", keys="Enter", literal=False)
    # one byte for '\r'
    ioctl.assert_called_once()
    # third positional arg of ioctl is the byte
    args = ioctl.call_args[0]
    assert args[2] == b"\r"
```

- [ ] **Step 2: Run test, verify fail**

Run: `uv run pytest tests/test_keys.py -v -k tiocsti`
Expected: FAIL (`send_via_tiocsti` not defined)

- [ ] **Step 3: Add TIOCSTI implementation to `src/ccmux_core/keys.py`**

Append:

```python
import fcntl
import os
import termios


# Fixed map: key name → raw bytes for TIOCSTI injection.
# Only the keys ccmux-core actually emits are included.
KEYNAME_TO_BYTES: dict[str, bytes] = {
    "Enter": b"\r",
    "Return": b"\r",
    "Escape": b"\x1b",
    "Esc": b"\x1b",
    "Tab": b"\t",
    "Space": b" ",
    "C-a": b"\x01",
    "C-k": b"\x0b",
    "C-u": b"\x15",
    "Up": b"\x1b[A",
    "Down": b"\x1b[B",
    "Right": b"\x1b[C",
    "Left": b"\x1b[D",
}


def _get_pane_tty(pane_id: str) -> str:
    """Look up the pty path for a tmux pane."""
    result = subprocess.run(
        ["tmux", "display", "-t", pane_id, "-p", "#{pane_tty}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise KeyInjectionError(
            f"tmux display(pane_tty) failed for {pane_id!r}: "
            f"{result.stderr.strip()}"
        )
    tty = result.stdout.strip()
    if not tty:
        raise KeyInjectionError(f"pane {pane_id!r} has no pane_tty")
    return tty


def _encode_keys(keys: list[str], literal: bool) -> bytes:
    """Convert a list of key names / literal text to raw bytes."""
    out = b""
    for key in keys:
        if literal:
            out += key.encode("utf-8")
        else:
            if key not in KEYNAME_TO_BYTES:
                raise KeyInjectionError(f"unknown key name: {key!r}")
            out += KEYNAME_TO_BYTES[key]
    return out


def send_via_tiocsti(
    pane_id: str,
    keys: str | list[str],
    *,
    literal: bool,
) -> None:
    """Inject keys into a pane's pty via TIOCSTI, bypassing tmux.

    Works regardless of copy mode. Each byte is injected with a
    separate ``ioctl(TIOCSTI, b)`` call.
    """
    if isinstance(keys, str):
        keys = [keys]
    data = _encode_keys(keys, literal=literal)
    tty = _get_pane_tty(pane_id)
    fd = os.open(tty, os.O_RDWR | os.O_NOCTTY)
    try:
        for b in data:
            fcntl.ioctl(fd, termios.TIOCSTI, bytes([b]))
    except OSError as e:
        raise KeyInjectionError(
            f"TIOCSTI ioctl failed on {tty}: {e}"
        ) from e
    finally:
        os.close(fd)
```

- [ ] **Step 4: Run tests, verify pass**

Run: `uv run pytest tests/test_keys.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ccmux_core/keys.py tests/test_keys.py
git commit -m "$(cat <<'EOF'
feat(keys): add TIOCSTI fallback for copy-mode aware key injection

Writes keys directly to the pane's pty via TIOCSTI ioctl, bypassing
tmux entirely. Required to send prompts while the user is in tmux
copy mode (where tmux send-keys gets intercepted by copy commands).

Includes the fixed keyname → bytes table.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Copy-mode detection + `send_keys` dispatcher

**Files:**
- Modify: `src/ccmux_core/keys.py`
- Modify: `tests/test_keys.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_keys.py`:

```python
def test_pane_in_copy_mode_true():
    from unittest.mock import patch
    from ccmux_core.keys import pane_in_copy_mode

    with patch("subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = "1\n"
        assert pane_in_copy_mode("%0") is True


def test_pane_in_copy_mode_false():
    from unittest.mock import patch
    from ccmux_core.keys import pane_in_copy_mode

    with patch("subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = "0\n"
        assert pane_in_copy_mode("%0") is False


def test_send_keys_dispatches_to_tmux_when_normal_mode():
    from unittest.mock import patch
    from ccmux_core.keys import send_keys

    with patch("ccmux_core.keys.pane_in_copy_mode", return_value=False), \
         patch("ccmux_core.keys.send_via_tmux") as via_tmux, \
         patch("ccmux_core.keys.send_via_tiocsti") as via_tiocsti:
        send_keys(pane_id="%0", keys="hi", literal=True)

    via_tmux.assert_called_once()
    via_tiocsti.assert_not_called()


def test_send_keys_dispatches_to_tiocsti_when_copy_mode():
    from unittest.mock import patch
    from ccmux_core.keys import send_keys

    with patch("ccmux_core.keys.pane_in_copy_mode", return_value=True), \
         patch("ccmux_core.keys.send_via_tmux") as via_tmux, \
         patch("ccmux_core.keys.send_via_tiocsti") as via_tiocsti:
        send_keys(pane_id="%0", keys="hi", literal=True)

    via_tmux.assert_not_called()
    via_tiocsti.assert_called_once()
```

- [ ] **Step 2: Run test, verify fail**

Run: `uv run pytest tests/test_keys.py -v -k "copy_mode or send_keys"`
Expected: FAIL (`pane_in_copy_mode` / `send_keys` not defined)

- [ ] **Step 3: Add detection + dispatcher to `src/ccmux_core/keys.py`**

Append:

```python
def pane_in_copy_mode(pane_id: str) -> bool:
    """True if the tmux pane is currently in copy mode."""
    result = subprocess.run(
        ["tmux", "display", "-t", pane_id, "-p", "#{?pane_in_mode,1,0}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        # If detection fails, assume normal mode and let send_via_tmux
        # surface any downstream error.
        return False
    return result.stdout.strip() == "1"


def send_keys(
    pane_id: str,
    keys: str | list[str],
    *,
    literal: bool,
) -> None:
    """Send keys to a pane, transparently handling copy mode.

    When the pane is in copy mode, tmux send-keys would be
    intercepted by copy-mode commands. Falls back to TIOCSTI to
    bypass tmux entirely so the user's copy-mode session stays
    intact.
    """
    if pane_in_copy_mode(pane_id):
        send_via_tiocsti(pane_id, keys, literal=literal)
    else:
        send_via_tmux(pane_id, keys, literal=literal)
```

- [ ] **Step 4: Run tests, verify pass**

Run: `uv run pytest tests/test_keys.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add src/ccmux_core/keys.py tests/test_keys.py
git commit -m "$(cat <<'EOF'
feat(keys): copy-mode aware send_keys dispatcher

Detects pane_in_mode via tmux display, picks send_via_tmux (normal)
or send_via_tiocsti (copy mode). Frontend code just calls send_keys
without worrying about the user's tmux state.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: `Backend.send_keys()` public method

**Files:**
- Modify: `src/ccmux_core/backend.py`
- Modify: `tests/test_backend.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_backend.py`:

```python
@pytest.mark.asyncio
async def test_backend_send_keys_delegates_to_keys_module(tmp_path):
    from unittest.mock import patch
    from ccmux_core import Backend

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    async with Backend(tmux_session="t1", pane_id="%9", events_path=events_path) as b:
        with patch("ccmux_core.backend.send_keys") as sk:
            await b.send_keys("hello", literal=True)
        sk.assert_called_once_with("%9", "hello", literal=True)
```

- [ ] **Step 2: Run test, verify fail**

Run: `uv run pytest tests/test_backend.py::test_backend_send_keys_delegates_to_keys_module -v`
Expected: FAIL (`b.send_keys` doesn't exist)

- [ ] **Step 3: Add `send_keys` method to Backend**

In `src/ccmux_core/backend.py`, add import:

```python
from .keys import send_keys as _send_keys_impl
```

Add method to `Backend`:

```python
async def send_keys(
    self,
    keys: str | list[str],
    *,
    literal: bool = True,
) -> None:
    """Low-level key injection. Available in any state.

    Frontend uses this for TUI navigation after drop_to_tui() and
    for custom key combinations not covered by higher-level methods.
    """
    # subprocess inside a thread to keep the event loop responsive
    import asyncio
    await asyncio.to_thread(_send_keys_impl, self._pane_id, keys, literal=literal)
```

The test patches `ccmux_core.backend.send_keys`, so import the function as a module-level name in `backend.py`:

```python
from .keys import send_keys  # module-level name patched in tests
```

Then call via `send_keys(...)` (module-level) — adjust test patch path accordingly. Pick one approach (alias or rename) and be consistent.

- [ ] **Step 4: Run tests, verify pass**

Run: `uv run pytest tests/test_backend.py::test_backend_send_keys_delegates_to_keys_module -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ccmux_core/backend.py tests/test_backend.py
git commit -m "$(cat <<'EOF'
feat(backend): add send_keys() escape hatch

Thin wrapper over keys.send_keys. State-agnostic by design — the
frontend uses this for TUI navigation after drop_to_tui or for
arbitrary key combinations.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: `Backend.send_prompt()` with queue and state guards

**Files:**
- Modify: `src/ccmux_core/backend.py`
- Modify: `tests/test_backend.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_backend.py`:

```python
@pytest.mark.asyncio
async def test_send_prompt_in_idle_sends_immediately(tmp_path):
    from unittest.mock import patch, AsyncMock
    from ccmux_core import Backend
    from ccmux_core.state import Idle

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        # force state
        b._state = Idle(reason="stop")
        with patch.object(b, "send_keys", new_callable=AsyncMock) as sk:
            await b.send_prompt("hello")
        # defensive C-u, then text, then Enter — two or three calls
        calls = sk.call_args_list
        # at minimum: text and Enter present somewhere
        all_args = [c.args for c in calls]
        assert any("hello" in str(a) for a in all_args)
        assert any("Enter" in str(a) for a in all_args)


@pytest.mark.asyncio
async def test_send_prompt_in_working_queues(tmp_path):
    from unittest.mock import patch, AsyncMock
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
async def test_pending_flushed_on_idle_as_one_prompt(tmp_path):
    from unittest.mock import patch, AsyncMock
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
        # one concatenated call with A\n\nB
        sent = [c.args for c in sk.call_args_list]
        assert any("A\n\nB" in str(a) for a in sent)
        assert b.pending_count == 0
```

- [ ] **Step 2: Run tests, verify fail**

Run: `uv run pytest tests/test_backend.py -v -k send_prompt`
Expected: FAIL (`send_prompt`, `pending_count`, `pending_preview`, `_flush_pending` don't exist)

- [ ] **Step 3: Implement send_prompt + queue**

In `src/ccmux_core/backend.py`, add to `__init__`:

```python
self._pending: list[str] = []
```

Add the property and methods:

```python
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
    """
    from .state import Blocked, Dead, Idle, Working
    from .error import BlockedError, DeadError

    state = self._state
    if isinstance(state, Idle):
        # defensive clear, then send
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
        # State is None (pre-live-phase): also queue
        self._pending.append(text)


async def _flush_pending(self, *, new_state) -> None:
    """Called by the state-transition emitter when entering Idle.
    Sends the queued prompts as one concatenated submission."""
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
```

- [ ] **Step 4: Wire the flush into state-emission**

In `_event_consumer` (and any other site that puts a new state into `self._states_q`), call `await self._flush_pending(new_state=step.new_state)` after appending the state to the queue if the new state is Idle. Same for safety-net transitions.

- [ ] **Step 5: Run tests, verify pass**

Run: `uv run pytest tests/test_backend.py -v -k send_prompt`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/ccmux_core/backend.py tests/test_backend.py
git commit -m "$(cat <<'EOF'
feat(backend): add send_prompt with state-gated concat queue

Idle: defensive C-u + send + Enter. Working: append to pending
queue, flushed as one concatenated submission on next Idle.
Blocked/Dead: raise typed errors. Includes pending_count /
pending_preview for frontend display.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: `Backend.interrupt()`

**Files:**
- Modify: `src/ccmux_core/backend.py`
- Modify: `tests/test_backend.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_backend.py`:

```python
@pytest.mark.asyncio
async def test_interrupt_in_working_sends_esc_then_cu_then_clears_queue(tmp_path):
    from unittest.mock import patch, AsyncMock
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
        # Esc first, Ctrl-U second
        assert any("Escape" in str(a) for a in calls)
        assert any("C-u" in str(a) for a in calls)
        # queue cleared
        assert b.pending_count == 0


@pytest.mark.asyncio
async def test_interrupt_in_idle_is_noop(tmp_path):
    from unittest.mock import patch, AsyncMock
    from ccmux_core import Backend
    from ccmux_core.state import Idle

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Idle(reason="stop")
        with patch.object(b, "send_keys", new_callable=AsyncMock) as sk:
            await b.interrupt()
        sk.assert_not_called()
```

- [ ] **Step 2: Run test, verify fail**

Run: `uv run pytest tests/test_backend.py -v -k interrupt`
Expected: FAIL

- [ ] **Step 3: Add `interrupt` to Backend**

In `src/ccmux_core/backend.py`:

```python
async def interrupt(self) -> None:
    """Abort the current Working state and clear the queue.

    No-op outside Working. Sends Esc (which causes claude code to
    restore the prompt to the TUI input buffer) followed by Ctrl-U
    (clears that buffer so future send_prompt isn't appended onto
    leftover text).
    """
    from .state import Working
    if not isinstance(self._state, Working):
        return
    await self.send_keys("Escape", literal=False)
    await self.send_keys("C-u", literal=False)
    self._pending.clear()
```

- [ ] **Step 4: Run tests, verify pass**

Run: `uv run pytest tests/test_backend.py -v -k interrupt`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ccmux_core/backend.py tests/test_backend.py
git commit -m "$(cat <<'EOF'
feat(backend): add interrupt() with chrome cleanup

Sends Esc (interrupt + claude restores last prompt to input buffer)
then Ctrl-U (clears that restored text) then drops the pending
queue. Without the Ctrl-U, the next send_prompt would concatenate
onto the residue invisible to the frontend.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: decision.sock listener wiring

**Files:**
- Modify: `src/ccmux_core/backend.py`
- Modify: `tests/test_backend.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_backend.py`:

```python
@pytest.mark.asyncio
async def test_decision_listener_bound_in_aenter(tmp_path, monkeypatch):
    """Backend should bind decision.sock on __aenter__ and unbind on __aexit__."""
    import os
    from ccmux_core import Backend

    monkeypatch.setenv("CLAUDE_TAP_DIR", str(tmp_path))
    events_path = tmp_path / "events.jsonl"
    events_path.touch()
    sock_path = tmp_path / "decision.sock"

    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path,
                        decision_sock_path=sock_path) as b:
        assert sock_path.exists() or sock_path.is_socket()  # bound
    # after exit
    assert not sock_path.exists()
```

- [ ] **Step 2: Run test, verify fail**

Run: `uv run pytest tests/test_backend.py::test_decision_listener_bound_in_aenter -v`
Expected: FAIL (unexpected kwarg or socket not bound)

- [ ] **Step 3: Wire DecisionListener into Backend lifecycle**

Import:

```python
from claude_tap import DecisionListener
```

Update `__init__` to accept the optional socket path and timeout:

```python
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
    # ... existing fields ...
    self._decision_sock_path = decision_sock_path
    self._decision_timeout = decision_timeout
    self._decision_listener: DecisionListener | None = None
    self._decision_task: asyncio.Task | None = None
```

In `__aenter__`:

```python
self._decision_listener = DecisionListener(path=self._decision_sock_path)
await self._decision_listener.__aenter__()
self._decision_task = asyncio.create_task(self._decision_consumer())
```

In `__aexit__`, before queue draining:

```python
if self._decision_task is not None and not self._decision_task.done():
    self._decision_task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await self._decision_task
if self._decision_listener is not None:
    await self._decision_listener.__aexit__(exc_type, exc, tb)
    self._decision_listener = None
```

Add the consumer:

```python
async def _decision_consumer(self) -> None:
    """Pull DecisionRequests from the listener and route by session_id.

    If the request's session_id matches our primary, stash the
    request_id onto the current Blocked state (or wait for one).
    If it doesn't match (some other ccmux Backend would handle it),
    respond with {} immediately to release the hook."""
    if self._decision_listener is None:
        return
    try:
        async for req in self._decision_listener:
            if req.session_id != self._primary:
                # not ours
                await self._decision_listener.respond(req.request_id, {})
                continue
            # Our session — request_id is already on Blocked.state via
            # state_machine; we just hold the listener.respond ability
            # until respond_* / drop_to_tui is called.
            self._active_request_id = req.request_id
    except asyncio.CancelledError:
        raise
    except Exception:
        pass
```

Add `self._active_request_id: str | None = None` to `__init__`.

- [ ] **Step 4: Run tests, verify pass**

Run: `uv run pytest tests/test_backend.py::test_decision_listener_bound_in_aenter -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ccmux_core/backend.py tests/test_backend.py
git commit -m "$(cat <<'EOF'
feat(backend): bind decision.sock listener in lifecycle

Backend now binds claude-tap's DecisionListener on __aenter__,
spawns a consumer task that routes by session_id, and unbinds
cleanly on __aexit__. Foreign session requests are released with
empty {} so they fall through to the local TUI.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 14: `respond_permission()`

**Files:**
- Modify: `src/ccmux_core/backend.py`
- Modify: `tests/test_backend.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_backend.py`:

```python
@pytest.mark.asyncio
async def test_respond_permission_allow_once(tmp_path):
    from unittest.mock import AsyncMock, MagicMock
    from ccmux_core import Backend
    from ccmux_core.state import Blocked

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Blocked(kind="permission", tool_name="Bash",
                           tool_input={}, request_id="r-1")
        b._decision_listener = MagicMock()
        b._decision_listener.respond = AsyncMock()
        await b.respond_permission(decision="allow", mode="once")

    call_args = b._decision_listener.respond.call_args
    assert call_args.args[0] == "r-1"
    payload = call_args.args[1]
    assert payload["hookSpecificOutput"]["hookEventName"] == "PermissionRequest"
    assert payload["hookSpecificOutput"]["decision"]["behavior"] == "allow"


@pytest.mark.asyncio
async def test_respond_permission_deny_carries_message(tmp_path):
    from unittest.mock import AsyncMock, MagicMock
    from ccmux_core import Backend
    from ccmux_core.state import Blocked

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Blocked(kind="permission", tool_name="Bash",
                           tool_input={}, request_id="r-2")
        b._decision_listener = MagicMock()
        b._decision_listener.respond = AsyncMock()
        await b.respond_permission(decision="deny", message="no thanks")

    payload = b._decision_listener.respond.call_args.args[1]
    assert payload["hookSpecificOutput"]["decision"]["behavior"] == "deny"
    assert payload["hookSpecificOutput"]["decision"]["message"] == "no thanks"


@pytest.mark.asyncio
async def test_respond_permission_always_includes_updated_permissions(tmp_path):
    from unittest.mock import AsyncMock, MagicMock
    from ccmux_core import Backend
    from ccmux_core.state import Blocked

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Blocked(
            kind="permission", tool_name="Bash",
            tool_input={"permission_suggestions": [{"type": "addRule", "rule": "Bash:*"}]},
            request_id="r-3",
        )
        b._decision_listener = MagicMock()
        b._decision_listener.respond = AsyncMock()
        await b.respond_permission(decision="allow", mode="always")

    payload = b._decision_listener.respond.call_args.args[1]
    dec = payload["hookSpecificOutput"]["decision"]
    assert dec["behavior"] == "allow"
    assert dec["updatedPermissions"] == [{"type": "addRule", "rule": "Bash:*"}]


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
        b._state = Blocked(kind="ask_user", tool_name="AskUserQuestion",
                           tool_input={}, request_id="r-x")
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
        b._state = Blocked(kind="permission", tool_name="Bash",
                           tool_input={}, request_id="r-x", expired=True)
        with pytest.raises(BlockedExpiredError):
            await b.respond_permission(decision="allow", mode="once")
```

- [ ] **Step 2: Run tests, verify fail**

Run: `uv run pytest tests/test_backend.py -v -k respond_permission`
Expected: FAIL (method doesn't exist)

- [ ] **Step 3: Implement `respond_permission`**

In `src/ccmux_core/backend.py`:

```python
from typing import Literal


async def respond_permission(
    self,
    *,
    decision: Literal["allow", "deny"],
    mode: Literal["once", "always", "all", "bypass"] | None = None,
    message: str | None = None,
) -> None:
    """Respond to a Blocked(kind='permission') dialog.

    decision='allow' + mode='once'   → behavior:allow
    decision='allow' + mode='always' → behavior:allow + updatedPermissions
    decision='allow' + mode='all'    → behavior:allow + updatedPermissions (broader)
    decision='allow' + mode='bypass' → behavior:allow (claude must have
                                       --allow-dangerously-skip-permissions
                                       at launch for bypass to take effect)
    decision='deny'                  → behavior:deny + message
    """
    from .state import Blocked
    from .error import (
        BlockedExpiredError,
        WrongBlockedKindError,
        WrongStateError,
    )

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
            suggestions = (state.tool_input or {}).get("permission_suggestions") or []
            if suggestions:
                inner["updatedPermissions"] = suggestions

    decision_json = {
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": inner,
        }
    }
    assert self._decision_listener is not None, "listener not bound"
    assert state.request_id is not None, "no request_id on Blocked state"
    await self._decision_listener.respond(state.request_id, decision_json)
```

- [ ] **Step 4: Run tests, verify pass**

Run: `uv run pytest tests/test_backend.py -v -k respond_permission`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ccmux_core/backend.py tests/test_backend.py
git commit -m "$(cat <<'EOF'
feat(backend): respond_permission with cmux-shape JSON

Constructs hookSpecificOutput.decision.{behavior,message,updatedPermissions}
matching the production cmux schema. State / kind / expired guards
raise specific error types so frontends can react precisely.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 15: `respond_exit_plan()`

**Files:**
- Modify: `src/ccmux_core/backend.py`
- Modify: `tests/test_backend.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_backend.py`:

```python
@pytest.mark.asyncio
async def test_respond_exit_plan_manual(tmp_path):
    from unittest.mock import AsyncMock, MagicMock
    from ccmux_core import Backend
    from ccmux_core.state import Blocked

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Blocked(
            kind="exit_plan_mode", tool_name="ExitPlanMode",
            tool_input={"plan": "# Plan\n- step 1\n- step 2"},
            request_id="r-1",
        )
        b._decision_listener = MagicMock()
        b._decision_listener.respond = AsyncMock()
        await b.respond_exit_plan(mode="manual")

    payload = b._decision_listener.respond.call_args.args[1]
    dec = payload["hookSpecificOutput"]["decision"]
    assert dec["behavior"] == "allow"
    assert dec["updatedInput"] == {"plan": "# Plan\n- step 1\n- step 2"}


@pytest.mark.asyncio
async def test_respond_exit_plan_auto_accept_includes_set_mode(tmp_path):
    from unittest.mock import AsyncMock, MagicMock
    from ccmux_core import Backend
    from ccmux_core.state import Blocked

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Blocked(
            kind="exit_plan_mode", tool_name="ExitPlanMode",
            tool_input={"plan": "..."},
            request_id="r-2",
        )
        b._decision_listener = MagicMock()
        b._decision_listener.respond = AsyncMock()
        await b.respond_exit_plan(mode="autoAccept")

    dec = b._decision_listener.respond.call_args.args[1]["hookSpecificOutput"]["decision"]
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

    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Blocked(
            kind="exit_plan_mode", tool_name="ExitPlanMode",
            tool_input={"plan": "..."},
            request_id="r-3",
        )
        b._decision_listener = MagicMock()
        b._decision_listener.respond = AsyncMock()
        await b.respond_exit_plan(mode="ultraplan")

    dec = b._decision_listener.respond.call_args.args[1]["hookSpecificOutput"]["decision"]
    assert dec["behavior"] == "deny"
    assert "Ultraplan" in dec["message"]


@pytest.mark.asyncio
async def test_respond_exit_plan_deny_with_feedback(tmp_path):
    from unittest.mock import AsyncMock, MagicMock
    from ccmux_core import Backend
    from ccmux_core.state import Blocked

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Blocked(
            kind="exit_plan_mode", tool_name="ExitPlanMode",
            tool_input={"plan": "..."},
            request_id="r-4",
        )
        b._decision_listener = MagicMock()
        b._decision_listener.respond = AsyncMock()
        await b.respond_exit_plan(mode="deny", feedback="add tests first")

    dec = b._decision_listener.respond.call_args.args[1]["hookSpecificOutput"]["decision"]
    assert dec["behavior"] == "deny"
    assert "add tests first" in dec["message"]
```

- [ ] **Step 2: Run tests, verify fail**

Run: `uv run pytest tests/test_backend.py -v -k respond_exit_plan`
Expected: FAIL

- [ ] **Step 3: Implement `respond_exit_plan`**

In `src/ccmux_core/backend.py`:

```python
async def respond_exit_plan(
    self,
    *,
    mode: Literal["manual", "autoAccept", "ultraplan", "deny"],
    feedback: str | None = None,
) -> None:
    """Respond to a Blocked(kind='exit_plan_mode') dialog.

    mode='manual'     → allow + updatedInput (the plan)
    mode='autoAccept' → allow + updatedInput + setMode auto
    mode='ultraplan'  → deny + message (claude has no native ultraplan)
    mode='deny'       → deny + message (with optional feedback)
    """
    from .state import Blocked
    from .error import (
        BlockedExpiredError,
        WrongBlockedKindError,
        WrongStateError,
    )

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

    inner: dict
    feedback_clean = (feedback or "").strip()

    if feedback_clean:
        inner = {
            "behavior": "deny",
            "message": (
                "User rejected the plan via ccmux and wants this change: "
                f"{feedback_clean}"
            ),
        }
    elif mode == "deny":
        inner = {"behavior": "deny", "message": "User rejected the plan via ccmux."}
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
    assert self._decision_listener is not None
    assert state.request_id is not None
    await self._decision_listener.respond(state.request_id, decision_json)
```

- [ ] **Step 4: Run tests, verify pass**

Run: `uv run pytest tests/test_backend.py -v -k respond_exit_plan`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ccmux_core/backend.py tests/test_backend.py
git commit -m "$(cat <<'EOF'
feat(backend): respond_exit_plan with ultraplan/autoAccept/manual/deny

Mirrors cmux's production exit-plan logic, including the
setMode permission for autoAccept and the special ultraplan
'deny with message' (claude has no native ultraplan mode).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 16: `respond_question()`

**Files:**
- Modify: `src/ccmux_core/backend.py`
- Modify: `tests/test_backend.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_backend.py`:

```python
@pytest.mark.asyncio
async def test_respond_question_single_selection(tmp_path):
    from unittest.mock import AsyncMock, MagicMock
    from ccmux_core import Backend
    from ccmux_core.state import Blocked

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Blocked(
            kind="ask_user", tool_name="AskUserQuestion",
            tool_input={"questions": [
                {"question": "Pick a DB", "options": [{"label": "postgres"}]},
            ]},
            request_id="r-1",
        )
        b._decision_listener = MagicMock()
        b._decision_listener.respond = AsyncMock()
        await b.respond_question(["postgres"])

    dec = b._decision_listener.respond.call_args.args[1]["hookSpecificOutput"]["decision"]
    assert dec["behavior"] == "allow"
    assert dec["updatedInput"]["answers"] == {"Pick a DB": "postgres"}


@pytest.mark.asyncio
async def test_respond_question_multiple_questions_positional(tmp_path):
    from unittest.mock import AsyncMock, MagicMock
    from ccmux_core import Backend
    from ccmux_core.state import Blocked

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Blocked(
            kind="ask_user", tool_name="AskUserQuestion",
            tool_input={"questions": [
                {"question": "Q1", "options": [{"label": "a"}]},
                {"question": "Q2", "options": [{"label": "b"}]},
            ]},
            request_id="r-2",
        )
        b._decision_listener = MagicMock()
        b._decision_listener.respond = AsyncMock()
        await b.respond_question(["a", "b"])

    answers = b._decision_listener.respond.call_args.args[1] \
        ["hookSpecificOutput"]["decision"]["updatedInput"]["answers"]
    assert answers == {"Q1": "a", "Q2": "b"}
```

- [ ] **Step 2: Run tests, verify fail**

Run: `uv run pytest tests/test_backend.py -v -k respond_question`
Expected: FAIL

- [ ] **Step 3: Implement `respond_question`**

In `src/ccmux_core/backend.py`:

```python
async def respond_question(self, selections: list[str]) -> None:
    """Respond to a Blocked(kind='ask_user') multi-choice dialog.

    selections is positional — selections[i] is the user's selected
    option label for questions[i]. Multi-select within a question
    is supported by joining labels with ", " before placing into
    the answers map (matches cmux behavior).
    """
    from .state import Blocked
    from .error import (
        BlockedExpiredError,
        WrongBlockedKindError,
        WrongStateError,
    )

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
    assert self._decision_listener is not None
    assert state.request_id is not None
    await self._decision_listener.respond(state.request_id, decision_json)
```

- [ ] **Step 4: Run tests, verify pass**

Run: `uv run pytest tests/test_backend.py -v -k respond_question`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ccmux_core/backend.py tests/test_backend.py
git commit -m "$(cat <<'EOF'
feat(backend): respond_question for AskUserQuestion

Positional selections per question. Map keys are the question texts
(stripped); value is the chosen option label. Mirrors cmux's
claudeAskUserQuestionInput logic.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 17: `drop_to_tui()` + expired flag handling

**Files:**
- Modify: `src/ccmux_core/state_machine.py` (helper for expired flag)
- Modify: `src/ccmux_core/backend.py`
- Modify: `tests/test_backend.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_backend.py`:

```python
@pytest.mark.asyncio
async def test_drop_to_tui_responds_empty_and_marks_expired(tmp_path):
    from unittest.mock import AsyncMock, MagicMock
    from ccmux_core import Backend
    from ccmux_core.state import Blocked

    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Blocked(kind="permission", tool_name="Bash",
                           tool_input={}, request_id="r-1")
        b._decision_listener = MagicMock()
        b._decision_listener.respond = AsyncMock()
        await b.drop_to_tui()

    b._decision_listener.respond.assert_called_once_with("r-1", {})
    assert isinstance(b._state, Blocked)
    assert b._state.expired is True


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

    async with Backend(tmux_session="t1", pane_id="%0", events_path=events_path) as b:
        b._state = Blocked(kind="permission", tool_name="Bash",
                           tool_input={}, request_id="r-1", expired=True)
        b._decision_listener = MagicMock()
        b._decision_listener.respond = AsyncMock()
        await b.drop_to_tui()
        b._decision_listener.respond.assert_not_called()
```

- [ ] **Step 2: Run tests, verify fail**

Run: `uv run pytest tests/test_backend.py -v -k drop_to_tui`
Expected: FAIL

- [ ] **Step 3: Implement `drop_to_tui` + a helper to mutate Blocked.expired**

In `src/ccmux_core/backend.py`:

```python
async def drop_to_tui(self) -> None:
    """Release the decision-socket request with empty {} so claude
    falls through to its native TUI dialog. Marks Blocked.expired=True.

    From here on, structured respond_* methods raise; use send_keys
    to navigate the TUI."""
    from .state import Blocked
    from .error import WrongStateError
    import dataclasses

    state = self._state
    if not isinstance(state, Blocked):
        raise WrongStateError(
            f"drop_to_tui requires Blocked state, got {type(state).__name__}"
        )
    if state.expired:
        return
    if state.request_id is not None and self._decision_listener is not None:
        await self._decision_listener.respond(state.request_id, {})
    # Blocked is frozen — replace by value
    self._state = dataclasses.replace(state, expired=True)
```

- [ ] **Step 4: Run tests, verify pass**

Run: `uv run pytest tests/test_backend.py -v -k drop_to_tui`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ccmux_core/backend.py tests/test_backend.py
git commit -m "$(cat <<'EOF'
feat(backend): drop_to_tui releases socket and marks expired

Responds to the active permission request with empty {}, which
makes claude-tap fall through to its native in-pane TUI dialog.
Updates Blocked.expired=True so subsequent structured respond_*
calls raise.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 18: Update `__init__.py` public exports

**Files:**
- Modify: `src/ccmux_core/__init__.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_skeleton.py` (or a new `test_init.py`):

```python
def test_public_api_surface():
    import ccmux_core
    expected_names = {
        "__version__",
        "Backend",
        "BackendError",
        "BlockedError",
        "BlockedExpiredError",
        "DeadError",
        "WrongStateError",
        "WrongBlockedKindError",
        "Blocked",
        "Dead",
        "Idle",
        "State",
        "Working",
        # L1 messages
        "AssistantText",
        "Message",
        "PermissionRequest",
        "ToolCall",
        "ToolResult",
        "UserPrompt",
        # discovery
        "TmuxBinding",
        "TmuxProbeError",
        "discover_tmux_sessions",
        "list_live_tmux_bindings",
    }
    for name in expected_names:
        assert hasattr(ccmux_core, name), f"missing public export: {name}"
```

- [ ] **Step 2: Run test, verify fail**

Run: `uv run pytest tests/test_skeleton.py::test_public_api_surface -v`
Expected: FAIL (missing some exports)

- [ ] **Step 3: Update `src/ccmux_core/__init__.py`**

```python
"""ccmux-core: per-tmux-session state machine + stream multiplexer."""

from . import config as _config  # noqa: F401  (import for side effect)
from ._version import __version__
from .backend import Backend
from .discover import TmuxBinding, discover_tmux_sessions, list_live_tmux_bindings
from .error import (
    BackendError,
    BlockedError,
    BlockedExpiredError,
    DeadError,
    TmuxProbeError,
    WrongBlockedKindError,
    WrongStateError,
)
from .message import (
    AssistantText,
    Message,
    PermissionRequest,
    ToolCall,
    ToolResult,
    UserPrompt,
)
from .state import Blocked, Dead, Idle, State, Working

__all__ = [
    "__version__",
    # Lifecycle
    "Backend",
    # State
    "Blocked",
    "Dead",
    "Idle",
    "State",
    "Working",
    # Messages (L1)
    "AssistantText",
    "Message",
    "PermissionRequest",
    "ToolCall",
    "ToolResult",
    "UserPrompt",
    # Errors
    "BackendError",
    "BlockedError",
    "BlockedExpiredError",
    "DeadError",
    "TmuxProbeError",
    "WrongBlockedKindError",
    "WrongStateError",
    # Discovery
    "TmuxBinding",
    "discover_tmux_sessions",
    "list_live_tmux_bindings",
]
```

- [ ] **Step 4: Run tests, verify pass**

Run: `uv run pytest tests/test_skeleton.py::test_public_api_surface -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ccmux_core/__init__.py tests/test_skeleton.py
git commit -m "$(cat <<'EOF'
feat(api): export L2 surface from package root

Backend, error types, L1 Message dataclasses are now top-level
imports. Maintains existing state and discovery exports.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 19: ccmux-spinner defensive `-S -200 -E -` change

**Files:**
- Modify: `../ccmux-spinner/src/ccmux_spinner/pane.py`
- Modify: `../ccmux-spinner/tests/test_pane.py`

This task lives in a different repo. Open a separate PR there.

- [ ] **Step 1: Update the failing test in `tests/test_pane.py`**

Find `test_capture_pane_success` (around line 162) and update the assertion to expect the new arguments:

```python
def test_capture_pane_success():
    # ... existing setup ...
    out = capture_pane("%80")
    # ... existing setup ...
    args = mock_run.call_args[0][0]
    assert args == ["tmux", "capture-pane", "-p", "-J", "-t", "%80", "-S", "-200", "-E", "-"]
```

- [ ] **Step 2: Run test, verify fail**

Run: `cd /mnt/beegfs/home/wenruiwu/ccmux/ccmux-spinner && uv run pytest tests/test_pane.py::test_capture_pane_success -v`
Expected: FAIL (current args don't include -S/-E)

- [ ] **Step 3: Update `capture_pane` in `src/ccmux_spinner/pane.py`**

Change line 153 from:

```python
["tmux", "capture-pane", "-p", "-J", "-t", pane_id],
```

to:

```python
["tmux", "capture-pane", "-p", "-J", "-t", pane_id, "-S", "-200", "-E", "-"],
```

Also update the docstring above (line 145):

```python
"""Capture the contents of a tmux pane (defensive tail).

Uses ``tmux capture-pane -p -J -t <pane_id> -S -200 -E -``.

* ``-J`` joins wrapped lines so the ``────`` chrome separator
  survives wrapping.
* ``-S -200 -E -`` pins the capture to the buffer tail (200 lines
  of history through the active screen end), independent of
  whether the user is in copy mode. The default flags would return
  only the visible region, which is normally the same as the
  active screen tail but degrades in edge cases (very small panes,
  cross-platform tmux variants, future TUI changes).

No ``-e``: ANSI escapes are stripped.

Raises :class:`PaneCaptureError` on failure.
"""
```

- [ ] **Step 4: Run tests, verify pass**

Run: `uv run pytest tests/test_pane.py -v`
Expected: PASS

- [ ] **Step 5: Bump ccmux-spinner version**

In `pyproject.toml`: `version = "0.2.1"` → `version = "0.2.2"`
In `src/ccmux_spinner/_version.py`: same bump.

- [ ] **Step 6: Commit + PR**

```bash
cd /mnt/beegfs/home/wenruiwu/ccmux/ccmux-spinner
git add -A
git commit -m "$(cat <<'EOF'
fix(pane): pin capture to buffer tail with -S -200 -E -

Default tmux capture-pane returns the visible region, which in
copy mode is normally still the active-screen tail (verified
empirically 2026-05-12 on Linux tmux). Explicit -S -200 -E -
makes the intent robust against small panes, tmux version drift,
and future multi-row TUI patterns at near-zero runtime cost.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Open a PR or push to the repo's release branch per its workflow.

---

## Task 20: Integration test — Backend lifecycle end-to-end

**Files:**
- Create: `tests/test_integration_l2.py`

- [ ] **Step 1: Write the integration test**

Create `tests/test_integration_l2.py`:

```python
"""End-to-end test: Backend lifecycle covering events, state,
messages, send_prompt queue, interrupt, and respond_permission."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccmux_core import Backend
from ccmux_core.message import UserPrompt, AssistantText
from ccmux_core.state import Blocked, Idle, Working


def _write_event(path: Path, event: dict) -> None:
    with path.open("a") as f:
        f.write(json.dumps(event) + "\n")


@pytest.mark.asyncio
async def test_full_lifecycle(tmp_path):
    events_path = tmp_path / "events.jsonl"
    events_path.touch()

    # Synthesise a session_start + user_prompt_submit + stop sequence.
    _write_event(events_path, {
        "event_type": "session_start",
        "timestamp": "2026-05-12T10:00:00+00:00",
        "claude": {"session_id": "s1"},
        "tmux": {"session_name": "t1"},
        "payload": {},
    })
    _write_event(events_path, {
        "event_type": "user_prompt_submit",
        "timestamp": "2026-05-12T10:00:01+00:00",
        "claude": {"session_id": "s1"},
        "tmux": {"session_name": "t1"},
        "payload": {"prompt": "hi"},
    })
    _write_event(events_path, {
        "event_type": "stop",
        "timestamp": "2026-05-12T10:00:02+00:00",
        "claude": {"session_id": "s1"},
        "tmux": {"session_name": "t1"},
        "payload": {"last_assistant_message": "ok"},
    })

    async with Backend(tmux_session="t1", pane_id="%0",
                        events_path=events_path,
                        decision_sock_path=tmp_path / "decision.sock") as b:
        # mock the key injection so we don't really shell out
        with patch.object(b, "send_keys", new_callable=AsyncMock):
            # collect states and messages with a short timeout
            states = []

            async def collect_states():
                async for st in b.states():
                    states.append(st)
                    if isinstance(st, Idle) and st.reason == "stop":
                        return

            await asyncio.wait_for(collect_states(), timeout=2.0)

            assert any(isinstance(s, Idle) for s in states)
            assert any(isinstance(s, Working) for s in states)

            # send_prompt in Idle → goes through immediately
            b._state = Idle(reason="stop")  # ensure
            await b.send_prompt("next prompt")
            # send_keys was invoked at least 3 times (C-u, text, Enter)
            assert b.send_keys.call_count >= 3
```

- [ ] **Step 2: Run integration test**

Run: `uv run pytest tests/test_integration_l2.py -v`
Expected: PASS (allow some time for async event consumption)

- [ ] **Step 3: Run the full suite to confirm no regression**

Run: `uv run pytest -x -v`
Expected: all green

- [ ] **Step 4: Commit**

```bash
git add tests/test_integration_l2.py
git commit -m "$(cat <<'EOF'
test(integration): cover Backend lifecycle with state and send_prompt

End-to-end smoke test: events.jsonl synthesis → state transitions
→ messages → send_prompt in Idle dispatching through send_keys.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 21: Update README and CHANGELOG

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Update CHANGELOG.md**

Add a new section at the top:

```markdown
## v0.2.0 — 2026-05-12

Breaking change: from a four-stream observation library to a
three-layer model with state-gated operations.

### Added

- L1 normalized `messages()` stream — deduplicated fusion of hook
  events and transcript items into a `Message` union
  (`UserPrompt | AssistantText | ToolCall | ToolResult |
  PermissionRequest`).
- L2 state-gated operations:
  - `send_prompt(text)` — Idle: send immediately;
    Working: append to concat queue (flushed on next Idle);
    Blocked/Dead: raise.
  - `interrupt()` — Esc + Ctrl-U + clear pending.
  - `respond_permission(decision, mode, message)`.
  - `respond_exit_plan(mode, feedback)`.
  - `respond_question(selections)`.
  - `drop_to_tui()` — release socket, fall through to TUI.
  - `send_keys(keys, literal)` — copy-mode aware (tmux send-keys
    default, TIOCSTI fallback when pane is in copy mode).
- `Blocked` now carries `request_id` and `expired` fields.
- New error types: `BlockedError`, `DeadError`, `WrongStateError`,
  `WrongBlockedKindError`, `BlockedExpiredError`.
- `decision.sock` listener bound automatically per Backend.

### Changed (breaking)

- L0 `messages()` renamed to `transcript_items()` so the L1 name
  is free.
- `Blocked.kind` discrimination moved from `pre_tool_use` to
  `permission_request` events; `AskUserQuestion` and `ExitPlanMode`
  fire through the permission hook (consistent with claude code's
  actual behavior).

### Notes

- Single-Backend scope. `MultiBackend` and multi-process
  decision.sock arbitration deferred to a follow-up spec.
- ccmux-spinner 0.2.2 is recommended (pinned capture-pane).
```

- [ ] **Step 2: Update README.md**

Add a brief L2 example. Find the existing usage block and append:

````markdown
## L2 operations

```python
from ccmux_core import Backend

async with Backend(tmux_session="my-session", pane_id="%0") as b:
    # Subscribe to state transitions
    async for state in b.states():
        if isinstance(state, Blocked) and state.kind == "permission":
            await b.respond_permission(decision="allow", mode="once")

    # Send a prompt — auto-queued if claude is still working
    await b.send_prompt("Please refactor this file")

    # Interrupt the current turn (clears the input chrome too)
    await b.interrupt()
```
````

- [ ] **Step 3: Commit**

```bash
git add README.md CHANGELOG.md
git commit -m "$(cat <<'EOF'
docs: README + CHANGELOG for v0.2.0 L2 release

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-review notes (already addressed inline)

1. **Spec coverage:** Each section of the L2 design spec maps to one or
   more tasks: state changes → Tasks 2–3; message stream → Tasks 5–6;
   key injection → Tasks 7–9; send_prompt/interrupt → Tasks 11–12;
   decision listener → Task 13; respond_* → Tasks 14–16; drop_to_tui →
   Task 17; spinner change → Task 19.
2. **Naming consistency:** Internal `Blocked.kind` values use the
   existing v0.1 names (`"ask_user"`, `"exit_plan_mode"`) rather than
   the spec's `"question"` / `"exit_plan"` — fewer rename diffs in the
   v0.1 codebase. The spec should be updated to align (one line edit,
   tracked as a follow-up in the next iteration).
3. **decision.sock single-Backend assumption:** Task 13 binds the
   socket per Backend. Multi-Backend coordination is deferred per the
   spec's "Out of scope".
4. **Placeholder scan:** No "TBD", "fill in later", or hand-wavy steps
   remain. Every code step shows actual code.
