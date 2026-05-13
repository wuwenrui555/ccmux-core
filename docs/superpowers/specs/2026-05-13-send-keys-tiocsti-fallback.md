<!-- markdownlint-disable MD024 -->

# `send_keys` copy-mode handling — drop TIOCSTI, cancel mode then `send-keys`

- **Date**: 2026-05-13
- **Repo**: `ccmux-core`
- **Status**: design draft, awaiting user review
- **Targets**: ccmux-core v0.3.2
- **Issue**: [#14](https://github.com/wuwenrui555/ccmux-core/issues/14)
- **Builds on**: [2026-05-12-ccmux-core-l2-design.md](2026-05-12-ccmux-core-l2-design.md)

## Context

`ccmux_core.keys.send_keys` is the single entry point for
key injection into a tmux pane. It currently dispatches between
two backends:

- `send_via_tmux`: the normal path, runs `tmux send-keys`.
- `send_via_tiocsti`: meant as a fallback for when the pane is in
  copy mode (where `tmux send-keys` is intercepted by copy-mode
  bindings instead of reaching the shell). Opens the target
  pane's pty and injects each byte via `ioctl(TIOCSTI, b)`.

The TIOCSTI fallback was designed to bypass tmux entirely so the
user's copy-mode buffer would be preserved. In practice it never
worked in any real ccmux-core deployment, and was masked by unit
tests that mock `os.open` and `fcntl.ioctl` so the failure mode
was never exercised.

### Why TIOCSTI fails in real deployments

The Linux kernel has, for ~25 years, restricted `TIOCSTI` in
`drivers/tty/tty_io.c`:

```c
case TIOCSTI:
    if (current->signal->tty != tty && !capable(CAP_SYS_ADMIN))
        return -EPERM;
```

TIOCSTI is permitted only when the target tty is the calling
process's own controlling terminal, OR when the caller has
`CAP_SYS_ADMIN`. In every real ccmux-core deployment, the bridge
process (e.g. `ccmux-core-telegram`) and the claude process live
in different tmux panes, with different controlling terminals.
`os.open(claude_pane_tty, O_RDWR)` succeeds (the pty is
world-writable per `crw--w----`), but the subsequent
`ioctl(TIOCSTI, ...)` returns `EPERM`.

This is **not** a "hardened kernel" issue. The original issue body
attributed it to `kernel.yama.ptrace_scope` and similar
hardening; that was a red-herring. The TIOCSTI restriction is a
default kernel behavior present on stock Ubuntu 20.04 (kernel
5.4) — verified empirically on the cct test machine
(`/dev/pts/17` with `errno=1 EPERM`). The newer
`dev.tty.legacy_tiocsti` sysctl (Linux 6.2+) and grsec patches
add additional restrictions on top, but the baseline rejection
already kills the fallback path everywhere.

### Discovery

- 2026-05-12: `ccmux-core-telegram` v0.1.0 e2e test on the cct host.
  User in a Telegram topic bound to a tmux pane that happened to be
  in copy mode. `Backend.send_prompt` → `send_keys("C-u",
  literal=False)` → `pane_in_copy_mode=True` →
  `send_via_tiocsti(pane_id, "C-u", literal=False)` →
  `ioctl(TIOCSTI, b'\x15')` → `OSError: [Errno 1] Operation not
  permitted` → `KeyInjectionError` → propagated to PTB top-level →
  Telegram message silently swallowed, claude pane received nothing.
- Same `KeyInjectionError` also fired during the safety-net path:
  `_grace_timer` → `_trigger_safety("spinner_grace")` → emits
  `Idle(interrupted)` → `_flush_pending` → `send_keys("C-u",
  literal=False)` → same TIOCSTI failure. Visible repeatedly in cct
  logs during normal operation.

### Why a single fix in `send_keys` covers all paths

Every key-injection call site in `Backend` flows through
`send_keys`:

- `send_prompt` (backend.py:321-323): `C-u` + text + `Enter`.
- `clear_buffer` (backend.py:349-350): `Escape` + `C-u`.
- `_flush_pending` (backend.py:593-595): `C-u` + combined + `Enter`.
  This is the path triggered by both `send_prompt` (when state was
  not Idle and the prompt got queued) and by every `_trigger_safety`
  call that lands on `Idle`, which includes `_grace_timer`'s
  `spinner_grace` trigger.
- `prompt_send` navigation keys (backend.py and prompt.py:
  Up/Down/Left/Right/Escape/Enter/Space/Tab) — all via `send_keys`.

Fixing the dispatcher fixes all callers; no `backend.py` change
needed.

## Goals

- Eliminate the silent-swallow bug: when the pane is in any tmux
  mode (copy / view / choose / clock), key injection from
  `send_keys` must reach the shell rather than getting intercepted
  or silently failing.
- Remove the TIOCSTI code path entirely, since it never worked in
  real deployments and its presence misled the original issue
  diagnosis.
- Preserve all existing public surface that callers actually use:
  `send_keys`, `send_via_tmux`, `KeyInjectionError`, the
  copy-mode probe (renamed for honesty about its scope).

## Non-Goals

- No `backend.py` changes. The fix is entirely inside
  `keys.py` + `tests/test_keys.py`.
- No new logging dependency. `ccmux_core` is currently logger-free
  (zero `import logging` in the package); this fix preserves that.
- No integration tests. `tests/` has no real-tmux fixtures today;
  introducing them is out of scope. Verification is unit tests
  (mocked) plus manual e2e via cct on the affected host.
- No downstream cct code changes. cct already pins
  `ccmux-core>=0.3.1` in `pyproject.toml` and will receive v0.3.2
  automatically. The cct CI pin bump (`@v0.3.1` → `@v0.3.2` in
  `.github/workflows/ci.yml:28`) is a separate cct PR after
  ccmux-core v0.3.2 ships, not part of this spec.

## Design

### New `send_keys`

```python
def send_keys(
    pane_id: str,
    keys: str | list[str],
    *,
    literal: bool,
) -> None:
    """Send keys to a pane, handling tmux mode interception.

    When the pane is in any tmux mode (copy / view / choose /
    clock), `tmux send-keys` is interpreted as a mode command
    instead of reaching the shell. Cancel the mode first
    (`send-keys -X cancel`, a no-op outside any mode), then send
    the keys normally.

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

Notes:

- `check=False` matches existing `subprocess.run` calls in this
  module. If `cancel` fails for some reason, the subsequent
  `send_via_tmux` will either succeed (mode wasn't actually
  blocking) or raise `KeyInjectionError` (existing behavior).
- No log on the cancel: ccmux-core has no logger and this
  fix doesn't introduce one. `send_via_tmux` and `pane_in_mode`
  already swallow non-fatal cases silently — staying consistent.

### Rename `pane_in_copy_mode` → `pane_in_mode`

The current implementation queries `#{?pane_in_mode,1,0}`, which
tmux returns as 1 for any of: copy-mode, view-mode, choose-mode
(tree / buffer / client / window), clock-mode. The function name
suggests copy-mode only and the docstring reinforces that, both
out of step with the actual behavior.

Rename the function and update the docstring to reflect the
broader scope. ccmux-core is in 0.x with no public-API
compatibility promise; rename is zero-cost (verified: only
internal callers and tests reference it; no external repo —
ccmux-spinner / claude-tap / ccmux-telegram / ccmux-backend / cct
— imports it).

### Removed code

Delete from `keys.py`:

- `KEYNAME_TO_BYTES` constant (the bytes table used only by TIOCSTI).
- `_get_pane_tty` function (TIOCSTI helper to look up the pane's pty path).
- `_encode_keys` function (TIOCSTI helper to encode key names to bytes).
- `send_via_tiocsti` function.
- `import fcntl`, `import os`, `import termios` (no other users in this module).
- The "Task 8 adds the TIOCSTI fallback" line from the module docstring header.

`KeyInjectionError` stays — `send_via_tmux` still raises it.

## Tests

`tests/test_keys.py` — replace the TIOCSTI-heavy test surface
with the new dispatcher behavior. Final test set:

- Keep, unchanged:
  - `test_send_via_tmux_literal_text`
  - `test_send_via_tmux_named_key`
  - `test_send_via_tmux_list_of_keys`
  - `test_send_via_tmux_raises_on_failure`
- Rename (`pane_in_copy_mode` → `pane_in_mode`), no logic change:
  - `test_pane_in_copy_mode_true` → `test_pane_in_mode_true`
  - `test_pane_in_copy_mode_false` → `test_pane_in_mode_false`
  - `test_pane_in_copy_mode_falls_back_to_false_on_tmux_error` →
    `test_pane_in_mode_falls_back_to_false_on_tmux_error`
- Replace dispatcher tests:
  - `test_send_keys_dispatches_to_tmux_when_normal_mode` becomes
    `test_send_keys_skips_cancel_when_not_in_mode`. Mock
    `pane_in_mode` → False, `subprocess.run`. Assert exactly one
    `subprocess.run` call (the `send_via_tmux` invocation). No
    cancel command.
  - `test_send_keys_dispatches_to_tiocsti_when_copy_mode` becomes
    `test_send_keys_cancels_mode_then_sends_when_in_mode`. Mock
    `pane_in_mode` → True, `subprocess.run`. Assert two
    `subprocess.run` calls in order: first argv =
    `["tmux", "send-keys", "-X", "-t", "%0", "cancel"]`, second
    argv begins with `["tmux", "send-keys", "-t", "%0"]` and ends
    with the literal text.
- Add:
  - `test_send_keys_proceeds_when_cancel_subprocess_fails`. Mock
    `pane_in_mode` → True. First `subprocess.run` returns rc=1
    (cancel failed for some reason); second `subprocess.run`
    returns rc=0. Assert no exception raised, both calls made.
- Delete:
  - `test_keyname_to_bytes_table_covers_required_keys`
  - `test_send_via_tiocsti_writes_literal_text`
  - `test_send_via_tiocsti_named_key_resolves_to_bytes`
  - `test_send_via_tiocsti_unknown_named_key_raises`
  - `test_send_via_tiocsti_closes_fd_on_ioctl_failure`

## Verification

- `pytest tests/test_keys.py -v` — all green, no skipped/xfail.
- `pytest tests/ -v` — all green (other modules unaffected).
- `pre-commit run --all-files` — clean.
- Manual e2e on the affected host (Ubuntu 20.04, kernel 5.4,
  TIOCSTI restricted): in cct, send a Telegram message to a topic
  bound to a tmux pane that is currently in copy mode; confirm
  the message lands in claude's input box and the pane exits copy
  mode.
- ccmux-core CI required-checks pass: pytest py3.11, pytest
  py3.12, pre-commit.

## Release plan

git-flow, per repo convention:

1. Branch already created: `bugfix/send-keys-tiocsti-fallback` off `dev`.
2. Spec commit on this branch (this document).
3. Implementation commits via subagent-driven-development.
4. PR `bugfix/send-keys-tiocsti-fallback` → `dev`. Required checks
   must pass before merge.
5. After merge to `dev`: cut `release/v0.3.2`. Bump `_version.py`
   and `pyproject.toml` to `0.3.2`. Update `CHANGELOG.md`:
   - **Fixed**: copy-mode silent-swallow bug (link #14). Note the
     `_grace_timer` log noise also resolves as a side effect.
   - **Removed**: `send_via_tiocsti`, `_get_pane_tty`,
     `_encode_keys`, `KEYNAME_TO_BYTES`. Reason: kernel rejects
     TIOCSTI to non-controlling tty by default, so the path was
     never reachable in real deployments.
   - **Changed**: `pane_in_copy_mode` renamed to `pane_in_mode`
     (no public-API compatibility promise in 0.x).
6. PR `release/v0.3.2` → `main`. Required checks must pass.
7. Tag `v0.3.2` on `main`.
8. Back-merge `main` → `dev`.
9. Close #14, cross-link to v0.3.2 release notes.

Out of scope, follow-up:

- Separate cct PR: bump `.github/workflows/ci.yml:28` from
  `@v0.3.1` to `@v0.3.2`. cct runtime pin
  (`ccmux-core>=0.3.1`) does not need to change.
