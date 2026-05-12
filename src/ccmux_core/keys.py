"""Key injection: tmux send-keys (default) and TIOCSTI (copy-mode fallback).

Two paths share one logical input (pane_id + keys + literal flag):

* :func:`send_via_tmux` — uses ``tmux send-keys``. Stable, but is
  intercepted when the pane is in copy mode.

Task 8 adds the TIOCSTI fallback for copy-mode-aware injection.
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
            f"tmux send-keys failed for pane {pane_id!r}: " f"{result.stderr.strip()}"
        )
