"""Environment-variable / settings.env resolution for ccmux-core.

Mirrors :mod:`claude_tap.config` and :mod:`ccmux_spinner.config`:
same KEY=value format, same lookup order, same shell-exports-win
semantics. Parser duplicated (not imported) to keep ccmux-core's
runtime dependency footprint to ``{claude-tap, ccmux-spinner}``.

Recognized settings:

* ``CCMUX_CORE_DIR`` — state directory (default ``~/.ccmux-core``).
  Hosts the ``settings.env`` file.
* ``CCMUX_CORE_SPINNER_GRACE`` — seconds Working with an observed
  non-Spinner activity (None / IdleDecoration) staying current
  before falling back to ``Idle(interrupted)`` (default 3).
* ``CCMUX_CORE_PROCESS_PROBE_INTERVAL`` — seconds between
  successive ``tmux list-panes`` probes (default 10).
* ``CCMUX_CORE_PROCESS_PROBE_STARTUP_GRACE`` — seconds after
  Backend ``__aenter__`` during which no process probe runs
  (default 10).
* ``CCMUX_CORE_CLAUDE_PROC_NAMES`` — comma-separated set of
  foreground process names that count as "claude is alive"
  (default ``claude,node``).
* ``CCMUX_CORE_PRETTY_WIDTH`` — visual cell width used by
  ``ccmux-core watch`` (pretty mode) for both the separator line
  and the body trim cap (default 100). Independent of
  ``CLAUDE_TAP_PRETTY_WIDTH`` so a narrow split-pane viewer can
  shrink ccmux-core without affecting other tools.

Upstream-aliased settings (facade pattern):

The following ``CCMUX_CORE_*`` keys are not consumed by ccmux-core
directly. Setting them (in this file or as shell exports) mirrors
the value into the corresponding upstream env var via
``setdefault`` so ``claude-tap`` / ``ccmux-spinner`` read it as
their own. This lets users tune the whole stack from one
namespace + one settings file.

* ``CCMUX_CORE_HOOK_POLL_INTERVAL`` →
  ``CLAUDE_TAP_POLL_INTERVAL`` (events.jsonl tail cadence).
* ``CCMUX_CORE_HOOK_POLL_MAX_DURATION`` →
  ``CLAUDE_TAP_POLL_MAX_DURATION`` (mid-turn scoped polling
  lifetime; raise for long generations where mid-turn text would
  otherwise be delayed).
* ``CCMUX_CORE_SPINNER_POLL_INTERVAL`` →
  ``CCMUX_SPINNER_POLL_INTERVAL`` (tmux pane capture cadence).

Priority (highest first): shell export > ccmux-core alias >
upstream package's own settings.env > upstream default.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

DEFAULT_DIR = "~/.ccmux-core"
DEFAULT_SPINNER_GRACE = 3.0
DEFAULT_PROCESS_PROBE_INTERVAL = 10.0
DEFAULT_PROCESS_PROBE_STARTUP_GRACE = 10.0
DEFAULT_CLAUDE_PROC_NAMES = frozenset({"claude", "node"})
DEFAULT_PRETTY_WIDTH = 100

_SETTINGS_ENV_FILENAME = "settings.env"
_LOADED_SETTINGS_FROM: list[Path] = []
_KEY_VALUE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")

# CCMUX_CORE_* facade aliases for upstream env vars. The settings.env
# loader mirrors the user's CCMUX_CORE_X value into the corresponding
# upstream env var (via ``os.environ.setdefault``) so claude-tap and
# ccmux-spinner pick it up at their own read sites. ccmux-core itself
# never reads the LHS keys — they exist purely as a user-facing facade.
_UPSTREAM_ALIASES: dict[str, str] = {
    "CCMUX_CORE_HOOK_POLL_INTERVAL": "CLAUDE_TAP_POLL_INTERVAL",
    "CCMUX_CORE_HOOK_POLL_MAX_DURATION": "CLAUDE_TAP_POLL_MAX_DURATION",
    "CCMUX_CORE_SPINNER_POLL_INTERVAL": "CCMUX_SPINNER_POLL_INTERVAL",
}


def ccmux_core_dir() -> Path:
    raw = os.environ.get("CCMUX_CORE_DIR", DEFAULT_DIR)
    return Path(raw).expanduser()


def settings_env_path() -> Path:
    return ccmux_core_dir() / _SETTINGS_ENV_FILENAME


def spinner_grace() -> float:
    return _float_env("CCMUX_CORE_SPINNER_GRACE", DEFAULT_SPINNER_GRACE)


def process_probe_interval() -> float:
    return _float_env(
        "CCMUX_CORE_PROCESS_PROBE_INTERVAL", DEFAULT_PROCESS_PROBE_INTERVAL
    )


def process_probe_startup_grace() -> float:
    return _float_env(
        "CCMUX_CORE_PROCESS_PROBE_STARTUP_GRACE",
        DEFAULT_PROCESS_PROBE_STARTUP_GRACE,
    )


def claude_proc_names() -> frozenset[str]:
    raw = os.environ.get("CCMUX_CORE_CLAUDE_PROC_NAMES", "")
    if not raw:
        return DEFAULT_CLAUDE_PROC_NAMES
    names = {p.strip() for p in raw.split(",") if p.strip()}
    return frozenset(names) if names else DEFAULT_CLAUDE_PROC_NAMES


def pretty_width() -> int:
    """Visual cell width for ccmux-core watch pretty mode."""
    raw = os.environ.get("CCMUX_CORE_PRETTY_WIDTH", "")
    if not raw:
        return DEFAULT_PRETTY_WIDTH
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_PRETTY_WIDTH
    return value if value > 0 else DEFAULT_PRETTY_WIDTH


def loaded_settings_files() -> list[Path]:
    return list(_LOADED_SETTINGS_FROM)


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _parse_settings_env(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    out: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _KEY_VALUE_RE.match(line)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        if value and not (value.startswith('"') or value.startswith("'")):
            if "#" in value:
                value = value.split("#", 1)[0].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        out[key] = value
    return out


def _load_settings_env_files() -> None:
    """Source settings.env into os.environ once at import.

    Order (later wins among files; shell exports always win via
    ``setdefault``):
      1. ``./settings.env`` (cwd)
      2. ``$CCMUX_CORE_DIR/settings.env`` (global)

    After loading the files, :func:`_mirror_upstream_aliases` runs so
    facade keys (``CCMUX_CORE_HOOK_*`` etc.) propagate to the upstream
    env vars (``CLAUDE_TAP_*`` / ``CCMUX_SPINNER_*``).
    """
    paths = [Path(_SETTINGS_ENV_FILENAME), settings_env_path()]
    for path in paths:
        try:
            if not path.is_file():
                continue
        except OSError:
            continue
        values = _parse_settings_env(path)
        if not values:
            continue
        for key, val in values.items():
            os.environ.setdefault(key, val)
        _LOADED_SETTINGS_FROM.append(path)
    _mirror_upstream_aliases()


def _mirror_upstream_aliases() -> None:
    """For each ``CCMUX_CORE_*`` facade alias currently set in
    ``os.environ``, setdefault the corresponding upstream env var.

    Called by :func:`_load_settings_env_files` after the file load
    pass so that a value set via shell export *or* via ccmux-core's
    settings.env reaches claude-tap / ccmux-spinner at their next
    read. ``setdefault`` keeps shell-exported upstream values (e.g.
    a directly-exported ``CLAUDE_TAP_POLL_INTERVAL``) intact.

    Must run before claude-tap and ccmux-spinner's own settings.env
    loaders fire — see ``ccmux_core.__init__`` for the import-order
    contract.
    """
    for alias_key, upstream_key in _UPSTREAM_ALIASES.items():
        value = os.environ.get(alias_key)
        if value is not None:
            os.environ.setdefault(upstream_key, value)


_load_settings_env_files()
