"""Tests for the key-injection layer."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from ccmux_core.keys import KeyInjectionError, send_via_tmux


def test_send_via_tmux_literal_text():
    with patch("subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stderr = ""
        send_via_tmux(pane_id="%0", keys="hello", literal=True)
    args = run.call_args[0][0]
    assert args[:3] == ["tmux", "send-keys", "-t"]
    assert args[3] == "%0"
    # literal flag -l present
    assert "-l" in args
    assert "hello" in args


def test_send_via_tmux_named_key():
    with patch("subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stderr = ""
        send_via_tmux(pane_id="%0", keys="Enter", literal=False)
    args = run.call_args[0][0]
    # no literal flag for named keys
    assert "-l" not in args
    assert "Enter" in args


def test_send_via_tmux_list_of_keys():
    with patch("subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stderr = ""
        send_via_tmux(pane_id="%0", keys=["Up", "Up", "Enter"], literal=False)
    args = run.call_args[0][0]
    assert "Up" in args
    assert "Enter" in args


def test_send_via_tmux_raises_on_failure():
    with patch("subprocess.run") as run:
        run.return_value.returncode = 1
        run.return_value.stderr = "can't find pane"
        with pytest.raises(KeyInjectionError) as excinfo:
            send_via_tmux(pane_id="%0", keys="hi", literal=True)
        assert "can't find pane" in str(excinfo.value)
