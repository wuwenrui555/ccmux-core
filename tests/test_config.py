"""Tests for config — env var defaults and parsing."""

from __future__ import annotations

from ccmux_core import config


def test_dir_defaults(isolated_core_dir, monkeypatch):
    monkeypatch.delenv("CCMUX_CORE_DIR", raising=False)
    assert "ccmux-core" in str(config.ccmux_core_dir())


def test_dir_from_env(isolated_core_dir):
    assert config.ccmux_core_dir() == isolated_core_dir


def test_spinner_grace_default(isolated_core_dir):
    assert config.spinner_grace() == 3.0


def test_spinner_grace_from_env(isolated_core_dir, monkeypatch):
    monkeypatch.setenv("CCMUX_CORE_SPINNER_GRACE", "2.5")
    assert config.spinner_grace() == 2.5


def test_spinner_grace_invalid_falls_back(isolated_core_dir, monkeypatch):
    monkeypatch.setenv("CCMUX_CORE_SPINNER_GRACE", "abc")
    assert config.spinner_grace() == 3.0


def test_process_probe_interval_default(isolated_core_dir):
    assert config.process_probe_interval() == 10.0


def test_process_probe_startup_grace_default(isolated_core_dir):
    assert config.process_probe_startup_grace() == 10.0


def test_claude_proc_names_default(isolated_core_dir):
    assert config.claude_proc_names() == frozenset({"claude", "node"})


def test_claude_proc_names_from_env(isolated_core_dir, monkeypatch):
    monkeypatch.setenv("CCMUX_CORE_CLAUDE_PROC_NAMES", "claude,node,python")
    assert config.claude_proc_names() == frozenset({"claude", "node", "python"})


def test_claude_proc_names_handles_whitespace(isolated_core_dir, monkeypatch):
    monkeypatch.setenv("CCMUX_CORE_CLAUDE_PROC_NAMES", "claude , node ,  python")
    assert config.claude_proc_names() == frozenset({"claude", "node", "python"})


def test_settings_env_file_loaded(isolated_core_dir, monkeypatch):
    """A settings.env file in CCMUX_CORE_DIR is sourced on import."""
    settings = isolated_core_dir / "settings.env"
    settings.write_text("CCMUX_CORE_SPINNER_GRACE=7\n")
    config._LOADED_SETTINGS_FROM.clear()
    config._load_settings_env_files()
    assert config.spinner_grace() == 7.0


def test_shell_export_wins_over_settings_env(isolated_core_dir, monkeypatch):
    """Shell-exported env values must override settings.env file."""
    settings = isolated_core_dir / "settings.env"
    settings.write_text("CCMUX_CORE_SPINNER_GRACE=7\n")
    monkeypatch.setenv("CCMUX_CORE_SPINNER_GRACE", "9")
    config._LOADED_SETTINGS_FROM.clear()
    config._load_settings_env_files()
    assert config.spinner_grace() == 9.0
