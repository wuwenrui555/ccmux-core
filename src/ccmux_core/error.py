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
