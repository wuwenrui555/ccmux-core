"""Key injection: tmux send-keys (default) and TIOCSTI (copy-mode fallback).

Two paths share one logical input (pane_id + keys + literal flag):

* :func:`send_via_tmux` — uses ``tmux send-keys``. Stable, but is
  intercepted when the pane is in copy mode.

Task 8 adds the TIOCSTI fallback for copy-mode-aware injection.
"""

from __future__ import annotations

import fcntl
import os
import subprocess
import termios

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
            f"tmux send-keys failed for pane {pane_id!r}: {result.stderr.strip()}"
        )


# Fixed map: tmux key name → raw bytes for TIOCSTI injection.
# Only the keys ccmux-core emits are included.
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
    """Look up the pty path for a tmux pane (e.g. '/dev/pts/42')."""
    result = subprocess.run(
        ["tmux", "display", "-t", pane_id, "-p", "#{pane_tty}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise KeyInjectionError(
            f"tmux display(pane_tty) failed for {pane_id!r}: {result.stderr.strip()}"
        )
    tty = result.stdout.strip()
    if not tty:
        raise KeyInjectionError(f"pane {pane_id!r} has no pane_tty")
    return tty


def _encode_keys(keys: list[str], literal: bool) -> bytes:
    """Convert key names / literal text to raw bytes for TIOCSTI."""
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
        raise KeyInjectionError(f"TIOCSTI ioctl failed on {tty}: {e}") from e
    finally:
        os.close(fd)


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
    """Send keys to a pane, transparently handling copy mode.

    When the pane is in copy mode, ``tmux send-keys`` would be
    intercepted by copy-mode commands. Falls back to TIOCSTI to
    bypass tmux entirely so the user's copy-mode session stays
    intact.
    """
    if pane_in_mode(pane_id):
        send_via_tiocsti(pane_id, keys, literal=literal)
    else:
        send_via_tmux(pane_id, keys, literal=literal)
