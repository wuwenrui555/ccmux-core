"""ccmux-core: per-tmux-session state machine + stream multiplexer.

v0.2 exposes three layers:

* **L0** — raw observations: ``Backend.transcript_items()``,
  ``Backend.events()``, ``Backend.spinners()``.
* **L1** — derived signals: ``Backend.state``,
  ``Backend.states()``, ``Backend.messages()``.
* **L2** — state-gated operations: ``Backend.send_prompt()``,
  ``Backend.interrupt()``, ``Backend.respond_permission()``,
  ``Backend.respond_exit_plan()``, ``Backend.respond_question()``,
  ``Backend.drop_to_tui()``, ``Backend.send_keys()``.

See the L2 design spec for the dedup table and state-guard rules.
"""

# Eagerly load ccmux-core's settings.env (and mirror its
# CCMUX_CORE_HOOK_* / CCMUX_CORE_SPINNER_POLL_INTERVAL aliases into
# upstream env vars) BEFORE anything imports claude-tap or
# ccmux-spinner.
from . import config as _config  # noqa: F401  (import for side effect)
from ._version import __version__
from .backend import Backend
from .bindings import (
    TmuxBinding,
    discover_tmux_sessions,
    list_live_tmux_bindings,
)
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
    # State family
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
