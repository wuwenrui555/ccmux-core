# `send_keys` TIOCSTI Removal + Cancel-Mode Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the unreachable TIOCSTI fallback in `ccmux_core.keys.send_keys` with a `cancel`-then-`tmux send-keys` flow that mirrors `ccmux-backend`, fixing the silent-swallow bug seen in `ccmux-core-telegram` v0.1.0 e2e (issue [#14](https://github.com/wuwenrui555/ccmux-core/issues/14)).

**Architecture:** Single dispatcher (`send_keys`) probes for tmux mode via `pane_in_mode`. When in any mode (copy / view / choose / clock), runs `tmux send-keys -X cancel` to exit, then calls `send_via_tmux`. The TIOCSTI code path is removed entirely (kernel rejects `TIOCSTI` to non-controlling tty by default, so it never worked in real deployments).

**Tech Stack:** Python 3.11+, `subprocess.run` for tmux invocations, `pytest` + `unittest.mock.patch` for tests. No new dependencies. ccmux-core remains logger-free.

**Spec:** [docs/superpowers/specs/2026-05-13-send-keys-tiocsti-fallback.md](../specs/2026-05-13-send-keys-tiocsti-fallback.md)

---

## File Structure

Two files touched, no new files:

- `src/ccmux_core/keys.py` — Tasks 1–3 (rename + dispatcher rewrite + dead-code deletion)
- `tests/test_keys.py` — Tasks 1–3 (parallel to source changes)

Branch: `bugfix/send-keys-tiocsti-fallback` (already created off `dev`, spec already committed as `bf6ecf7`).

---

## Task 1: Rename `pane_in_copy_mode` → `pane_in_mode`

This is a pure refactor: the function already returns true for any tmux mode (copy/view/choose/clock) because it queries `#{?pane_in_mode,1,0}`. Only the name and docstring lie about the scope. No behavior change.

**Files:**
- Modify: `src/ccmux_core/keys.py:133-147` (function definition + docstring)
- Modify: `src/ccmux_core/keys.py:163` (caller inside `send_keys`)
- Modify: `tests/test_keys.py:136-168` (three test names + import names)
- Modify: `tests/test_keys.py:177,193` (mock patch targets in dispatcher tests)

- [ ] **Step 1.1: Rename the function and update its docstring in `src/ccmux_core/keys.py`**

Replace lines 133-147:

```python
def pane_in_mode(pane_id: str) -> bool:
    """True if the tmux pane is in any mode (copy / view / choose /
    clock).

    Detected via tmux's ``#{?pane_in_mode,1,0}`` format spec, which
    returns 1 for any active pane mode (not just copy mode). If the
    detection call itself fails, returns False (assume normal mode
    and let send_via_tmux surface any downstream error)."""
    result = subprocess.run(
        ["tmux", "display", "-t", pane_id, "-p", "#{?pane_in_mode,1,0}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return False
    return result.stdout.strip() == "1"
```

- [ ] **Step 1.2: Update the caller inside `send_keys` in `src/ccmux_core/keys.py:163`**

Change line 163 from:

```python
    if pane_in_copy_mode(pane_id):
```

to:

```python
    if pane_in_mode(pane_id):
```

(`send_keys` body otherwise unchanged in this task — the dispatcher rewrite happens in Task 2.)

- [ ] **Step 1.3: Rename test functions and import names in `tests/test_keys.py`**

Replace lines 136-168 (three test functions) with:

```python
def test_pane_in_mode_true():
    from unittest.mock import patch

    from ccmux_core.keys import pane_in_mode

    with patch("subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = "1\n"
        assert pane_in_mode("%0") is True


def test_pane_in_mode_false():
    from unittest.mock import patch

    from ccmux_core.keys import pane_in_mode

    with patch("subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = "0\n"
        assert pane_in_mode("%0") is False


def test_pane_in_mode_falls_back_to_false_on_tmux_error():
    """If detection itself fails, assume normal mode."""
    from unittest.mock import patch

    from ccmux_core.keys import pane_in_mode

    with patch("subprocess.run") as run:
        run.return_value.returncode = 1
        run.return_value.stdout = ""
        run.return_value.stderr = "can't find pane"
        assert pane_in_mode("%0") is False
```

