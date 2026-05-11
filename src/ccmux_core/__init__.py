"""ccmux-core: per-tmux-session state machine + stream multiplexer."""

# Eagerly load ccmux-core's settings.env (and mirror its
# CCMUX_CORE_HOOK_* / CCMUX_CORE_SPINNER_POLL_INTERVAL aliases into
# upstream env vars) BEFORE anything imports claude-tap or
# ccmux-spinner. Their own settings.env loaders run at module-load
# time and rely on os.environ being already populated; if they fire
# first, our setdefault-based mirror becomes a no-op.
from . import config as _config  # noqa: F401  (import for side effect)
from ._version import __version__
from .backend import Backend
from .discover import TmuxBinding, discover_tmux_sessions, list_live_tmux_bindings
from .error import BackendError, TmuxProbeError
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
