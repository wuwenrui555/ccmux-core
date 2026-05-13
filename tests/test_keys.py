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


def test_pane_in_mode_true():
    from unittest.mock import patch

    from ccmux_core.keys import pane_in_mode

    with patch("subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = "1\n"
        assert pane_in_mode("%0") is True


def test_pane_in_mode_false():
    from unittest.mock import patch

    from ccmux_core.keys import pane_in_mode

    with patch("subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = "0\n"
        assert pane_in_mode("%0") is False


def test_pane_in_mode_falls_back_to_false_on_tmux_error():
    """If detection itself fails, assume normal mode."""
    from unittest.mock import patch

    from ccmux_core.keys import pane_in_mode

    with patch("subprocess.run") as run:
        run.return_value.returncode = 1
        run.return_value.stdout = ""
        run.return_value.stderr = "can't find pane"
        assert pane_in_mode("%0") is False


def test_send_keys_skips_cancel_when_not_in_mode():
    """When pane is not in any tmux mode, send-keys runs directly
    with no cancel preamble."""
    from unittest.mock import patch

    from ccmux_core.keys import send_keys

    with (
        patch("ccmux_core.keys.pane_in_mode", return_value=False),
        patch("subprocess.run") as run,
    ):
        run.return_value.returncode = 0
        run.return_value.stderr = ""
        send_keys(pane_id="%0", keys="hi", literal=True)

    # Exactly one subprocess call: the send-keys.
    assert run.call_count == 1
    send_argv = run.call_args_list[0][0][0]
    assert send_argv[:4] == ["tmux", "send-keys", "-t", "%0"]
    assert "-l" in send_argv
    assert "hi" in send_argv


def test_send_keys_cancels_mode_then_sends_when_in_mode():
    """When pane is in any tmux mode, cancel the mode first, then
    send keys via tmux send-keys (the TIOCSTI fallback was removed
    in v0.3.2 because the kernel rejects TIOCSTI to a
    non-controlling tty by default — see issue #14)."""
    from unittest.mock import patch

    from ccmux_core.keys import send_keys

    with (
        patch("ccmux_core.keys.pane_in_mode", return_value=True),
        patch("subprocess.run") as run,
    ):
        run.return_value.returncode = 0
        run.return_value.stderr = ""
        send_keys(pane_id="%0", keys="hi", literal=True)

    # Two subprocess calls in order: cancel, then send-keys.
    assert run.call_count == 2
    cancel_argv = run.call_args_list[0][0][0]
    assert cancel_argv == ["tmux", "send-keys", "-X", "-t", "%0", "cancel"]
    send_argv = run.call_args_list[1][0][0]
    assert send_argv[:4] == ["tmux", "send-keys", "-t", "%0"]
    assert "-l" in send_argv
    assert "hi" in send_argv


def test_send_keys_proceeds_when_cancel_subprocess_fails():
    """If `tmux send-keys -X cancel` fails (non-zero rc), the
    subsequent send_via_tmux still runs. Caller errors only if
    that final send fails."""
    from unittest.mock import patch

    from ccmux_core.keys import send_keys

    call_results = [
        # First call: cancel — fails.
        type("R", (), {"returncode": 1, "stderr": "cancel oops"})(),
        # Second call: send_via_tmux — succeeds.
        type("R", (), {"returncode": 0, "stderr": ""})(),
    ]

    with (
        patch("ccmux_core.keys.pane_in_mode", return_value=True),
        patch("subprocess.run", side_effect=call_results) as run,
    ):
        # Should not raise — cancel failure is swallowed.
        send_keys(pane_id="%0", keys="hi", literal=True)

    assert run.call_count == 2