- [ ] **Step 1.4: Update mock patch targets in dispatcher tests in `tests/test_keys.py`**

Change line 177:

```python
        patch("ccmux_core.keys.pane_in_copy_mode", return_value=False),
```

to:

```python
        patch("ccmux_core.keys.pane_in_mode", return_value=False),
```

Change line 193:

```python
        patch("ccmux_core.keys.pane_in_copy_mode", return_value=True),
```

to:

```python
        patch("ccmux_core.keys.pane_in_mode", return_value=True),
```

- [ ] **Step 1.5: Verify nothing else references the old name**

Run: `grep -rn "pane_in_copy_mode" src/ tests/`
Expected: zero matches (the historical mention in `docs/superpowers/plans/2026-05-12-ccmux-core-l2.md` is fine; that file documents the old plan and is not source code).

- [ ] **Step 1.6: Run the full test suite to verify rename is complete**

Run: `pytest tests/test_keys.py -v`
Expected: all tests PASS (12 tests at this stage — the rename does not change test count or behavior).

Run: `pytest tests/ -v`
Expected: all PASS (other modules unaffected by a `keys.py`-internal rename, but verify no module imports `pane_in_copy_mode` from elsewhere).

- [ ] **Step 1.7: Commit**

```bash
cd ~/ccmux/ccmux-core
git add src/ccmux_core/keys.py tests/test_keys.py
git commit -m "$(cat <<'EOF'
refactor(keys): rename pane_in_copy_mode to pane_in_mode (#14)

The tmux probe #{?pane_in_mode,1,0} returns true for any pane mode
(copy / view / choose / clock), not just copy mode. Function name
and docstring now match the actual scope. No behavior change.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Replace `send_keys` dispatcher (TIOCSTI → cancel + tmux)

Drop the `send_via_tiocsti` branch from `send_keys` and replace it with `tmux send-keys -X cancel` followed by `send_via_tmux`. TDD: write the new dispatcher tests first, watch them fail under the current TIOCSTI-dispatching implementation, then update the implementation.

**Files:**
- Modify: `src/ccmux_core/keys.py:150-166` (function body of `send_keys`)
- Modify: `tests/test_keys.py:171-200` (replace two existing dispatcher tests, add one new test)

- [ ] **Step 2.1: Write the failing test for in-mode behavior in `tests/test_keys.py`**

Replace the existing `test_send_keys_dispatches_to_tiocsti_when_copy_mode` (lines 187-200 in the original file, now at the equivalent position after Task 1) with this new test. The function name changes; the behavior asserted changes from "calls send_via_tiocsti" to "runs cancel then send_via_tmux".

```python
def test_send_keys_cancels_mode_then_sends_when_in_mode():
    """When pane is in any tmux mode, cancel the mode first, then
    send keys via tmux send-keys (the TIOCSTI fallback was removed
    in v0.3.2 because the kernel rejects TIOCSTI to a
    non-controlling tty by default — see issue #14)."""
    from unittest.mock import patch

    from ccmux_core.keys import send_keys

    with (
        patch("ccmux_core.keys.pane_in_mode", return_value=True),
        patch("subprocess.run") as run,
    ):
        run.return_value.returncode = 0
        run.return_value.stderr = ""
        send_keys(pane_id="%0", keys="hi", literal=True)

    # Two subprocess calls in order: cancel, then send-keys.
    assert run.call_count == 2
    cancel_argv = run.call_args_list[0][0][0]
    assert cancel_argv == ["tmux", "send-keys", "-X", "-t", "%0", "cancel"]
    send_argv = run.call_args_list[1][0][0]
    assert send_argv[:4] == ["tmux", "send-keys", "-t", "%0"]
    assert "-l" in send_argv
    assert "hi" in send_argv
