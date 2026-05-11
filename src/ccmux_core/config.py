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

_SETTINGS_ENV_FILENAME = "settings.env"
_LOADED_SETTINGS_FROM: list[Path] = []
_KEY_VALUE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


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


_load_settings_env_files()
