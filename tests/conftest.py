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
    ):
        monkeypatch.delenv(var, raising=False)
    return tmp_path