```

- [ ] **Step 2.2: Run the new test to verify it fails**

Run: `pytest tests/test_keys.py::test_send_keys_cancels_mode_then_sends_when_in_mode -v`
Expected: FAIL. Under the current implementation, `send_keys` dispatches to `send_via_tiocsti` when in mode. `send_via_tiocsti` calls `_get_pane_tty` and `os.open`, neither of which is mocked in this test, so the test will fail with either a `KeyInjectionError` from `_get_pane_tty` (tmux probe fails outside a real tmux session) or an `OSError` from `os.open`. The exact failure message doesn't matter — we just need a non-zero exit confirming the test detects the wrong dispatch.

- [ ] **Step 2.3: Replace the `send_keys` body in `src/ccmux_core/keys.py`**

Replace lines 150-166:

```python
def send_keys(
    pane_id: str,
    keys: str | list[str],
    *,
    literal: bool,
) -> None:
    """Send keys to a pane, handling tmux mode interception.

    When the pane is in any tmux mode (copy / view / choose /
    clock), ``tmux send-keys`` is interpreted as a mode command
    instead of reaching the shell. Cancel the mode first
    (``tmux send-keys -X cancel`` is a no-op outside any mode),
    then send the keys normally.

    Trade-off: when the pane is in copy mode the user's selection
    buffer is lost. Better than the prompt being silently
    swallowed.
    """
    if pane_in_mode(pane_id):
        subprocess.run(
            ["tmux", "send-keys", "-X", "-t", pane_id, "cancel"],
            check=False,
        )
    send_via_tmux(pane_id, keys, literal=literal)
```

- [ ] **Step 2.4: Run the new test to verify it passes**

Run: `pytest tests/test_keys.py::test_send_keys_cancels_mode_then_sends_when_in_mode -v`
Expected: PASS.

- [ ] **Step 2.5: Replace the not-in-mode dispatcher test in `tests/test_keys.py`**

Replace `test_send_keys_dispatches_to_tmux_when_normal_mode` (originally at lines 171-184) with:

```python
def test_send_keys_skips_cancel_when_not_in_mode():
    """When pane is not in any tmux mode, send-keys runs directly
    with no cancel preamble."""
    from unittest.mock import patch

    from ccmux_core.keys import send_keys

    with (
        patch("ccmux_core.keys.pane_in_mode", return_value=False),
        patch("subprocess.run") as run,
    ):
        run.return_value.returncode = 0
        run.return_value.stderr = ""
        send_keys(pane_id="%0", keys="hi", literal=True)

    # Exactly one subprocess call: the send-keys.
    assert run.call_count == 1
    send_argv = run.call_args_list[0][0][0]
    assert send_argv[:4] == ["tmux", "send-keys", "-t", "%0"]
    assert "-l" in send_argv
    assert "hi" in send_argv
```

- [ ] **Step 2.6: Run the not-in-mode test to verify it passes**

Run: `pytest tests/test_keys.py::test_send_keys_skips_cancel_when_not_in_mode -v`
Expected: PASS.

- [ ] **Step 2.7: Add the cancel-failure resilience test in `tests/test_keys.py`**

Add this new test at the end of the file (after `test_send_keys_cancels_mode_then_sends_when_in_mode`):

```python
def test_send_keys_proceeds_when_cancel_subprocess_fails():
    """If `tmux send-keys -X cancel` fails (non-zero rc), the
    subsequent send_via_tmux still runs. Caller errors only if
    that final send fails."""
    from unittest.mock import patch

    from ccmux_core.keys import send_keys

    call_results = [
        # First call: cancel — fails.
        type("R", (), {"returncode": 1, "stderr": "cancel oops"})(),
        # Second call: send_via_tmux — succeeds.
        type("R", (), {"returncode": 0, "stderr": ""})(),
    ]

    with (
        patch("ccmux_core.keys.pane_in_mode", return_value=True),
        patch("subprocess.run", side_effect=call_results) as run,
    ):
        # Should not raise — cancel failure is swallowed.
        send_keys(pane_id="%0", keys="hi", literal=True)

    assert run.call_count == 2
