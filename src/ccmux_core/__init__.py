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
