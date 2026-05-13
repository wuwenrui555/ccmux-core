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