```

- [ ] **Step 2.8: Run the resilience test to verify it passes**

Run: `pytest tests/test_keys.py::test_send_keys_proceeds_when_cancel_subprocess_fails -v`
Expected: PASS.

- [ ] **Step 2.9: Run the whole `test_keys.py` suite as a regression check**

Run: `pytest tests/test_keys.py -v`
Expected: all PASS. The TIOCSTI direct tests (`test_send_via_tiocsti_*`, `test_keyname_to_bytes_table_covers_required_keys`, `test_send_via_tiocsti_closes_fd_on_ioctl_failure`) still pass because they call `send_via_tiocsti` directly — those functions still exist and are removed in Task 3.

- [ ] **Step 2.10: Commit**

```bash
cd ~/ccmux/ccmux-core
git add src/ccmux_core/keys.py tests/test_keys.py
git commit -m "$(cat <<'EOF'
fix(keys): cancel tmux mode then send-keys, drop TIOCSTI dispatch (#14)

send_keys previously dispatched to send_via_tiocsti when the pane
was in copy mode, but the kernel rejects TIOCSTI to a
non-controlling tty by default (errno=1 EPERM), so the path never
worked in real ccmux-core deployments. Replace with the same flow
ccmux-backend uses: probe `#{?pane_in_mode}` and run
`tmux send-keys -X cancel` first when in any tmux mode (copy /
view / choose / clock), then send the keys normally.

Trade-off: in-mode invocations now lose the user's copy-mode
selection buffer. Acceptable — the prior behavior silently dropped
the entire prompt on hardened kernels (and in fact on any stock
kernel where the bridge process and target pane have different
controlling terminals).

The fix lives in the dispatcher, so all callers benefit without
backend.py changes: send_prompt, clear_buffer, _flush_pending
(both the prompt-queue flush path and the safety-net paths driven
by _grace_timer / _trigger_safety), and prompt-picker navigation.
The repeated KeyInjectionError noise visible in cct logs from the
spinner_grace path also resolves as a side effect.

The TIOCSTI implementation itself is removed in a follow-up commit.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Delete TIOCSTI dead code

Now that `send_keys` no longer uses TIOCSTI, the `send_via_tiocsti` family of functions, the `KEYNAME_TO_BYTES` constant, and the related imports/tests are dead. Delete them.

**Files:**
- Modify: `src/ccmux_core/keys.py:1-130` (module docstring, imports, deleted functions/constants)
- Modify: `tests/test_keys.py` (delete five TIOCSTI-direct tests)

- [ ] **Step 3.1: Delete the TIOCSTI test functions in `tests/test_keys.py`**

Delete these five test functions in their entirety (their original line ranges in the file before any edits — exact line numbers will have shifted; locate by name):

- `test_keyname_to_bytes_table_covers_required_keys`
- `test_send_via_tiocsti_writes_literal_text`
- `test_send_via_tiocsti_named_key_resolves_to_bytes`
- `test_send_via_tiocsti_unknown_named_key_raises`
- `test_send_via_tiocsti_closes_fd_on_ioctl_failure`

After deletion, only `KeyInjectionError` and `send_via_tmux` should be referenced from `ccmux_core.keys` in test imports. Verify with:

Run: `grep -E "send_via_tiocsti|KEYNAME_TO_BYTES|_get_pane_tty|_encode_keys" tests/test_keys.py`
Expected: zero matches.

- [ ] **Step 3.2: Run the test suite to confirm only the deleted tests are gone**

Run: `pytest tests/test_keys.py -v`
Expected: all remaining tests PASS. Test count after deletion: 4 (`test_send_via_tmux_*`) + 3 (`test_pane_in_mode_*`) + 3 (`test_send_keys_*`) = 10 tests.

- [ ] **Step 3.3: Delete the dead code in `src/ccmux_core/keys.py`**

Replace the entire file contents with:

```python
"""Key injection: ``tmux send-keys`` with mode-cancel preamble.

When a tmux pane is in any of its modes (copy / view / choose /
clock), ``tmux send-keys`` is interpreted as a mode command
instead of reaching the shell. :func:`send_keys` probes for that
state via :func:`pane_in_mode` and runs ``tmux send-keys -X
cancel`` to exit the mode before injecting keys.

Earlier versions of this module also exposed a :func:`send_via_tiocsti`
fallback intended to bypass tmux entirely via the ``ioctl(TIOCSTI)``
syscall. That path was removed in v0.3.2: the Linux kernel rejects
``TIOCSTI`` to any tty that is not the calling process's controlling
terminal (or unless the caller has ``CAP_SYS_ADMIN``), so the
fallback never worked in real ccmux-core deployments where the
bridge process and the target pane live in different terminals.
See issue #14.
"""

from __future__ import annotations

import subprocess

from .error import BackendError


class KeyInjectionError(BackendError):
    """``tmux send-keys`` invocation failed."""


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
            f"tmux send-keys failed for pane {pane_id!r}: {result.stderr.strip()}"
        )


def pane_in_mode(pane_id: str) -> bool:
    """True if the tmux pane is in any mode (copy / view / choose /
    clock).

    Detected via tmux's ``#{?pane_in_mode,1,0}`` format spec, which
    returns 1 for any active pane mode (not just copy mode). If the
    detection call itself fails, returns False (assume normal mode
    and let send_via_tmux surface any downstream error)."""
    result = subprocess.run(
        ["tmux", "display", "-t", pane_id, "-p", "#{?pane_in_mode,1,0}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return False
    return result.stdout.strip() == "1"


def send_keys(
    pane_id: str,
    keys: str | list[str],
    *,
    literal: bool,
) -> None:
    """Send keys to a pane, handling tmux mode interception.

    When the pane is in any tmux mode (copy / view / choose /
    clock), ``tmux send-keys`` is interpreted as a mode command
    instead of reaching the shell. Cancel the mode first
    (``tmux send-keys -X cancel`` is a no-op outside any mode),
    then send the keys normally.

    Trade-off: when the pane is in copy mode the user's selection
    buffer is lost. Better than the prompt being silently
    swallowed.
    """
    if pane_in_mode(pane_id):
        subprocess.run(
            ["tmux", "send-keys", "-X", "-t", pane_id, "cancel"],
            check=False,
        )
    send_via_tmux(pane_id, keys, literal=literal)
```

- [ ] **Step 3.4: Verify no stale references survive**

Run: `grep -E "send_via_tiocsti|KEYNAME_TO_BYTES|_get_pane_tty|_encode_keys|fcntl|termios" src/ccmux_core/keys.py`
Expected: zero matches.

Run: `grep -rE "send_via_tiocsti|KEYNAME_TO_BYTES" src/ tests/`
Expected: zero matches.

- [ ] **Step 3.5: Run the full repo test suite**

Run: `pytest tests/ -v`
Expected: all PASS, including the 10 tests in `test_keys.py` and all other module tests.

- [ ] **Step 3.6: Run pre-commit hooks**

Run: `pre-commit run --all-files`
Expected: PASS (ruff, ruff-format, markdownlint).

If pre-commit auto-fixes formatting, re-stage the modified files and re-run until clean.

- [ ] **Step 3.7: Commit**

```bash
cd ~/ccmux/ccmux-core
git add src/ccmux_core/keys.py tests/test_keys.py
git commit -m "$(cat <<'EOF'
refactor(keys): remove unreachable TIOCSTI fallback (#14)

Delete send_via_tiocsti, _get_pane_tty, _encode_keys, the
KEYNAME_TO_BYTES table, and the fcntl/os/termios imports they
needed. The kernel rejects TIOCSTI to a non-controlling tty by
default, so this path never worked in real ccmux-core deployments
— the unit tests masked the issue by mocking os.open and
fcntl.ioctl. Issue #14 covers the discovery and rationale.

Module docstring updated to explain why TIOCSTI was removed.
KeyInjectionError stays — send_via_tmux still raises it.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Manual e2e verification on the affected host

The unit tests are mocked. The actual bug only reproduces against a real tmux pane on a kernel where TIOCSTI is rejected for non-controlling-tty access (which is the default everywhere). Verify the fix end-to-end on the cct host that originally surfaced the issue.

**Files:** none (runtime verification only).

- [ ] **Step 4.1: Reinstall cct against the local ccmux-core checkout**

cct's `pyproject.toml` already has `ccmux-core = { path = "../ccmux-core", editable = true }` under `[tool.uv.sources]` (verified in earlier exploration). The branch must be on the `dev`-style checkout that cct points to, which is `~/ccmux/ccmux-core` itself.

Confirm cct will pick up the bugfix branch:

Run: `cd ~/ccmux/ccmux-core-telegram && uv pip show ccmux-core`
Expected: shows `Location` pointing to `~/ccmux/ccmux-core/src` (editable install). If not, run `uv sync` from `~/ccmux/ccmux-core-telegram` to refresh.

- [ ] **Step 4.2: Confirm TIOCSTI is still blocked on this host**

Run:

```bash
PANE_TTY=$(tmux list-panes -F '#{pane_tty}' | head -1)
python3 -c "
import sys, fcntl, os, termios
tty = sys.argv[1]
fd = os.open(tty, os.O_RDWR | os.O_NOCTTY)
try:
    fcntl.ioctl(fd, termios.TIOCSTI, b'\\x00')
    print('TIOCSTI: OK')
except OSError as e:
    print(f'TIOCSTI: BLOCKED — errno={e.errno} {e}')
finally:
    os.close(fd)
" \"$PANE_TTY\"
```

Expected: `TIOCSTI: BLOCKED — errno=1 [Errno 1] Operation not permitted`. (If TIOCSTI is somehow allowed on this host, the e2e test does not validate the bug fix scenario; document and seek a host that exhibits the issue.)

- [ ] **Step 4.3: Reproduce the bug scenario in cct**

In a separate terminal: start cct (`cd ~/ccmux/ccmux-core-telegram && uv run ccmux-core-telegram` or however cct is launched per its README). In a tmux pane bound to a Telegram topic, manually enter copy mode (`Ctrl-b [`).

Send a text message in the bound Telegram topic. Observe:

- The pane should exit copy mode.
- The text should appear in claude's input prompt.
- claude should respond as normal.

Without the fix, the message would be silently swallowed (TIOCSTI ioctl raises EPERM, `KeyInjectionError` propagates up to PTB and is logged but the user sees nothing).

- [ ] **Step 4.4: Check cct logs for the formerly-noisy `_grace_timer` path**

Run normal cct usage for a few minutes (let claude finish a few prompts so the spinner-grace safety net fires).

Check the cct log file (`./log/...` per cct convention) for any `KeyInjectionError` entries from the `_flush_pending` / `_trigger_safety` path. Expected: none. (Pre-fix logs showed multiple per session.)

- [ ] **Step 4.5: Document verification result inline in the issue**

After successful e2e, post a comment on issue #14 summarizing the verification (TIOCSTI confirmed blocked on host, copy-mode → cancel → send works, grace_timer noise gone). This will become the close justification when v0.3.2 ships. Don't close the issue yet — that happens after the release/v0.3.2 PR merges to main.

No commit for this task (verification only).

---

## Task 5: Open PR `bugfix/send-keys-tiocsti-fallback` → `dev`

Tasks 1-4 produce three commits on `bugfix/send-keys-tiocsti-fallback` (plus the spec commit `bf6ecf7` already there). Open the PR for review and CI.

**Files:** none (PR metadata only).

- [ ] **Step 5.1: Push the branch**

Run: `cd ~/ccmux/ccmux-core && git push -u origin bugfix/send-keys-tiocsti-fallback`
Expected: branch pushed, prints PR-creation hint URL.

- [ ] **Step 5.2: Create the PR via `gh`**

Run:

```bash
cd ~/ccmux/ccmux-core
gh pr create --base dev --head bugfix/send-keys-tiocsti-fallback --title "fix(keys): cancel tmux mode + drop TIOCSTI fallback (#14)" --body "$(cat <<'EOF'
## Summary

- Remove the `TIOCSTI` fallback from `send_keys`. The kernel rejects
  `TIOCSTI` to any tty that is not the calling process's controlling
  terminal (or unless the caller has `CAP_SYS_ADMIN`), so the
  fallback never worked in real ccmux-core deployments where the
  bridge process and the target pane live in different terminals.
- When the pane is in any tmux mode (copy / view / choose / clock),
  `send_keys` now runs `tmux send-keys -X cancel` first, then
  proceeds with `send_via_tmux`. Mirrors the proven pattern from
  `ccmux-backend`.
- Rename `pane_in_copy_mode` to `pane_in_mode`. The probe queries
  `#{?pane_in_mode,1,0}` which is true for any tmux mode, not just
  copy mode. The old name lied about its scope.
- Side effect: the repeated `KeyInjectionError` log noise from
  `_grace_timer` / `_flush_pending` resolves automatically since the
  whole safety-net path flows through `send_keys`.

## Spec

[docs/superpowers/specs/2026-05-13-send-keys-tiocsti-fallback.md](docs/superpowers/specs/2026-05-13-send-keys-tiocsti-fallback.md)

## Test plan

- [x] `pytest tests/test_keys.py -v` — 10 tests pass.
- [x] `pytest tests/ -v` — full suite passes.
- [x] `pre-commit run --all-files` — clean.
- [x] Manual e2e on Ubuntu 20.04 / kernel 5.4 host (confirmed
  `TIOCSTI` blocked there): cct sends Telegram text to a pane in
  copy mode → pane exits copy mode → claude receives the prompt.
- [x] cct logs free of `KeyInjectionError` from the spinner_grace
  safety net during normal operation.

Closes #14 once `release/v0.3.2` lands on `main`.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: PR created. Capture the PR URL in the run output.

- [ ] **Step 5.3: Wait for required checks**

Required: `pytest py3.11`, `pytest py3.12`, `pre-commit`. All three must pass before merge.

Run: `gh pr checks --watch` (will block until all checks complete or fail).

Expected: all three green. If any fail, investigate the failure on the PR page, fix on the branch, push (re-runs checks), and re-watch.

- [ ] **Step 5.4: Merge the PR**

After all checks pass:

Run: `gh pr merge --merge --delete-branch=false`
(Use `--merge` to preserve the branch history per repo convention; do NOT delete the local branch yet — the release branch in Task 6 may want to inspect commits.)

Or, if the user prefers to merge through the GitHub UI, leave it for them and stop here.

---

## Task 6: Cut `release/v0.3.2`

Once the bugfix is on `dev`, prepare the release.

**Files:**
- Modify: `src/ccmux_core/_version.py` (bump string)
- Modify: `pyproject.toml` (bump `version = ...` line)
- Modify: `CHANGELOG.md` (prepend a `## [0.3.2]` section)

- [ ] **Step 6.1: Branch off `dev`**

Run:

```bash
cd ~/ccmux/ccmux-core
git checkout dev
git pull origin dev
git checkout -b release/v0.3.2
```

Expected: clean working tree, on `release/v0.3.2`.

- [ ] **Step 6.2: Bump `src/ccmux_core/_version.py`**

Read the file first to confirm format. Expected current contents:

```python
__version__ = "0.3.1"
```

Replace with:

```python
__version__ = "0.3.2"
```

- [ ] **Step 6.3: Bump `pyproject.toml`**

Locate the `version = "0.3.1"` line under `[project]` and change to `version = "0.3.2"`.

- [ ] **Step 6.4: Update `CHANGELOG.md`**

Insert this section directly after the existing `# Changelog` header (and after the existing front-matter blurb), before the `## [0.3.1]` section:

```markdown
## [0.3.2] - 2026-05-13

### Fixed

- `send_keys` no longer silently drops the prompt when the target
  tmux pane is in copy mode. The previous implementation fell
  back to `ioctl(TIOCSTI, ...)`, which the Linux kernel rejects
  by default for any tty that is not the calling process's
  controlling terminal. In real `ccmux-core` deployments the
  bridge process and the target pane live in different
  terminals, so the fallback always failed with `EPERM` and
  `KeyInjectionError` propagated up to the caller. Now `send_keys`
  runs `tmux send-keys -X cancel` to exit any active mode (copy /
  view / choose / clock) and then sends keys normally. Trade-off:
  in-mode invocations lose the user's copy-mode selection buffer,
  but the prompt actually gets delivered. Closes [#14].
- The `KeyInjectionError` noise observed in `ccmux-core-telegram`
  logs from the `_grace_timer` → `_trigger_safety` →
  `_flush_pending` path resolves as a side effect of the same
  fix, since every key-injection call flows through `send_keys`.

### Removed

- `send_via_tiocsti`, `_get_pane_tty`, `_encode_keys`, and the
  `KEYNAME_TO_BYTES` constant. The TIOCSTI code path was
  unreachable in real deployments (see Fixed above) and was
  masked by unit tests that mocked `os.open` and `fcntl.ioctl`.

### Changed

- `pane_in_copy_mode` renamed to `pane_in_mode`. The function
  probes `#{?pane_in_mode,1,0}` which is true for any tmux mode
  (copy / view / choose / clock), not just copy mode. No
  behavior change. Not backwards-compatible, but `ccmux-core`
  is in 0.x with no public-API stability promise.

[#14]: https://github.com/wuwenrui555/ccmux-core/issues/14
```

- [ ] **Step 6.5: Sanity check the version bump**

Run:

```bash
cd ~/ccmux/ccmux-core
python -c "from ccmux_core._version import __version__; print(__version__)"
```

Expected: `0.3.2`.

Run: `grep '^version = ' pyproject.toml`
Expected: `version = "0.3.2"`.

- [ ] **Step 6.6: Run the full test suite + pre-commit one more time**

Run: `pytest tests/ -v && pre-commit run --all-files`
Expected: all green.

- [ ] **Step 6.7: Commit the release bump**

```bash
cd ~/ccmux/ccmux-core
git add src/ccmux_core/_version.py pyproject.toml CHANGELOG.md
git commit -m "$(cat <<'EOF'
chore: bump version to 0.3.2 and update CHANGELOG

Release notes cover the send_keys TIOCSTI removal + cancel-mode
fallback (closes #14).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 6.8: Push and open the release PR**

```bash
cd ~/ccmux/ccmux-core
git push -u origin release/v0.3.2
gh pr create --base main --head release/v0.3.2 --title "release: v0.3.2" --body "$(cat <<'EOF'
## Summary

Release v0.3.2 — fixes #14 (send_keys silent-drop on copy-mode panes).

See [CHANGELOG.md](CHANGELOG.md) `## [0.3.2]` for full details.

## Test plan

- [x] `pytest tests/ -v` — full suite passes on dev.
- [x] `pre-commit run --all-files` — clean.
- [x] Manual e2e verified on the cct host (Ubuntu 20.04 / kernel 5.4).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 6.9: Wait for required checks and merge**

Run: `gh pr checks --watch`
Expected: all green (pytest py3.11, pytest py3.12, pre-commit).

Run: `gh pr merge --merge --delete-branch=false`
(Or via UI per user preference.)

---

## Task 7: Tag `v0.3.2` and back-merge

After `release/v0.3.2` lands on `main`, tag the release and back-merge into `dev`.

**Files:** none.

- [ ] **Step 7.1: Pull latest main and tag**

```bash
cd ~/ccmux/ccmux-core
git checkout main
git pull origin main
git tag -a v0.3.2 -m "v0.3.2 — fix #14 send_keys silent-drop on copy-mode panes"
git push origin v0.3.2
```

Expected: tag created and pushed.

- [ ] **Step 7.2: Back-merge `main` into `dev`**

```bash
cd ~/ccmux/ccmux-core
git checkout dev
git pull origin dev
git merge --no-ff main -m "chore: back-merge v0.3.2 from main"
git push origin dev
```

Expected: clean merge (no conflicts since `dev` was the source of `release/v0.3.2`).

- [ ] **Step 7.3: Close issue #14**

Run:

```bash
gh issue close 14 --comment "Fixed in v0.3.2 (release notes: https://github.com/wuwenrui555/ccmux-core/blob/v0.3.2/CHANGELOG.md). Manual e2e on the affected host confirms copy-mode → cancel → tmux send-keys delivers the prompt and clears the formerly-noisy spinner_grace KeyInjectionError logs."
```

Expected: issue closed with cross-link to release.

- [ ] **Step 7.4: Done**

Out of scope follow-up (separate cct PR, not part of this plan): bump
`.github/workflows/ci.yml:28` in `ccmux-core-telegram` from
`@v0.3.1` to `@v0.3.2` so cct CI tests against the version users
will actually pull at runtime.
