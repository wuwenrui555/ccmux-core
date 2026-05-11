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
