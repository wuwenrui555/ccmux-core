"""Shared pytest fixtures."""

import pytest


@pytest.fixture
def isolated_core_dir(tmp_path, monkeypatch):
    """Override CCMUX_CORE_DIR to a fresh tmp dir; clear known env vars."""
    monkeypatch.setenv("CCMUX_CORE_DIR", str(tmp_path))
    for var in (
        "CCMUX_CORE_SPINNER_GRACE",
        "CCMUX_CORE_PROCESS_PROBE_INTERVAL",
        "CCMUX_CORE_PROCESS_PROBE_STARTUP_GRACE",
        "CCMUX_CORE_CLAUDE_PROC_NAMES",
        "CCMUX_CORE_PRETTY_WIDTH",
        "CCMUX_CORE_HOOK_POLL_INTERVAL",
        "CCMUX_CORE_HOOK_POLL_MAX_DURATION",
        "CCMUX_CORE_SPINNER_POLL_INTERVAL",
        "CLAUDE_TAP_POLL_INTERVAL",
        "CLAUDE_TAP_POLL_MAX_DURATION",
        "CCMUX_SPINNER_POLL_INTERVAL",
    ):
        monkeypatch.delenv(var, raising=False)
    return tmp_path
